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


# ============ El secreto de limpieza falla cerrado ============

def test_el_endpoint_de_limpieza_rechaza_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes el guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto. Se llama a la función del
    endpoint directamente: la imagen de este servicio no trae httpx, que necesita el TestClient."""
    import pytest
    from fastapi import HTTPException

    import main

    db = SessionLocal()
    try:
        for secreto in (None, ""):
            monkeypatch.setattr(main, "QA_LIMPIEZA_SECRETO", secreto)
            for cabecera in (None, ""):
                with pytest.raises(HTTPException) as error:
                    main.eliminar_notificacion(str(uuid.uuid4()), db, x_servicio_secreto=cabecera)
                assert error.value.status_code == 403, (secreto, cabecera)

        monkeypatch.setattr(main, "QA_LIMPIEZA_SECRETO", "secreto-de-prueba")
        with pytest.raises(HTTPException) as error:
            main.eliminar_notificacion(str(uuid.uuid4()), db, x_servicio_secreto="otro")
        assert error.value.status_code == 403
        with pytest.raises(HTTPException) as error:  # el correcto llega al endpoint: no existe -> 404
            main.eliminar_notificacion(str(uuid.uuid4()), db, x_servicio_secreto="secreto-de-prueba")
        assert error.value.status_code == 404
    finally:
        db.close()
