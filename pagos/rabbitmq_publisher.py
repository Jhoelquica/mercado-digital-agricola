"""Los eventos de Pagos salen por el exchange eventos_pagos. Cómo se publican está en comun/rabbitmq.py."""
from functools import partial

from comun.rabbitmq import publicar_evento as publicar_en

EXCHANGE = "eventos_pagos"
publicar_evento = partial(publicar_en, EXCHANGE)
