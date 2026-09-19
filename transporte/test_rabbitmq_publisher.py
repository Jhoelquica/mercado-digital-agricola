"""El evento debe publicarse como mensaje PERSISTENTE (delivery_mode=2).

Sin eso el mensaje solo vive en memoria: si RabbitMQ se reinicia con el evento todavía sin consumir
(p. ej. al reiniciar Docker Desktop) se pierde. No necesita Postgres ni RabbitMQ reales: se
sustituye pika.BlockingConnection.
"""
from unittest.mock import MagicMock

import rabbitmq_publisher


def test_publica_el_evento_como_mensaje_persistente(monkeypatch):
    canal = MagicMock()
    conexion = MagicMock()
    conexion.channel.return_value = canal
    monkeypatch.setattr(rabbitmq_publisher.pika, "BlockingConnection", lambda *args, **kwargs: conexion)

    rabbitmq_publisher.publicar_evento({"evento": "envio_actualizado", "envio_id": "abc"})

    kwargs = canal.basic_publish.call_args.kwargs
    assert kwargs["exchange"] == "eventos_envios"
    assert kwargs["routing_key"] == ""
    assert kwargs["properties"].delivery_mode == 2
    assert '"envio_actualizado"' in kwargs["body"]
    conexion.close.assert_called_once()
