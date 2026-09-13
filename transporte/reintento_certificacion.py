"""Red de seguridad para envíos "entregado" sin notificación a Certificación confirmada: un hilo
en segundo plano (mismo patrón que reintento_huerfanos.py y reintento_liquidacion_repartidor.py —
hilo daemon con loop y time.sleep) que cada INTERVALO_SEGUNDOS busca esos envíos y reintenta
notificar_entrega_a_certificacion() para cada uno.

Dominio propio a propósito — no extiende reintento_huerfanos.py (asignación envío↔repartidor) ni
reintento_liquidacion_repartidor.py (liquidación hacia Pagos): mismo criterio de separación por
dominio que ya establecimos con ese archivo.

Usa el módulo logging, no print — mismo motivo ya documentado en pagos/main.py y
transporte/main.py: sin logging.basicConfig() configurado en algún punto del proceso, un
logger.info() no aparece en absoluto en `kubectl logs`. basicConfig se llama una sola vez en
main.py, no acá — alcanza con que corra antes de que este hilo emita su primer log, y ambos viven
en el mismo proceso.
"""
import time
import threading
import logging

from database import SessionLocal
from logica_certificacion import notificar_entrega_a_certificacion, envios_entregados_sin_certificar

INTERVALO_SEGUNDOS = 120

logger = logging.getLogger("transporte.reintento_certificacion")


def reintentar_certificaciones_pendientes(db):
    """Un ciclo completo: busca los envíos "entregado" sin certificación confirmada y reintenta
    notificar_entrega_a_certificacion() para cada uno — si uno sigue fallando, no interrumpe el
    procesamiento de los demás (cada intento tiene su propio try/except y su propio
    commit/rollback)."""
    pendientes = envios_entregados_sin_certificar(db)
    if not pendientes:
        logger.info("Reintento de certificación: 0 envíos pendientes.")
        return

    resueltos = 0
    fallidos = []
    for envio in pendientes:
        try:
            notificar_entrega_a_certificacion(db, envio)
            db.commit()
            resueltos += 1
        except Exception:
            db.rollback()
            fallidos.append(str(envio.pedido_id))
            logger.error(
                "Reintento de certificación sigue fallando para el pedido %s", envio.pedido_id, exc_info=True,
            )

    logger.info(
        "Reintento de certificación: %d pendiente(s), %d resuelto(s), %d siguen fallando%s",
        len(pendientes), resueltos, len(fallidos),
        f" ({', '.join(fallidos)})" if fallidos else "",
    )


def _bucle_reintento():
    while True:
        db = SessionLocal()
        try:
            reintentar_certificaciones_pendientes(db)
        except Exception as e:
            logger.error(
                "Ciclo de reintento de certificación falló por completo, reintenta en %ds: %s",
                INTERVALO_SEGUNDOS, e, exc_info=True,
            )
        finally:
            db.close()
        time.sleep(INTERVALO_SEGUNDOS)


def lanzar_reintento_certificacion_en_hilo():
    hilo = threading.Thread(target=_bucle_reintento, daemon=True)
    hilo.start()
