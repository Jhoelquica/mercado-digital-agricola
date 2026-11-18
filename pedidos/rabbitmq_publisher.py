"""Los eventos de Pedidos salen por el exchange eventos_pedidos. Cómo se publican está en comun/rabbitmq.py."""
from functools import partial

from comun.rabbitmq import publicar_evento as publicar_en

EXCHANGE = "eventos_pedidos"
publicar_evento = partial(publicar_en, EXCHANGE)
