# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Publicar y consumir los eventos de RabbitMQ.

Publicar: publicar_evento(exchange, datos) deja un mensaje persistente en un exchange fanout. La conexión se cierra
siempre, también si la publicación falla.

Consumir: lanzar_consumidor_en_hilo(servicio, suscripciones) abre un hilo que se conecta, se suscribe y, si la conexión
se cae, cierra la vieja y vuelve a conectarse a los ESPERA_PARA_RECONECTAR segundos. Cada mensaje se procesa hasta
INTENTOS veces; si fallan todas, va a <cola>.fallidos con el motivo en la cabecera x-motivo, y queda un log de error.
La ruta a <cola>.fallidos es una policy de RabbitMQ (fallidos-<cola>, dead-letter) y no un argumento de la cola: así no
hay que borrar ni redeclarar las colas que ya existen. La pone el consumidor al conectarse, por la API de gestión.
"""
import base64
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

import pika
from sqlalchemy import exc as errores_de_la_base

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.getenv("RABBITMQ_USER") or sys.exit("Falta la variable de entorno RABBITMQ_USER: el servicio no arranca sin ella.")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD") or sys.exit("Falta la variable de entorno RABBITMQ_PASSWORD: el servicio no arranca sin ella.")
# La API de gestión (la del panel): con ella el consumidor pone sus policies de dead-letter.
RABBITMQ_GESTION_URL = (os.getenv("RABBITMQ_GESTION_URL") or f"http://{RABBITMQ_HOST}:15672").rstrip("/")

INTENTOS = 3
PAUSAS_ENTRE_INTENTOS = (1, 2)  # segundos antes del 2.º y del 3.er intento
ESPERA_PARA_RECONECTAR = 5
ESPERA_SI_LA_BASE_NO_RESPONDE = 5
CABECERA_MOTIVO = "x-motivo"
CABECERA_INTENTOS = "x-intentos"
LARGO_MAXIMO_DEL_MOTIVO = 500
# Errores que no son culpa del mensaje: la base reiniciándose o sin conexiones libres. El mensaje vuelve a la cola sin
# gastar intentos (si no, un reinicio de la base mandaría a fallidos eventos que estaban bien).
ERRORES_DE_LA_BASE = (errores_de_la_base.OperationalError, errores_de_la_base.InterfaceError,
                      errores_de_la_base.TimeoutError)

logger = logging.getLogger("comun.rabbitmq")


def _parametros() -> pika.ConnectionParameters:
    return pika.ConnectionParameters(host=RABBITMQ_HOST,
                                     credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD))


def _cerrar(conexion) -> None:
    """Cierra la conexión si sigue abierta, sin tapar el error que haya llevado a cerrarla."""
    if conexion is None:
        return
    try:
        if conexion.is_open:
            conexion.close()
    except Exception:
        logger.warning("No se pudo cerrar una conexión con RabbitMQ", exc_info=True)


def publicar_evento(exchange: str, datos: dict) -> None:
    conexion = pika.BlockingConnection(_parametros())
    try:
        canal = conexion.channel()
        canal.exchange_declare(exchange=exchange, exchange_type="fanout", durable=True)
        # delivery_mode=2 (persistente): sin esto el mensaje se pierde si RabbitMQ se reinicia antes de que lo consuman,
        # aunque el exchange y las colas sean durables.
        canal.basic_publish(exchange=exchange, routing_key="", body=json.dumps(datos, default=str),
                            properties=pika.BasicProperties(delivery_mode=2))
    finally:
        _cerrar(conexion)


def politica_de_fallidos(cola: str) -> tuple[str, dict]:
    """El nombre y la definición de la policy que manda a <cola>.fallidos lo que se rechaza en <cola>."""
    return f"fallidos-{cola}", {
        "pattern": f"^{re.escape(cola)}$",
        "definition": {"dead-letter-exchange": "", "dead-letter-routing-key": f"{cola}.fallidos"},
        "apply-to": "queues",
        "priority": 0,
    }


def asegurar_politicas(colas) -> None:
    """Crea o deja igual (PUT idempotente) la policy de fallidos de cada cola. Lanza si la API de gestión no responde."""
    credenciales = base64.b64encode(f"{RABBITMQ_USER}:{RABBITMQ_PASSWORD}".encode()).decode()
    for cola in colas:
        nombre, cuerpo = politica_de_fallidos(cola)
        peticion = urllib.request.Request(
            f"{RABBITMQ_GESTION_URL}/api/policies/%2F/{urllib.parse.quote(nombre, safe='')}",
            data=json.dumps(cuerpo).encode(), method="PUT",
            headers={"Content-Type": "application/json", "Authorization": f"Basic {credenciales}"},
        )
        with urllib.request.urlopen(peticion, timeout=5):
            pass


def _apartar(canal, cola, metodo, propiedades, cuerpo, motivo) -> None:
    """Devuelve el mensaje a su cola con el motivo y los intentos en la cabecera, y confirma el original. Al llegar de
    nuevo se rechaza, y la policy lo lleva a <cola>.fallidos: así llega con el motivo (un rechazo no puede agregar
    cabeceras) y la ruta a fallidos sigue siendo la de RabbitMQ."""
    cabeceras = dict(getattr(propiedades, "headers", None) or {})
    cabeceras.update({CABECERA_MOTIVO: motivo, CABECERA_INTENTOS: INTENTOS})
    copia = pika.BasicProperties(delivery_mode=2, headers=cabeceras,
                                 content_type=getattr(propiedades, "content_type", None),
                                 message_id=getattr(propiedades, "message_id", None),
                                 timestamp=getattr(propiedades, "timestamp", None))
    canal.basic_publish(exchange="", routing_key=cola, body=cuerpo, properties=copia)
    canal.basic_ack(delivery_tag=metodo.delivery_tag)


def _intentos_previos(propiedades) -> int:
    """Los intentos que ya gastó el mensaje (la cabecera x-intentos). Una cabecera rara cuenta como ninguno: si se dejara
    lanzar, el mensaje no se confirmaría nunca y el consumidor se reconectaría sin fin."""
    try:
        return int((getattr(propiedades, "headers", None) or {}).get(CABECERA_INTENTOS) or 0)
    except (TypeError, ValueError):
        return 0


def con_reintentos(cola: str, procesar):
    """Envuelve el callback de una cola. `procesar` es el de siempre (canal, método, propiedades, cuerpo): confirma el
    mensaje él mismo cuando le va bien y lanza una excepción cuando no."""

    def al_recibir(canal, metodo, propiedades, cuerpo):
        if _intentos_previos(propiedades) >= INTENTOS:
            # Ya falló INTENTOS veces: rechazado, la policy fallidos-<cola> lo lleva a <cola>.fallidos.
            canal.basic_reject(delivery_tag=metodo.delivery_tag, requeue=False)
            return
        for intento in range(1, INTENTOS + 1):
            try:
                procesar(canal, metodo, propiedades, cuerpo)
                return
            except pika.exceptions.AMQPError:
                raise  # la conexión o el canal con RabbitMQ: lo resuelve la reconexión, no un reintento
            except ERRORES_DE_LA_BASE as error:
                logger.warning("%s: la base no responde (%s); el mensaje vuelve a la cola sin gastar intentos", cola,
                               error)
                time.sleep(ESPERA_SI_LA_BASE_NO_RESPONDE)
                canal.basic_nack(delivery_tag=metodo.delivery_tag, requeue=True)
                return
            except Exception as error:
                motivo = f"{type(error).__name__}: {error}"[:LARGO_MAXIMO_DEL_MOTIVO]
                if intento < INTENTOS:
                    logger.warning("%s: falló el intento %d de %d (%s); se reintenta", cola, intento, INTENTOS, motivo)
                    time.sleep(PAUSAS_ENTRE_INTENTOS[intento - 1])
                    continue
                logger.error("%s: el mensaje falló %d veces y va a %s.fallidos. Motivo: %s", cola, INTENTOS, cola,
                             motivo, exc_info=True)
                _apartar(canal, cola, metodo, propiedades, cuerpo, motivo)

    return al_recibir


def consumir(servicio: str, suscripciones, antes_de_consumir=None) -> None:
    """El bucle del hilo consumidor; no termina nunca. `suscripciones` son (exchange, cola, procesar). Antes de consumir
    se aseguran las policies de fallidos: sin ellas un mensaje rechazado se perdería."""
    colas = [cola for _, cola, _ in suscripciones]
    while True:
        conexion = None
        try:
            asegurar_politicas(colas)
            conexion = pika.BlockingConnection(_parametros())
            if antes_de_consumir:
                antes_de_consumir(conexion)
            canal = conexion.channel()
            for exchange, cola, procesar in suscripciones:
                canal.exchange_declare(exchange=exchange, exchange_type="fanout", durable=True)
                canal.queue_declare(queue=cola, durable=True)
                canal.queue_bind(exchange=exchange, queue=cola)
                canal.queue_declare(queue=f"{cola}.fallidos", durable=True)
                canal.basic_consume(queue=cola, on_message_callback=con_reintentos(cola, procesar))
            logger.info("%s: escuchando %s", servicio, ", ".join(colas))
            canal.start_consuming()
        except Exception as error:
            logger.warning("%s: sin conexión con RabbitMQ, se reconecta en %d s (%s)", servicio, ESPERA_PARA_RECONECTAR,
                           error)
        finally:
            _cerrar(conexion)  # la vieja, antes de abrir otra: si no, cada reconexión dejaba una colgada
        time.sleep(ESPERA_PARA_RECONECTAR)


def lanzar_consumidor_en_hilo(servicio: str, suscripciones, antes_de_consumir=None) -> None:
    threading.Thread(target=consumir, args=(servicio, suscripciones, antes_de_consumir), daemon=True,
                     name=f"consumidor-{servicio}").start()
