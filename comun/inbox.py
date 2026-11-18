"""Consumidor idempotente (patrón inbox).

Cada consumidor anota en eventos_procesados el evento_id de lo que procesó, en la MISMA transacción que su efecto. Si el
mismo mensaje llega otra vez (RabbitMQ lo reentrega, la bandeja de salida lo reenvía o un reintento lo repite), la marca
ya está y el efecto no se repite. Si el efecto falla, el rollback se lleva también la marca y el reintento lo procesa
de nuevo. Dos entregas a la vez se turnan en el índice único: la segunda espera a que la primera confirme o se deshaga.
"""
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID, insert

from comun.database import Base, SessionLocal

logger = logging.getLogger("comun.inbox")


def _dias_de_retencion() -> int:
    valor = os.getenv("DIAS_RETENCION_EVENTOS") or "30"
    if not valor.isdigit() or int(valor) < 1:
        sys.exit(f"La variable de entorno DIAS_RETENCION_EVENTOS debe ser un número de días (1 o más), no {valor!r}.")
    return int(valor)


# Las marcas se guardan este tiempo: un repetido llega en minutos (RabbitMQ) o en días (la bandeja de salida de Pedidos
# borra los suyos a la semana), así que con 30 días sobra. Las más viejas las borra un hilo, cada hora.
DIAS_RETENCION_EVENTOS = _dias_de_retencion()
INTERVALO_DE_LIMPIEZA_SEGUNDOS = 3600
INDICE_DE_FECHA = "ix_eventos_procesados_fecha"


class EventoProcesado(Base):
    __tablename__ = "eventos_procesados"

    consumidor = Column(String, primary_key=True)  # la cola (o el servicio) que lo procesó
    evento_id = Column(UUID(as_uuid=True), primary_key=True)  # la clave (consumidor, evento_id) es única
    fecha = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)  # índice: para la limpieza


def evento_id_de(datos: dict):
    """El evento_id del mensaje como UUID, o None si no trae (los publicados antes de que existiera). Uno que no es un
    UUID lanza ValueError: el mensaje está mal y, tras sus intentos, va a fallidos."""
    valor = datos.get("evento_id")
    return uuid.UUID(str(valor)) if valor else None


def ya_procesado(sesion, consumidor: str, evento_id) -> bool:
    """Anota (consumidor, evento_id) en la transacción de `sesion` y dice si ya estaba.

    True: otro intento ya lo procesó y confirmó, no hay que repetir el efecto. False: es la primera vez; la marca queda
    pendiente del mismo commit que el efecto. Sin evento_id no hay cómo saberlo: se procesa (False) y queda un aviso."""
    if evento_id is None:
        logger.warning("%s: mensaje sin evento_id; se procesa sin poder descartar un repetido", consumidor)
        return False
    anotado = sesion.execute(
        insert(EventoProcesado)
        .values(consumidor=consumidor, evento_id=evento_id, fecha=datetime.utcnow())
        .on_conflict_do_nothing(index_elements=["consumidor", "evento_id"])
        .returning(EventoProcesado.evento_id)
    ).first()
    return anotado is None


def asegurar_indice_de_fecha(engine) -> bool:
    """El índice por fecha en una tabla de antes de él (create_all no agrega índices a una tabla que ya existe).
    Idempotente; nunca lanza: si falla, la limpieza va más lenta y queda en el log."""
    try:
        with engine.begin() as conexion:
            conexion.execute(text(f"CREATE INDEX IF NOT EXISTS {INDICE_DE_FECHA} ON eventos_procesados (fecha)"))
    except Exception:
        logger.exception("No se pudo crear el índice %s de eventos_procesados", INDICE_DE_FECHA)
        return False
    return True


def borrar_procesados_viejos(sesion, dias: int = None, ahora: datetime = None) -> int:
    """Borra las marcas con más de `dias` días (DIAS_RETENCION_EVENTOS si no se dice). Devuelve cuántas borró."""
    limite = (ahora or datetime.utcnow()) - timedelta(days=dias or DIAS_RETENCION_EVENTOS)
    return (sesion.query(EventoProcesado).filter(EventoProcesado.fecha < limite)
            .delete(synchronize_session=False))


def limpiar_una_vez(sesiones=None):
    """Una pasada de la limpieza, en su propia sesión. Nunca lanza, tampoco si falla el rollback o el cierre: el hilo
    que la llama no debe morir. Devuelve cuántas marcas borró, o None si no pudo."""
    db = None
    try:
        db = (sesiones or SessionLocal)()
        borradas = borrar_procesados_viejos(db)
        db.commit()
        if borradas:
            logger.info("eventos_procesados: %d marca(s) de más de %d días borrada(s)", borradas, DIAS_RETENCION_EVENTOS)
        return borradas
    except Exception:
        logger.exception("No se pudo limpiar eventos_procesados; se reintenta en %d s", INTERVALO_DE_LIMPIEZA_SEGUNDOS)
        try:
            if db is not None:
                db.rollback()
        except Exception:
            logger.exception("Tampoco se pudo deshacer la limpieza de eventos_procesados")
        return None
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            logger.exception("No se pudo cerrar la sesión de la limpieza de eventos_procesados")


def _limpiar_siempre():
    while True:
        try:
            limpiar_una_vez()
        except Exception:  # limpiar_una_vez no lanza; esto es por si algo cambia
            logger.exception("La limpieza de eventos_procesados falló")
        time.sleep(INTERVALO_DE_LIMPIEZA_SEGUNDOS)


def lanzar_limpieza_en_hilo() -> None:
    threading.Thread(target=_limpiar_siempre, daemon=True, name="limpieza-eventos-procesados").start()
