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


def _calcular_monto(items: list) -> float:
    return sum(item.get("cantidad", 1) * item.get("precio_unitario", 0) for item in items)


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)

    if datos.get("evento") == "pedido_creado":
        db = SessionLocal()
        try:
            monto = _calcular_monto(datos.get("items", []))
            nuevo_pago = models.Pago(
                pedido_id=datos["pedido_id"],
                monto=monto,
                estado="pendiente",
            )
            db.add(nuevo_pago)
            db.commit()
            print(f"[Pagos] Registro de pago creado para pedido {datos['pedido_id']} (monto estimado: S/ {monto})")
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
            channel.queue_declare(queue="pagos_pedidos", durable=True)
            channel.queue_bind(exchange="eventos_pedidos", queue="pagos_pedidos")

            channel.basic_consume(queue="pagos_pedidos", on_message_callback=_procesar_mensaje)
            print("[Pagos] Escuchando eventos de pedidos...")
            channel.start_consuming()

        except Exception as e:
            print(f"[Pagos] RabbitMQ no disponible, reintentando en 5s... ({e})")
            time.sleep(5)


def lanzar_consumidor_en_hilo():
    hilo = threading.Thread(target=_iniciar_consumo, daemon=True)
    hilo.start()