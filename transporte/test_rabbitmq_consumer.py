"""
Tests del consumidor de RabbitMQ de Transporte (rabbitmq_consumer.py): el envío de un pedido nace cuando su pago se
CONFIRMA, nunca antes. Antes nacía con pedido_creado y se le proponía a un repartidor un pedido sin pagar, que podía
aceptar, entregar y liquidar.

Usan la base Postgres real (igual que test_main.py). No se levanta RabbitMQ: se llama a _procesar_mensaje con un canal
falso, y proponer_envio_a_repartidor se sustituye para no depender de Pedidos.
"""
import json
import uuid
from unittest.mock import MagicMock

import pytest

import main  # noqa: F401  -- crea las tablas (create_all) antes de usar la base
import models
import rabbitmq_consumer
from database import SessionLocal


@pytest.fixture
def propuestas(monkeypatch):
    llamadas = []
    monkeypatch.setattr(rabbitmq_consumer, "proponer_envio_a_repartidor", lambda envio_id, db: llamadas.append(envio_id))
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
