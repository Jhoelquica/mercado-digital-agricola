"""
Tests del consumer de RabbitMQ (rabbitmq_consumer.py) — no del transporte en sí (eso requeriría
un RabbitMQ real), sino de _procesar_mensaje: dado el body de un evento ya recibido, ¿arma y
guarda la Notificacion correcta?

Cubre el evento nuevo producto_disponible (nace de una Reserva en Productos, no de un Pedido):
crea la notificación con pedido_id=None y producto_id/usuario_id/mensaje correctos.

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de postgresql +
create_all() al importar) — no se puede sustituir por SQLite, mismo criterio que el resto de los
servicios de este proyecto.
"""
import json
import uuid
from unittest.mock import MagicMock

from database import Base, engine, SessionLocal
import models
from rabbitmq_consumer import _procesar_mensaje

Base.metadata.create_all(bind=engine)


def _procesar(evento: dict):
    ch = MagicMock()
    method = MagicMock()
    _procesar_mensaje(ch, method, None, json.dumps(evento).encode())
    ch.basic_ack.assert_called_once_with(delivery_tag=method.delivery_tag)


def test_producto_disponible_crea_notificacion_con_pedido_id_nulo():
    usuario_id = str(uuid.uuid4())
    producto_id = str(uuid.uuid4())

    _procesar({
        "evento": "producto_disponible",
        "usuario_id": usuario_id,
        "producto_id": producto_id,
        "nombre_producto": "Papa Nativa",
    })

    db = SessionLocal()
    try:
        notificacion = db.query(models.Notificacion).filter(
            models.Notificacion.producto_id == producto_id
        ).first()
    finally:
        db.close()

    assert notificacion is not None
    assert notificacion.pedido_id is None
    assert str(notificacion.usuario_id) == usuario_id
    assert str(notificacion.producto_id) == producto_id
    assert notificacion.tipo == "producto_disponible"
    assert "Papa Nativa" in notificacion.mensaje
