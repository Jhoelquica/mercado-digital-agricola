import json

from comun import rabbitmq
from comun.database import SessionLocal
import models


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


# (exchange, cola, manejador). La conexión, los reintentos y las colas de fallidos están en comun/rabbitmq.py.
SUSCRIPCIONES = (
    ("eventos_pagos", "pedidos_pagos", _procesar_mensaje_pago),
    ("eventos_envios", "pedidos_envios", _procesar_mensaje_envio),
)


def lanzar_consumidor_en_hilo():
    rabbitmq.lanzar_consumidor_en_hilo("Pedidos", SUSCRIPCIONES)
