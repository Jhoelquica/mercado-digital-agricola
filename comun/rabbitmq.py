"""Publicar y consumir los eventos de RabbitMQ.

Publicar: publicar_evento(exchange, datos) deja un mensaje persistente en un exchange fanout. La conexión se cierra
siempre, también si la publicación falla.

Consumir: lanzar_consumidor_en_hilo(servicio, suscripciones) abre un hilo que se conecta, se suscribe y, si la conexión
se cae, cierra la vieja y vuelve a conectarse a los ESPERA_PARA_RECONECTAR segundos. Cada mensaje se procesa hasta
INTENTOS veces; si fallan todas, va a <cola>.fallidos con el motivo en la cabecera x-motivo, y queda un log de error.

La ruta a fallidos es configuración de RabbitMQ, no de las colas (así no hay que borrar ni redeclarar las que existen):
la policy "fallidos" de deploy/rabbitmq/definiciones.json, que RabbitMQ importa al arrancar, manda lo que se rechaza en
cualquier cola de trabajo al exchange "fallidos"; cada consumidor liga ahí su <cola>.fallidos con el nombre de su cola.
Lo que llega a ese exchange sin el nombre de una cola (sin ruta) va a fallidos.sin_ruta, su alternate exchange.
"""
import json
import logging
import os
import sys
import threading
import time
import uuid

import pika
from sqlalchemy import exc as errores_de_la_base

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.getenv("RABBITMQ_USER") or sys.exit("Falta la variable de entorno RABBITMQ_USER: el servicio no arranca sin ella.")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD") or sys.exit("Falta la variable de entorno RABBITMQ_PASSWORD: el servicio no arranca sin ella.")

# El dead-letter exchange de la policy "fallidos" (deploy/rabbitmq/definiciones.json).
EXCHANGE_FALLIDOS = "fallidos"

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


def publicar_evento(exchange: str, datos: dict) -> str:
    """Publica el evento y devuelve su evento_id. Todo mensaje lleva uno (un UUID): con él los consumidores reconocen un
    repetido (comun/inbox.py). Si `datos` ya trae uno se respeta (la bandeja de salida de Pedidos usa el id de su fila,
    así un reenvío llega con el mismo); si no, se genera. Uno que no es un UUID no se publica (ValueError)."""
    mensaje = dict(datos)
    evento_id = str(uuid.UUID(str(mensaje.get("evento_id") or uuid.uuid4())))
    mensaje["evento_id"] = evento_id
    conexion = pika.BlockingConnection(_parametros())
    try:
        canal = conexion.channel()
        canal.exchange_declare(exchange=exchange, exchange_type="fanout", durable=True)
        # delivery_mode=2 (persistente): sin esto el mensaje se pierde si RabbitMQ se reinicia antes de que lo consuman,
        # aunque el exchange y las colas sean durables. message_id: el mismo evento_id, a la vista en el panel.
        canal.basic_publish(exchange=exchange, routing_key="", body=json.dumps(mensaje, default=str),
                            properties=pika.BasicProperties(delivery_mode=2, message_id=evento_id))
    finally:
        _cerrar(conexion)
    return evento_id


def _apartar(canal, cola, metodo, propiedades, cuerpo, motivo) -> None:
    """Devuelve el mensaje a su cola con el motivo y los intentos en la cabecera, y confirma el original. Al llegar de
    nuevo se rechaza, y la policy lo lleva a <cola>.fallidos: así llega con el motivo (un rechazo no puede agregar
    cabeceras) y la ruta a fallidos sigue siendo la de RabbitMQ. Se publica con el nombre de la cola como routing key:
    el exchange "fallidos" lo usa para elegir la cola de fallidos."""
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
            # Ya falló INTENTOS veces: rechazado, la policy "fallidos" lo lleva a <cola>.fallidos.
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
    """El bucle del hilo consumidor; no termina nunca. `suscripciones` son (exchange, cola, procesar). Cada cola tiene su
    <cola>.fallidos, ligada al exchange "fallidos" con el nombre de la cola (lo que la policy rechaza llega ahí)."""
    colas = [cola for _, cola, _ in suscripciones]
    while True:
        conexion = None
        try:
            conexion = pika.BlockingConnection(_parametros())
            if antes_de_consumir:
                antes_de_consumir(conexion)
            canal = conexion.channel()
            # Lo crea RabbitMQ con sus definiciones; declararlo igual (con lo mismo, es idempotente) evita que la ligadura de
            # abajo falle si el consumidor llega antes de que termine la importación.
            canal.exchange_declare(exchange=EXCHANGE_FALLIDOS, exchange_type="direct", durable=True)
            for exchange, cola, procesar in suscripciones:
                canal.exchange_declare(exchange=exchange, exchange_type="fanout", durable=True)
                canal.queue_declare(queue=cola, durable=True)
                canal.queue_bind(exchange=exchange, queue=cola)
                canal.queue_declare(queue=f"{cola}.fallidos", durable=True)
                canal.queue_bind(exchange=EXCHANGE_FALLIDOS, queue=f"{cola}.fallidos", routing_key=cola)
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
