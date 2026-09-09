"""
Tests del endpoint POST /pedidos, centrados en las dos validaciones fail-fast agregadas:
restricción geográfica (destino dentro de Ayacucho) y monto mínimo de compra (S/30.00).

Requiere una base de datos Postgres real accesible según database.py (usa tipos
UUID de postgresql y create_all() sobre esa conexión) — no se puede sustituir por SQLite.
El servicio de Productos (httpx.Client) y la publicación de eventos (RabbitMQ) se simulan
con mocks para no depender de esos servicios estando levantados.
"""
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)

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
