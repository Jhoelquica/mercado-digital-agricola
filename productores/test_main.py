"""
Tests de la entidad Chacra y su vínculo con RegistroProduccion.

Cubre:
- creación válida de chacra
- código duplicado para el MISMO productor -> 400
- mismo código para dos productores DISTINTOS -> ambos OK (el UniqueConstraint es compuesto)
- crear RegistroProduccion con una chacra que no es del productor autenticado -> 403

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de
postgresql + create_all() al importar main) — no se puede sustituir por SQLite.
No se mockea nada: crear_registro_produccion no llama a servicios externos cuando el
registro aún no está cosechado (cantidad_cosechada is None), y el test de 403 falla
antes de llegar a crear el registro.
"""
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)


def _auth(sub: str, rol: str = "productor"):
    """Simula al usuario autenticado. Se sobreescribe `verificar_token` (dependencia estable),
    no `requiere_rol(...)` que genera un closure nuevo por llamada."""
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}


def _nuevo_productor() -> str:
    """Crea un productor con un usuario nuevo y deja ese usuario como el autenticado.
    Devuelve el `sub` (usuario_id) usado."""
    sub = str(uuid.uuid4())
    _auth(sub)
    resp = client.post("/productores", json={"nombre": f"Productor {sub[:8]}"})
    assert resp.status_code == 200, resp.text
    return sub


def _chacra_payload(codigo="CH-01", nombre="Chacra del cerro"):
    return {
        "codigo": codigo,
        "nombre": nombre,
        "ubicacion_latitud": -13.16,
        "ubicacion_longitud": -74.22,
    }


def _registro_payload(chacra_id):
    siembra = datetime(2026, 1, 1)
    return {
        "chacra_id": str(chacra_id),
        "cultivo": "papa",
        "numero_parcelas": 2,
        "costo_semillas": 100.0,
        "costo_insumos": 50.0,
        "unidad_medida": "kg",
        "fecha_siembra": siembra.isoformat(),
        "fecha_cosecha_estimada": (siembra + timedelta(days=120)).isoformat(),
    }


def test_crear_chacra_valida():
    _nuevo_productor()
    resp = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["codigo"] == "CH-01"
    assert cuerpo["nombre"] == "Chacra del cerro"
    assert cuerpo["ubicacion_latitud"] == -13.16
    assert "id" in cuerpo and "productor_id" in cuerpo


def test_codigo_duplicado_mismo_productor_falla_400():
    _nuevo_productor()
    primera = client.post("/chacras", json=_chacra_payload(codigo="CH-07"))
    assert primera.status_code == 200, primera.text

    repetida = client.post("/chacras", json=_chacra_payload(codigo="CH-07"))
    assert repetida.status_code == 400
    assert repetida.json()["detail"] == "Ya tienes una chacra registrada con el código 'CH-07'."


def test_mismo_codigo_dos_productores_distintos_ok():
    _nuevo_productor()
    r1 = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))
    assert r1.status_code == 200, r1.text

    _nuevo_productor()  # segundo productor, usuario distinto
    r2 = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))
    assert r2.status_code == 200, r2.text

    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["productor_id"] != r2.json()["productor_id"]


def test_registro_produccion_con_chacra_ajena_falla_403():
    # Productor A crea una chacra.
    _nuevo_productor()
    chacra_a = client.post("/chacras", json=_chacra_payload(codigo="CH-A"))
    assert chacra_a.status_code == 200, chacra_a.text
    chacra_a_id = chacra_a.json()["id"]

    # Productor B (usuario distinto) intenta usar la chacra de A.
    _nuevo_productor()
    resp = client.post("/productores/produccion", json=_registro_payload(chacra_a_id))

    assert resp.status_code == 403
    assert resp.json()["detail"] == "La chacra especificada no pertenece a tu cuenta."


def test_registro_produccion_con_chacra_propia_ok():
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-OK"))
    assert chacra.status_code == 200, chacra.text
    chacra_id = chacra.json()["id"]

    resp = client.post("/productores/produccion", json=_registro_payload(chacra_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["chacra_id"] == chacra_id
