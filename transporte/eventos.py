"""Los eventos de Transporte salen por el exchange eventos_envios, siempre por la bandeja de salida (comun/bandeja_salida.py):
registrar_evento(sesion, tipo, datos) los deja en la transacción del cambio que los origina y solo el relevo publica."""
from functools import partial

from comun.bandeja_salida import registrar_evento as registrar_en

EXCHANGE = "eventos_envios"
registrar_evento = partial(registrar_en, exchange=EXCHANGE)
