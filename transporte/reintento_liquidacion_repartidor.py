"""Red de seguridad para envíos "entregado" sin liquidación de repartidor confirmada en Pagos: un
hilo en segundo plano (mismo patrón que reintento_huerfanos.py y
rabbitmq_consumer.lanzar_consumidor_en_hilo — hilo daemon con loop y time.sleep) que cada
INTERVALO_SEGUNDOS busca esos envíos y reintenta notificar_liquidacion_repartidor() para cada uno.

Dominio distinto a reintento_huerfanos.py a propósito — no se extiende ese archivo: ese resuelve
asignación envío↔repartidor (Envio.estado == "pendiente_asignacion"), esto resuelve un efecto
secundario cross-servicio hacia Pagos sobre envíos YA entregados. Mismo criterio por el que en
Pagos crear_liquidaciones/reintento_liquidaciones quedaron separados del resto de ese servicio.

A diferencia de reintento_huerfanos.py (que usa print), acá se usa el módulo logging — mismo
motivo que ya documentamos en pagos/main.py: sin logging.basicConfig() configurado en algún
punto del proceso, un logger.info() no aparece en absoluto en `kubectl logs` (Python cae al
"handler de último recurso", que solo muestra WARNING o superior). basicConfig se llama una sola
vez en main.py, no acá — alcanza con que corra antes de que este hilo emita su primer log, y
ambos viven en el mismo proceso.
"""
import time
import threading
import logging

from database import SessionLocal
from logica_liquidacion_repartidor import notificar_liquidacion_repartidor, envios_entregados_sin_liquidar

INTERVALO_SEGUNDOS = 120

logger = logging.getLogger("transporte.reintento_liquidacion_repartidor")


def reintentar_liquidaciones_repartidor_pendientes(db):
    """Un ciclo completo: busca los envíos "entregado" sin liquidación confirmada y reintenta
    notificar_liquidacion_repartidor() para cada uno — si uno sigue fallando, no interrumpe el
    procesamiento de los demás (cada intento tiene su propio try/except y su propio
    commit/rollback)."""
    pendientes = envios_entregados_sin_liquidar(db)
    if not pendientes:
        logger.info("Reintento de liquidación de repartidor: 0 envíos pendientes.")
        return

    resueltos = 0
    fallidos = []
    for envio in pendientes:
        try:
            notificar_liquidacion_repartidor(db, envio)
            db.commit()
            resueltos += 1
        except Exception:
            db.rollback()
            fallidos.append(str(envio.pedido_id))
            logger.error(
                "Reintento de liquidación de repartidor sigue fallando para el pedido %s", envio.pedido_id, exc_info=True,
            )

    logger.info(
        "Reintento de liquidación de repartidor: %d pendiente(s), %d resuelto(s), %d siguen fallando%s",
        len(pendientes), resueltos, len(fallidos),
        f" ({', '.join(fallidos)})" if fallidos else "",
    )


def _bucle_reintento():
    while True:
        db = SessionLocal()
        try:
            reintentar_liquidaciones_repartidor_pendientes(db)
        except Exception as e:
            logger.error(
                "Ciclo de reintento de liquidación de repartidor falló por completo, reintenta en %ds: %s",
                INTERVALO_SEGUNDOS, e, exc_info=True,
            )
        finally:
            db.close()
        time.sleep(INTERVALO_SEGUNDOS)


def lanzar_reintento_liquidacion_repartidor_en_hilo():
    hilo = threading.Thread(target=_bucle_reintento, daemon=True)
    hilo.start()
