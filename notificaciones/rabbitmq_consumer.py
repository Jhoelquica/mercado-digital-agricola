import os
import json
import time
import threading
import pika

from database import SessionLocal
import models
from mensajes import mensaje_envio, mensaje_pago, mensaje_pedido_creado

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "admin")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "admin123")


def _guardar_notificacion(tipo: str, mensaje: str, pedido_id: str = None, producto_id: str = None, usuario_id: str = None,
                          estado: str = None):
    db = SessionLocal()
    try:
        # La inferencia por pedido_id (cuando el evento no trae usuario_id, ej.
        # envio_actualizado) solo tiene sentido si el evento es de un pedido — un evento de
        # producto siempre trae su propio usuario_id (la reserva ya lo tiene), así que acá no
        # hace falta ni tiene con qué inferirlo.
        if usuario_id is None and pedido_id is not None:
            anterior = db.query(models.Notificacion).filter(
                models.Notificacion.pedido_id == pedido_id
            ).first()
            usuario_id = anterior.usuario_id if anterior else None

        nueva = models.Notificacion(
            pedido_id=pedido_id, producto_id=producto_id, tipo=tipo, mensaje=mensaje, usuario_id=usuario_id, estado=estado,
        )
        db.add(nueva)
        db.commit()
        print(f"[Notificaciones] {mensaje}")
    finally:
        db.close()


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)
    evento = datos.get("evento")

    if evento == "pedido_creado":
        _guardar_notificacion(
            pedido_id=datos["pedido_id"],
            tipo="pedido_creado",
            mensaje=mensaje_pedido_creado(datos),
            usuario_id=datos.get("usuario_id"),
        )
    elif evento == "envio_actualizado":
        _guardar_notificacion(
            pedido_id=datos["pedido_id"],
            tipo="envio_actualizado",
            mensaje=mensaje_envio(datos),
            estado=datos.get("estado"),
        )
    elif evento == "producto_disponible":
        _guardar_notificacion(
            producto_id=datos["producto_id"],
            usuario_id=datos.get("usuario_id"),
            tipo="producto_disponible",
            mensaje=f"¡Buenas noticias! El producto \"{datos.get('nombre_producto')}\" que reservaste ya está disponible para comprar.",
        )
    elif evento in ("pago_confirmado", "saldo_pagado", "pago_rechazado", "saldo_rechazado") and datos.get("pedido_id"):
        # Pagos no manda el usuario: se toma del aviso de pedido_creado de ese pedido (ver _guardar_notificacion).
        _guardar_notificacion(pedido_id=datos["pedido_id"], tipo=evento, mensaje=mensaje_pago(datos))

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
            channel.queue_declare(queue="notificaciones_pedidos", durable=True)
            channel.queue_bind(exchange="eventos_pedidos", queue="notificaciones_pedidos")
            channel.basic_consume(queue="notificaciones_pedidos", on_message_callback=_procesar_mensaje)

            channel.exchange_declare(exchange="eventos_envios", exchange_type="fanout", durable=True)
            channel.queue_declare(queue="notificaciones_envios", durable=True)
            channel.queue_bind(exchange="eventos_envios", queue="notificaciones_envios")
            channel.basic_consume(queue="notificaciones_envios", on_message_callback=_procesar_mensaje)

            channel.exchange_declare(exchange="eventos_productos", exchange_type="fanout", durable=True)
            channel.queue_declare(queue="notificaciones_productos", durable=True)
            channel.queue_bind(exchange="eventos_productos", queue="notificaciones_productos")
            channel.basic_consume(queue="notificaciones_productos", on_message_callback=_procesar_mensaje)

            channel.exchange_declare(exchange="eventos_pagos", exchange_type="fanout", durable=True)
            channel.queue_declare(queue="notificaciones_pagos", durable=True)
            channel.queue_bind(exchange="eventos_pagos", queue="notificaciones_pagos")
            channel.basic_consume(queue="notificaciones_pagos", on_message_callback=_procesar_mensaje)

            print("[Notificaciones] Escuchando eventos de pedidos, envíos, productos y pagos...")
            channel.start_consuming()

        except Exception as e:
            print(f"[Notificaciones] RabbitMQ no disponible, reintentando en 5s... ({e})")
            time.sleep(5)


def lanzar_consumidor_en_hilo():
    hilo = threading.Thread(target=_iniciar_consumo, daemon=True)
    hilo.start()