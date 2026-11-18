import json
import logging

from sqlalchemy import and_, or_

from comun import inbox, rabbitmq
from comun.database import SessionLocal
import models

logger = logging.getLogger("pedidos.rabbitmq_consumer")

COLA_PAGOS = "pedidos_pagos"


def _transicion(evento: str):
    """A qué estado lleva un evento de pago y desde cuáles vale (la condición del UPDATE).

    Un pago rechazado (la tarjeta, o el plazo que venció) cancela el pedido, pero el comprador puede volver a pagar sobre
    el mismo pago: por eso pago_confirmado vale desde "pendiente" y también desde un "cancelado" por pago (sin
    motivo_cancelacion). No vale sobre un pedido entregado ni sobre uno que canceló el admin devolviendo el dinero (con
    motivo). pago_rechazado solo cancela un pedido "pendiente": uno tardío no deshace un pago que ya se confirmó."""
    Pedido = models.Pedido
    if evento == "pago_confirmado":
        return "confirmado", or_(Pedido.estado == "pendiente",
                                 and_(Pedido.estado == "cancelado", Pedido.motivo_cancelacion.is_(None)))
    return "cancelado", Pedido.estado == "pendiente"


def _procesar_mensaje_pago(ch, method, properties, body):
    datos = json.loads(body)
    evento = datos.get("evento")

    if evento in ("pago_confirmado", "pago_rechazado"):
        evento_id = inbox.evento_id_de(datos)
        db = SessionLocal()
        try:
            # La marca del evento y el cambio de estado van en la misma transacción (comun/inbox.py).
            if inbox.ya_procesado(db, COLA_PAGOS, evento_id):
                logger.info("Pedido %s: el %s %s ya se procesó; no se repite", datos.get("pedido_id"), evento, evento_id)
            else:
                nuevo, desde = _transicion(evento)
                cambiados = (db.query(models.Pedido)
                             .filter(models.Pedido.id == datos["pedido_id"], desde)
                             .update({models.Pedido.estado: nuevo}, synchronize_session=False))
                if cambiados:
                    logger.info("Pedido %s: estado actualizado a '%s'", datos["pedido_id"], nuevo)
                else:
                    actual = db.query(models.Pedido.estado).filter(models.Pedido.id == datos["pedido_id"]).scalar()
                    if actual is not None and actual != nuevo:
                        logger.warning("Pedido %s: llegó %s con el pedido en '%s'; se ignora", datos["pedido_id"],
                                       evento, actual)
            db.commit()
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
    ("eventos_pagos", COLA_PAGOS, _procesar_mensaje_pago),
    ("eventos_envios", "pedidos_envios", _procesar_mensaje_envio),
)


def lanzar_consumidor_en_hilo():
    rabbitmq.lanzar_consumidor_en_hilo("Pedidos", SUSCRIPCIONES)
