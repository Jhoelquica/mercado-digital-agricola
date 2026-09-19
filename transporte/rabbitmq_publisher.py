import os
import json
import pika

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "admin")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "admin123")

def publicar_evento(datos: dict):
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
    )
    channel = connection.channel()
    channel.exchange_declare(exchange="eventos_envios", exchange_type="fanout", durable=True)
    # delivery_mode=2 (persistente): sin esto el mensaje se pierde si RabbitMQ se reinicia antes de
    # que lo consuman, aunque el exchange y las colas sean durables.
    channel.basic_publish(
        exchange="eventos_envios", routing_key="", body=json.dumps(datos, default=str),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()