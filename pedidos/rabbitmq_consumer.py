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


def _procesar_mensaje_pago(ch, method, properties, body):
    datos = json.loads(body)
    evento = datos.get("evento")

    if evento in ("pago_confirmado", "pago_rechazado"):
        db = SessionLocal()
        try:
            pedido = db.query(models.Pedido).filter(models.Pedido.id == datos["pedido_id"]).first()
            if pedido:
                pedido.estado = "confirmado" if evento == "pago_confirmado" else "cancelado"
                db.commit()
                print(f"[Pedidos] Estado actualizado a '{pedido.estado}' para pedido {datos['pedido_id']}")
        finally:
            db.close()

    ch.basic_ack(delivery_tag=method.delivery_tag)


def _procesar_mensaje_envio(ch, method, properties, body):
    datos = json.loads(body)

    if datos.get("evento") == "envio_actualizado" and datos.get("estado") == "entregado":
        db = SessionLocal()
        try:
            pedido = db.query(models.Pedido).filter(models.Pedido.id == datos["pedido_id"]).first()
            if pedido:
                pedido.estado = "entregado"
                db.commit()
                print(f"[Pedidos] Estado actualizado a 'entregado' para pedido {datos['pedido_id']}")
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

            channel.exchange_declare(exchange="eventos_pagos", exchange_type="fanout", durable=True)
            channel.queue_declare(queue="pedidos_pagos", durable=True)
            channel.queue_bind(exchange="eventos_pagos", queue="pedidos_pagos")
            channel.basic_consume(queue="pedidos_pagos", on_message_callback=_procesar_mensaje_pago)

            channel.exchange_declare(exchange="eventos_envios", exchange_type="fanout", durable=True)
            channel.queue_declare(queue="pedidos_envios", durable=True)
            channel.queue_bind(exchange="eventos_envios", queue="pedidos_envios")
            channel.basic_consume(queue="pedidos_envios", on_message_callback=_procesar_mensaje_envio)

            print("[Pedidos] Escuchando eventos de pagos y envíos...")
            channel.start_consuming()

        except Exception as e:
            print(f"[Pedidos] RabbitMQ no disponible, reintentando en 5s... ({e})")
            time.sleep(5)


def lanzar_consumidor_en_hilo():
    hilo = threading.Thread(target=_iniciar_consumo, daemon=True)
    hilo.start()