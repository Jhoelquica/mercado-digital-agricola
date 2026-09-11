"""
Tests del estado borrador/publicado de Producto y su interacción con el catálogo público.

Cubre:
- GET /productos (catálogo público) excluye productos en "borrador"
- GET /productos/mios trae ambos estados, solo del productor autenticado
- PATCH .../publicar: sin precio -> 400, sin imagen -> 400, con todo completo -> 200,
  ya publicado -> 400
- POST /productos/interno/crear-desde-cosecha: sin el secreto correcto -> 403,
  con el secreto correcto crea el borrador esperado (sin precio/categoría, no visible en el
  catálogo)

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de
postgresql + create_all() al importar main) — no se puede sustituir por SQLite. No requiere
Redis real: cache.py se degrada a "sin caché" solo con que Redis no responda (ver su propio
docstring), así que un Redis inalcanzable en el entorno de test no rompe nada, solo hace que
cada GET pegue directo a Postgres.

crear_producto (el flujo manual POST /productos) y las operaciones sobre imágenes/publicar
validan el perfil de productor llamando a PRODUCTORES_URL/productores/me con httpx.Client()
crudo (no un helper con circuit breaker) — se mockea reemplazando main.httpx.Client por un
stub, en vez de levantar un servicio de Productores real. Los productos que estos tests
necesitan como fixture se insertan directo en la base vía SessionLocal(), no a través de
POST /productos, para no depender de ese mock en cada test que no está probando ese endpoint
puntual.
"""
import uuid

from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)

# listar_mis_productos y publicar_producto declaran, además de Depends(requiere_rol(...)), un
# Depends(security) aparte (para reenviar el token crudo a Productores) — overridear solo
# verificar_token no alcanza para ese segundo, independiente HTTPBearer(): sin un header
# Authorization real en la petición, FastAPI lo rechaza con 401 antes de llegar al cuerpo del
# endpoint. El valor en sí no importa (verificar_token está overrideado y nunca lo decodifica).
HEADERS_AUTH = {"Authorization": "Bearer faketoken"}


def _auth(sub: str, rol: str = "productor"):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}


class _RespuestaFalsa:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class _ClienteFalso:
    def __init__(self, status_code, data):
        self._status_code = status_code
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, *args, **kwargs):
        return _RespuestaFalsa(self._status_code, self._data)


def _mockear_productores_me(monkeypatch, productor_id: str, status_code: int = 200):
    """listar_mis_productos y publicar_producto resuelven el perfil del productor autenticado
    con un httpx.Client() crudo — se reemplaza acá por un stub que siempre devuelve la misma
    respuesta fija, sin tocar la red."""
    datos = {"id": productor_id, "nombre": "Productor de prueba"} if status_code == 200 else {}
    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _ClienteFalso(status_code, datos))


def _crear_producto_directo(**overrides) -> "main.models.Producto":
    db = main.SessionLocal()
    try:
        valores = dict(
            productor_id=uuid.uuid4(),
            productor_nombre="Productor Test",
            nombre="Producto Test",
            categoria="fruta",
            precio=10.0,
            stock=5,
            unidad_medida="kg",
            estado="publicado",
        )
        valores.update(overrides)
        producto = main.models.Producto(**valores)
        db.add(producto)
        db.commit()
        db.refresh(producto)
        db.expunge(producto)
        return producto
    finally:
        db.close()


def _agregar_imagen(producto_id) -> None:
    db = main.SessionLocal()
    try:
        db.add(main.models.ProductoImagen(producto_id=producto_id, url="https://ejemplo.test/img.jpg"))
        db.commit()
    finally:
        db.close()


# ============ GET /productos (catálogo público) ============

def test_listar_productos_excluye_borradores():
    publicado = _crear_producto_directo(nombre="Publicado Test", estado="publicado")
    borrador = _crear_producto_directo(nombre="Borrador Test", estado="borrador", precio=None, categoria=None)

    resp = client.get("/productos")
    assert resp.status_code == 200, resp.text
    ids = {p["id"] for p in resp.json()}

    assert str(publicado.id) in ids
    assert str(borrador.id) not in ids


# ============ GET /productos/mios ============

def test_mios_trae_ambos_estados_del_productor_correcto(monkeypatch):
    productor_id = str(uuid.uuid4())
    borrador = _crear_producto_directo(
        productor_id=productor_id, nombre="Mi borrador", estado="borrador", precio=None, categoria=None,
    )
    publicado = _crear_producto_directo(
        productor_id=productor_id, nombre="Mi publicado", estado="publicado",
    )
    # Producto de otro productor: no debe aparecer.
    ajeno = _crear_producto_directo(nombre="Producto ajeno", estado="publicado")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.get("/productos/mios", headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    ids = {p["id"] for p in resp.json()}

    assert str(borrador.id) in ids
    assert str(publicado.id) in ids
    assert str(ajeno.id) not in ids

    fila_borrador = next(p for p in resp.json() if p["id"] == str(borrador.id))
    assert fila_borrador["estado"] == "borrador"


def test_mios_requiere_rol_productor():
    _auth(str(uuid.uuid4()), rol="comprador")
    resp = client.get("/productos/mios", headers=HEADERS_AUTH)
    assert resp.status_code == 403


# ============ PATCH /productos/{id}/publicar ============

def test_publicar_sin_precio_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=None, categoria="fruta",
    )
    _agregar_imagen(producto.id)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}/publicar", headers=HEADERS_AUTH)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Agrega un precio mayor a 0 antes de publicar."


def test_publicar_sin_categoria_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=5.0, categoria=None,
    )
    _agregar_imagen(producto.id)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}/publicar", headers=HEADERS_AUTH)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Elige una categoría antes de publicar."


def test_publicar_sin_imagen_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=5.0, categoria="fruta",
    )
    # Sin _agregar_imagen(...) a propósito.

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}/publicar", headers=HEADERS_AUTH)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Agrega al menos una imagen antes de publicar."


def test_publicar_con_todo_completo_funciona(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=12.5, categoria="verdura",
    )
    _agregar_imagen(producto.id)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}/publicar", headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "publicado"

    # Ahora sí debe aparecer en el catálogo público.
    catalogo = client.get("/productos")
    assert catalogo.status_code == 200, catalogo.text
    ids_catalogo = {p["id"] for p in catalogo.json()}
    assert str(producto.id) in ids_catalogo


def test_publicar_ya_publicado_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="publicado")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}/publicar", headers=HEADERS_AUTH)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Este producto ya está publicado"


def test_publicar_producto_ajeno_falla_403(monkeypatch):
    dueño_real = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=dueño_real, estado="borrador", precio=5.0, categoria="fruta",
    )
    _agregar_imagen(producto.id)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, str(uuid.uuid4()))  # otro productor autenticado

    resp = client.patch(f"/productos/{producto.id}/publicar", headers=HEADERS_AUTH)
    assert resp.status_code == 403


# ============ POST /productos/interno/crear-desde-cosecha ============

def test_crear_desde_cosecha_sin_secreto_falla_403():
    resp = client.post(
        "/productos/interno/crear-desde-cosecha",
        json={
            "productor_id": str(uuid.uuid4()),
            "productor_nombre": "Chacra de Juana",
            "nombre": "Papa Nativa",
            "unidad_medida": "kg",
            "stock": 50,
            "registro_produccion_id": str(uuid.uuid4()),
        },
        headers={"X-Servicio-Secreto": "secreto-incorrecto"},
    )
    assert resp.status_code == 403


def test_crear_desde_cosecha_con_secreto_crea_borrador():
    productor_id = str(uuid.uuid4())
    registro_id = str(uuid.uuid4())

    resp = client.post(
        "/productos/interno/crear-desde-cosecha",
        json={
            "productor_id": productor_id,
            "productor_nombre": "Chacra de Juana",
            "nombre": "Papa Nativa",
            "unidad_medida": "kg",
            "stock": 50,
            "registro_produccion_id": registro_id,
        },
        headers={"X-Servicio-Secreto": main.PRODUCTORES_A_PRODUCTOS_SECRETO},
    )
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["estado"] == "borrador"
    assert cuerpo["precio"] is None
    assert cuerpo["categoria"] is None
    assert cuerpo["productor_id"] == productor_id
    assert cuerpo["productor_nombre"] == "Chacra de Juana"
    assert cuerpo["registro_produccion_id"] == registro_id
    assert cuerpo["stock"] == 50

    # No debe aparecer en el catálogo público todavía.
    catalogo = client.get("/productos")
    assert catalogo.status_code == 200, catalogo.text
    ids_catalogo = {p["id"] for p in catalogo.json()}
    assert cuerpo["id"] not in ids_catalogo
