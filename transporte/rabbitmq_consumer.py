import os
import json
import time
import threading
import pika

from database import SessionLocal
import models

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "admin")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "admin123")


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)

    if datos.get("evento") == "pedido_creado":
        db = SessionLocal()
        try:
            nuevo_envio = models.Envio(pedido_id=datos["pedido_id"], estado="pendiente")
            db.add(nuevo_envio)
            db.commit()
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