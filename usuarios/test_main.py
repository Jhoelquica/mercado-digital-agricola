"""
Tests del sistema de invitaciones por código de un solo uso (rol admin).

Cubre: creación por admin, rechazo por no-admin (403), revocación de invitación no usada,
rechazo de revocación de invitación ya usada (400), validación de código expirado y de
código inexistente. (+1 extra: validación de código válido, para cubrir el camino feliz
de validar_codigo_invitacion.)

Requiere Postgres real según database.py (tipos UUID de postgresql + FK a usuarios +
create_all() al importar main). No se mockea nada; el admin y los usuarios consumidores
se crean vía POST /usuarios/registro (que hoy acepta cualquier rol sin validar — eso lo
gatea la sub-entrega B). Las invitaciones "usada" y "expirada" se insertan directo por
SessionLocal porque su consumo real todavía no existe.
"""
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)


def _auth(user_id: str, rol: str):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": user_id, "rol": rol}


def _crear_usuario(rol: str) -> str:
    resp = client.post("/usuarios/registro", json={
        "nombre": f"Usuario {rol}",
        # dominio real (no .test/.example, que email-validator rechaza como reservados)
        "email": f"qa-{uuid.uuid4().hex}@chakrashop.pe",
        "password": "test1234",
        "rol": rol,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _admin_autenticado() -> str:
    admin_id = _crear_usuario("admin")
    _auth(admin_id, "admin")
    return admin_id


def _insertar_invitacion(*, creado_por, codigo=None, usado=False, usado_por=None,
                         fecha_expiracion=None, rol_destino="verificador"):
    """Inserta una Invitacion directo en la BD — para estados que el flujo normal aún no
    puede producir (usada, expirada)."""
    db = main.SessionLocal()
    try:
        inv = main.models.Invitacion(
            codigo=codigo or f"cod-{uuid.uuid4().hex[:14]}",
            rol_destino=rol_destino,
            usado=usado,
            usado_por=usado_por,
            creado_por=creado_por,
            fecha_expiracion=fecha_expiracion or (datetime.utcnow() + timedelta(days=7)),
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)
        return {"id": str(inv.id), "codigo": inv.codigo}
    finally:
        db.close()


def test_crear_invitacion_por_admin():
    _admin_autenticado()
    resp = client.post("/admin/invitaciones", json={"rol_destino": "verificador"})

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["rol_destino"] == "verificador"
    assert cuerpo["usado"] is False
    assert cuerpo["usado_por"] is None
    assert cuerpo["codigo"]
    assert cuerpo["fecha_expiracion"]
    assert cuerpo["id"]


def test_crear_invitacion_no_admin_falla_403():
    comprador_id = _crear_usuario("comprador")
    _auth(comprador_id, "comprador")
    resp = client.post("/admin/invitaciones", json={"rol_destino": "verificador"})
    assert resp.status_code == 403


def test_revocar_invitacion_no_usada():
    _admin_autenticado()
    creada = client.post("/admin/invitaciones", json={"rol_destino": "verificador"})
    inv_id = creada.json()["id"]

    resp = client.delete(f"/admin/invitaciones/{inv_id}")
    assert resp.status_code == 200, resp.text

    listado = client.get("/admin/invitaciones").json()
    assert all(i["id"] != inv_id for i in listado)


def test_revocar_invitacion_usada_falla_400():
    admin_id = _admin_autenticado()
    consumidor_id = _crear_usuario("verificador")
    inv = _insertar_invitacion(creado_por=admin_id, usado=True, usado_por=consumidor_id)

    resp = client.delete(f"/admin/invitaciones/{inv['id']}")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "No se puede revocar una invitación ya utilizada"


def test_validar_codigo_expirado():
    admin_id = _admin_autenticado()
    inv = _insertar_invitacion(
        creado_por=admin_id,
        fecha_expiracion=datetime.utcnow() - timedelta(days=1),
    )
    resp = client.get(f"/admin/invitaciones/validar/{inv['codigo']}")

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["valido"] is False
    assert "expir" in cuerpo["motivo"].lower()
    assert cuerpo["rol_destino"] == "verificador"


def test_validar_codigo_inexistente():
    _admin_autenticado()
    resp = client.get(f"/admin/invitaciones/validar/no-existe-{uuid.uuid4().hex}")

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["valido"] is False
    assert cuerpo["rol_destino"] is None


def test_validar_codigo_valido():
    _admin_autenticado()
    creada = client.post("/admin/invitaciones", json={"rol_destino": "verificador"})
    codigo = creada.json()["codigo"]

    resp = client.get(f"/admin/invitaciones/validar/{codigo}")
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["valido"] is True
    assert cuerpo["rol_destino"] == "verificador"
    assert cuerpo["motivo"] is None
