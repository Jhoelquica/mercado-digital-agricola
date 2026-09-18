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


def _generar_pin_entrega():
    """4 dígitos, con ceros a la izquierda (ej. "0042") — el comprador lo ve en su pedido y se
    lo dicta al repartidor en la puerta, ver POST /envios/{id}/entregar."""
    return f"{secrets.randbelow(10000):04d}"


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)

    if datos.get("evento") == "pedido_creado":
        db = SessionLocal()
        try:
            nuevo_envio = models.Envio(
                pedido_id=datos["pedido_id"],
                estado="pendiente",
                pin_entrega=_generar_pin_entrega(),
            )
            db.add(nuevo_envio)
            db.commit()
            proponer_envio_a_repartidor(nuevo_envio.id, db)
            print(f"[Transporte] Envío creado automáticamente para pedido {datos['pedido_id']}")
        finally:
            db.close()

    ch.basic_ack(delivery_tag=method.delivery_tag)


def _iniciar_consumo():
    while True:
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
            )
            channel = connection.channel()

            channel.exchange_declare(exchange="eventos_pedidos", exchange_type="fanout", durable=True)
            channel.queue_declare(queue="transporte_pedidos", durable=True)
            channel.queue_bind(exchange="eventos_pedidos", queue="transporte_pedidos")

            channel.basic_consume(queue="transporte_pedidos", on_message_callback=_procesar_mensaje)
            print("[Transporte] Escuchando eventos de pedidos...")
            channel.start_consuming()

        except Exception as e:
            print(f"[Transporte] RabbitMQ no disponible, reintentando en 5s... ({e})")
            time.sleep(5)


def lanzar_consumidor_en_hilo():
    hilo = threading.Thread(target=_iniciar_consumo, daemon=True)
    hilo.start()