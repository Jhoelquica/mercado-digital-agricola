"""Los eventos de Transporte salen por el exchange eventos_envios. Cómo se publican está en comun/rabbitmq.py."""
from functools import partial

from comun.rabbitmq import publicar_evento as publicar_en

EXCHANGE = "eventos_envios"
publicar_evento = partial(publicar_en, EXCHANGE)
