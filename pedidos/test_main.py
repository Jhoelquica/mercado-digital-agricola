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

# Bypassea la autenticación real: en todos los tests se simula un comprador ya autenticado.
# Se sobreescribe la dependencia estable `verificar_token` (no `requiere_rol(...)`, que genera
# un closure nuevo en cada llamada y por eso no es un objetivo válido de override).
main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}

# Coordenadas de ejemplo dentro de Ayacucho (Huamanga, aprox.) y claramente fuera (Lima).
DESTINO_DENTRO_AYACUCHO = {"destino_latitud": -13.1588, "destino_longitud": -74.2239}
DESTINO_FUERA_AYACUCHO = {"destino_latitud": -12.0464, "destino_longitud": -77.0428}


def _mock_httpx_client(precio, stock=100, nombre="Producto de prueba"):
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
    }
    mock_client.get.return_value = respuesta_get
    mock_client.patch.return_value = MagicMock(status_code=200)
    return mock_client


def _payload(destino, precio_item, cantidad=1):
    return {
        "comprador_nombre": "Cliente de prueba",
        "destino_latitud": destino["destino_latitud"],
        "destino_longitud": destino["destino_longitud"],
        "items": [{"producto_id": str(uuid.uuid4()), "cantidad": cantidad}],
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
