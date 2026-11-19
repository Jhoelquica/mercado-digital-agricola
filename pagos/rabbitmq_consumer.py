import os
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from comun import rabbitmq
from comun.database import SessionLocal
import models
from logica_cobros import ESTADOS_CUMPLIDOS

logger = logging.getLogger("pagos.rabbitmq_consumer")

# Cuánto tiempo se guarda el stock de un pedido sin pagar (main.vencer_pagos_sin_pagar lo devuelve al vencer).
PLAZO_PARA_PAGAR_MINUTOS = int(os.getenv("PLAZO_PARA_PAGAR_MINUTOS", "60"))


def _calcular_monto(items: list, costo_envio: float = 0) -> float:
    subtotal = sum(item.get("cantidad", 1) * item.get("precio_unitario", 0) for item in items)
    return subtotal + costo_envio


def crear_pago_si_falta(datos: dict) -> bool:
    """El registro de pago del pedido, con lo que trae pedido_creado (o lo que dice Pedidos al cobrar, que es lo mismo:
    ver main._pago_o_crearlo). En su propia sesión. RabbitMQ entrega "al menos una vez", Pedidos reenvía su bandeja de
    salida y el cobro también puede crearlo: uno repetido no crea un segundo pago; si dos llegan a la vez, el índice
    único de pagos.pedido_id frena al segundo. Devuelve si lo creó."""
    db = SessionLocal()
    try:
        if db.query(models.Pago).filter(models.Pago.pedido_id == datos["pedido_id"]).first():
            logger.info("Pedido %s: ya tiene su registro de pago; no se crea otro", datos["pedido_id"])
            return False
        # costo_envio viaja en el evento desde Pedidos (calculado ahí contra la ubicación del almacén, ver
        # calcular_costo_envio en pedidos/main.py); el 0 por defecto es solo para un evento viejo sin esa clave.
        monto = _calcular_monto(datos.get("items", []), datos.get("costo_envio", 0))
        db.add(models.Pago(
            pedido_id=datos["pedido_id"],
            monto=monto,
            estado="pendiente",
            items=json.dumps(datos.get("items", [])),
            # Pedido en camión: primero se cobra el adelanto, el resto al llegar (ver Pago.monto_adelanto).
            monto_adelanto=datos.get("monto_adelanto") if datos.get("modalidad_envio") == "camion" else None,
            vence_en=datetime.utcnow() + timedelta(minutes=PLAZO_PARA_PAGAR_MINUTOS),
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.info("Pedido %s: otro creó su registro de pago a la vez", datos["pedido_id"])
            return False
        logger.info("Pedido %s: registro de pago creado (S/ %s)", datos["pedido_id"], monto)
        return True
    finally:
        db.close()


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)

    if datos.get("evento") == "pedido_creado":
        crear_pago_si_falta(datos)

    elif datos.get("evento") == "envio_actualizado" and datos.get("estado") in ESTADOS_CUMPLIDOS and datos.get("pedido_id"):
        # El pedido llegó (o quedó en la agencia de la ciudad del comprador): desde ahora el agricultor puede cobrar lo
        # que vendió en él (ver logica_cobros.py). Idempotente: vale la primera vez que se cumplió.
        db = SessionLocal()
        try:
            if not db.query(models.EntregaPedido).filter(models.EntregaPedido.pedido_id == datos["pedido_id"]).first():
                db.add(models.EntregaPedido(pedido_id=datos["pedido_id"], estado=datos["estado"], fecha=datetime.utcnow()))
                db.commit()
            else:
                logger.info("Pedido %s: su entrega ya estaba registrada; el envio_actualizado '%s' se ignora",
                            datos["pedido_id"], datos["estado"])
        except IntegrityError:
            db.rollback()
        finally:
            db.close()

    ch.basic_ack(delivery_tag=method.delivery_tag)


# (exchange, cola, manejador). La conexión, los reintentos y las colas de fallidos están en comun/rabbitmq.py.
SUSCRIPCIONES = (
    ("eventos_pedidos", "pagos_pedidos", _procesar_mensaje),
    # Las entregas: cuándo el agricultor ya puede cobrar lo vendido.
    ("eventos_envios", "pagos_envios", _procesar_mensaje),
)


def lanzar_consumidor_en_hilo():
    rabbitmq.lanzar_consumidor_en_hilo("Pagos", SUSCRIPCIONES)
