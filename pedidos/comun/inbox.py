# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Consumidor idempotente (patrón inbox).

Cada consumidor anota en eventos_procesados el evento_id de lo que procesó, en la MISMA transacción que su efecto. Si el
mismo mensaje llega otra vez (RabbitMQ lo reentrega, la bandeja de salida lo reenvía o un reintento lo repite), la marca
ya está y el efecto no se repite. Si el efecto falla, el rollback se lleva también la marca y el reintento lo procesa
de nuevo. Dos entregas a la vez se turnan en el índice único: la segunda espera a que la primera confirme o se deshaga.
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID, insert

from comun.database import Base

logger = logging.getLogger("comun.inbox")


class EventoProcesado(Base):
    __tablename__ = "eventos_procesados"

    consumidor = Column(String, primary_key=True)  # la cola (o el servicio) que lo procesó
    evento_id = Column(UUID(as_uuid=True), primary_key=True)  # la clave (consumidor, evento_id) es única
    fecha = Column(DateTime, nullable=False, default=datetime.utcnow)


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
