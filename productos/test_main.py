"""
Tests del ciclo de vida borrador -> en_transito -> disponible de Producto (antes solo
borrador/publicado — "publicado" se renombró a "disponible" y se agregó el estado intermedio
"en_transito") y su interacción con el catálogo público y las reservas de compradores.

Cubre:
- GET /productos (catálogo público) incluye "en_transito" y "disponible", excluye "borrador"
- GET /productos/mios trae los tres estados, solo del productor autenticado
- PATCH .../publicar: sin precio -> 400, sin imagen -> 400, con todo completo -> 200 y pasa a
  "en_transito" (no a "disponible" directo), ya publicado -> 400
- PATCH /productos/{id}/confirmar-llegada-almacen: requiere rol admin, solo funciona desde
  "en_transito", pasa a "disponible"
- POST /productos/{id}/reservar: requiere rol comprador, solo funciona desde "en_transito",
  falla si el mismo comprador ya reservó ese producto
- GET /productos/{id}/reservas: admin o el productor dueño del producto
- PATCH /productos/{id}: edita categoria y/o precio de un borrador propio; falla si no es el
  dueño, si el producto ya está publicado, o si el precio es <= 0; la categoría debe ser una de
  las 8 (se normaliza a minúsculas sin tildes) y cualquier otra cosa -> 422
- POST /productos ya no existe (405): un producto solo nace de una cosecha aprobada
- POST /productos/interno/crear-desde-cosecha: sin el secreto correcto -> 403,
  con el secreto correcto crea el borrador esperado (sin precio/categoría, no visible en el
  catálogo)

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de
postgresql + create_all() al importar main) — no se puede sustituir por SQLite. No requiere
Redis real: cache.py se degrada a "sin caché" solo con que Redis no responda (ver su propio
docstring), así que un Redis inalcanzable en el entorno de test no rompe nada, solo hace que
cada GET pegue directo a Postgres.

Las operaciones sobre imágenes/publicar/editar
validan el perfil de productor llamando a PRODUCTORES_URL/productores/me con httpx.Client()
crudo (no un helper con circuit breaker) — se mockea reemplazando main.httpx.Client por un
stub, en vez de levantar un servicio de Productores real. Los productos que estos tests
necesitan como fixture se insertan directo en la base vía SessionLocal(), no a través de
POST /productos, para no depender de ese mock en cada test que no está probando ese endpoint
puntual.
"""
import uuid
from unittest.mock import MagicMock

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
            estado="disponible",
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

def test_listar_productos_incluye_en_transito_y_disponible_excluye_borrador():
    en_transito = _crear_producto_directo(nombre="En transito Test", estado="en_transito")
    disponible = _crear_producto_directo(nombre="Disponible Test", estado="disponible")
    borrador = _crear_producto_directo(nombre="Borrador Test", estado="borrador", precio=None, categoria=None)

    resp = client.get("/productos")
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    ids = {p["id"] for p in cuerpo}

    assert str(en_transito.id) in ids
    assert str(disponible.id) in ids
    assert str(borrador.id) not in ids

    fila_en_transito = next(p for p in cuerpo if p["id"] == str(en_transito.id))
    fila_disponible = next(p for p in cuerpo if p["id"] == str(disponible.id))
    assert fila_en_transito["estado"] == "en_transito"
    assert fila_disponible["estado"] == "disponible"


# ============ GET /productos/mios ============

def test_mios_trae_ambos_estados_del_productor_correcto(monkeypatch):
    productor_id = str(uuid.uuid4())
    borrador = _crear_producto_directo(
        productor_id=productor_id, nombre="Mi borrador", estado="borrador", precio=None, categoria=None,
    )
    disponible = _crear_producto_directo(
        productor_id=productor_id, nombre="Mi disponible", estado="disponible",
    )
    # Producto de otro productor: no debe aparecer.
    ajeno = _crear_producto_directo(nombre="Producto ajeno", estado="disponible")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.get("/productos/mios", headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    ids = {p["id"] for p in resp.json()}

    assert str(borrador.id) in ids
    assert str(disponible.id) in ids
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
    # Transiciona a "en_transito", no directo a "disponible" — todavía tiene que llegar
    # físicamente al almacén y ser confirmado por un Admin.
    assert resp.json()["estado"] == "en_transito"

    # Ya visible en el catálogo público (reservable, aunque no comprable todavía).
    catalogo = client.get("/productos")
    assert catalogo.status_code == 200, catalogo.text
    ids_catalogo = {p["id"] for p in catalogo.json()}
    assert str(producto.id) in ids_catalogo


def test_publicar_ya_publicado_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="disponible")

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


# ============ PATCH /productos/{id}/confirmar-llegada-almacen ============

def _mockear_cadena(monkeypatch, status=200, roto=False):
    """La llegada se registra en Certificación con un httpx.post directo: se reemplaza por un stub que
    guarda cada (url, kwargs) para comprobar qué se le mandó, sin tocar la red."""
    llamadas = []

    def falso_post(url, **kwargs):
        llamadas.append((url, kwargs))
        if roto:
            raise main.httpx.RequestError("boom")
        return _RespuestaFalsa(status, {"creado": True, "indice": 2})

    monkeypatch.setattr(main.httpx, "post", falso_post)
    monkeypatch.setattr(main, "PRODUCTOS_A_CERTIFICACION_SECRETO", "secreto-de-prueba-certificacion")
    return llamadas


def _confirmar_llegada(producto, cantidad, **extra):
    return client.patch(
        f"/productos/{producto.id}/confirmar-llegada-almacen",
        json={"cantidad_recibida": cantidad, **extra},
    )


def _estado_y_stock(producto_id):
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Producto).filter(main.models.Producto.id == producto_id).first()
        return fila.estado, fila.stock
    finally:
        db.close()


def test_confirmar_llegada_con_toda_la_cantidad_declarada_funciona(monkeypatch):
    producto = _crear_producto_directo(estado="en_transito", stock=50)
    llamadas = _mockear_cadena(monkeypatch)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = _confirmar_llegada(producto, 50)

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "disponible"
    assert _estado_y_stock(producto.id) == ("disponible", 50)
    # queda escrito en la cadena, con el secreto del par Productos -> Certificación
    assert len(llamadas) == 1
    url, kwargs = llamadas[0]
    assert url.endswith(f"/certificacion/{producto.id}/llegada-almacen")
    assert kwargs["headers"] == {"X-Servicio-Secreto": "secreto-de-prueba-certificacion"}
    assert "50 kg" in kwargs["json"]["datos"] and "cantidad completa" in kwargs["json"]["datos"]


def test_confirmar_llegada_con_menos_cantidad_deja_el_stock_en_lo_recibido_y_la_diferencia_en_la_cadena(monkeypatch):
    """Antes no se registraba ninguna cantidad: el stock seguía siendo lo que el productor declaró, aunque
    en el almacén hubiera llegado menos."""
    producto = _crear_producto_directo(estado="en_transito", stock=50, nombre="Papa Nativa")
    llamadas = _mockear_cadena(monkeypatch)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = _confirmar_llegada(producto, 48, observaciones="  2 cajas golpeadas  ")

    assert resp.status_code == 200, resp.text
    assert resp.json()["stock"] == 48
    assert _estado_y_stock(producto.id) == ("disponible", 48)
    texto = llamadas[0][1]["json"]["datos"]
    assert "48 kg de Papa Nativa" in texto
    assert "2 kg menos de las 50 declaradas" in texto
    assert texto.endswith("Observaciones: 2 cajas golpeadas")


def test_no_puede_llegar_mas_de_lo_declarado_por_el_productor(monkeypatch):
    """La cantidad declarada es la que certificó la cosecha aprobada: recibir más inflaría el stock por
    encima de lo certificado."""
    producto = _crear_producto_directo(estado="en_transito", stock=50)
    llamadas = _mockear_cadena(monkeypatch)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = _confirmar_llegada(producto, 51)

    assert resp.status_code == 422, resp.text
    assert "50 kg" in resp.json()["detail"]
    assert _estado_y_stock(producto.id) == ("en_transito", 50)
    assert llamadas == []


def test_la_cantidad_recibida_es_obligatoria_y_debe_ser_un_entero_positivo(monkeypatch):
    producto = _crear_producto_directo(estado="en_transito", stock=50)
    llamadas = _mockear_cadena(monkeypatch)
    _auth(str(uuid.uuid4()), rol="admin")

    for cantidad in (0, -3, "abc", 2.5, 10_000_000):
        resp = _confirmar_llegada(producto, cantidad)
        assert resp.status_code == 422, (cantidad, resp.text)
    assert client.patch(f"/productos/{producto.id}/confirmar-llegada-almacen").status_code == 422  # sin cuerpo
    assert client.patch(f"/productos/{producto.id}/confirmar-llegada-almacen", json={}).status_code == 422
    assert _estado_y_stock(producto.id) == ("en_transito", 50)
    assert llamadas == []


def test_las_observaciones_de_la_llegada_se_acotan(monkeypatch):
    producto = _crear_producto_directo(estado="en_transito", stock=50)
    _mockear_cadena(monkeypatch)
    _auth(str(uuid.uuid4()), rol="admin")

    for observaciones in ("mal\u0000texto", "x" * (main.OBSERVACIONES_LLEGADA_MAXIMO + 1)):
        assert _confirmar_llegada(producto, 50, observaciones=observaciones).status_code == 422
    assert _estado_y_stock(producto.id) == ("en_transito", 50)


def test_sin_observaciones_o_en_blanco_la_cadena_no_lleva_esa_frase(monkeypatch):
    producto = _crear_producto_directo(estado="en_transito", stock=50)
    llamadas = _mockear_cadena(monkeypatch)
    _auth(str(uuid.uuid4()), rol="admin")

    resp = _confirmar_llegada(producto, 50, observaciones="   ")

    assert resp.status_code == 200, resp.text
    assert "Observaciones" not in llamadas[0][1]["json"]["datos"]


def test_si_la_cadena_no_responde_o_rechaza_el_producto_sigue_en_transito(monkeypatch):
    """Ningún producto pasa a "disponible" sin su registro de llegada: el Admin reintenta."""
    _auth(str(uuid.uuid4()), rol="admin")

    producto = _crear_producto_directo(estado="en_transito", stock=50)
    _mockear_cadena(monkeypatch, roto=True)
    resp = _confirmar_llegada(producto, 50)
    assert resp.status_code == 503, resp.text
    assert "sigue en tránsito" in resp.json()["detail"]
    assert _estado_y_stock(producto.id) == ("en_transito", 50)

    _mockear_cadena(monkeypatch, status=500)
    assert _confirmar_llegada(producto, 50).status_code == 503
    assert _estado_y_stock(producto.id) == ("en_transito", 50)

    # y al reintentar cuando Certificación responde, funciona
    _mockear_cadena(monkeypatch)
    assert _confirmar_llegada(producto, 50).status_code == 200
    assert _estado_y_stock(producto.id) == ("disponible", 50)


def test_confirmar_dos_veces_la_llegada_no_registra_dos_eventos(monkeypatch):
    producto = _crear_producto_directo(estado="en_transito", stock=50)
    llamadas = _mockear_cadena(monkeypatch)
    _auth(str(uuid.uuid4()), rol="admin")

    assert _confirmar_llegada(producto, 50).status_code == 200
    segunda = _confirmar_llegada(producto, 50)

    assert segunda.status_code == 400, segunda.text
    assert len(llamadas) == 1


def test_confirmar_llegada_falla_si_no_esta_en_transito(monkeypatch):
    producto = _crear_producto_directo(estado="borrador", precio=None, categoria=None)
    llamadas = _mockear_cadena(monkeypatch)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = _confirmar_llegada(producto, 5)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Este producto no está en tránsito hacia el almacén"
    assert llamadas == []


def test_confirmar_llegada_de_un_producto_que_no_existe_o_con_id_mal_formado(monkeypatch):
    _mockear_cadena(monkeypatch)
    _auth(str(uuid.uuid4()), rol="admin")

    assert client.patch(f"/productos/{uuid.uuid4()}/confirmar-llegada-almacen", json={"cantidad_recibida": 5}).status_code == 404
    assert client.patch("/productos/no-es-uuid/confirmar-llegada-almacen", json={"cantidad_recibida": 5}).status_code == 422


def test_confirmar_llegada_requiere_admin(monkeypatch):
    producto = _crear_producto_directo(estado="en_transito")
    llamadas = _mockear_cadena(monkeypatch)

    for rol in ("productor", "comprador", "verificador", "repartidor"):
        _auth(str(uuid.uuid4()), rol=rol)
        resp = client.patch(
            f"/productos/{producto.id}/confirmar-llegada-almacen", json={"cantidad_recibida": 1}, headers=HEADERS_AUTH
        )
        assert resp.status_code == 403, (rol, resp.text)
    assert _estado_y_stock(producto.id)[0] == "en_transito"
    assert llamadas == []


def test_confirmar_llegada_publica_un_evento_por_cada_reserva(monkeypatch):
    """No se levanta un RabbitMQ real: se reemplaza publicar_evento por un mock y se revisa qué
    se le pasó, mismo criterio que _mockear_productores_me con httpx."""
    producto = _crear_producto_directo(estado="en_transito")
    comprador_1 = str(uuid.uuid4())
    comprador_2 = str(uuid.uuid4())

    _auth(comprador_1, rol="comprador")
    resp1 = client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)
    assert resp1.status_code == 200, resp1.text

    _auth(comprador_2, rol="comprador")
    resp2 = client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)
    assert resp2.status_code == 200, resp2.text

    mock_publicar = MagicMock()
    monkeypatch.setattr(main, "publicar_evento", mock_publicar)
    _mockear_cadena(monkeypatch)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = _confirmar_llegada(producto, producto.stock)
    assert resp.status_code == 200, resp.text

    assert mock_publicar.call_count == 2
    eventos = [llamada.args[0] for llamada in mock_publicar.call_args_list]
    for evento in eventos:
        assert evento["evento"] == "producto_disponible"
        assert evento["producto_id"] == str(producto.id)
        assert evento["nombre_producto"] == producto.nombre
    assert {e["usuario_id"] for e in eventos} == {comprador_1, comprador_2}


def test_confirmar_llegada_sin_reservas_no_publica_nada(monkeypatch):
    producto = _crear_producto_directo(estado="en_transito")

    mock_publicar = MagicMock()
    monkeypatch.setattr(main, "publicar_evento", mock_publicar)
    _mockear_cadena(monkeypatch)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = _confirmar_llegada(producto, producto.stock)
    assert resp.status_code == 200, resp.text
    mock_publicar.assert_not_called()


# ============ POST /productos/{id}/reservar ============

def test_reservar_en_transito_funciona():
    producto = _crear_producto_directo(estado="en_transito")

    _auth(str(uuid.uuid4()), rol="comprador")
    resp = client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json()["producto_id"] == str(producto.id)


def test_reservar_producto_disponible_falla_400():
    producto = _crear_producto_directo(estado="disponible")

    _auth(str(uuid.uuid4()), rol="comprador")
    resp = client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)
    assert resp.status_code == 400
    assert "en tránsito" in resp.json()["detail"]


def test_reservar_dos_veces_el_mismo_comprador_falla_400():
    producto = _crear_producto_directo(estado="en_transito")
    comprador_id = str(uuid.uuid4())

    _auth(comprador_id, rol="comprador")
    primera = client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)
    assert primera.status_code == 200, primera.text

    segunda = client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)
    assert segunda.status_code == 400
    assert segunda.json()["detail"] == "Ya reservaste este producto"


# ============ GET /productos/{id}/reservas ============

def test_listar_reservas_admin_funciona():
    producto = _crear_producto_directo(estado="en_transito")
    _auth(str(uuid.uuid4()), rol="comprador")
    client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = client.get(f"/productos/{producto.id}/reservas", headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


def test_listar_reservas_productor_dueno_funciona(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="en_transito")

    _auth(str(uuid.uuid4()), rol="comprador")
    client.post(f"/productos/{producto.id}/reservar", headers=HEADERS_AUTH)

    _auth(str(uuid.uuid4()), rol="productor")
    _mockear_productores_me(monkeypatch, productor_id)
    resp = client.get(f"/productos/{producto.id}/reservas", headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


def test_listar_reservas_productor_ajeno_falla_403(monkeypatch):
    producto = _crear_producto_directo(productor_id=str(uuid.uuid4()), estado="en_transito")

    _auth(str(uuid.uuid4()), rol="productor")
    _mockear_productores_me(monkeypatch, str(uuid.uuid4()))  # otro productor autenticado
    resp = client.get(f"/productos/{producto.id}/reservas", headers=HEADERS_AUTH)
    assert resp.status_code == 403


# ============ Unidades alternativas (POST/GET/PATCH/DELETE) ============

def test_crear_unidad_alternativa_valida(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0},
        headers=HEADERS_AUTH,
    )
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["unidad"] == "saco"
    assert float(cuerpo["precio"]) == 45.0
    assert float(cuerpo["factor_a_base"]) == 10.0

    listado = client.get(f"/productos/{producto.id}/unidades")
    assert listado.status_code == 200, listado.text
    assert len(listado.json()) == 1


def test_crear_unidad_alternativa_duplica_base_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "kg", "precio": 5.0, "factor_a_base": 1.0},
        headers=HEADERS_AUTH,
    )
    assert resp.status_code == 400


def test_crear_unidad_alternativa_duplicada_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    primera = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0},
        headers=HEADERS_AUTH,
    )
    assert primera.status_code == 200, primera.text

    segunda = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "saco", "precio": 50.0, "factor_a_base": 12.0},
        headers=HEADERS_AUTH,
    )
    assert segunda.status_code == 400


def test_crear_unidad_alternativa_no_dueno_falla_403(monkeypatch):
    dueño_real = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=dueño_real, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, str(uuid.uuid4()))  # otro productor autenticado

    resp = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0},
        headers=HEADERS_AUTH,
    )
    assert resp.status_code == 403


def test_crear_unidad_alternativa_no_valida_falla_422(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "tonelada", "precio": 500.0, "factor_a_base": 1000.0},
        headers=HEADERS_AUTH,
    )
    assert resp.status_code == 422


def test_actualizar_unidad_alternativa_precio_y_factor(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    creada = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0},
        headers=HEADERS_AUTH,
    ).json()

    resp = client.patch(
        f"/productos/unidades/{creada['id']}",
        json={"precio": 48.0, "factor_a_base": 11.0},
        headers=HEADERS_AUTH,
    )
    assert resp.status_code == 200, resp.text
    assert float(resp.json()["precio"]) == 48.0
    assert float(resp.json()["factor_a_base"]) == 11.0


def test_eliminar_unidad_alternativa(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    creada = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0},
        headers=HEADERS_AUTH,
    ).json()

    resp = client.delete(f"/productos/unidades/{creada['id']}", headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text

    listado = client.get(f"/productos/{producto.id}/unidades")
    assert listado.json() == []


def test_listar_productos_incluye_unidades_alternativas(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg", estado="disponible")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)
    creada = client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0},
        headers=HEADERS_AUTH,
    )
    assert creada.status_code == 200, creada.text

    catalogo = client.get("/productos")
    assert catalogo.status_code == 200, catalogo.text
    fila = next(p for p in catalogo.json() if p["id"] == str(producto.id))
    assert len(fila["unidades_alternativas"]) == 1
    assert fila["unidades_alternativas"][0]["unidad"] == "saco"
    assert float(fila["unidades_alternativas"][0]["precio"]) == 45.0


def test_obtener_producto_incluye_unidades_alternativas(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, unidad_medida="kg")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)
    client.post(
        f"/productos/{producto.id}/unidades",
        json={"unidad": "arroba", "precio": 30.0, "factor_a_base": 11.5},
        headers=HEADERS_AUTH,
    )

    detalle = client.get(f"/productos/{producto.id}")
    assert detalle.status_code == 200, detalle.text
    assert len(detalle.json()["unidades_alternativas"]) == 1
    assert detalle.json()["unidades_alternativas"][0]["unidad"] == "arroba"


# ============ PATCH /productos/{id} (editar categoria/precio de un borrador) ============

def test_actualizar_categoria_sola(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=None, categoria=None,
    )

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}", json={"categoria": "verdura"}, headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json()["categoria"] == "verdura"
    assert resp.json()["precio"] is None


def test_actualizar_precio_solo(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=None, categoria="fruta",
    )

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}", json={"precio": 15.5}, headers=HEADERS_AUTH)
    assert resp.status_code == 200, resp.text
    assert float(resp.json()["precio"]) == 15.5
    assert resp.json()["categoria"] == "fruta"


def test_actualizar_categoria_y_precio_juntos(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=None, categoria=None,
    )

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(
        f"/productos/{producto.id}", json={"categoria": "tuberculo", "precio": 8.0}, headers=HEADERS_AUTH,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["categoria"] == "tuberculo"
    assert float(resp.json()["precio"]) == 8.0


def test_actualizar_producto_ajeno_falla_403(monkeypatch):
    dueño_real = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=dueño_real, estado="borrador", precio=None, categoria=None)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, str(uuid.uuid4()))  # otro productor autenticado

    resp = client.patch(f"/productos/{producto.id}", json={"precio": 9.0}, headers=HEADERS_AUTH)
    assert resp.status_code == 403


def test_actualizar_producto_ya_publicado_falla_400(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="disponible")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}", json={"precio": 20.0}, headers=HEADERS_AUTH)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Solo se puede editar un producto mientras está en borrador"


def test_actualizar_precio_invalido_falla_422(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="borrador", precio=None, categoria="fruta")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}", json={"precio": 0}, headers=HEADERS_AUTH)
    assert resp.status_code == 422


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


def test_crear_desde_cosecha_con_equivalencia_kg_se_propaga_y_aparece_en_get():
    """Producto con unidad base "litro" — equivalencia_kg viaja en el body (mismo criterio que
    RegistroProduccion.equivalencia_kg en Productores) y queda visible en GET /productos/{id},
    que es lo que Pedidos necesita para calcular peso real (ver crear_pedido en pedidos/main.py)."""
    resp = client.post(
        "/productos/interno/crear-desde-cosecha",
        json={
            "productor_id": str(uuid.uuid4()),
            "productor_nombre": "Chacra de Juana",
            "nombre": "Leche de cabra",
            "unidad_medida": "litro",
            "stock": 20,
            "registro_produccion_id": str(uuid.uuid4()),
            "equivalencia_kg": 1.03,
        },
        headers={"X-Servicio-Secreto": main.PRODUCTORES_A_PRODUCTOS_SECRETO},
    )
    assert resp.status_code == 200, resp.text
    producto_id = resp.json()["id"]
    assert float(resp.json()["equivalencia_kg"]) == 1.03

    # nace como borrador: el detalle de un borrador solo lo ve su productor o el admin (aquí, un admin)
    detalle = client.get(f"/productos/{producto_id}", headers=_con_sesion(uuid.uuid4(), "admin"))
    assert detalle.status_code == 200, detalle.text
    assert float(detalle.json()["equivalencia_kg"]) == 1.03


def test_crear_desde_cosecha_unidad_no_kg_sin_equivalencia_falla_422():
    resp = client.post(
        "/productos/interno/crear-desde-cosecha",
        json={
            "productor_id": str(uuid.uuid4()),
            "productor_nombre": "Chacra de Juana",
            "nombre": "Leche de cabra",
            "unidad_medida": "litro",
            "stock": 20,
            "registro_produccion_id": str(uuid.uuid4()),
        },
        headers={"X-Servicio-Secreto": main.PRODUCTORES_A_PRODUCTOS_SECRETO},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == (
        "Cuando la unidad de medida no es 'kg', debes indicar la equivalencia a kg (ej. 1 litro = X kg)"
    )


def test_crear_desde_cosecha_en_kg_deja_equivalencia_null():
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
        headers={"X-Servicio-Secreto": main.PRODUCTORES_A_PRODUCTOS_SECRETO},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["equivalencia_kg"] is None


def test_crear_desde_cosecha_duplicado_falla_409():
    # Simula el reintento de Productores tras un fallo de red que sí había llegado a crear el
    # producto la primera vez: mismo registro_produccion_id, segunda llamada.
    payload = {
        "productor_id": str(uuid.uuid4()),
        "productor_nombre": "Chacra de Juana",
        "nombre": "Papa Nativa",
        "unidad_medida": "kg",
        "stock": 50,
        "registro_produccion_id": str(uuid.uuid4()),
    }
    headers = {"X-Servicio-Secreto": main.PRODUCTORES_A_PRODUCTOS_SECRETO}

    primera = client.post("/productos/interno/crear-desde-cosecha", json=payload, headers=headers)
    assert primera.status_code == 200, primera.text

    segunda = client.post("/productos/interno/crear-desde-cosecha", json=payload, headers=headers)
    assert segunda.status_code == 409
    assert segunda.json()["detail"] == "Ya existe un producto para esta cosecha."


# ============ Categoría validada en el servidor + POST /productos cerrado ============

def test_post_productos_ya_no_existe():
    """POST /productos dejaba a cualquier productor crear un producto "disponible" sin cosecha ni
    verificación, con precio y stock libres. Ya no existe: un producto solo nace de una cosecha
    aprobada (POST /productos/interno/crear-desde-cosecha, con secreto de servicio)."""
    _auth(str(uuid.uuid4()))
    resp = client.post(
        "/productos",
        json={"nombre": "QA falso", "categoria": "fruta", "precio": -5, "stock": 999},
        headers=HEADERS_AUTH,
    )
    assert resp.status_code == 405


def test_actualizar_categoria_no_valida_falla_422(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="borrador", precio=None, categoria=None)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    for basura in ['"><img src=x onerror=alert(1)>', "Hortaliza", "<b>fruta</b>", ""]:
        resp = client.patch(f"/productos/{producto.id}", json={"categoria": basura}, headers=HEADERS_AUTH)
        assert resp.status_code == 422, (basura, resp.text)

    # y el borrador no cambió
    db = main.SessionLocal()
    try:
        assert db.query(main.models.Producto).get(producto.id).categoria is None
    finally:
        db.close()


def test_actualizar_categoria_se_normaliza_a_minusculas_sin_tildes(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="borrador", precio=None, categoria=None)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    for escrito, canonico in [("Tubérculo ", "tuberculo"), ("LÁCTEO", "lacteo"), ("Fruta", "fruta")]:
        resp = client.patch(f"/productos/{producto.id}", json={"categoria": escrito}, headers=HEADERS_AUTH)
        assert resp.status_code == 200, (escrito, resp.text)
        assert resp.json()["categoria"] == canonico


def test_publicar_con_categoria_no_valida_falla_400(monkeypatch):
    """Defensa en profundidad: aunque un borrador llegara con una categoría sucia (dato viejo o
    escrito directo en la base), no se puede publicar."""
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(
        productor_id=productor_id, estado="borrador", precio=10.0, categoria='"><img src=x onerror=alert(1)>',
    )
    _agregar_imagen(producto.id)

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    resp = client.patch(f"/productos/{producto.id}/publicar", headers=HEADERS_AUTH)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Elige una categoría válida antes de publicar."


# ============ Números finitos y textos acotados ============

def _con_json_crudo(metodo, ruta, texto_json):
    """JSON.stringify no puede producir NaN ni Infinity, pero cualquier otro cliente de la API sí."""
    return client.request(metodo, ruta, content=texto_json, headers={**HEADERS_AUTH, "Content-Type": "application/json"})


def test_actualizar_precio_no_finito_o_gigante_falla_422(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="borrador", precio=None, categoria="fruta")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    for valor in ("NaN", "Infinity", "-Infinity", "1e9", "100001"):
        resp = _con_json_crudo("PATCH", f"/productos/{producto.id}", '{"precio": %s}' % valor)
        assert resp.status_code == 422, (valor, resp.text)

    db = main.SessionLocal()
    try:
        assert db.query(main.models.Producto).get(producto.id).precio is None
    finally:
        db.close()


def test_unidad_alternativa_con_numeros_no_finitos_o_gigantes_falla_422(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto = _crear_producto_directo(productor_id=productor_id, estado="disponible")

    _auth(str(uuid.uuid4()))
    _mockear_productores_me(monkeypatch, productor_id)

    for precio, factor in (("NaN", "10"), ("Infinity", "10"), ("100001", "10"), ("5", "NaN"), ("5", "Infinity"), ("5", "10001")):
        cuerpo = '{"unidad": "saco", "precio": %s, "factor_a_base": %s}' % (precio, factor)
        resp = _con_json_crudo("POST", f"/productos/{producto.id}/unidades", cuerpo)
        assert resp.status_code == 422, (precio, factor, resp.text)


def test_resena_y_busqueda_limitan_el_largo_del_texto():
    import pytest
    from pydantic import ValidationError

    assert main.ResenaCrear(calificacion=5, comentario="  Muy buena  ").comentario == "Muy buena"
    with pytest.raises(ValidationError):
        main.ResenaCrear(calificacion=5, comentario="x" * 1001)
    with pytest.raises(ValidationError):
        main.ResenaCrear(calificacion=5, comentario="mal\u0000texto")
    with pytest.raises(ValidationError):
        main.BusquedaRegistrar(termino="b" * 101)
    assert main.BusquedaRegistrar(termino="  papa ").termino == "papa"


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/productos/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
        ("POST", f"/productos/{_uuid.uuid4()}/stock/reponer", {"cantidad": 1}, "PAGOS_A_PRODUCTOS_SECRETO"),
        ("PATCH", f"/productos/{_uuid.uuid4()}/stock", {"cantidad": 1}, "PEDIDOS_A_PRODUCTOS_SECRETO"),
        ("POST", "/productos/interno/descontar-stock-lote", {"lineas": [{"producto_id": str(_uuid.uuid4()), "cantidad": 1}]}, "PEDIDOS_A_PRODUCTOS_SECRETO"),
        ("POST", "/productos/interno/crear-desde-cosecha", {"productor_id": str(_uuid.uuid4()), "productor_nombre": "x", "nombre": "x", "stock": 1, "registro_produccion_id": str(_uuid.uuid4())}, "PRODUCTORES_A_PRODUCTOS_SECRETO"),
    ]
    for metodo, ruta, cuerpo, variable in protegidos:
        for valor in (None, ""):
            monkeypatch.setattr(main, variable, valor)
            sin_cabecera = client.request(metodo, ruta, json=cuerpo)
            assert sin_cabecera.status_code == 403, (ruta, valor, sin_cabecera.text)
            cabecera_vacia = client.request(metodo, ruta, json=cuerpo, headers={"X-Servicio-Secreto": ""})
            assert cabecera_vacia.status_code == 403, (ruta, valor, cabecera_vacia.text)

    # con el secreto configurado: el incorrecto no pasa y el correcto sí llega al endpoint
    metodo, ruta, cuerpo, variable = protegidos[0]
    monkeypatch.setattr(main, variable, "secreto-de-prueba")
    assert client.request(metodo, ruta, json=cuerpo, headers={"X-Servicio-Secreto": "otro"}).status_code == 403
    assert client.request(metodo, ruta, json=cuerpo, headers={"X-Servicio-Secreto": "secreto-de-prueba"}).status_code != 403



# ============ Descuento de stock en lote y estado del producto ============

SECRETO_PEDIDOS = "secreto-de-prueba-pedidos"


def _descontar_lote(monkeypatch, lineas, secreto=SECRETO_PEDIDOS):
    monkeypatch.setattr(main, "PEDIDOS_A_PRODUCTOS_SECRETO", SECRETO_PEDIDOS)
    headers = {"X-Servicio-Secreto": secreto} if secreto is not None else {}
    return client.post(
        "/productos/interno/descontar-stock-lote",
        json={"lineas": [{"producto_id": str(pid), "cantidad": cantidad} for pid, cantidad in lineas]},
        headers=headers,
    )


def _stock_de(producto_id):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Producto).filter(main.models.Producto.id == producto_id).first().stock
    finally:
        db.close()


def test_descontar_lote_baja_el_stock_de_todas_las_lineas(monkeypatch):
    a = _crear_producto_directo(stock=10)
    b = _crear_producto_directo(stock=20)

    resp = _descontar_lote(monkeypatch, [(a.id, 3), (b.id, 5)])

    assert resp.status_code == 200, resp.text
    assert (_stock_de(a.id), _stock_de(b.id)) == (7, 15)


def test_descontar_lote_sin_el_secreto_correcto_falla_403_y_no_toca_el_stock(monkeypatch):
    a = _crear_producto_directo(stock=10)

    assert _descontar_lote(monkeypatch, [(a.id, 3)], secreto=None).status_code == 403
    assert _descontar_lote(monkeypatch, [(a.id, 3)], secreto="otro").status_code == 403
    assert _stock_de(a.id) == 10


def test_descontar_lote_es_todo_o_nada_si_una_linea_no_alcanza(monkeypatch):
    """Antes se descontaba línea por línea y no se miraba la respuesta: si la segunda fallaba, la primera
    ya estaba descontada y el pedido se creaba igual."""
    alcanza = _crear_producto_directo(stock=10)
    no_alcanza = _crear_producto_directo(stock=2)

    resp = _descontar_lote(monkeypatch, [(alcanza.id, 4), (no_alcanza.id, 3)])

    assert resp.status_code == 409, resp.text
    assert "Stock insuficiente" in resp.json()["detail"]
    assert (_stock_de(alcanza.id), _stock_de(no_alcanza.id)) == (10, 2)


def test_descontar_lote_suma_las_lineas_repetidas_del_mismo_producto(monkeypatch):
    """Dos líneas de 6 con stock 10: cada una por separado cabe, juntas no."""
    producto = _crear_producto_directo(stock=10)

    resp = _descontar_lote(monkeypatch, [(producto.id, 6), (producto.id, 6)])

    assert resp.status_code == 409, resp.text
    assert _stock_de(producto.id) == 10

    ok = _descontar_lote(monkeypatch, [(producto.id, 6), (producto.id, 4)])
    assert ok.status_code == 200, ok.text
    assert _stock_de(producto.id) == 0


def test_descontar_lote_solo_vende_productos_disponibles(monkeypatch):
    """Un producto en tránsito hacia el almacén (solo reservable) o un borrador no se compran por API."""
    disponible = _crear_producto_directo(stock=10)
    en_transito = _crear_producto_directo(stock=10, estado="en_transito")
    borrador = _crear_producto_directo(stock=10, estado="borrador", precio=None, categoria=None)

    for producto in (en_transito, borrador):
        resp = _descontar_lote(monkeypatch, [(disponible.id, 1), (producto.id, 1)])
        assert resp.status_code == 409, resp.text
        assert "no está disponible" in resp.json()["detail"]
        assert (_stock_de(disponible.id), _stock_de(producto.id)) == (10, 10)


def test_descontar_lote_con_un_producto_que_no_existe_falla_404(monkeypatch):
    existente = _crear_producto_directo(stock=10)

    resp = _descontar_lote(monkeypatch, [(existente.id, 1), (uuid.uuid4(), 1)])

    assert resp.status_code == 404, resp.text
    assert _stock_de(existente.id) == 10


def test_descontar_lote_redondea_hacia_arriba_una_cantidad_fraccionaria(monkeypatch):
    """1 arroba = 11,5 kg: el stock es un entero, así que se descuentan 12 (antes el PATCH con un
    número fraccionario se rechazaba y, como nadie miraba la respuesta, no se descontaba nada)."""
    producto = _crear_producto_directo(stock=20)

    resp = _descontar_lote(monkeypatch, [(producto.id, 11.5)])

    assert resp.status_code == 200, resp.text
    assert _stock_de(producto.id) == 8


def test_descontar_lote_rechaza_cantidades_negativas_cero_o_no_numericas(monkeypatch):
    """Con una cantidad negativa el stock subía en vez de bajar."""
    producto = _crear_producto_directo(stock=10)
    monkeypatch.setattr(main, "PEDIDOS_A_PRODUCTOS_SECRETO", SECRETO_PEDIDOS)
    headers = {"X-Servicio-Secreto": SECRETO_PEDIDOS}

    for cantidad in (-5, 0, "abc", 10_000_000):
        resp = client.post(
            "/productos/interno/descontar-stock-lote",
            json={"lineas": [{"producto_id": str(producto.id), "cantidad": cantidad}]},
            headers=headers,
        )
        assert resp.status_code == 422, (cantidad, resp.text)
    # NaN no es JSON válido pero Python lo emite y acepta: se manda como texto crudo
    resp = client.post(
        "/productos/interno/descontar-stock-lote",
        content='{"lineas": [{"producto_id": "%s", "cantidad": NaN}]}' % producto.id,
        headers={**headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    for cuerpo in ({"lineas": []}, {"lineas": [{"producto_id": "no-es-uuid", "cantidad": 1}]}):
        assert client.post("/productos/interno/descontar-stock-lote", json=cuerpo, headers=headers).status_code == 422
    assert _stock_de(producto.id) == 10


def test_el_descuento_y_la_reposicion_individuales_tampoco_aceptan_cantidades_negativas(monkeypatch):
    producto = _crear_producto_directo(stock=10)
    monkeypatch.setattr(main, "PEDIDOS_A_PRODUCTOS_SECRETO", SECRETO_PEDIDOS)
    monkeypatch.setattr(main, "PAGOS_A_PRODUCTOS_SECRETO", "secreto-de-prueba-pagos")

    descontar = client.patch(f"/productos/{producto.id}/stock", json={"cantidad": -5}, headers={"X-Servicio-Secreto": SECRETO_PEDIDOS})
    reponer = client.post(f"/productos/{producto.id}/stock/reponer", json={"cantidad": -5}, headers={"X-Servicio-Secreto": "secreto-de-prueba-pagos"})

    assert descontar.status_code == 422 and reponer.status_code == 422
    assert _stock_de(producto.id) == 10


def test_el_detalle_publico_del_producto_incluye_su_estado():
    producto = _crear_producto_directo(estado="en_transito")

    resp = client.get(f"/productos/{producto.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "en_transito"


def test_descontar_lote_tambien_lo_acepta_del_secreto_de_pagos(monkeypatch):
    """Pagos vuelve a reservar el stock al reintentar el pago de un pedido rechazado; cada servicio usa su
    propia clave y cada comparación falla cerrada por separado."""
    producto = _crear_producto_directo(stock=10)
    monkeypatch.setattr(main, "PEDIDOS_A_PRODUCTOS_SECRETO", "secreto-de-prueba-pedidos")
    monkeypatch.setattr(main, "PAGOS_A_PRODUCTOS_SECRETO", "secreto-de-prueba-pagos")
    cuerpo = {"lineas": [{"producto_id": str(producto.id), "cantidad": 1}]}

    for secreto, esperado in (("secreto-de-prueba-pagos", 200), ("secreto-de-prueba-pedidos", 200), ("otro", 403)):
        resp = client.post("/productos/interno/descontar-stock-lote", json=cuerpo, headers={"X-Servicio-Secreto": secreto})
        assert resp.status_code == esperado, (secreto, resp.text)
    assert _stock_de(producto.id) == 8

    # sin ninguna de las dos claves configuradas no pasa nadie
    monkeypatch.setattr(main, "PEDIDOS_A_PRODUCTOS_SECRETO", None)
    monkeypatch.setattr(main, "PAGOS_A_PRODUCTOS_SECRETO", None)
    for cabeceras in ({}, {"X-Servicio-Secreto": ""}):
        assert client.post("/productos/interno/descontar-stock-lote", json=cuerpo, headers=cabeceras).status_code == 403
    assert _stock_de(producto.id) == 8


# ============ GET /productos/{id}: un borrador solo lo ve su productor (o el admin) ============

def _token_real(sub, rol, segundos=3600):
    """usuario_opcional decodifica el token por su cuenta (no pasa por verificar_token), así que estos tests
    firman uno de verdad con el mismo secreto del servicio."""
    import time

    import auth
    from jose import jwt
    return jwt.encode({"sub": str(sub), "rol": rol, "exp": int(time.time()) + segundos}, auth.JWT_SECRET, algorithm="HS256")


def _con_sesion(sub, rol, **kwargs):
    return {"Authorization": f"Bearer {_token_real(sub, rol, **kwargs)}"}


def _borrador_de_prueba():
    return _crear_producto_directo(nombre="Borrador Privado", estado="borrador", precio=None, categoria=None)


def test_un_visitante_no_ve_un_borrador_pero_si_los_productos_publicados():
    """Antes cualquiera que conociera el id leía un borrador (productor, stock, precio) sin sesión."""
    borrador = _borrador_de_prueba()
    en_transito = _crear_producto_directo(estado="en_transito")
    disponible = _crear_producto_directo(estado="disponible")

    resp = client.get(f"/productos/{borrador.id}")
    assert resp.status_code == 404, resp.text
    assert "Borrador Privado" not in resp.text
    assert client.get(f"/productos/{en_transito.id}").status_code == 200
    assert client.get(f"/productos/{disponible.id}").status_code == 200


def test_el_productor_dueno_y_el_admin_ven_el_borrador(monkeypatch):
    borrador = _borrador_de_prueba()
    _mockear_productores_me(monkeypatch, str(borrador.productor_id))

    dueno = client.get(f"/productos/{borrador.id}", headers=_con_sesion(uuid.uuid4(), "productor"))
    assert dueno.status_code == 200, dueno.text
    assert dueno.json()["estado"] == "borrador" and dueno.json()["nombre"] == "Borrador Privado"

    admin = client.get(f"/productos/{borrador.id}", headers=_con_sesion(uuid.uuid4(), "admin"))
    assert admin.status_code == 200, admin.text


def test_otro_productor_un_comprador_y_un_verificador_no_ven_el_borrador(monkeypatch):
    borrador = _borrador_de_prueba()
    _mockear_productores_me(monkeypatch, str(uuid.uuid4()))  # el perfil del productor que pregunta NO es el dueño

    for rol in ("productor", "comprador", "verificador", "repartidor"):
        resp = client.get(f"/productos/{borrador.id}", headers=_con_sesion(uuid.uuid4(), rol))
        assert resp.status_code == 404, (rol, resp.text)
        assert "Borrador Privado" not in resp.text


def test_un_token_vencido_o_invalido_se_trata_como_visitante_sin_dar_401():
    """Mirar un producto público no exige sesión: un token vencido no debe romper la página (ni cerrar la sesión)."""
    borrador = _borrador_de_prueba()
    disponible = _crear_producto_directo(estado="disponible")
    vencido = _con_sesion(uuid.uuid4(), "productor", segundos=-60)
    basura = {"Authorization": "Bearer esto-no-es-un-token"}

    for cabeceras in (vencido, basura):
        assert client.get(f"/productos/{disponible.id}", headers=cabeceras).status_code == 200
        assert client.get(f"/productos/{borrador.id}", headers=cabeceras).status_code == 404


def test_si_productores_no_responde_un_productor_que_pide_un_borrador_recibe_503(monkeypatch):
    borrador = _borrador_de_prueba()
    _mockear_productores_me(monkeypatch, "", status_code=503)  # /productores/me responde con error

    resp = client.get(f"/productos/{borrador.id}", headers=_con_sesion(uuid.uuid4(), "productor"))

    assert resp.status_code == 404  # sin perfil de productor no es el dueño
    # y si el servicio ni siquiera contesta:
    class _Caido:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *a, **kw):
            raise main.httpx.RequestError("boom")

    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _Caido())
    resp = client.get(f"/productos/{borrador.id}", headers=_con_sesion(uuid.uuid4(), "productor"))
    assert resp.status_code == 503, resp.text


def test_un_borrador_no_se_guarda_ni_se_sirve_desde_la_cache(monkeypatch):
    borrador = _borrador_de_prueba()
    publicado = _crear_producto_directo(estado="disponible")
    _mockear_productores_me(monkeypatch, str(borrador.productor_id))
    guardar = MagicMock()
    monkeypatch.setattr(main.cache, "guardar", guardar)

    assert client.get(f"/productos/{publicado.id}").status_code == 200
    assert guardar.call_count == 1  # lo publicado sí se cachea

    assert client.get(f"/productos/{borrador.id}", headers=_con_sesion(uuid.uuid4(), "productor")).status_code == 200
    assert guardar.call_count == 1  # el borrador no

    # una entrada de la caché que era de un borrador (o sin "estado", de antes) no se sirve a un visitante
    for entrada in ({"estado": "borrador", "nombre": "Borrador Privado"}, {"nombre": "Borrador Privado"}):
        monkeypatch.setattr(main.cache, "obtener", lambda clave, entrada=entrada: entrada)
        resp = client.get(f"/productos/{borrador.id}")
        assert resp.status_code == 404, (entrada, resp.text)

    # y una entrada publicada sí se sirve tal cual
    monkeypatch.setattr(main.cache, "obtener", lambda clave: {"estado": "disponible", "nombre": "Desde caché"})
    assert client.get(f"/productos/{publicado.id}").json()["nombre"] == "Desde caché"


def test_un_id_de_producto_mal_formado_falla_422():
    assert client.get("/productos/no-es-un-uuid").status_code == 422
    assert client.get("/productos/no-es-un-uuid/unidades").status_code == 422


def test_las_unidades_alternativas_de_un_borrador_solo_las_ve_su_productor(monkeypatch):
    borrador = _borrador_de_prueba()
    db = main.SessionLocal()
    try:
        db.add(main.models.UnidadAlternativa(producto_id=borrador.id, unidad="saco", precio=45, factor_a_base=10))
        db.commit()
    finally:
        db.close()

    assert client.get(f"/productos/{borrador.id}/unidades").json() == []  # visitante: como si no hubiera ninguna

    _mockear_productores_me(monkeypatch, str(borrador.productor_id))
    propias = client.get(f"/productos/{borrador.id}/unidades", headers=_con_sesion(uuid.uuid4(), "productor"))
    assert propias.status_code == 200 and [u["unidad"] for u in propias.json()] == ["saco"]

    _mockear_productores_me(monkeypatch, str(uuid.uuid4()))
    ajenas = client.get(f"/productos/{borrador.id}/unidades", headers=_con_sesion(uuid.uuid4(), "productor"))
    assert ajenas.json() == []


# ============ POST /productos/{id}/imagenes/subir: tamaño y contenido reales ============

import imagenes

PNG_VALIDO = b"\x89PNG\r\n\x1a\n" + bytes(64)
JPEG_VALIDO = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(64)
WEBP_VALIDO = b"RIFF\x24\x00\x00\x00WEBPVP8 " + bytes(32)


def _subir(producto, contenido, nombre="foto.png", tipo="image/png", extra=None):
    return client.post(
        f"/productos/{producto.id}/imagenes/subir?orden=0",
        files={"archivo": (nombre, contenido, tipo)},
        headers={**HEADERS_AUTH, **(extra or {})},
    )


def _preparar_subida(monkeypatch):
    producto = _crear_producto_directo(estado="borrador", precio=None, categoria=None)
    _auth(str(uuid.uuid4()), "productor")
    _mockear_productores_me(monkeypatch, str(producto.productor_id))
    subidas = []

    def subir_falso(archivo, content_type):
        subidas.append((archivo.read(), content_type))
        return "http://minio.test/productos-imagenes/falsa.png"

    monkeypatch.setattr(main, "subir_imagen", subir_falso)
    return producto, subidas


def _imagenes_de(producto):
    db = main.SessionLocal()
    try:
        return db.query(main.models.ProductoImagen).filter(main.models.ProductoImagen.producto_id == producto.id).count()
    finally:
        db.close()


def test_la_firma_del_contenido_distingue_jpeg_png_webp_y_nada_mas():
    assert imagenes.tipo_segun_el_contenido(JPEG_VALIDO) == "image/jpeg"
    assert imagenes.tipo_segun_el_contenido(PNG_VALIDO) == "image/png"
    assert imagenes.tipo_segun_el_contenido(WEBP_VALIDO) == "image/webp"
    for no_es_imagen in (b"", b"GIF89a....", b"%PDF-1.7", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"RIFF\x24\x00\x00\x00WAVEfmt ", b"\xff\xd8"):
        assert imagenes.tipo_segun_el_contenido(no_es_imagen) is None, no_es_imagen


def test_una_imagen_valida_se_sube_con_el_tipo_detectado(monkeypatch):
    producto, subidas = _preparar_subida(monkeypatch)

    for contenido, tipo in ((PNG_VALIDO, "image/png"), (JPEG_VALIDO, "image/jpeg"), (WEBP_VALIDO, "image/webp")):
        resp = _subir(producto, contenido, tipo=tipo)
        assert resp.status_code == 200, (tipo, resp.text)

    assert subidas == [(PNG_VALIDO, "image/png"), (JPEG_VALIDO, "image/jpeg"), (WEBP_VALIDO, "image/webp")]
    assert _imagenes_de(producto) == 3


def test_lo_que_se_guarda_lleva_el_tipo_real_no_el_que_declara_el_cliente(monkeypatch):
    producto, subidas = _preparar_subida(monkeypatch)

    resp = _subir(producto, PNG_VALIDO, nombre="foto.jpg", tipo="image/jpeg")  # un PNG que dice ser JPEG

    assert resp.status_code == 200, resp.text
    assert subidas == [(PNG_VALIDO, "image/png")]


def test_un_archivo_que_no_es_una_imagen_se_rechaza_aunque_diga_ser_png(monkeypatch):
    """Antes bastaba ponerle "image/png": se podía subir un HTML, un SVG con scripts o un PDF."""
    producto, subidas = _preparar_subida(monkeypatch)
    intentos = (
        b"<html><script>alert(1)</script></html>",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"%PDF-1.7 ...",
        b"GIF89a.....",
        b"",
    )

    for contenido in intentos:
        resp = _subir(producto, contenido)
        assert resp.status_code == 422, (contenido[:20], resp.text)
        assert "JPEG, PNG o WEBP" in resp.json()["detail"]

    assert subidas == [] and _imagenes_de(producto) == 0


def test_una_imagen_de_mas_de_5_mb_se_rechaza_con_413(monkeypatch):
    producto, subidas = _preparar_subida(monkeypatch)
    justo = PNG_VALIDO + bytes(imagenes.TAMANO_MAXIMO_BYTES - len(PNG_VALIDO))  # exactamente 5 MB: pasa
    de_mas = justo + b"\x00"  # 5 MB y un byte: lo rechaza el endpoint
    enorme = PNG_VALIDO + bytes(imagenes.LIMITE_CUERPO_BYTES)  # más que el tope del cuerpo: lo rechaza el middleware

    assert _subir(producto, justo).status_code == 200
    for contenido in (de_mas, enorme):
        resp = _subir(producto, contenido)
        assert resp.status_code == 413, resp.status_code
        assert "5 MB" in resp.json()["detail"]

    assert len(subidas) == 1 and _imagenes_de(producto) == 1


def test_el_413_por_tamano_lleva_las_cabeceras_cors_para_que_el_navegador_muestre_el_mensaje(monkeypatch):
    producto, _ = _preparar_subida(monkeypatch)
    enorme = PNG_VALIDO + bytes(imagenes.LIMITE_CUERPO_BYTES)

    resp = _subir(producto, enorme, extra={"Origin": "http://localhost:5500"})

    assert resp.status_code == 413
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_subir_una_imagen_sigue_pidiendo_ser_el_productor_dueno(monkeypatch):
    producto = _crear_producto_directo(estado="borrador", precio=None, categoria=None)
    _auth(str(uuid.uuid4()), "productor")
    _mockear_productores_me(monkeypatch, str(uuid.uuid4()))  # otro productor
    monkeypatch.setattr(main, "subir_imagen", lambda *a: "http://x")

    assert _subir(producto, PNG_VALIDO).status_code == 403
    assert _subir(producto, b"no soy una imagen").status_code == 403  # la propiedad se comprueba antes que el contenido



# ============ Bucket de imágenes: se prepara solo (antes se hacía a mano en la consola de MinIO) ============
# En un MinIO nuevo (otro entorno) no existía `productos-imagenes`: toda subida fallaba con NoSuchBucket y, aunque se
# creara a mano, sus fotos no se veían sin la política de lectura pública.

import io
import json as _json

import pytest

import minio_client
from minio.error import S3Error


def _error_s3(codigo):
    return S3Error(None, codigo, "simulado", "/productos-imagenes", "id-de-prueba", "host-de-prueba")  # la respuesta HTTP va primero


class _MinioFalso:
    def __init__(self, existe=False, fallas_al_crear=(), fallas_al_subir=()):
        self.existe = existe
        self.fallas_al_crear = list(fallas_al_crear)
        self.fallas_al_subir = list(fallas_al_subir)
        self.buckets_creados = []
        self.politicas = []
        self.subidas = []

    def bucket_exists(self, nombre):
        return self.existe

    def make_bucket(self, nombre):
        if self.fallas_al_crear:
            raise self.fallas_al_crear.pop(0)
        self.buckets_creados.append(nombre)
        self.existe = True

    def set_bucket_policy(self, nombre, politica):
        self.politicas.append((nombre, _json.loads(politica)))

    def put_object(self, bucket, nombre, archivo, **kwargs):
        contenido = archivo.read()  # como el cliente real, consume el archivo
        if self.fallas_al_subir:
            raise self.fallas_al_subir.pop(0)
        self.subidas.append((bucket, nombre, contenido))


def _con_minio_falso(monkeypatch, **kwargs):
    falso = _MinioFalso(**kwargs)
    monkeypatch.setattr(minio_client, "client", falso)
    return falso


def test_el_bucket_de_imagenes_se_crea_y_queda_de_lectura_publica(monkeypatch):
    falso = _con_minio_falso(monkeypatch, existe=False)

    minio_client.asegurar_bucket_publico()

    assert falso.buckets_creados == ["productos-imagenes"]
    [(bucket, politica)] = falso.politicas
    assert bucket == "productos-imagenes"
    [regla] = politica["Statement"]
    # cualquiera puede LEER las fotos; nada más (escribir exige credenciales)
    assert regla["Effect"] == "Allow" and regla["Principal"] == {"AWS": ["*"]}
    assert regla["Action"] == ["s3:GetObject"]
    assert regla["Resource"] == ["arn:aws:s3:::productos-imagenes/*"]


def test_si_el_bucket_ya_existe_no_se_crea_de_nuevo_pero_se_reafirma_la_politica(monkeypatch):
    falso = _con_minio_falso(monkeypatch, existe=True)

    minio_client.asegurar_bucket_publico()
    minio_client.asegurar_bucket_publico()

    assert falso.buckets_creados == []
    assert len(falso.politicas) == 2  # idempotente: repetirlo no rompe nada


def test_si_otra_replica_creo_el_bucket_a_la_vez_se_sigue_adelante(monkeypatch):
    falso = _con_minio_falso(monkeypatch, existe=False, fallas_al_crear=[_error_s3("BucketAlreadyOwnedByYou")])

    minio_client.asegurar_bucket_publico()

    assert len(falso.politicas) == 1  # igual se fijó la política


def test_un_error_real_al_preparar_el_bucket_no_se_traga(monkeypatch):
    _con_minio_falso(monkeypatch, existe=False, fallas_al_crear=[_error_s3("AccessDenied")])
    with pytest.raises(S3Error):
        minio_client.asegurar_bucket_publico()


def test_subir_una_imagen_a_un_minio_sin_bucket_lo_prepara_y_reintenta_una_vez(monkeypatch):
    falso = _con_minio_falso(monkeypatch, existe=False, fallas_al_subir=[_error_s3("NoSuchBucket")])
    monkeypatch.setattr(minio_client, "MINIO_PUBLIC_URL", "https://tienda.ejemplo/media")
    contenido = b"\x89PNG\r\n\x1a\n" + bytes(64)

    url = minio_client.subir_imagen(io.BytesIO(contenido), "image/png")

    assert url.startswith("https://tienda.ejemplo/media/productos-imagenes/") and url.endswith(".png")
    assert falso.buckets_creados == ["productos-imagenes"]
    [(bucket, nombre, subido)] = falso.subidas
    assert subido == contenido  # el reintento subió el archivo COMPLETO, no lo que quedó tras el primer intento


def test_otros_errores_al_subir_se_propagan_sin_reintentar(monkeypatch):
    falso = _con_minio_falso(monkeypatch, existe=True, fallas_al_subir=[_error_s3("AccessDenied")])

    with pytest.raises(S3Error):
        minio_client.subir_imagen(io.BytesIO(b"x"), "image/png")

    assert falso.subidas == [] and falso.buckets_creados == []


def test_el_arranque_no_se_cae_si_minio_todavia_no_responde(monkeypatch):
    class _MinioCaido:
        def bucket_exists(self, nombre):
            raise ConnectionError("MinIO todavía no responde")

    monkeypatch.setattr(minio_client, "client", _MinioCaido())
    main.preparar_almacen_de_imagenes()  # solo avisa: subir_imagen lo vuelve a intentar en la primera subida
