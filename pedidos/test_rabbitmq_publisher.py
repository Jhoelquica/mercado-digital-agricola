"""El evento debe publicarse como mensaje PERSISTENTE (delivery_mode=2).

Sin eso el mensaje solo vive en memoria: si RabbitMQ se reinicia con el evento todavía sin consumir
(p. ej. al reiniciar Docker Desktop) se pierde, y un pedido puede quedar sin envío. No necesita
Postgres ni RabbitMQ reales: se sustituye pika.BlockingConnection.
"""
from unittest.mock import MagicMock

import rabbitmq_publisher


def test_publica_el_evento_como_mensaje_persistente(monkeypatch):
    canal = MagicMock()
    conexion = MagicMock()
    conexion.channel.return_value = canal
    monkeypatch.setattr(rabbitmq_publisher.pika, "BlockingConnection", lambda *args, **kwargs: conexion)

    rabbitmq_publisher.publicar_evento({"evento": "pedido_creado", "pedido_id": "abc"})

    kwargs = canal.basic_publish.call_args.kwargs
    assert kwargs["exchange"] == "eventos_pedidos"
    assert kwargs["routing_key"] == ""
    assert kwargs["properties"].delivery_mode == 2
    assert '"pedido_creado"' in kwargs["body"]
    conexion.close.assert_called_once()
