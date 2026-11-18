"""Pruebas de comun/rabbitmq.py sin un RabbitMQ real: se sustituyen la conexión, el canal y la API de gestión."""
import json
import uuid
from unittest.mock import MagicMock

import pika
import pytest
from sqlalchemy.exc import OperationalError

from comun import rabbitmq


class Metodo:
    delivery_tag = 7


class Salir(BaseException):
    """Corta el bucle del consumidor (que no termina nunca) en la prueba: no es Exception, así el bucle no la atrapa."""


def _propiedades(**cabeceras):
    return pika.BasicProperties(delivery_mode=2, headers=cabeceras or None)


@pytest.fixture
def pausas(monkeypatch):
    hechas = []
    monkeypatch.setattr(rabbitmq.time, "sleep", hechas.append)
    return hechas


def _conexion_falsa(canal):
    conexion = MagicMock()
    conexion.channel.return_value = canal
    return conexion


# ---- Publicar ----

def test_publica_persistente_en_el_exchange_dado_y_cierra(monkeypatch):
    canal = MagicMock()
    conexion = _conexion_falsa(canal)
    monkeypatch.setattr(pika, "BlockingConnection", lambda *args, **kwargs: conexion)
    evento_id = rabbitmq.publicar_evento("eventos_prueba", {"evento": "algo", "id": 1})
    datos = canal.basic_publish.call_args.kwargs
    assert (datos["exchange"], datos["routing_key"], datos["properties"].delivery_mode) == ("eventos_prueba", "", 2)
    assert json.loads(datos["body"]) == {"evento": "algo", "id": 1, "evento_id": evento_id}
    assert str(uuid.UUID(evento_id)) == evento_id == datos["properties"].message_id
    canal.exchange_declare.assert_called_once_with(exchange="eventos_prueba", exchange_type="fanout", durable=True)
    conexion.close.assert_called_once()


def test_respeta_el_evento_id_que_trae_y_genera_uno_distinto_cada_vez(monkeypatch):
    canal = MagicMock()
    monkeypatch.setattr(pika, "BlockingConnection", lambda *args, **kwargs: _conexion_falsa(canal))
    propio = str(uuid.uuid4())
    assert rabbitmq.publicar_evento("eventos_prueba", {"evento": "algo", "evento_id": propio}) == propio
    assert json.loads(canal.basic_publish.call_args.kwargs["body"])["evento_id"] == propio
    generados = {rabbitmq.publicar_evento("eventos_prueba", {"evento": "algo"}) for _ in range(3)}
    assert len(generados) == 3


def test_un_evento_id_que_no_es_uuid_no_se_publica(monkeypatch):
    conexiones = []
    monkeypatch.setattr(pika, "BlockingConnection", lambda *args, **kwargs: conexiones.append(1))
    with pytest.raises(ValueError):
        rabbitmq.publicar_evento("eventos_prueba", {"evento": "algo", "evento_id": "pedido-123"})
    assert conexiones == []


def test_cierra_la_conexion_aunque_la_publicacion_falle(monkeypatch):
    canal = MagicMock()
    canal.basic_publish.side_effect = pika.exceptions.ChannelClosedByBroker(406, "PRECONDITION_FAILED")
    conexion = _conexion_falsa(canal)
    monkeypatch.setattr(pika, "BlockingConnection", lambda *args, **kwargs: conexion)
    with pytest.raises(pika.exceptions.ChannelClosedByBroker):
        rabbitmq.publicar_evento("eventos_prueba", {"evento": "algo"})
    conexion.close.assert_called_once()


# ---- Reintentos y fallidos ----

def test_un_fallo_que_se_arregla_solo_no_llega_a_fallidos(pausas):
    llamadas = []

    def procesar(canal, metodo, propiedades, cuerpo):
        llamadas.append(cuerpo)
        if len(llamadas) < 3:
            raise ValueError("todavía no")
        canal.basic_ack(delivery_tag=metodo.delivery_tag)

    canal = MagicMock()
    rabbitmq.con_reintentos("cola_prueba", procesar)(canal, Metodo(), _propiedades(), b"{}")
    assert len(llamadas) == 3 and pausas == [1, 2]
    canal.basic_ack.assert_called_once_with(delivery_tag=7)
    canal.basic_publish.assert_not_called()


def test_tras_3_fallos_vuelve_con_el_motivo_y_al_llegar_se_rechaza(pausas):
    def procesar(*_):
        raise KeyError("pedido_id")

    canal = MagicMock()
    rabbitmq.con_reintentos("cola_prueba", procesar)(canal, Metodo(), _propiedades(origen="pagos"), b'{"evento": "x"}')
    copia = canal.basic_publish.call_args.kwargs
    assert (copia["exchange"], copia["routing_key"], copia["body"]) == ("", "cola_prueba", b'{"evento": "x"}')
    assert copia["properties"].headers == {"origen": "pagos", "x-motivo": "KeyError: 'pedido_id'", "x-intentos": 3}
    assert copia["properties"].delivery_mode == 2
    canal.basic_ack.assert_called_once_with(delivery_tag=7)  # el original

    # La copia vuelve a la cola: se rechaza sin procesarla y la policy fallidos-cola_prueba la lleva a cola_prueba.fallidos.
    procesar_otra_vez = MagicMock()
    canal_nuevo = MagicMock()
    rabbitmq.con_reintentos("cola_prueba", procesar_otra_vez)(canal_nuevo, Metodo(), copia["properties"], copia["body"])
    procesar_otra_vez.assert_not_called()
    canal_nuevo.basic_reject.assert_called_once_with(delivery_tag=7, requeue=False)


def test_si_la_base_no_responde_vuelve_a_la_cola_sin_gastar_intentos(pausas):
    procesar = MagicMock(side_effect=OperationalError("SELECT 1", {}, Exception("connection refused")))
    canal = MagicMock()
    rabbitmq.con_reintentos("cola_prueba", procesar)(canal, Metodo(), _propiedades(), b"{}")
    assert procesar.call_count == 1 and pausas == [rabbitmq.ESPERA_SI_LA_BASE_NO_RESPONDE]
    canal.basic_nack.assert_called_once_with(delivery_tag=7, requeue=True)
    canal.basic_publish.assert_not_called()
    canal.basic_reject.assert_not_called()


def test_un_error_de_rabbitmq_no_se_reintenta(pausas):
    procesar = MagicMock(side_effect=pika.exceptions.StreamLostError("se cortó"))
    with pytest.raises(pika.exceptions.StreamLostError):
        rabbitmq.con_reintentos("cola_prueba", procesar)(MagicMock(), Metodo(), _propiedades(), b"{}")
    assert procesar.call_count == 1


def test_una_cabecera_de_intentos_rara_cuenta_como_ninguno(pausas):
    procesar = MagicMock()
    rabbitmq.con_reintentos("cola_prueba", procesar)(MagicMock(), Metodo(), _propiedades(**{"x-intentos": "tres"}), b"{}")
    procesar.assert_called_once()


# ---- El bucle del consumidor ----

def test_cierra_la_conexion_vieja_antes_de_abrir_otra(monkeypatch):
    historia = []

    class Conexion:
        def __init__(self, numero):
            self.numero, self.is_open = numero, True
            historia.append(f"abre {numero}")

        def channel(self):
            canal = MagicMock()
            canal.start_consuming.side_effect = pika.exceptions.ChannelClosedByBroker(406, "PRECONDITION_FAILED")
            return canal

        def close(self):
            self.is_open = False
            historia.append(f"cierra {self.numero}")

    numeros = iter(range(1, 10))
    monkeypatch.setattr(pika, "BlockingConnection", lambda *args, **kwargs: Conexion(next(numeros)))
    esperas = []

    def esperar(segundos):
        esperas.append(segundos)
        if len(esperas) == 2:
            raise Salir

    monkeypatch.setattr(rabbitmq.time, "sleep", esperar)
    with pytest.raises(Salir):
        rabbitmq.consumir("Prueba", [("eventos_prueba", "cola_prueba", MagicMock())])
    assert historia == ["abre 1", "cierra 1", "abre 2", "cierra 2"]
    assert esperas == [rabbitmq.ESPERA_PARA_RECONECTAR] * 2


def test_cada_cola_tiene_su_cola_de_fallidos_ligada_con_su_nombre(monkeypatch):
    canal = MagicMock()
    canal.start_consuming.side_effect = Salir
    monkeypatch.setattr(pika, "BlockingConnection", lambda *args, **kwargs: _conexion_falsa(canal))
    with pytest.raises(Salir):
        rabbitmq.consumir("Prueba", [("eventos_a", "cola_a", MagicMock()), ("eventos_b", "cola_b", MagicMock())])
    canal.exchange_declare.assert_any_call(exchange="fallidos", exchange_type="direct", durable=True)
    declaradas = [llamada.kwargs["queue"] for llamada in canal.queue_declare.call_args_list]
    assert declaradas == ["cola_a", "cola_a.fallidos", "cola_b", "cola_b.fallidos"]
    ligaduras = [(llamada.kwargs["exchange"], llamada.kwargs["queue"], llamada.kwargs.get("routing_key"))
                 for llamada in canal.queue_bind.call_args_list]
    # Lo que la policy rechaza en cola_a llega al exchange "fallidos" con routing key cola_a (la de la copia que
    # vuelve con el motivo): por eso cola_a.fallidos se liga con ese nombre.
    assert ligaduras == [("eventos_a", "cola_a", None), ("fallidos", "cola_a.fallidos", "cola_a"),
                         ("eventos_b", "cola_b", None), ("fallidos", "cola_b.fallidos", "cola_b")]
    assert [llamada.kwargs["queue"] for llamada in canal.basic_consume.call_args_list] == ["cola_a", "cola_b"]
