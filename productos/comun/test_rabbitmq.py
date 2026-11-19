# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Pruebas de comun/rabbitmq.py sin un RabbitMQ real: se sustituyen la conexión, el canal y la API de gestión."""
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


# ---- Publicar (solo el relevo de la bandeja de salida) ----

@pytest.fixture
def conexion_falsa(monkeypatch):
    """Sustituye pika.BlockingConnection: devuelve la conexión falsa, con su canal, y los parámetros con que se abrió."""
    canal = MagicMock()
    conexion = _conexion_falsa(canal)
    abiertas = []

    def abrir(parametros):
        abiertas.append(parametros)
        return conexion

    monkeypatch.setattr(pika, "BlockingConnection", abrir)
    return conexion, canal, abiertas


def test_publica_de_a_uno_persistente_obligatorio_y_confirmado(conexion_falsa):
    conexion, canal, abiertas = conexion_falsa
    with rabbitmq.Publicador() as publicador:
        publicador.publicar("eventos_prueba", '{"evento": "algo"}', "id-1")
        publicador.publicar("eventos_prueba", '{"evento": "otro"}', "id-2")
    canal.confirm_delivery.assert_called_once()
    primero, segundo = (llamada.kwargs for llamada in canal.basic_publish.call_args_list)
    assert (primero["exchange"], primero["routing_key"], primero["body"]) == ("eventos_prueba", "", '{"evento": "algo"}')
    assert primero["mandatory"] is True and primero["properties"].delivery_mode == 2
    assert (primero["properties"].message_id, segundo["properties"].message_id) == ("id-1", "id-2")
    canal.exchange_declare.assert_called_once_with(exchange="eventos_prueba", exchange_type="fanout", durable=True)
    conexion.close.assert_called_once()
    assert abiertas[0].heartbeat == rabbitmq.LATIDO_DEL_PUBLICADOR_SEGUNDOS
    assert abiertas[0].blocked_connection_timeout == rabbitmq.ESPERA_MAXIMA_SI_RABBITMQ_FRENA


@pytest.mark.parametrize("error", [pika.exceptions.UnroutableError([]), pika.exceptions.NackError([]),
                                   pika.exceptions.StreamLostError("se cortó")])
def test_sin_cola_un_rechazo_o_una_conexion_caida_lanzan_y_cierran(conexion_falsa, error):
    conexion, canal, _ = conexion_falsa
    canal.basic_publish.side_effect = error
    with pytest.raises(type(error)):
        with rabbitmq.Publicador() as publicador:
            publicador.publicar("eventos_prueba", "{}", "id-1")
    conexion.close.assert_called_once()


def test_si_no_se_puede_abrir_el_canal_cierra_la_conexion(conexion_falsa):
    conexion, _, _ = conexion_falsa
    conexion.channel.side_effect = pika.exceptions.ChannelClosedByBroker(406, "PRECONDITION_FAILED")
    with pytest.raises(pika.exceptions.ChannelClosedByBroker):
        with rabbitmq.Publicador():
            pass
    conexion.close.assert_called_once()


def test_dias_de_retencion_por_defecto_y_mal_escritos(correr_python):
    resultado = correr_python("from comun import rabbitmq; print(rabbitmq.DIAS_RETENCION_EVENTOS)",
                              DIAS_RETENCION_EVENTOS=None)
    assert resultado.returncode == 0 and resultado.stdout.strip() == "30", resultado.stderr
    for valor in ("treinta", "0"):
        resultado = correr_python("import comun.rabbitmq", DIAS_RETENCION_EVENTOS=valor)
        assert resultado.returncode != 0 and "DIAS_RETENCION_EVENTOS" in resultado.stderr


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
