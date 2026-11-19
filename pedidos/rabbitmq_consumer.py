import json
import logging

from sqlalchemy import and_, or_

from comun import inbox, rabbitmq
from comun.database import SessionLocal
import models

logger = logging.getLogger("pedidos.rabbitmq_consumer")

COLA_PAGOS = "pedidos_pagos"
COLA_ENVIOS = "pedidos_envios"


def _transicion(evento: str):
    """A qué estado lleva el evento y desde cuáles vale (la condición del UPDATE). Solo hacia adelante.

    El orden es pendiente < confirmado < entregado. "cancelado" va aparte:
    - con motivo_cancelacion (lo canceló el admin y devolvió el dinero) es final: nada lo cambia;
    - sin motivo (el pago se rechazó o venció) lo reabre un pago confirmado (el comprador volvió a pagar) y también la
      entrega (un envío solo nace con un pago confirmado).
    Lo que no hace avanzar al pedido (un repetido, uno atrasado o uno sobre un final) se ignora con un log; así da igual
    el orden en que lleguen los eventos de pagos y los de envíos, que vienen por colas distintas."""
    Pedido = models.Pedido
    cancelado_sin_motivo = and_(Pedido.estado == "cancelado", Pedido.motivo_cancelacion.is_(None))
    if evento == "pago_confirmado":
        return "confirmado", or_(Pedido.estado == "pendiente", cancelado_sin_motivo)
    if evento == "pago_rechazado":
        return "cancelado", Pedido.estado == "pendiente"  # uno tardío no deshace un pago que ya se confirmó
    if evento == "entregado":
        return "entregado", or_(Pedido.estado.in_(("pendiente", "confirmado")), cancelado_sin_motivo)
    raise ValueError(f"Evento sin transición: {evento}")


def _por_que_no(db, pedido_id, evento: str, nuevo: str) -> None:
    """El log de un evento que no movió al pedido: repetido (info), atrasado (warning) o sobre un pedido que el admin
    canceló con devolución (warning; error si es la entrega: algo se entregó igual, y lo marca la conciliación)."""
    fila = db.query(models.Pedido.estado, models.Pedido.motivo_cancelacion).filter(models.Pedido.id == pedido_id).first()
    if fila is None:
        logger.warning("Pedido %s: llegó %s y el pedido no existe; se ignora", pedido_id, evento)
    elif fila.estado == nuevo:
        logger.info("Pedido %s: llegó %s y ya estaba '%s'; se ignora", pedido_id, evento, nuevo)
    elif fila.estado == "cancelado" and fila.motivo_cancelacion and evento == "entregado":
        logger.error("Pedido %s: llegó la entrega de un pedido que el admin canceló con devolución (%s); se ignora. "
                     "REVISAR: la conciliación lo marca", pedido_id, fila.motivo_cancelacion)
    else:
        logger.warning("Pedido %s: llegó %s con el pedido en '%s'; no lo hace avanzar y se ignora", pedido_id, evento,
                       fila.estado)


def _aplicar(cola: str, datos: dict, evento: str) -> None:
    """La transición del evento sobre su pedido, con la marca del inbox en la misma transacción (comun/inbox.py)."""
    evento_id = inbox.evento_id_de(datos)
    pedido_id = datos["pedido_id"]
    db = SessionLocal()
    try:
        if inbox.ya_procesado(db, cola, evento_id):
            logger.info("Pedido %s: el %s %s ya se procesó; no se repite", pedido_id, evento, evento_id)
        else:
            nuevo, desde = _transicion(evento)
            cambiados = (db.query(models.Pedido)
                         .filter(models.Pedido.id == pedido_id, desde)
                         .update({models.Pedido.estado: nuevo}, synchronize_session=False))
            if cambiados:
                logger.info("Pedido %s: estado actualizado a '%s' (%s)", pedido_id, nuevo, evento)
            else:
                _por_que_no(db, pedido_id, evento, nuevo)
        db.commit()
    finally:
        db.close()


def _procesar_mensaje_pago(ch, method, properties, body):
    datos = json.loads(body)
    if datos.get("evento") in ("pago_confirmado", "pago_rechazado"):
        _aplicar(COLA_PAGOS, datos, datos["evento"])
    ch.basic_ack(delivery_tag=method.delivery_tag)


def _procesar_mensaje_envio(ch, method, properties, body):
    datos = json.loads(body)
    # Pedidos no sigue los estados intermedios del envío (no tiene "en camino"): solo la entrega lo cambia.
    if datos.get("evento") == "envio_actualizado" and datos.get("estado") == "entregado":
        _aplicar(COLA_ENVIOS, datos, "entregado")
    ch.basic_ack(delivery_tag=method.delivery_tag)


# (exchange, cola, manejador). La conexión, los reintentos y las colas de fallidos están en comun/rabbitmq.py.
SUSCRIPCIONES = (
    ("eventos_pagos", COLA_PAGOS, _procesar_mensaje_pago),
    ("eventos_envios", COLA_ENVIOS, _procesar_mensaje_envio),
)


def lanzar_consumidor_en_hilo():
    rabbitmq.lanzar_consumidor_en_hilo("Pedidos", SUSCRIPCIONES)
