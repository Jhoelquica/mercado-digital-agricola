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


def _mock_httpx_client(precio, stock=100, nombre="Producto de prueba", unidades_alternativas=None, unidad_medida="kg", equivalencia_kg=None):
    """Reemplaza httpx.Client() dentro de main: GET devuelve el producto simulado,
    PATCH (descuento de stock) no hace nada real."""
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
    mock_client.patch.return_value = MagicMock(status_code=200)
    return mock_client


def _payload(destino, precio_item, cantidad=1, unidad=None):
    item = {"producto_id": str(uuid.uuid4()), "cantidad": cantidad}
    if unidad is not None:
        item["unidad"] = unidad
    return {
        "comprador_nombre": "Cliente de prueba",
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

    # descontar_stock recibe la cantidad YA convertida a unidad base: 3 sacos * 10 kg/saco = 30 kg.
    llamada_stock = mock_client.patch.call_args
    assert llamada_stock.kwargs["json"]["cantidad"] == 30.0


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
    mock_client.patch.return_value = MagicMock(status_code=200)

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
    reconsulta = client.get(f"/pedidos/{primero.json()['id']}")
    assert reconsulta.status_code == 200, reconsulta.text
    assert reconsulta.json()["requiere_vehiculo_grande"] is True
