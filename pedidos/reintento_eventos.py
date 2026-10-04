"""Reenvío de la bandeja de salida de Pedidos (models.EventoSaliente, patrón outbox).

crear_pedido guarda el evento pedido_creado en la misma transacción que el pedido y lo publica enseguida; si RabbitMQ no
respondió en ese momento, el evento queda sin fecha_envio y este hilo lo reenvía cada INTERVALO_SEGUNDOS, en el orden en
que se crearon. Solo toma los que tienen más de ESPERA_ANTES_DE_REENVIAR (el propio pedido publica primero) y los bloquea
con SKIP LOCKED, así dos réplicas no mandan el mismo. RabbitMQ entrega "al menos una vez": los que lo consumen (Pagos)
ya ignoran un pedido_creado repetido.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta

from database import SessionLocal
import models
from rabbitmq_publisher import publicar_evento

INTERVALO_SEGUNDOS = 15
ESPERA_ANTES_DE_REENVIAR = timedelta(seconds=30)
LOTE = 50
DIAS_QUE_SE_GUARDAN_LOS_ENVIADOS = 7

logger = logging.getLogger("pedidos.reintento_eventos")


def reenviar_eventos_pendientes(db, ahora=None) -> int:
    """Un ciclo: publica los eventos pendientes (hasta LOTE) y borra los enviados hace más de una semana. Devuelve cuántos
    publicó. Si RabbitMQ sigue sin responder se corta el ciclo (los demás esperan al siguiente)."""
    ahora = ahora or datetime.utcnow()
    pendientes = (
        db.query(models.EventoSaliente)
        .filter(models.EventoSaliente.fecha_envio.is_(None),
                models.EventoSaliente.fecha_creacion < ahora - ESPERA_ANTES_DE_REENVIAR)
        .order_by(models.EventoSaliente.fecha_creacion)
        .limit(LOTE)
        .with_for_update(skip_locked=True)
        .all()
    )
    enviados = 0
    for evento in pendientes:
        try:
            publicar_evento(json.loads(evento.cuerpo))
        except Exception:
            logger.warning("RabbitMQ sigue sin responder: %d evento(s) esperan el próximo ciclo", len(pendientes) - enviados)
            break
        evento.fecha_envio = ahora
        evento.intentos = (evento.intentos or 0) + 1
        enviados += 1
    db.commit()
    if enviados:
        logger.info("Bandeja de salida: %d evento(s) reenviado(s)", enviados)
    db.query(models.EventoSaliente).filter(
        models.EventoSaliente.fecha_envio < ahora - timedelta(days=DIAS_QUE_SE_GUARDAN_LOS_ENVIADOS)
    ).delete(synchronize_session=False)
    db.commit()
    return enviados


def _bucle():
    while True:
        db = SessionLocal()
        try:
            reenviar_eventos_pendientes(db)
        except Exception:
            db.rollback()
            logger.error("El ciclo de la bandeja de salida falló, reintenta en %ds", INTERVALO_SEGUNDOS, exc_info=True)
        finally:
            db.close()
        time.sleep(INTERVALO_SEGUNDOS)


def lanzar_reintento_eventos_en_hilo():
    threading.Thread(target=_bucle, daemon=True).start()
