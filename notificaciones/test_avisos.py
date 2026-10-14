"""H6 (validación con usuarios del 2026-10-13): los avisos también llegan al celular (Web Push) y, de respaldo, por correo.

No sale nada a internet: webpush, el SMTP y la lectura del correo en Usuarios se reemplazan por dobles. Las funciones de
los endpoints se llaman directo (la imagen de este servicio no trae httpx, que necesita el TestClient).
"""
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pywebpush import WebPushException

import avisos_externos
import main
import models
from database import SessionLocal
from test_main import _procesar


@pytest.fixture
def db():
    sesion = SessionLocal()
    try:
        yield sesion
    finally:
        sesion.close()


@pytest.fixture
def canales(monkeypatch):
    """Push y correo encendidos, el envío en el mismo hilo y lo que se "manda" guardado en listas."""
    monkeypatch.setattr(avisos_externos, "VAPID_CLAVE_PUBLICA", "clave-publica-de-prueba")
    monkeypatch.setattr(avisos_externos, "VAPID_CLAVE_PRIVADA", "clave-privada-de-prueba")
    monkeypatch.setattr(avisos_externos, "VAPID_CONTACTO", "mailto:avisos@ejemplo.test")
    monkeypatch.setattr(avisos_externos, "SMTP_HOST", "smtp.ejemplo.test")
    monkeypatch.setattr(avisos_externos, "SMTP_REMITENTE", "ChakraShop <avisos@ejemplo.test>")
    monkeypatch.setattr(avisos_externos, "_hilos", SimpleNamespace(submit=lambda f, *a: f(*a)))
    enviados = SimpleNamespace(push=[], correos=[], fallan=set())

    def webpush_falso(subscription_info, data, vapid_private_key, vapid_claims, ttl, timeout):
        if subscription_info["endpoint"] in enviados.fallan:
            raise WebPushException("ya no existe", response=SimpleNamespace(status_code=410))
        enviados.push.append((subscription_info["endpoint"], json.loads(data)))

    class SmtpFalso:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **kw): pass
        def login(self, *a): pass
        def send_message(self, mensaje): enviados.correos.append((mensaje["To"], mensaje["Subject"], mensaje.get_content()))

    monkeypatch.setattr(avisos_externos, "webpush", webpush_falso)
    monkeypatch.setattr(avisos_externos.smtplib, "SMTP", SmtpFalso)
    return enviados


def _endpoint():
    return f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4()}"


def _activar(db, usuario_id, endpoint):
    datos = main.SuscripcionPushEntrada(endpoint=endpoint, keys={"p256dh": "B" * 87, "auth": "a" * 22})
    return main.activar_avisos_en_este_navegador(datos, db, {"sub": usuario_id, "rol": "comprador"})


def test_sin_claves_ni_smtp_los_avisos_fuera_de_la_app_quedan_apagados(monkeypatch):
    for nombre in ("VAPID_CLAVE_PUBLICA", "VAPID_CLAVE_PRIVADA", "VAPID_CONTACTO", "SMTP_HOST", "SMTP_REMITENTE"):
        monkeypatch.setattr(avisos_externos, nombre, None)
    llamados = []
    monkeypatch.setattr(avisos_externos, "_hilos", SimpleNamespace(submit=lambda *a: llamados.append(a)))

    assert main.configuracion_de_avisos() == {"push": False, "clave_publica": None, "correo": False}
    _procesar({"evento": "producto_disponible", "usuario_id": str(uuid.uuid4()), "producto_id": str(uuid.uuid4()),
               "nombre_producto": "Kiwicha"})
    assert llamados == []  # el aviso queda en la aplicación, como antes


def test_la_reserva_que_llega_avisa_al_celular_del_comprador(db, canales):
    comprador, endpoint = str(uuid.uuid4()), _endpoint()
    assert main.configuracion_de_avisos()["clave_publica"] == "clave-publica-de-prueba"
    assert _activar(db, comprador, endpoint) == {"dispositivos": 1, "correo": None}

    _procesar({"evento": "producto_disponible", "usuario_id": comprador, "producto_id": str(uuid.uuid4()),
               "nombre_producto": "Papa Nativa"})

    assert len(canales.push) == 1
    a_donde, datos = canales.push[0]
    assert a_donde == endpoint and datos["titulo"] == "ChakraShop · Tu reserva" and "Papa Nativa" in datos["cuerpo"]
    assert datos["url"] == "/?vista=notificaciones"
    assert canales.correos == []  # le llegó al celular: no hace falta el correo


def test_solo_se_aceptan_direcciones_de_los_servicios_de_push(db, canales):
    for malo in ("http://usuarios:8006/usuarios/x" + "y" * 10, "https://ejemplo.test/fcm.googleapis.com/abc",
                 "https://fcm.googleapis.com.ejemplo.test/send/abc", "http://fcm.googleapis.com/fcm/send/abc"):
        with pytest.raises(HTTPException) as error:
            _activar(db, str(uuid.uuid4()), malo)
        assert error.value.status_code == 422, malo
    for bueno in ("https://updates.push.services.mozilla.com/wpush/v2/abc", "https://web.push.apple.com/QAbc",
                  "https://wns2-par02p.notify.windows.com/w/?token=abc"):
        assert avisos_externos.endpoint_valido(bueno), bueno


def test_el_mismo_navegador_pasa_a_la_cuenta_que_entra_y_se_quita_al_cerrar_sesion(db, canales):
    antes, despues, endpoint = str(uuid.uuid4()), str(uuid.uuid4()), _endpoint()
    _activar(db, antes, endpoint)
    assert _activar(db, despues, endpoint)["dispositivos"] == 1
    assert main.mis_avisos(db, {"sub": antes})["dispositivos"] == 0

    assert main.quitar_avisos_de_este_navegador(main.EndpointPush(endpoint=endpoint), db) == {"quitadas": 1}
    assert main.mis_avisos(db, {"sub": despues})["dispositivos"] == 0


def test_un_navegador_que_ya_no_existe_se_borra_y_el_aviso_va_por_correo(db, canales, monkeypatch):
    agricultor, endpoint = str(uuid.uuid4()), _endpoint()
    _activar(db, agricultor, endpoint)
    monkeypatch.setattr(avisos_externos, "correo_del_usuario", lambda usuario_id, token: "rosa@ejemplo.test")
    estado = main.cambiar_aviso_por_correo(main.PreferenciaCorreoEntrada(activo=True), db, {"sub": agricultor},
                                           SimpleNamespace(credentials="token-de-rosa"))
    assert estado == {"dispositivos": 1, "correo": "rosa@ejemplo.test"}
    canales.fallan.add(endpoint)  # borró los datos del navegador: el servicio de push responde 410

    _procesar({"evento": "recojo_actualizado", "usuario_id": agricultor, "producto_id": str(uuid.uuid4()),
               "estado": "asignado", "producto_nombre": "Papa"})

    assert main.mis_avisos(db, {"sub": agricultor})["dispositivos"] == 0
    assert len(canales.correos) == 1
    destino, asunto, texto = canales.correos[0]
    assert destino == "rosa@ejemplo.test" and asunto == "ChakraShop · Tu recojo" and "Puedes apagarlo" in texto


def test_el_correo_de_respaldo_se_apaga_y_sin_smtp_no_se_puede_encender(db, canales, monkeypatch):
    comprador = str(uuid.uuid4())
    monkeypatch.setattr(avisos_externos, "correo_del_usuario", lambda usuario_id, token: "luis@ejemplo.test")
    credenciales = SimpleNamespace(credentials="token")
    main.cambiar_aviso_por_correo(main.PreferenciaCorreoEntrada(activo=True), db, {"sub": comprador}, credenciales)
    assert main.cambiar_aviso_por_correo(main.PreferenciaCorreoEntrada(activo=False), db, {"sub": comprador},
                                         credenciales) == {"dispositivos": 0, "correo": None}
    _procesar({"evento": "producto_disponible", "usuario_id": comprador, "producto_id": str(uuid.uuid4()),
               "nombre_producto": "Haba"})
    assert canales.correos == []  # lo apagó

    monkeypatch.setattr(avisos_externos, "SMTP_HOST", None)
    with pytest.raises(HTTPException) as error:
        main.cambiar_aviso_por_correo(main.PreferenciaCorreoEntrada(activo=True), db, {"sub": comprador}, credenciales)
    assert error.value.status_code == 503


def test_el_aviso_de_prueba_pide_activar_primero(db, canales):
    usuario = str(uuid.uuid4())
    with pytest.raises(HTTPException) as error:
        main.probar_avisos(db, {"sub": usuario})
    assert error.value.status_code == 409
    _activar(db, usuario, _endpoint())
    assert main.probar_avisos(db, {"sub": usuario}) == {"enviados": 1}


def test_una_cuenta_no_acumula_navegadores_sin_fin(db, canales, monkeypatch):
    monkeypatch.setattr(avisos_externos, "MAXIMO_DISPOSITIVOS", 2)
    usuario = str(uuid.uuid4())
    for _ in range(3):
        estado = _activar(db, usuario, _endpoint())
    assert estado["dispositivos"] == 2


def test_las_claves_generadas_firman_y_cifran_un_aviso_que_el_navegador_puede_leer():
    """Las claves de scripts/generar_claves_avisos.py con el pywebpush de verdad (sin red): el servicio de push recibe
    la firma VAPID con nuestra clave pública y un contenido que solo el navegador suscrito puede descifrar."""
    import base64
    import os

    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from pywebpush import webpush

    import claves_vapid

    b64url = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    publica, privada = claves_vapid.generar()
    navegador = ec.generate_private_key(ec.SECP256R1())
    secreto = os.urandom(16)
    recibido = {}

    class ServicioDePushFalso:
        def post(self, endpoint, timeout=None, data=None, headers=None):
            recibido.update(endpoint=endpoint, data=data, headers=headers)
            return SimpleNamespace(status_code=201, text="", headers={})

    webpush({"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
             "keys": {"p256dh": b64url(navegador.public_key().public_bytes(serialization.Encoding.X962,
                                                                            serialization.PublicFormat.UncompressedPoint)),
                      "auth": b64url(secreto)}},
            data=json.dumps({"titulo": "ChakraShop · Tu reserva"}), vapid_private_key=privada,
            vapid_claims={"sub": "mailto:avisos@ejemplo.test"}, ttl=60, requests_session=ServicioDePushFalso())

    assert len(publica) == 87 and recibido["headers"]["authorization"].endswith(f"k={publica}")
    claro = http_ece.decrypt(recibido["data"], private_key=navegador, auth_secret=secreto, version="aes128gcm")
    assert json.loads(claro) == {"titulo": "ChakraShop · Tu reserva"}


def test_un_recojo_sin_vehiculo_le_llega_a_todos_los_admins_y_a_nadie_mas(db, canales):
    """H8: va a la lista de cada admin y a sus celulares; un comprador no lo ve."""
    admin, otro_admin, comprador = (str(uuid.uuid4()) for _ in range(3))
    endpoint_admin, endpoint_comprador = _endpoint(), _endpoint()
    main.activar_avisos_en_este_navegador(main.SuscripcionPushEntrada(endpoint=endpoint_admin, keys={"p256dh": "B" * 87, "auth": "a" * 22}),
                                          db, {"sub": admin, "rol": "admin"})
    _activar(db, comprador, endpoint_comprador)
    producto_id = str(uuid.uuid4())

    _procesar({"evento": "recojo_sin_vehiculo", "producto_id": producto_id, "productor_nombre": "Agro Andina",
               "peso_kg": 1600, "sacos": 32, "producto_nombre": "Papa", "distancia_km": 12})

    para_admins = [n for n in main.listar_todas(db, {"sub": otro_admin, "rol": "admin"}) if str(n.producto_id) == producto_id]
    assert len(para_admins) == 1 and para_admins[0].rol_destino == "admin" and para_admins[0].usuario_id is None
    assert "ningún vehículo registrado" in para_admins[0].mensaje
    assert [n for n in main.listar_todas(db, {"sub": comprador, "rol": "comprador"}) if str(n.producto_id) == producto_id] == []
    llegaron = [a_donde for a_donde, datos in canales.push if datos["titulo"] == "ChakraShop · Recojo sin vehículo"]
    assert endpoint_admin in llegaron and endpoint_comprador not in llegaron
