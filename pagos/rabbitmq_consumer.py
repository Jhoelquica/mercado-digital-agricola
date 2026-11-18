import os
import json
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from comun import rabbitmq
from comun.database import SessionLocal
import models
from logica_cobros import ESTADOS_CUMPLIDOS

# Cuánto tiempo se guarda el stock de un pedido sin pagar (main.vencer_pagos_sin_pagar lo devuelve al vencer).
PLAZO_PARA_PAGAR_MINUTOS = int(os.getenv("PLAZO_PARA_PAGAR_MINUTOS", "60"))


def _calcular_monto(items: list, costo_envio: float = 0) -> float:
    subtotal = sum(item.get("cantidad", 1) * item.get("precio_unitario", 0) for item in items)
    return subtotal + costo_envio


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)

    if datos.get("evento") == "pedido_creado":
        db = SessionLocal()
        try:
            # RabbitMQ entrega "al menos una vez" y Pedidos reenvía su bandeja de salida: un pedido_creado repetido no
            # crea un segundo pago.
            if db.query(models.Pago).filter(models.Pago.pedido_id == datos["pedido_id"]).first():
                print(f"[Pagos] El pedido {datos['pedido_id']} ya tiene su registro de pago, no se crea otro")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return
            # costo_envio viaja en el evento desde Pedidos (calculado ahí contra la ubicación del
            # almacén, ver calcular_costo_envio en pedidos/main.py) — el default 0 es solo para
            # no romper si algún día llega un evento viejo sin esa clave.
            monto = _calcular_monto(datos.get("items", []), datos.get("costo_envio", 0))
            nuevo_pago = models.Pago(
                pedido_id=datos["pedido_id"],
                monto=monto,
                estado="pendiente",
                items=json.dumps(datos.get("items", [])),
                # Pedido en camión: primero se cobra el adelanto, el resto al llegar (ver Pago.monto_adelanto).
                monto_adelanto=datos.get("monto_adelanto") if datos.get("modalidad_envio") == "camion" else None,
                vence_en=datetime.utcnow() + timedelta(minutes=PLAZO_PARA_PAGAR_MINUTOS),
            )
            db.add(nuevo_pago)
            db.commit()
            print(f"[Pagos] Registro de pago creado para pedido {datos['pedido_id']} (monto estimado: S/ {monto})")
        finally:
            db.close()

    elif datos.get("evento") == "envio_actualizado" and datos.get("estado") in ESTADOS_CUMPLIDOS and datos.get("pedido_id"):
        # El pedido llegó (o quedó en la agencia de la ciudad del comprador): desde ahora el agricultor puede cobrar lo
        # que vendió en él (ver logica_cobros.py). Idempotente: vale la primera vez que se cumplió.
        db = SessionLocal()
        try:
            if not db.query(models.EntregaPedido).filter(models.EntregaPedido.pedido_id == datos["pedido_id"]).first():
                db.add(models.EntregaPedido(pedido_id=datos["pedido_id"], estado=datos["estado"], fecha=datetime.utcnow()))
                db.commit()
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
