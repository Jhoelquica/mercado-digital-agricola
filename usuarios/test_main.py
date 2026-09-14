"""
Tests del sistema de invitaciones + registro gateado por rol restringido + login/registro con
Google (POST /usuarios/oauth/google).

Cubre: admin/invitaciones (crear, 403 no-admin, revocar, revocar-usada, validar
existente/expirado/inexistente), POST /usuarios/registro (comprador abierto, verificador
sin código -> 400, con código válido -> 200 + invitación usada, código de otro rol_destino
-> 400, código usado -> 400, código expirado -> 400, y todo-o-nada: si el registro falla
por email duplicado la invitación NO queda usada), y POST /usuarios/oauth/google (login por
google_id existente, vinculación automática por email de una cuenta con contraseña, registro
nuevo con rol abierto/restringido reusando el mismo gating que el registro normal, id_token
inválido -> 401, y el login normal contra una cuenta sin contraseña -> 401 sin excepción).

Requiere Postgres real según database.py (UUID de postgresql + FK a usuarios + create_all()
al importar main). No se mockea nada salvo google_id_token.verify_oauth2_token (única llamada
saliente real de este servicio) — nunca se golpea la red de Google de verdad. Los usuarios de
rol restringido (admin/verificador) se insertan directo por SessionLocal porque
/usuarios/registro ya no los deja auto-crearse sin invitación; los estados "usada"/"expirada"
de invitación también se siembran directo.
"""
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

import main
import seguridad
from auth import verificar_token

client = TestClient(main.app)


def _decodificar_jwt(token: str) -> dict:
    return jose_jwt.decode(token, seguridad.JWT_SECRET, algorithms=[seguridad.JWT_ALGORITHM])


def _mockear_google(monkeypatch, *, sub=None, email=None, nombre="Usuario Google"):
    """Reemplaza main.google_id_token.verify_oauth2_token: nunca golpea la red de Google de
    verdad. Devuelve el payload usado, para que el test pueda referenciar el mismo sub/email."""
    payload = {"sub": sub or f"google-{uuid.uuid4().hex}", "email": email or _email(), "name": nombre}
    monkeypatch.setattr(main.google_id_token, "verify_oauth2_token", lambda *a, **kw: payload)
    return payload


def _mockear_google_invalido(monkeypatch):
    def _falla(*a, **kw):
        raise ValueError("Token inválido o expirado")
    monkeypatch.setattr(main.google_id_token, "verify_oauth2_token", _falla)


def _auth(user_id: str, rol: str):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": user_id, "rol": rol}


def _email():
    # dominio real (no .test/.example, que email-validator rechaza como reservados)
    return f"qa-{uuid.uuid4().hex}@chakrashop.pe"


def _crear_usuario(rol: str) -> str:
    """Roles abiertos -> vía POST /usuarios/registro. Roles restringidos (verificador/admin)
    -> insert directo, porque ese endpoint ahora exige un código de invitación para ellos."""
    if rol in main.ROLES_DE_REGISTRO_ABIERTO:
        resp = client.post("/usuarios/registro", json={
            "nombre": f"Usuario {rol}", "email": _email(), "password": "test1234", "rol": rol,
        })
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    db = main.SessionLocal()
    try:
        u = main.models.Usuario(nombre=f"Usuario {rol}", email=_email(),
                                password_hash="x", rol=rol)
        db.add(u)
        db.commit()
        db.refresh(u)
        return str(u.id)
    finally:
        db.close()


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


# ============ Registro gateado por invitación (sub-entrega D) ============

def _registro(rol, codigo=None):
    body = {"nombre": f"Reg {rol}", "email": _email(), "password": "test1234", "rol": rol}
    if codigo is not None:
        body["codigo_invitacion"] = codigo
    return client.post("/usuarios/registro", json=body)


def _invitacion_directa(rol_destino="verificador", **kw):
    admin_id = _crear_usuario("admin")
    return _insertar_invitacion(creado_por=admin_id, rol_destino=rol_destino, **kw)


def test_registro_comprador_sin_codigo_sigue_abierto():
    resp = _registro("comprador")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rol"] == "comprador"


def test_registro_verificador_sin_codigo_falla_400():
    resp = _registro("verificador")
    assert resp.status_code == 400
    assert "invitación" in resp.json()["detail"].lower()


def test_registro_verificador_con_codigo_valido_ok_y_marca_usada():
    inv = _invitacion_directa("verificador")
    resp = _registro("verificador", codigo=inv["codigo"])
    assert resp.status_code == 200, resp.text
    nuevo_id = resp.json()["id"]
    assert resp.json()["rol"] == "verificador"

    db = main.SessionLocal()
    try:
        row = db.query(main.models.Invitacion).filter(
            main.models.Invitacion.codigo == inv["codigo"]
        ).first()
        assert row.usado is True
        assert str(row.usado_por) == nuevo_id
    finally:
        db.close()


def test_registro_verificador_con_codigo_de_otro_rol_falla_400():
    inv = _invitacion_directa("admin")  # código emitido para "admin"
    resp = _registro("verificador", codigo=inv["codigo"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Este código no habilita el rol solicitado."


def test_registro_verificador_con_codigo_usado_falla_400():
    consumidor_id = _crear_usuario("verificador")
    inv = _invitacion_directa("verificador", usado=True, usado_por=consumidor_id)
    resp = _registro("verificador", codigo=inv["codigo"])
    assert resp.status_code == 400
    assert "utiliz" in resp.json()["detail"].lower()


def test_registro_con_codigo_expirado_falla_400():
    inv = _invitacion_directa("verificador", fecha_expiracion=datetime.utcnow() - timedelta(days=1))
    resp = _registro("verificador", codigo=inv["codigo"])
    assert resp.status_code == 400
    assert "expir" in resp.json()["detail"].lower()


def test_codigo_valido_pero_email_duplicado_no_marca_invitacion_usada():
    """Todo o nada: si la creación del usuario falla (email repetido), la invitación NO
    debe quedar marcada como usada."""
    email = _email()
    primera = client.post("/usuarios/registro", json={
        "nombre": "Primero", "email": email, "password": "test1234", "rol": "comprador",
    })
    assert primera.status_code == 200

    inv = _invitacion_directa("verificador")
    repetida = client.post("/usuarios/registro", json={
        "nombre": "Segundo", "email": email, "password": "test1234",
        "rol": "verificador", "codigo_invitacion": inv["codigo"],
    })
    assert repetida.status_code == 409

    db = main.SessionLocal()
    try:
        row = db.query(main.models.Invitacion).filter(
            main.models.Invitacion.codigo == inv["codigo"]
        ).first()
        assert row.usado is False
        assert row.usado_por is None
    finally:
        db.close()


# ============ Login/registro con Google (POST /usuarios/oauth/google) ============

def test_oauth_google_login_cuenta_existente_por_google_id(monkeypatch):
    """Ya hay un Usuario vinculado a este google_id (de un login/registro anterior) — el
    endpoint debe reconocerlo y emitir un JWT con SU rol de siempre, sin tocar nada más."""
    google_sub = f"google-{uuid.uuid4().hex}"
    db = main.SessionLocal()
    try:
        u = main.models.Usuario(nombre="Ya Vinculado", email=_email(), password_hash=None,
                                 google_id=google_sub, rol="productor")
        db.add(u)
        db.commit()
        db.refresh(u)
        usuario_id = str(u.id)
    finally:
        db.close()

    _mockear_google(monkeypatch, sub=google_sub)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 200, resp.text
    payload = _decodificar_jwt(resp.json()["access_token"])
    assert payload["sub"] == usuario_id
    assert payload["rol"] == "productor"


def test_oauth_google_vincula_automaticamente_cuenta_con_password_existente(monkeypatch):
    """No hay match por google_id, pero SÍ por email — una cuenta creada con contraseña antes
    de esta sub-entrega — debe vincularse (guardar google_id) y devolver login con su rol de
    siempre, no un registro nuevo."""
    usuario_id = _crear_usuario("productor")
    db = main.SessionLocal()
    try:
        u = db.query(main.models.Usuario).filter(main.models.Usuario.id == usuario_id).one()
        email = u.email
        assert u.google_id is None
    finally:
        db.close()

    google_sub = f"google-{uuid.uuid4().hex}"
    _mockear_google(monkeypatch, sub=google_sub, email=email)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 200, resp.text
    payload = _decodificar_jwt(resp.json()["access_token"])
    assert payload["sub"] == usuario_id
    assert payload["rol"] == "productor"

    db = main.SessionLocal()
    try:
        u = db.query(main.models.Usuario).filter(main.models.Usuario.id == usuario_id).one()
        assert u.google_id == google_sub
    finally:
        db.close()


def test_oauth_google_registro_nuevo_con_rol_abierto_funciona(monkeypatch):
    for rol in ("comprador", "productor", "repartidor"):
        email = _email()
        _mockear_google(monkeypatch, email=email, nombre=f"Nuevo {rol}")
        resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token", "rol": rol})

        assert resp.status_code == 200, resp.text
        payload = _decodificar_jwt(resp.json()["access_token"])
        assert payload["rol"] == rol

        db = main.SessionLocal()
        try:
            u = db.query(main.models.Usuario).filter(main.models.Usuario.email == email).one()
            assert u.password_hash is None  # cuenta 100% Google, sin contraseña propia
            assert u.google_id is not None
            assert u.rol == rol
        finally:
            db.close()


def test_oauth_google_sin_rol_en_registro_nuevo_falla_400(monkeypatch):
    """Sin cuenta previa (ni por google_id ni por email) y sin rol elegido — el caso típico de
    intentar "Continuar con Google" desde el tab de Login sin tener cuenta todavía."""
    _mockear_google(monkeypatch)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Selecciona un rol antes de continuar con Google"


def test_oauth_google_rol_restringido_sin_codigo_falla_400_mismo_mensaje_que_registro_normal(monkeypatch):
    _mockear_google(monkeypatch)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token", "rol": "verificador"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Registrarse como 'verificador' requiere un código de invitación"


def test_oauth_google_rol_restringido_con_codigo_valido_funciona_y_marca_usada(monkeypatch):
    inv = _invitacion_directa("verificador")
    email = _email()
    _mockear_google(monkeypatch, email=email)
    resp = client.post("/usuarios/oauth/google", json={
        "id_token": "fake-token", "rol": "verificador", "codigo_invitacion": inv["codigo"],
    })

    assert resp.status_code == 200, resp.text
    payload = _decodificar_jwt(resp.json()["access_token"])
    assert payload["rol"] == "verificador"

    db = main.SessionLocal()
    try:
        row = db.query(main.models.Invitacion).filter(
            main.models.Invitacion.codigo == inv["codigo"]
        ).first()
        assert row.usado is True
        assert str(row.usado_por) == payload["sub"]
    finally:
        db.close()


def test_oauth_google_id_token_invalido_falla_401(monkeypatch):
    _mockear_google_invalido(monkeypatch)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "token-basura"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token de Google inválido o expirado"


def test_login_normal_con_password_hash_none_falla_401_sin_excepcion():
    """Una cuenta 100% Google (sin contraseña propia) no debe poder loguearse por el endpoint
    normal — y sobre todo no debe explotar con una excepción sin capturar al intentarlo."""
    email = _email()
    db = main.SessionLocal()
    try:
        u = main.models.Usuario(nombre="Solo Google", email=email, password_hash=None,
                                 google_id=f"google-{uuid.uuid4().hex}", rol="comprador")
        db.add(u)
        db.commit()
    finally:
        db.close()

    resp = client.post("/usuarios/login", json={"email": email, "password": "cualquier-cosa"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Email o contraseña incorrectos"


# ============ Bootstrap del primer admin (sembrar_admin_inicial) ============
# Se llama a la función directo (no vía TestClient/lifespan ASGI, cuyo timing de startup varía
# entre versiones de starlette) — sigue siendo la función real, sin mockear la BD.
#
# `_borrar_todos_los_admins()` deja la tabla sin admins para poder probar "no hay ninguno" de
# forma determinística en una BD que comparten todos los tests de este archivo. Es seguro:
# ningún otro test de este archivo asume que los admins de OTROS tests sigan existiendo (cada
# uno crea el suyo con email random cuando lo necesita) — por eso estos tests van al final.

def _borrar_todos_los_admins():
    """Deja la tabla usuarios sin ningún rol=admin. Primero hay que soltar las Invitacion que
    los referencian (creado_por / usado_por) o el DELETE choca con la FK."""
    db = main.SessionLocal()
    try:
        admin_ids = [a.id for a in db.query(main.models.Usuario).filter(
            main.models.Usuario.rol == "admin"
        ).all()]
        if admin_ids:
            db.query(main.models.Invitacion).filter(
                main.models.Invitacion.creado_por.in_(admin_ids)
            ).delete(synchronize_session=False)
            db.query(main.models.Invitacion).filter(
                main.models.Invitacion.usado_por.in_(admin_ids)
            ).delete(synchronize_session=False)
            db.query(main.models.Usuario).filter(
                main.models.Usuario.id.in_(admin_ids)
            ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_bootstrap_sin_env_vars_no_hace_nada(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", None)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", None)
    _borrar_todos_los_admins()

    main.sembrar_admin_inicial()

    db = main.SessionLocal()
    try:
        assert db.query(main.models.Usuario).filter(main.models.Usuario.rol == "admin").first() is None
    finally:
        db.close()


def test_bootstrap_crea_admin_si_no_hay_ninguno(monkeypatch):
    email = f"boot-{uuid.uuid4().hex}@chakrashop.pe"
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", email)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", "un-password-cualquiera")
    _borrar_todos_los_admins()

    main.sembrar_admin_inicial()

    db = main.SessionLocal()
    try:
        creado = db.query(main.models.Usuario).filter(main.models.Usuario.email == email).first()
        assert creado is not None
        assert creado.rol == "admin"
    finally:
        db.close()


def test_bootstrap_no_duplica_si_ya_existe_admin(monkeypatch):
    _crear_usuario("admin")  # ya hay al menos un admin (con email random, no el del bootstrap)
    email_que_no_deberia_crearse = f"boot-{uuid.uuid4().hex}@chakrashop.pe"
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", email_que_no_deberia_crearse)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", "otro-password")

    main.sembrar_admin_inicial()

    db = main.SessionLocal()
    try:
        no_se_creo = db.query(main.models.Usuario).filter(
            main.models.Usuario.email == email_que_no_deberia_crearse
        ).first()
        assert no_se_creo is None
    finally:
        db.close()
