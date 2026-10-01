import os
import json
import time
import threading
import secrets

from sqlalchemy.orm import Session

import pika
from logica_repartidores import proponer_envio_a_repartidor
from database import SessionLocal
import models

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "admin")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "admin123")

COLA = "transporte_pagos"
# Cola de antes de este arreglo, ligada a eventos_pedidos: si se deja, RabbitMQ la sigue llenando con cada
# pedido_creado sin que nadie la lea. Se borra al conectarse.
COLA_ANTERIOR = "transporte_pedidos"


def _generar_pin_entrega():
    """4 dígitos, con ceros a la izquierda (ej. "0042") — el comprador lo ve en su pedido y se
    lo dicta al repartidor en la puerta, ver POST /envios/{id}/entregar."""
    return f"{secrets.randbelow(10000):04d}"


def crear_envio_si_falta(pedido_id):
    """Crea el envío del pedido y lo propone a un repartidor. Idempotente: si el envío ya existe (el mismo
    pago_confirmado entregado dos veces, o un pedido de antes de este arreglo, cuyo envío nació con
    pedido_creado) no hace nada. Devuelve el envío nuevo, o None si ya existía."""
    db = SessionLocal()
    try:
        if db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).first():
            print(f"[Transporte] El pedido {pedido_id} ya tiene envío, no se crea otro")
            return None
        nuevo_envio = models.Envio(
            pedido_id=pedido_id,
            estado="pendiente",
            pin_entrega=_generar_pin_entrega(),
        )
        db.add(nuevo_envio)
        db.commit()
        proponer_envio_a_repartidor(nuevo_envio.id, db)
        print(f"[Transporte] Envío creado para el pedido pagado {pedido_id}")
        return nuevo_envio
    finally:
        db.close()


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)

    # El envío nace cuando el pago se CONFIRMA, no cuando se crea el pedido: antes se proponía a un repartidor un
    # pedido que nadie había pagado, y podía aceptarlo, entregarlo y generar su liquidación. Un pago rechazado no
    # crea envío.
    if datos.get("evento") == "pago_confirmado":
        crear_envio_si_falta(datos["pedido_id"])

    ch.basic_ack(delivery_tag=method.delivery_tag)


def _borrar_cola_anterior(connection):
    # En su propio canal: si RabbitMQ respondiera con un error, solo se cierra este canal y no el de consumo.
    try:
        canal = connection.channel()
        canal.queue_delete(queue=COLA_ANTERIOR)
        canal.close()
    except Exception as e:
        print(f"[Transporte] No se pudo borrar la cola anterior {COLA_ANTERIOR}: {e}")


def _iniciar_consumo():
    while True:
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
            )
            _borrar_cola_anterior(connection)
            channel = connection.channel()

            channel.exchange_declare(exchange="eventos_pagos", exchange_type="fanout", durable=True)
            channel.queue_declare(queue=COLA, durable=True)
            channel.queue_bind(exchange="eventos_pagos", queue=COLA)

            channel.basic_consume(queue=COLA, on_message_callback=_procesar_mensaje)
            print("[Transporte] Escuchando eventos de pagos...")
            channel.start_consuming()

        except Exception as e:
            print(f"[Transporte] RabbitMQ no disponible, reintentando en 5s... ({e})")
            time.sleep(5)


def lanzar_consumidor_en_hilo():
    hilo = threading.Thread(target=_iniciar_consumo, daemon=True)
    hilo.start()
