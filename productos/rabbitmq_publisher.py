"""Los eventos de Productos salen por el exchange eventos_productos. Cómo se publican está en comun/rabbitmq.py."""
from functools import partial

from comun.rabbitmq import publicar_evento as publicar_en

EXCHANGE = "eventos_productos"
publicar_evento = partial(publicar_en, EXCHANGE)
