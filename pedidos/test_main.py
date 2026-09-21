"""
Tests del endpoint POST /pedidos: las dos validaciones fail-fast ya existentes (restricción
geográfica dentro de Ayacucho, monto mínimo de compra S/30.00) y el cálculo de costo_envio por
tramos de distancia contra la ubicación configurable del almacén (ConfiguracionSistema).

Cubre además:
- GET/PATCH /configuracion/almacen: PATCH requiere rol admin
- cada tramo de distancia (<=10km, 10-25km, >25km) calcula el costo correcto
- cambiar la ubicación del almacén afecta el costo de un pedido creado DESPUÉS del cambio,
  con el mismo destino — sin tocar código

Requiere una base de datos Postgres real accesible según database.py (usa tipos
UUID de postgresql y create_all() sobre esa conexión) — no se puede sustituir por SQLite.
El servicio de Productos (httpx.Client) y la publicación de eventos (RabbitMQ) se simulan
con mocks para no depender de esos servicios estando levantados. sembrar_configuracion_almacen()
es un evento de arranque de FastAPI, pero un `TestClient(main.app)` simple (sin usarlo como
context manager `with`) no dispara eventos de startup en esta versión de FastAPI/Starlette — se
llama a mano una vez acá abajo para que el almacén exista antes del primer test, igual que
pasaría en un arranque real.
"""
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)
main.sembrar_configuracion_almacen()
main.sembrar_configuracion_umbral_pedido_grande()

# Bypassea la autenticación real: en todos los tests se simula un comprador ya autenticado.
# Se sobreescribe la dependencia estable `verificar_token` (no `requiere_rol(...)`, que genera
# un closure nuevo en cada llamada y por eso no es un objetivo válido de override).
main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}

# Coordenadas de ejemplo dentro de Ayacucho (Huamanga, aprox.) y claramente fuera (Lima).
DESTINO_DENTRO_AYACUCHO = {"destino_latitud": -13.1588, "destino_longitud": -74.2239}
DESTINO_FUERA_AYACUCHO = {"destino_latitud": -12.0464, "destino_longitud": -77.0428}


def _como(sub: str, rol: str):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}


# GET /pedidos/{id} además declara Depends(security) para reenviar el token a Transporte: sin un header
# Authorization real, HTTPBearer lo rechaza antes de llegar al cuerpo. El valor no importa (verificar_token
# está overrideado y nunca lo decodifica).
CON_TOKEN = {"Authorization": "Bearer token-de-prueba"}


def _mock_httpx_client(precio, stock=100, nombre="Producto de prueba", unidades_alternativas=None, unidad_medida="kg", equivalencia_kg=None):
    """Reemplaza httpx.Client() dentro de main: GET devuelve el producto simulado,
    POST (descuento de stock en lote) no hace nada real."""
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    respuesta_get = MagicMock()
    respuesta_get.status_code = 200
    respuesta_get.json.return_value = {
        "id": str(uuid.uuid4()),
        "nombre": nombre,
        "precio": precio,
        "stock": stock,
        "unidades_alternativas": unidades_alternativas or [],
        "unidad_medida": unidad_medida,
        "equivalencia_kg": equivalencia_kg,
    }
    mock_client.get.return_value = respuesta_get
    mock_client.post.return_value = MagicMock(status_code=200)
    return mock_client


def _payload(destino, precio_item, cantidad=1, unidad=None):
    item = {"producto_id": str(uuid.uuid4()), "cantidad": cantidad}
    if unidad is not None:
        item["unidad"] = unidad
    return {
        "comprador_nombre": "Cliente de prueba",
        "comprador_telefono": "987654321",
        "destino_latitud": destino["destino_latitud"],
        "destino_longitud": destino["destino_longitud"],
        "items": [item],
    }


def test_pedido_valido_dentro_de_ayacucho_y_sobre_el_minimo():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), \
         patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["comprador_nombre"] == "Cliente de prueba"
    assert len(cuerpo["items"]) == 1
    mock_publicar.assert_called_once()


def test_pedido_sin_telefono_falla_con_422():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
    del payload["comprador_telefono"]

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), \
         patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 422, resp.text
    mock_publicar.assert_not_called()


def test_pedido_con_telefono_invalido_falla_con_422():
    for telefono in ["12345", "887654321", "98765432a", ""]:
        payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
        payload["comprador_telefono"] = telefono

        with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), \
             patch("main.publicar_evento"):
            resp = client.post("/pedidos", json=payload)

        assert resp.status_code == 422, f"{telefono!r}: {resp.text}"


def test_pedido_normaliza_el_telefono_sin_espacios():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
    payload["comprador_telefono"] = "987 654 321"

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), \
         patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    assert resp.json()["comprador_telefono"] == "987654321"


def test_pedido_fuera_de_ayacucho_falla_con_400():
    payload = _payload(DESTINO_FUERA_AYACUCHO, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)) as mock_ctor, \
         patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "El destino del pedido debe estar dentro de la región de Ayacucho."
    # La validación geográfica va antes que cualquier llamada a Productos o publicación de eventos.
    mock_ctor.assert_not_called()
    mock_publicar.assert_not_called()


def test_pedido_bajo_el_monto_minimo_falla_con_400():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=10.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=10.0)), \
         patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "El monto mínimo de compra es S/30.00. Agrega más productos para continuar."
    mock_publicar.assert_not_called()


# ============ Costo de envío por tramos (ConfiguracionSistema del almacén) ============

KM_POR_GRADO_LATITUD = 111.19  # aproximación estándar — suficiente para offsets puramente en latitud


def _punto_a_km_al_sur(lat, lon, km):
    """Un punto a ~km kilómetros al sur de (lat, lon) — mover en una sola dirección (latitud
    pura, longitud fija) hace que la distancia resultante sea predecible sin tener que replicar
    la fórmula Haversine completa acá."""
    return lat - km / KM_POR_GRADO_LATITUD, lon


def _fijar_almacen(lat, lon):
    """Deja el almacén en una ubicación conocida como admin, sin importar qué haya hecho
    cualquier otro test antes (el valor sembrado al arrancar, o un PATCH previo) — y restaura el
    rol "comprador" que el resto del archivo asume por defecto."""
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "admin"}
    try:
        resp = client.patch("/configuracion/almacen", json={"latitud": lat, "longitud": lon})
        assert resp.status_code == 200, resp.text
    finally:
        main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}


ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA = -13.1553, -74.2287  # mismo valor por defecto real


def _fijar_umbral(umbral_kg):
    """Mismo propósito que _fijar_almacen: deja el umbral de pedido grande en un valor conocido,
    sin importar qué haya dejado el valor sembrado al arrancar o un PATCH de un test anterior."""
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "admin"}
    try:
        resp = client.patch("/configuracion/umbral-pedido-grande", json={"umbral_kg": umbral_kg})
        assert resp.status_code == 200, resp.text
    finally:
        main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}


def test_costo_envio_tramo_hasta_10km():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    destino_lat, destino_lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 5)  # ~5km
    payload = _payload({"destino_latitud": destino_lat, "destino_longitud": destino_lon}, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    assert float(resp.json()["costo_envio"]) == 5.00


def test_costo_envio_tramo_10_a_25km():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    destino_lat, destino_lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 17)  # ~17km
    payload = _payload({"destino_latitud": destino_lat, "destino_longitud": destino_lon}, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    assert float(resp.json()["costo_envio"]) == 8.00


def test_costo_envio_tramo_mas_de_25km():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    destino_lat, destino_lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 35)  # ~35km
    payload = _payload({"destino_latitud": destino_lat, "destino_longitud": destino_lon}, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    assert float(resp.json()["costo_envio"]) == 12.00


def test_evento_pedido_creado_incluye_costo_envio():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    destino_lat, destino_lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 5)
    payload = _payload({"destino_latitud": destino_lat, "destino_longitud": destino_lon}, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), \
         patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    evento_publicado = mock_publicar.call_args[0][0]
    assert evento_publicado["costo_envio"] == 5.00


def test_cambiar_ubicacion_almacen_afecta_costo_de_pedidos_futuros():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    destino_lat, destino_lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 5)  # ~5km del almacén original
    payload = _payload({"destino_latitud": destino_lat, "destino_longitud": destino_lon}, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        primero = client.post("/pedidos", json=payload)
    assert primero.status_code == 200, primero.text
    assert float(primero.json()["costo_envio"]) == 5.00

    # Movemos el ALMACÉN (no el destino) ~35km más lejos de ese mismo punto de entrega — el
    # mismo destino de siempre debería pasar del tramo <=10km al tramo >25km, sin tocar ninguna
    # línea de código, solo la configuración vía PATCH.
    almacen_lejano_lat, almacen_lejano_lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 40)
    _fijar_almacen(almacen_lejano_lat, almacen_lejano_lon)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        segundo = client.post("/pedidos", json=payload)  # mismo destino que el primer pedido
    assert segundo.status_code == 200, segundo.text
    assert float(segundo.json()["costo_envio"]) == 12.00


# ============ Cotización del envío (GET /pedidos/costo-envio) ============

def _cotizar(lat, lon):
    return client.get("/pedidos/costo-envio", params={"latitud": lat, "longitud": lon})


def test_cotizar_envio_devuelve_el_costo_de_cada_tramo():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    for km, costo_esperado in [(5, 5.00), (17, 8.00), (35, 12.00)]:
        lat, lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, km)
        resp = _cotizar(lat, lon)
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert cuerpo["costo_envio"] == costo_esperado
        assert abs(cuerpo["distancia_km"] - km) < 0.5


def test_cotizar_envio_coincide_con_el_costo_que_guarda_crear_pedido():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    lat, lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 17)
    cotizado = _cotizar(lat, lon).json()["costo_envio"]

    payload = _payload({"destino_latitud": lat, "destino_longitud": lon}, precio_item=50.0)
    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        creado = client.post("/pedidos", json=payload)

    assert creado.status_code == 200, creado.text
    assert float(creado.json()["costo_envio"]) == cotizado


def test_cotizar_envio_no_requiere_autenticacion():
    # Sin ninguna sobreescritura de auth explícita, igual que /configuracion/almacen: si el
    # endpoint exigiera un token, TestClient recibiría 401/403 en vez de 200. Además prueba que la
    # ruta se declaró antes de /pedidos/{pedido_id} (si no, "costo-envio" se leería como un id).
    resp = _cotizar(DESTINO_DENTRO_AYACUCHO["destino_latitud"], DESTINO_DENTRO_AYACUCHO["destino_longitud"])
    assert resp.status_code == 200, resp.text
    assert "costo_envio" in resp.json() and "distancia_km" in resp.json()


def test_cotizar_envio_fuera_de_ayacucho_falla_400():
    resp = _cotizar(DESTINO_FUERA_AYACUCHO["destino_latitud"], DESTINO_FUERA_AYACUCHO["destino_longitud"])
    assert resp.status_code == 400
    assert resp.json()["detail"] == "El destino del pedido debe estar dentro de la región de Ayacucho."


def test_cotizar_envio_sin_coordenadas_falla_422():
    assert client.get("/pedidos/costo-envio").status_code == 422
    assert client.get("/pedidos/costo-envio", params={"latitud": -13.15}).status_code == 422


def test_cotizar_envio_sigue_la_ubicacion_actual_del_almacen():
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)
    lat, lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 5)
    assert _cotizar(lat, lon).json()["costo_envio"] == 5.00

    lejano_lat, lejano_lon = _punto_a_km_al_sur(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA, 40)
    _fijar_almacen(lejano_lat, lejano_lon)
    assert _cotizar(lat, lon).json()["costo_envio"] == 12.00
    _fijar_almacen(ALMACEN_LAT_PRUEBA, ALMACEN_LON_PRUEBA)


def test_actualizar_almacen_sin_rol_admin_falla_403():
    # El override de módulo (comprador) sigue vigente acá — ningún test anterior lo deja
    # cambiado (_fijar_almacen siempre restaura "comprador" en su finally).
    resp = client.patch("/configuracion/almacen", json={"latitud": -13.0, "longitud": -74.0})
    assert resp.status_code == 403


def test_obtener_ubicacion_almacen_no_requiere_autenticacion():
    # Sin ninguna sobreescritura de auth explícita: si el endpoint exigiera un token, TestClient
    # (que no manda header Authorization por defecto) recibiría 401/403 en vez de 200.
    resp = client.get("/configuracion/almacen")
    assert resp.status_code == 200, resp.text
    assert "latitud" in resp.json() and "longitud" in resp.json()


# ============ Compra en unidad alternativa (item.unidad) ============

def test_pedido_en_unidad_base_guarda_unidad_null():
    """Regresión: sin "unidad" en el payload, todo se comporta exactamente igual que antes —
    precio y cantidad en la unidad base, PedidoItem.unidad queda en null."""
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["unidad"] is None
    assert float(item["precio_unitario"]) == 50.0


def test_pedido_en_unidad_alternativa_cobra_precio_y_descuenta_stock_convertido():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=3, unidad="saco")
    unidades_alt = [{"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0}]
    mock_client = _mock_httpx_client(precio=5.0, stock=100, unidades_alternativas=unidades_alt)

    with patch("main.httpx.Client", return_value=mock_client), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["unidad"] == "saco"
    # Precio de la unidad alternativa (45.0/saco), NUNCA el precio base (5.0/kg).
    assert float(item["precio_unitario"]) == 45.0
    assert item["cantidad"] == 3  # se guarda en la unidad que compró, no convertida

    # el descuento de stock recibe la cantidad YA convertida a unidad base: 3 sacos * 10 kg/saco = 30 kg.
    llamada_stock = mock_client.post.call_args
    assert llamada_stock.kwargs["json"]["lineas"][0]["cantidad"] == 30.0


def test_pedido_en_unidad_alternativa_valida_stock_ya_convertido():
    # 5 sacos * 10 kg/saco = 50 kg, pero el producto solo tiene 40 kg de stock — insuficiente,
    # aunque "5" en sí sea un número chico. Si la validación comparara sin convertir, pasaría mal.
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=5, unidad="saco")
    unidades_alt = [{"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0}]
    mock_client = _mock_httpx_client(precio=5.0, stock=40, unidades_alternativas=unidades_alt)

    with patch("main.httpx.Client", return_value=mock_client), patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 409
    mock_publicar.assert_not_called()


def test_pedido_en_unidad_no_existente_para_el_producto_falla_400():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=1, unidad="saco")
    mock_client = _mock_httpx_client(precio=5.0, unidades_alternativas=[])  # sin "saco" registrado

    with patch("main.httpx.Client", return_value=mock_client), patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Este producto no está disponible en esa unidad."
    mock_publicar.assert_not_called()


def test_precio_unitario_ignora_precio_enviado_en_el_payload():
    """ItemPedido no tiene (ni debería tener) un campo "precio" — si un payload malicioso lo
    manda igual, Pydantic simplemente lo descarta al parsear el body: el precio_unitario guardado
    siempre sale de la respuesta fresca de Productos, nunca de lo que mande el cliente."""
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
    payload["items"][0]["precio"] = 0.01  # intento de manipular el precio

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    assert float(resp.json()["items"][0]["precio_unitario"]) == 50.0


# ============ Peso total y umbral de vehículo grande (ConfiguracionSistema) ============

def test_obtener_umbral_pedido_grande_no_requiere_autenticacion():
    resp = client.get("/configuracion/umbral-pedido-grande")
    assert resp.status_code == 200, resp.text
    assert "umbral_kg" in resp.json()


def test_actualizar_umbral_sin_rol_admin_falla_403():
    resp = client.patch("/configuracion/umbral-pedido-grande", json={"umbral_kg": 30.0})
    assert resp.status_code == 403


def test_pedido_en_kg_bajo_el_umbral_no_requiere_vehiculo_grande():
    _fijar_umbral(25.0)
    # 10 kg a S/5.00 c/u: S/50.00 de subtotal (sobre el mínimo) y 10 kg de peso, bajo el umbral.
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=10)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=5.0, unidad_medida="kg")), \
         patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert float(cuerpo["peso_total_kg"]) == 10.0
    assert cuerpo["requiere_vehiculo_grande"] is False


def test_pedido_en_kg_sobre_el_umbral_requiere_vehiculo_grande():
    _fijar_umbral(25.0)
    # 30 kg a S/5.00 c/u: S/150.00 de subtotal y 30 kg de peso, sobre el umbral de 25.
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=30)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=5.0, unidad_medida="kg")), \
         patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert float(cuerpo["peso_total_kg"]) == 30.0
    assert cuerpo["requiere_vehiculo_grande"] is True


def test_pedido_con_producto_unidad_no_kg_deja_peso_null_y_no_marca_vehiculo_grande():
    """Regla explícita: si no podemos afirmar el peso (unidad base "unidad"/"litro" y el producto
    no tiene equivalencia_kg — ej. uno creado a mano), no se fuerza requiere_vehiculo_grande a
    True aunque la cantidad sea grande — queda en False. Sin regresión tras agregar
    equivalencia_kg: un producto que simplemente no la tiene se comporta exactamente igual."""
    _fijar_umbral(25.0)
    # 100 unidades a S/1.00 c/u: S/100.00 de subtotal, pero la unidad base del producto no es kg
    # y no trae equivalencia_kg (default del mock).
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=1.0, cantidad=100)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=1.0, unidad_medida="unidad")), \
         patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["peso_total_kg"] is None
    assert cuerpo["requiere_vehiculo_grande"] is False


def test_pedido_con_equivalencia_kg_conocida_aporta_al_peso_total():
    """Producto con unidad base "litro" pero CON equivalencia_kg (llegó desde
    RegistroProduccion.equivalencia_kg vía aprobar_cosecha) — a diferencia del test anterior, acá
    sí se puede calcular un peso real: cantidad_base * equivalencia_kg."""
    _fijar_umbral(25.0)
    # 30 litros a S/2.00 c/u: S/60.00 de subtotal. equivalencia_kg=1.0 -> 30 kg reales, sobre el
    # umbral de 25.
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=2.0, cantidad=30)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=2.0, unidad_medida="litro", equivalencia_kg=1.0)), \
         patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert float(cuerpo["peso_total_kg"]) == 30.0
    assert cuerpo["requiere_vehiculo_grande"] is True


def test_pedido_con_una_linea_no_kg_entre_varias_deja_peso_total_null():
    """Un solo item con peso desconocido invalida el total del pedido completo, aunque el resto
    de líneas sí tengan peso — no se suma "lo que se pueda", el total pasa a ser null entero."""
    _fijar_umbral(25.0)
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=10)
    payload["items"].append({"producto_id": str(uuid.uuid4()), "cantidad": 5})

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    respuesta_kg = MagicMock(status_code=200)
    respuesta_kg.json.return_value = {
        "id": str(uuid.uuid4()), "nombre": "Producto kg", "precio": 5.0, "stock": 100,
        "unidades_alternativas": [], "unidad_medida": "kg",
    }
    respuesta_unidad = MagicMock(status_code=200)
    respuesta_unidad.json.return_value = {
        "id": str(uuid.uuid4()), "nombre": "Producto por unidad", "precio": 5.0, "stock": 100,
        "unidades_alternativas": [], "unidad_medida": "unidad",
    }
    mock_client.get.side_effect = [respuesta_kg, respuesta_unidad]
    mock_client.post.return_value = MagicMock(status_code=200)

    with patch("main.httpx.Client", return_value=mock_client), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["peso_total_kg"] is None
    assert cuerpo["requiere_vehiculo_grande"] is False


def test_cambiar_umbral_afecta_pedidos_futuros_sin_tocar_los_ya_creados():
    _fijar_umbral(25.0)
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=30)  # 30 kg, S/150.00

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=5.0, unidad_medida="kg")), \
         patch("main.publicar_evento"):
        primero = client.post("/pedidos", json=payload)
    assert primero.status_code == 200, primero.text
    assert primero.json()["requiere_vehiculo_grande"] is True

    # Subimos el umbral por encima de esos mismos 30 kg — un pedido nuevo con la misma carga ya
    # NO debería requerir vehículo grande, sin tocar ninguna línea de código.
    _fijar_umbral(50.0)

    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=5.0, unidad_medida="kg")), \
         patch("main.publicar_evento"):
        segundo = client.post("/pedidos", json=payload)
    assert segundo.status_code == 200, segundo.text
    assert segundo.json()["requiere_vehiculo_grande"] is False

    # El pedido ya creado ANTES del cambio de umbral conserva su valor original (mismo criterio
    # que costo_envio con la ubicación del almacén: no se recalcula retroactivamente).
    _como(str(uuid.uuid4()), "admin")  # GET /pedidos/{id} ya no es público
    reconsulta = client.get(f"/pedidos/{primero.json()['id']}", headers=CON_TOKEN)
    _como(str(uuid.uuid4()), "comprador")
    assert reconsulta.status_code == 200, reconsulta.text
    assert reconsulta.json()["requiere_vehiculo_grande"] is True


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/pedidos/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
        ("GET", f"/pedidos/interno/{_uuid.uuid4()}", None, "TRANSPORTE_A_PEDIDOS_SECRETO"),
        ("GET", f"/pedidos/interno/{_uuid.uuid4()}/resumen-entrega", None, "TRANSPORTE_A_PEDIDOS_SECRETO"),
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


# ============ GET /pedidos/{id}: ya no es público ============

def _crear_pedido_de(comprador_sub: str) -> str:
    _como(comprador_sub, "comprador")
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
    with patch("main.httpx.Client", return_value=_mock_httpx_client(precio=50.0)), patch("main.publicar_evento"):
        resp = client.post("/pedidos", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class _ClienteTransporteFalso:
    """Reemplaza httpx.Client() para la pregunta a Transporte "¿este repartidor tiene el envío?": guarda cada
    (url, kwargs) y responde lo que se le indique, sin tocar la red."""
    def __init__(self, llamadas, status=200, estado=None, roto=False):
        self._llamadas, self._status, self._estado, self._roto = llamadas, status, estado, roto

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self._llamadas.append((url, kwargs))
        if self._roto:
            raise main.httpx.RequestError("boom")
        respuesta = MagicMock()
        respuesta.status_code = self._status
        respuesta.json.return_value = {"estado": self._estado}
        return respuesta


def _leer_como_repartidor(pedido_id, **transporte):
    llamadas = []
    _como(str(uuid.uuid4()), "repartidor")
    with patch("main.httpx.Client", return_value=_ClienteTransporteFalso(llamadas, **transporte)):
        resp = client.get(f"/pedidos/{pedido_id}", headers=CON_TOKEN)
    return resp, llamadas


def test_un_pedido_solo_lo_ven_su_comprador_y_el_admin():
    """Antes cualquiera, sin sesión, leía el nombre, el teléfono y las coordenadas exactas del comprador."""
    dueno = str(uuid.uuid4())
    pedido_id = _crear_pedido_de(dueno)
    try:
        _como(dueno, "comprador")
        propio = client.get(f"/pedidos/{pedido_id}", headers=CON_TOKEN)
        assert propio.status_code == 200, propio.text
        assert propio.json()["comprador_telefono"] == "987654321"

        # otro comprador, un verificador y un productor (que no lo necesita para nada): 403
        for rol in ("comprador", "verificador", "productor"):
            _como(str(uuid.uuid4()), rol)
            ajeno = client.get(f"/pedidos/{pedido_id}", headers=CON_TOKEN)
            assert ajeno.status_code == 403, (rol, ajeno.text)
            assert "987654321" not in ajeno.text

        _como(str(uuid.uuid4()), "admin")
        assert client.get(f"/pedidos/{pedido_id}", headers=CON_TOKEN).status_code == 200
    finally:
        _como(str(uuid.uuid4()), "comprador")


def test_un_repartidor_ve_el_pedido_solo_si_tiene_asignado_su_envio():
    """Tras exigir sesión, cualquier repartidor con solo conocer el id leía el nombre, el teléfono y las
    coordenadas del comprador de un pedido que no era suyo."""
    pedido_id = _crear_pedido_de(str(uuid.uuid4()))
    try:
        for estado in ("asignado", "en_camino", "entregado"):
            resp, llamadas = _leer_como_repartidor(pedido_id, estado=estado)
            assert resp.status_code == 200, (estado, resp.text)
            # le pregunta a Transporte con el token del propio repartidor
            url, kwargs = llamadas[0]
            assert url.endswith(f"/envios/{pedido_id}")
            assert kwargs["headers"] == {"Authorization": "Bearer token-de-prueba"}

        # una propuesta todavía no aceptada, o rechazada, no da acceso
        for estado in ("propuesto", "rechazado", "pendiente_asignacion", None):
            resp, _ = _leer_como_repartidor(pedido_id, estado=estado)
            assert resp.status_code == 403, (estado, resp.text)
            assert "987654321" not in resp.text

        # Transporte le dice que ese envío no es suyo (403) o que no existe (404)
        for status in (403, 404):
            resp, _ = _leer_como_repartidor(pedido_id, status=status)
            assert resp.status_code == 403, (status, resp.text)
    finally:
        _como(str(uuid.uuid4()), "comprador")


def test_si_transporte_no_responde_un_repartidor_recibe_503_no_el_pedido():
    pedido_id = _crear_pedido_de(str(uuid.uuid4()))
    try:
        resp, _ = _leer_como_repartidor(pedido_id, roto=True)
        assert resp.status_code == 503, resp.text
        assert "987654321" not in resp.text
    finally:
        _como(str(uuid.uuid4()), "comprador")


def test_el_comprador_dueno_y_el_admin_no_necesitan_preguntarle_a_transporte():
    dueno = str(uuid.uuid4())
    pedido_id = _crear_pedido_de(dueno)
    llamadas = []
    try:
        with patch("main.httpx.Client", return_value=_ClienteTransporteFalso(llamadas, status=403)):
            _como(dueno, "comprador")
            assert client.get(f"/pedidos/{pedido_id}", headers=CON_TOKEN).status_code == 200
            _como(str(uuid.uuid4()), "admin")
            assert client.get(f"/pedidos/{pedido_id}", headers=CON_TOKEN).status_code == 200
        assert llamadas == []
    finally:
        _como(str(uuid.uuid4()), "comprador")


def test_un_pedido_no_se_puede_leer_sin_sesion():
    pedido_id = _crear_pedido_de(str(uuid.uuid4()))
    guardado = main.app.dependency_overrides.pop(verificar_token)
    try:
        assert client.get(f"/pedidos/{pedido_id}").status_code in (401, 403)
    finally:
        main.app.dependency_overrides[verificar_token] = guardado


def test_la_ruta_interna_de_pedidos_solo_responde_con_el_secreto_de_transporte(monkeypatch):
    pedido_id = _crear_pedido_de(str(uuid.uuid4()))
    monkeypatch.setattr(main, "TRANSPORTE_A_PEDIDOS_SECRETO", "secreto-transporte")

    ok = client.get(f"/pedidos/interno/{pedido_id}", headers={"X-Servicio-Secreto": "secreto-transporte"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["id"] == pedido_id and "costo_envio" in ok.json() and "items" in ok.json()

    assert client.get(f"/pedidos/interno/{pedido_id}").status_code == 403
    assert client.get(f"/pedidos/interno/{pedido_id}", headers={"X-Servicio-Secreto": "otro"}).status_code == 403
    assert client.get(f"/pedidos/interno/{uuid.uuid4()}", headers={"X-Servicio-Secreto": "secreto-transporte"}).status_code == 404


def test_un_id_de_pedido_mal_formado_falla_422_no_500():
    assert client.get("/pedidos/no-es-un-uuid", headers=CON_TOKEN).status_code == 422



# ============ Cantidades y productos que se pueden comprar ============

def _pedidos_de(usuario_sub):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Pedido).filter(main.models.Pedido.usuario_id == uuid.UUID(usuario_sub)).count()
    finally:
        db.close()


def _intentar_pedido(payload, cliente_falso):
    sub = str(uuid.uuid4())
    _como(sub, "comprador")
    with patch("main.httpx.Client", return_value=cliente_falso), patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)
    return sub, resp, mock_publicar


def test_un_pedido_con_una_cantidad_negativa_cero_o_enorme_falla_422_y_no_crea_nada():
    """Una línea con cantidad negativa restaba dinero al total (el cargo suma cantidad x precio) y subía el
    stock del producto: con otra línea positiva para pasar el mínimo, el pedido salía casi gratis."""
    for cantidad in (-50, 0, 100_001):
        payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0, cantidad=cantidad)
        sub, resp, publicar = _intentar_pedido(payload, _mock_httpx_client(precio=50.0))
        assert resp.status_code == 422, (cantidad, resp.text)
        publicar.assert_not_called()
        assert _pedidos_de(sub) == 0


def test_el_ataque_de_la_linea_negativa_no_pasa():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0, cantidad=100)
    payload["items"].append({"producto_id": str(uuid.uuid4()), "cantidad": -50})

    sub, resp, publicar = _intentar_pedido(payload, _mock_httpx_client(precio=50.0))

    assert resp.status_code == 422, resp.text
    publicar.assert_not_called()
    assert _pedidos_de(sub) == 0


def test_un_pedido_sin_lineas_o_con_demasiadas_falla_422():
    vacio = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
    vacio["items"] = []
    largo = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
    largo["items"] = [{"producto_id": str(uuid.uuid4()), "cantidad": 1} for _ in range(main.LINEAS_MAXIMAS_POR_PEDIDO + 1)]

    for payload in (vacio, largo):
        _, resp, _ = _intentar_pedido(payload, _mock_httpx_client(precio=50.0))
        assert resp.status_code == 422, resp.text


def test_un_id_de_producto_que_no_es_uuid_falla_422_y_no_llega_a_la_url_de_productos():
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
    payload["items"][0]["producto_id"] = "../../productos/mios"
    cliente = _mock_httpx_client(precio=50.0)

    _, resp, _ = _intentar_pedido(payload, cliente)

    assert resp.status_code == 422, resp.text
    cliente.get.assert_not_called()


def test_el_nombre_de_quien_recibe_no_puede_estar_vacio_ni_ser_enorme():
    for nombre in ("", "   ", "x" * 101):
        payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)
        payload["comprador_nombre"] = nombre
        _, resp, _ = _intentar_pedido(payload, _mock_httpx_client(precio=50.0))
        assert resp.status_code == 422, (nombre, resp.text)


def test_un_producto_en_transito_o_borrador_no_se_puede_comprar():
    for estado, precio in (("en_transito", 50.0), ("borrador", None)):
        cliente = _mock_httpx_client(precio=precio)
        cliente.get.return_value.json.return_value["estado"] = estado
        payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)

        sub, resp, publicar = _intentar_pedido(payload, cliente)

        assert resp.status_code == 409, (estado, resp.text)
        assert "no está disponible" in resp.json()["detail"]
        cliente.post.assert_not_called()  # no se descontó nada
        publicar.assert_not_called()
        assert _pedidos_de(sub) == 0


def test_el_stock_se_descuenta_de_todas_las_lineas_juntas_y_con_el_secreto(monkeypatch):
    monkeypatch.setattr(main, "PEDIDOS_A_PRODUCTOS_SECRETO", "secreto-de-prueba-pedidos")
    cliente = _mock_httpx_client(precio=50.0)
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0, cantidad=2)

    sub, resp, _ = _intentar_pedido(payload, cliente)

    assert resp.status_code == 200, resp.text
    cliente.post.assert_called_once()
    llamada = cliente.post.call_args
    assert llamada.args[0].endswith("/productos/interno/descontar-stock-lote")
    assert llamada.kwargs["headers"] == {"X-Servicio-Secreto": "secreto-de-prueba-pedidos"}
    assert llamada.kwargs["json"]["lineas"] == [{"producto_id": payload["items"][0]["producto_id"], "cantidad": 2}]


def test_si_productos_rechaza_el_descuento_el_pedido_no_se_crea():
    """Antes la respuesta del descuento se ignoraba: si otro comprador se llevó el stock justo antes, el
    pedido se creaba igual y se vendía stock que no existía."""
    cliente = _mock_httpx_client(precio=50.0)
    cliente.post.return_value = MagicMock(status_code=409)
    cliente.post.return_value.json.return_value = {"detail": "Stock insuficiente para Producto de prueba"}
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)

    sub, resp, publicar = _intentar_pedido(payload, cliente)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "Stock insuficiente para Producto de prueba"
    publicar.assert_not_called()
    assert _pedidos_de(sub) == 0


def test_si_productos_no_responde_al_descontar_el_pedido_no_se_crea_y_da_503():
    cliente = _mock_httpx_client(precio=50.0)
    cliente.post.side_effect = main.httpx.RequestError("boom")
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=50.0)

    sub, resp, publicar = _intentar_pedido(payload, cliente)

    assert resp.status_code == 503, resp.text
    publicar.assert_not_called()
    assert _pedidos_de(sub) == 0


def test_el_evento_pedido_creado_lleva_la_cantidad_en_unidad_base_de_cada_linea():
    """Pagos devuelve o vuelve a reservar el stock a partir de este evento: necesita lo que se DESCONTÓ (3 sacos de
    10 kg = 30), no lo que compró el comprador (3)."""
    payload = _payload(DESTINO_DENTRO_AYACUCHO, precio_item=5.0, cantidad=3, unidad="saco")
    payload["items"].append({"producto_id": str(uuid.uuid4()), "cantidad": 2})  # una línea en la unidad base
    unidades_alt = [{"unidad": "saco", "precio": 45.0, "factor_a_base": 10.0}]
    cliente = _mock_httpx_client(precio=5.0, stock=100, unidades_alternativas=unidades_alt)

    with patch("main.httpx.Client", return_value=cliente), patch("main.publicar_evento") as mock_publicar:
        resp = client.post("/pedidos", json=payload)

    assert resp.status_code == 200, resp.text
    evento = mock_publicar.call_args[0][0]
    assert evento["evento"] == "pedido_creado"
    por_producto = {i["producto_id"]: i for i in evento["items"]}
    en_sacos = por_producto[payload["items"][0]["producto_id"]]
    en_base = por_producto[payload["items"][1]["producto_id"]]
    assert (en_sacos["cantidad"], en_sacos["unidad"], en_sacos["cantidad_base"]) == (3, "saco", 30.0)
    assert (en_base["cantidad"], en_base["unidad"], en_base["cantidad_base"]) == (2, None, 2)
    assert en_sacos["precio_unitario"] == 45.0


# ============ GET /pedidos/interno/{id}/resumen-entrega: lo que ve un repartidor antes de aceptar ============

def test_el_resumen_de_entrega_trae_distancia_peso_y_tarifa_sin_datos_del_comprador(monkeypatch):
    """El repartidor decide si acepta una propuesta con esto; el pedido completo (nombre, teléfono, coordenadas) solo
    se ve una vez aceptado el envío."""
    pedido_id = _crear_pedido_de(str(uuid.uuid4()))
    monkeypatch.setattr(main, "TRANSPORTE_A_PEDIDOS_SECRETO", "secreto-transporte")

    resp = client.get(f"/pedidos/interno/{pedido_id}/resumen-entrega", headers={"X-Servicio-Secreto": "secreto-transporte"})

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert set(cuerpo) == {"distancia_km", "peso_total_kg", "costo_envio", "requiere_vehiculo_grande"}
    db = main.SessionLocal()
    try:
        pedido = db.query(main.models.Pedido).filter(main.models.Pedido.id == pedido_id).first()
        distancia, _ = main.calcular_envio_a_destino(db, pedido.destino_latitud, pedido.destino_longitud)
        assert cuerpo["distancia_km"] == distancia
        assert cuerpo["costo_envio"] == float(pedido.costo_envio)
        assert cuerpo["peso_total_kg"] == (float(pedido.peso_total_kg) if pedido.peso_total_kg is not None else None)
        assert cuerpo["requiere_vehiculo_grande"] == bool(pedido.requiere_vehiculo_grande)
    finally:
        db.close()
    # nada del comprador: ni su nombre, ni su teléfono, ni las coordenadas exactas del destino
    assert "Cliente de prueba" not in resp.text and "987654321" not in resp.text
    assert str(DESTINO_DENTRO_AYACUCHO["destino_latitud"]) not in resp.text


def test_el_resumen_de_entrega_solo_lo_da_a_transporte_con_su_secreto(monkeypatch):
    pedido_id = _crear_pedido_de(str(uuid.uuid4()))
    monkeypatch.setattr(main, "TRANSPORTE_A_PEDIDOS_SECRETO", "secreto-transporte")
    ruta = f"/pedidos/interno/{pedido_id}/resumen-entrega"

    assert client.get(ruta).status_code == 403
    assert client.get(ruta, headers={"X-Servicio-Secreto": "otro"}).status_code == 403
    # una sesión de usuario (aunque sea admin) no reemplaza al secreto de servicio
    _como(str(uuid.uuid4()), "admin")
    try:
        assert client.get(ruta, headers=CON_TOKEN).status_code == 403
    finally:
        _como(str(uuid.uuid4()), "comprador")


def test_el_resumen_de_entrega_de_un_pedido_que_no_existe_falla_404_y_un_id_mal_formado_422(monkeypatch):
    monkeypatch.setattr(main, "TRANSPORTE_A_PEDIDOS_SECRETO", "secreto-transporte")
    cabeceras = {"X-Servicio-Secreto": "secreto-transporte"}

    assert client.get(f"/pedidos/interno/{uuid.uuid4()}/resumen-entrega", headers=cabeceras).status_code == 404
    assert client.get("/pedidos/interno/no-es-uuid/resumen-entrega", headers=cabeceras).status_code == 422


# ============ Condiciones de compra (GET /pedidos/condiciones): el mínimo que muestra el carrito ============

def test_las_condiciones_de_compra_son_publicas_y_dicen_el_monto_minimo():
    sesion = main.app.dependency_overrides.pop(verificar_token, None)  # sin sesión: es pública
    try:
        resp = client.get("/pedidos/condiciones")
    finally:
        if sesion is not None:
            main.app.dependency_overrides[verificar_token] = sesion

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"monto_minimo": main.MONTO_MINIMO_PEDIDO}


def test_el_minimo_que_se_muestra_es_el_mismo_que_se_exige_al_pedir(monkeypatch):
    """Lo que dice /pedidos/condiciones y lo que rechaza POST /pedidos salen de la misma constante."""
    monkeypatch.setattr(main, "MONTO_MINIMO_PEDIDO", 77.0)

    assert client.get("/pedidos/condiciones").json() == {"monto_minimo": 77.0}


def test_condiciones_no_se_confunde_con_un_id_de_pedido():
    """Se declaró antes de /pedidos/{pedido_id}: si no, "condiciones" se leería como un id (422) en vez de responder."""
    assert client.get("/pedidos/condiciones").status_code == 200
