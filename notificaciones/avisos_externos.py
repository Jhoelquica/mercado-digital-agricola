"""Avisos fuera de la aplicación (H6 de la validación con usuarios del 2026-10-13): antes los avisos solo se veían
entrando a "Notificaciones"; el comprador no se enteraba de que su reserva había llegado.

Cada aviso que se guarda para un usuario también se le manda:
  1. por Web Push a cada navegador donde activó "Avisos en este celular" (gratis: lo entregan Google, Mozilla, Microsoft
     o Apple; solo hacen falta las claves VAPID en el Secret), y
  2. si por ahí no le llegó (no lo activó, o el navegador ya no lo tiene) y pidió el respaldo por correo, por correo
     (cualquier SMTP: el de Gmail con una contraseña de aplicación o un proveedor con plan gratuito).
Sin las claves VAPID o sin SMTP, ese canal queda apagado y la aplicación funciona como antes. Nada de esto rompe el
consumo de eventos: un fallo se registra y el aviso igual queda guardado en la aplicación.

Configuración (variables de entorno, todas opcionales):
  VAPID_CLAVE_PUBLICA, VAPID_CLAVE_PRIVADA  claves del servidor para Web Push (scripts/generar_claves_avisos.py)
  VAPID_CONTACTO                            "mailto:<correo de quien opera ChakraShop>" (lo piden los servicios de push)
  SMTP_HOST, SMTP_PUERTO (587), SMTP_USUARIO, SMTP_CLAVE, SMTP_REMITENTE   el correo de respaldo
  URL_APP                                   la dirección pública de ChakraShop, para el enlace del correo
  USUARIOS_URL                              de dónde se lee el correo del usuario al activar el respaldo
"""
import json
import logging
import os
import smtplib
import ssl
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from urllib.parse import urlparse

import requests
from pywebpush import WebPushException, webpush

import models
from database import SessionLocal

logger = logging.getLogger("notificaciones.avisos")

VAPID_CLAVE_PUBLICA = os.getenv("VAPID_CLAVE_PUBLICA") or None
VAPID_CLAVE_PRIVADA = os.getenv("VAPID_CLAVE_PRIVADA") or None
VAPID_CONTACTO = os.getenv("VAPID_CONTACTO") or None
SMTP_HOST = os.getenv("SMTP_HOST") or None
SMTP_PUERTO = int(os.getenv("SMTP_PUERTO") or 587)
SMTP_USUARIO = os.getenv("SMTP_USUARIO") or None
SMTP_CLAVE = os.getenv("SMTP_CLAVE") or None
SMTP_REMITENTE = os.getenv("SMTP_REMITENTE") or None
URL_APP = os.getenv("URL_APP") or None
USUARIOS_URL = os.getenv("USUARIOS_URL", "http://localhost:8006")

# Un aviso que no se pudo entregar (celular apagado) espera hasta un día en el servicio de push; después ya no sirve.
VIDA_DEL_AVISO_SEGUNDOS = 24 * 60 * 60
# Al tocar el aviso se abre la aplicación en Notificaciones (ver frontend/sw.js y el ?vista= de app.js).
URL_AL_TOCAR = "/?vista=notificaciones"
# Cuántos navegadores puede tener activos una cuenta: al pasar, se quita el más antiguo.
MAXIMO_DISPOSITIVOS = 10
# Los servicios de push de los navegadores. Solo se aceptan endpoints de estos: el servidor les hace un POST, y sin esta
# lista cualquiera podría hacerle pedir una dirección interna del clúster (SSRF).
SERVICIOS_DE_PUSH = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "notify.windows.com",
                     "push.apple.com")

TITULOS = {
    "pedido_creado": "Tu pedido",
    "envio_actualizado": "Tu envío",
    "producto_disponible": "Tu reserva",
    "recojo_actualizado": "Tu recojo",
    "pago_a_beneficiario": "Tu cobro",
    "pago_confirmado": "Tu pago",
    "saldo_pagado": "Tu pago",
    "pago_rechazado": "Tu pago",
    "saldo_rechazado": "Tu pago",
    "pago_reembolsado": "Tu pago",
}


def push_disponible() -> bool:
    return bool(VAPID_CLAVE_PUBLICA and VAPID_CLAVE_PRIVADA and VAPID_CONTACTO)


def correo_disponible() -> bool:
    return bool(SMTP_HOST and SMTP_REMITENTE)


def titulo_de(tipo: str) -> str:
    return f"ChakraShop · {TITULOS.get(tipo, 'Aviso')}"


def endpoint_valido(endpoint: str) -> bool:
    """Solo https y de un servicio de push conocido (ver SERVICIOS_DE_PUSH)."""
    try:
        url = urlparse(endpoint)
    except ValueError:
        return False
    host = (url.hostname or "").lower()
    return url.scheme == "https" and any(host == s or host.endswith("." + s) for s in SERVICIOS_DE_PUSH)


def enviar_push(db, usuario_id, titulo: str, cuerpo: str) -> int:
    """Manda el aviso a cada navegador activo del usuario. Devuelve a cuántos llegó al servicio de push. Los que el
    servicio ya no reconoce (404/410: el usuario borró los datos del navegador o quitó el permiso) se borran."""
    if not push_disponible():
        return 0
    datos = json.dumps({"titulo": titulo, "cuerpo": cuerpo, "url": URL_AL_TOCAR}, ensure_ascii=False)
    enviados = 0
    for s in db.query(models.SuscripcionPush).filter(models.SuscripcionPush.usuario_id == usuario_id).all():
        try:
            webpush(subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                    data=datos, vapid_private_key=VAPID_CLAVE_PRIVADA,
                    vapid_claims={"sub": VAPID_CONTACTO},  # nuevo cada vez: pywebpush le agrega "aud" y "exp"
                    ttl=VIDA_DEL_AVISO_SEGUNDOS, timeout=10)
            enviados += 1
        except WebPushException as error:
            if getattr(error.response, "status_code", None) in (404, 410):
                db.delete(s)
                db.commit()
            else:
                logger.warning("No se pudo mandar el aviso al navegador %s: %s", s.id, error)
        except Exception:
            logger.exception("No se pudo mandar el aviso al navegador %s", s.id)
    return enviados


def enviar_correo(destino: str, asunto: str, texto: str) -> bool:
    if not correo_disponible():
        return False
    mensaje = EmailMessage()
    mensaje["From"] = SMTP_REMITENTE
    mensaje["To"] = destino
    mensaje["Subject"] = asunto
    pie = (f"\n\nMíralo en ChakraShop: {URL_APP}" if URL_APP else "") + (
        "\n\nTe llega este correo porque pediste los avisos por correo en ChakraShop. Puedes apagarlo en Notificaciones.")
    mensaje.set_content(texto + pie)
    try:
        if SMTP_PUERTO == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PUERTO, timeout=15, context=ssl.create_default_context()) as smtp:
                if SMTP_USUARIO:
                    smtp.login(SMTP_USUARIO, SMTP_CLAVE or "")
                smtp.send_message(mensaje)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PUERTO, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if SMTP_USUARIO:
                    smtp.login(SMTP_USUARIO, SMTP_CLAVE or "")
                smtp.send_message(mensaje)
        return True
    except Exception:
        logger.exception("No se pudo mandar el correo de aviso")
        return False


def avisar_fuera_de_la_app(usuario_id, tipo: str, mensaje: str):
    """Push a sus navegadores y, si no llegó a ninguno, el correo de respaldo (si lo pidió). Devuelve "push", "correo" o
    None. Nunca lanza: el aviso ya quedó guardado en la aplicación."""
    db = SessionLocal()
    try:
        titulo = titulo_de(tipo)
        if enviar_push(db, usuario_id, titulo, mensaje):
            return "push"
        preferencia = db.query(models.PreferenciaCorreo).filter(models.PreferenciaCorreo.usuario_id == usuario_id).first()
        if preferencia and enviar_correo(preferencia.correo, titulo, mensaje):
            return "correo"
        return None
    except Exception:
        logger.exception("No se pudo avisar fuera de la aplicación")
        return None
    finally:
        db.close()


_hilos = ThreadPoolExecutor(max_workers=4, thread_name_prefix="avisos")


def despachar(usuario_id, tipo: str, mensaje: str) -> None:
    """Lo llama el consumidor de RabbitMQ: en otro hilo, para no detenerlo (un correo puede tardar varios segundos)."""
    if usuario_id and (push_disponible() or correo_disponible()):
        _hilos.submit(avisar_fuera_de_la_app, usuario_id, tipo, mensaje)


def correo_del_usuario(usuario_id: str, token: str):
    """El correo de la cuenta, leído en Usuarios con el token del propio usuario (GET /usuarios/{id} solo deja leer el
    propio perfil). None si no se pudo."""
    try:
        resp = requests.get(f"{USUARIOS_URL}/usuarios/{usuario_id}", headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return (resp.json() or {}).get("email") or None
