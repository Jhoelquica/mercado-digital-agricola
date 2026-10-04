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
from migraciones import agregar_columnas_faltantes

Base.metadata.create_all(bind=engine)
agregar_columnas_faltantes(engine, Base)  # la base de pruebas puede venir de antes de la columna `estado`


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


# ============ GET /notificaciones/{pedido_id}: solo los avisos del propio usuario ============

def test_el_historial_de_un_pedido_solo_devuelve_los_avisos_del_usuario_que_lo_pide():
    """Antes cualquier usuario con sesión leía las notificaciones de cualquier pedido con solo conocer su id.
    Se llama a la función del endpoint directamente: la imagen de este servicio no trae httpx (TestClient)."""
    import main

    dueno = str(uuid.uuid4())
    pedido_id = str(uuid.uuid4())
    _procesar({"evento": "pedido_creado", "pedido_id": pedido_id, "usuario_id": dueno, "comprador_nombre": "Rosa", "costo_envio": 5})

    db = SessionLocal()
    try:
        propios = main.historial_por_pedido(uuid.UUID(pedido_id), db, {"sub": dueno, "rol": "comprador"})
        ajenos = main.historial_por_pedido(uuid.UUID(pedido_id), db, {"sub": str(uuid.uuid4()), "rol": "comprador"})
        admin = main.historial_por_pedido(uuid.UUID(pedido_id), db, {"sub": str(uuid.uuid4()), "rol": "admin"})
    finally:
        db.close()

    assert len(propios) == 1 and str(propios[0].usuario_id) == dueno
    assert ajenos == []
    assert len(admin) == 1


# ============ Avisos claros al comprador ============

def _avisos_de(pedido_id):
    db = SessionLocal()
    try:
        return db.query(models.Notificacion).filter(models.Notificacion.pedido_id == pedido_id).order_by(models.Notificacion.fecha_envio).all()
    finally:
        db.close()


def test_el_camion_que_llega_le_pide_al_comprador_su_saldo_y_guarda_el_estado():
    dueno, pedido_id = str(uuid.uuid4()), str(uuid.uuid4())
    _procesar({"evento": "pedido_creado", "pedido_id": pedido_id, "usuario_id": dueno, "comprador_nombre": "Rosa",
               "modalidad_envio": "camion"})
    _procesar({"evento": "pago_confirmado", "pedido_id": pedido_id, "adelanto": True, "monto": "700.00", "saldo": "300.00"})
    _procesar({"evento": "envio_actualizado", "pedido_id": pedido_id, "estado": "en_destino", "modalidad_envio": "camion",
               "destino_ciudad": "Lima", "sacos": 60})
    _procesar({"evento": "saldo_pagado", "pedido_id": pedido_id, "monto": "300.00"})

    creado, adelanto, llegada, saldo = _avisos_de(pedido_id)
    assert "camión compartido" in creado.mensaje and "70%" in creado.mensaje
    assert adelanto.tipo == "pago_confirmado" and "S/ 700.00" in adelanto.mensaje and "S/ 300.00" in adelanto.mensaje
    assert llegada.estado == "en_destino" and "llegó a Lima" in llegada.mensaje and "saldo (30%)" in llegada.mensaje
    assert saldo.tipo == "saldo_pagado" and "S/ 300.00" in saldo.mensaje and "código de entrega" in saldo.mensaje
    # los avisos de Pagos no traen el usuario: se toma del aviso del pedido
    assert {str(a.usuario_id) for a in (adelanto, llegada, saldo)} == {dueno}


def test_un_evento_de_pagos_que_no_es_para_el_comprador_no_crea_aviso():
    pedido_id = str(uuid.uuid4())
    _procesar({"evento": "liquidacion_creada", "pedido_id": pedido_id})
    assert _avisos_de(pedido_id) == []
