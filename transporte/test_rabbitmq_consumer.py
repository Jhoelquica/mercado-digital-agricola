"""
Tests del consumidor de RabbitMQ de Transporte (rabbitmq_consumer.py): el envío de un pedido nace cuando su pago se
CONFIRMA, nunca antes. Antes nacía con pedido_creado y se le proponía a un repartidor un pedido sin pagar, que podía
aceptar, entregar y liquidar.

Usan la base Postgres real (igual que test_main.py). No se levanta RabbitMQ: se llama a _procesar_mensaje con un canal
falso, y proponer_envio_a_repartidor se sustituye para no depender de Pedidos.
"""
import json
import threading
import uuid
from unittest.mock import MagicMock

import pytest

import main  # noqa: F401  -- crea las tablas (create_all) antes de usar la base
import models
import rabbitmq_consumer
from comun.database import SessionLocal


@pytest.fixture
def propuestas(monkeypatch):
    """En lugar de la asignación real: anota a qué envío se llamó y, como la real, lo saca de "pendiente"."""
    llamadas = []

    def proponer(envio_id, db):
        llamadas.append(envio_id)
        db.query(models.Envio).filter(models.Envio.id == envio_id).update({"estado": "propuesto"}, synchronize_session=False)
        db.commit()

    monkeypatch.setattr(rabbitmq_consumer, "proponer_envio_a_repartidor", proponer)
    return llamadas


def _entregar(evento, pedido_id):
    canal, metodo = MagicMock(), MagicMock(delivery_tag=7)
    rabbitmq_consumer._procesar_mensaje(canal, metodo, None, json.dumps({"evento": evento, "pedido_id": str(pedido_id)}))
    canal.basic_ack.assert_called_once_with(delivery_tag=7)  # siempre se confirma el mensaje, también los ignorados


def _envios_del_pedido(pedido_id):
    db = SessionLocal()
    try:
        return db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).all()
    finally:
        db.close()


def test_crear_un_pedido_no_crea_envio(propuestas):
    pedido_id = uuid.uuid4()
    _entregar("pedido_creado", pedido_id)
    assert _envios_del_pedido(pedido_id) == []
    assert propuestas == []


def test_un_pago_rechazado_no_crea_envio(propuestas):
    pedido_id = uuid.uuid4()
    _entregar("pago_rechazado", pedido_id)
    assert _envios_del_pedido(pedido_id) == []
    assert propuestas == []


def test_el_pago_confirmado_crea_el_envio_y_lo_propone(propuestas):
    pedido_id = uuid.uuid4()
    _entregar("pago_confirmado", pedido_id)

    envios = _envios_del_pedido(pedido_id)
    assert len(envios) == 1
    assert envios[0].pin_entrega.isdigit() and len(envios[0].pin_entrega) == 4
    assert propuestas == [envios[0].id]


def test_el_mismo_pago_confirmado_dos_veces_no_duplica_el_envio(propuestas):
    pedido_id = uuid.uuid4()
    _entregar("pago_confirmado", pedido_id)
    _entregar("pago_confirmado", pedido_id)  # RabbitMQ entrega "al menos una vez": puede repetirse

    assert len(_envios_del_pedido(pedido_id)) == 1
    assert len(propuestas) == 1


def test_un_pedido_de_antes_del_arreglo_con_envio_no_recibe_otro_al_pagarse(propuestas):
    pedido_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(models.Envio(pedido_id=pedido_id, estado="propuesto", pin_entrega="1234"))
        db.commit()
    finally:
        db.close()

    _entregar("pago_confirmado", pedido_id)

    assert len(_envios_del_pedido(pedido_id)) == 1
    assert propuestas == []


def test_dos_entregas_a_la_vez_del_mismo_pago_crean_un_solo_envio(propuestas, monkeypatch):
    pedido_id = uuid.uuid4()
    # Las dos ya miraron que no hay envío cuando la primera lo inserta: solo el índice único puede frenar a la segunda.
    las_dos_miraron = threading.Barrier(2, timeout=10)

    def obtener_pedido(pid):
        las_dos_miraron.wait()
        return None

    monkeypatch.setattr(rabbitmq_consumer, "_obtener_pedido", obtener_pedido)
    resultados = []
    hilos = [threading.Thread(target=lambda: resultados.append(rabbitmq_consumer.crear_envio_si_falta(pedido_id)))
             for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=20)

    assert len(_envios_del_pedido(pedido_id)) == 1
    assert sum(resultado is not None for resultado in resultados) == 1 and len(resultados) == 2
    assert len(propuestas) == 1


def test_si_falla_despues_de_crear_el_envio_el_reintento_lo_asigna(monkeypatch):
    import logica_repartidores
    monkeypatch.setattr(rabbitmq_consumer, "_obtener_pedido", lambda pid: None)
    monkeypatch.setattr(logica_repartidores, "_obtener_pedido", lambda pid: None)
    asignar = rabbitmq_consumer.proponer_envio_a_repartidor
    llamadas = []

    def proponer_que_falla_la_primera_vez(envio_id, db):
        llamadas.append(envio_id)
        if len(llamadas) == 1:
            raise RuntimeError("se cortó justo después de crear el envío")
        asignar(envio_id, db)

    monkeypatch.setattr(rabbitmq_consumer, "proponer_envio_a_repartidor", proponer_que_falla_la_primera_vez)
    pedido_id = uuid.uuid4()
    with pytest.raises(RuntimeError):
        rabbitmq_consumer.crear_envio_si_falta(pedido_id)
    [envio] = _envios_del_pedido(pedido_id)
    assert envio.estado == "pendiente"

    rabbitmq_consumer.crear_envio_si_falta(pedido_id)  # el reintento del mismo pago_confirmado

    [envio] = _envios_del_pedido(pedido_id)
    assert envio.estado in ("propuesto", "pendiente_asignacion", "pendiente_entrega_directa"), envio.estado
    assert llamadas == [envio.id, envio.id]
