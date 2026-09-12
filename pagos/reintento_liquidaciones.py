"""Red de seguridad para pagos "aprobado" sin liquidaciones: un hilo en segundo plano (mismo
patrón que transporte/reintento_huerfanos.py y rabbitmq_consumer.lanzar_consumidor_en_hilo — hilo
daemon con loop y time.sleep) que cada INTERVALO_SEGUNDOS busca esos pagos y reintenta
crear_liquidaciones() para cada uno.

Es el respaldo de crear_liquidaciones() fallando en el momento de procesar_pago (ver su docstring
en logica_liquidaciones.py): si Productos no respondió en ese momento, el pago quedó "aprobado"
pero sin liquidaciones — este job los va reintentando hasta que Productos vuelva a responder. Sin
límite de reintentos ni backoff: es un caso borde poco probable (Productos normalmente está
arriba) y esa infraestructura sería sobre-ingeniería acá.

Reutiliza EXACTAMENTE crear_liquidaciones/pagos_sin_liquidaciones de logica_liquidaciones.py — no
hay lógica de cálculo ni de query duplicada acá, solo el bucle que decide CUÁNDO reintentar.
"""
import time
import threading
import logging

from database import SessionLocal
from logica_liquidaciones import crear_liquidaciones, pagos_sin_liquidaciones

INTERVALO_SEGUNDOS = 120

logger = logging.getLogger("pagos.reintento_liquidaciones")


def reintentar_liquidaciones_pendientes(db):
    """Un ciclo completo: busca los pagos "aprobado" sin Liquidacion y reintenta
    crear_liquidaciones() para cada uno — si uno sigue fallando, no interrumpe el procesamiento
    de los demás (cada intento tiene su propio try/except y su propio commit/rollback)."""
    pendientes = pagos_sin_liquidaciones(db)
    if not pendientes:
        logger.info("Reintento de liquidaciones: 0 pagos pendientes de liquidar.")
        return

    resueltos = 0
    fallidos = []
    for pago in pendientes:
        try:
            crear_liquidaciones(db, pago)
            db.commit()
            resueltos += 1
        except Exception:
            db.rollback()
            fallidos.append(str(pago.pedido_id))
            logger.error(
                "Reintento de liquidaciones sigue fallando para el pedido %s", pago.pedido_id, exc_info=True,
            )

    logger.info(
        "Reintento de liquidaciones: %d pendiente(s), %d resuelto(s), %d siguen fallando%s",
        len(pendientes), resueltos, len(fallidos),
        f" ({', '.join(fallidos)})" if fallidos else "",
    )


def _bucle_reintento():
    while True:
        db = SessionLocal()
        try:
            reintentar_liquidaciones_pendientes(db)
        except Exception as e:
            logger.error(
                "Ciclo de reintento de liquidaciones falló por completo, reintenta en %ds: %s",
                INTERVALO_SEGUNDOS, e, exc_info=True,
            )
        finally:
            db.close()
        time.sleep(INTERVALO_SEGUNDOS)


def lanzar_reintento_liquidaciones_en_hilo():
    hilo = threading.Thread(target=_bucle_reintento, daemon=True)
    hilo.start()
