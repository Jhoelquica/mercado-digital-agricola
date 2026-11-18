# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""El logging de los servicios: un solo formato (hora, nivel y nombre del logger) y el nivel desde LOG_LEVEL.

Sin configurar, Python usa su "handler de último recurso": los logger.info() no salen en `kubectl logs` y los avisos
salen sin hora ni nivel. uvicorn configura solo sus propios loggers, no el de la raíz.
"""
import logging
import os
import sys

FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"
NIVELES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def configurar_logging() -> None:
    """Se llama una vez, lo primero en main.py: así lo que se registre al importar el resto ya sale con este formato.
    LOG_LEVEL elige el nivel (por defecto INFO); uno que no existe no deja arrancar el servicio."""
    nivel = (os.getenv("LOG_LEVEL") or "INFO").upper()
    if nivel not in NIVELES:
        sys.exit(f"La variable de entorno LOG_LEVEL vale {nivel!r}: tiene que ser uno de {', '.join(NIVELES)}.")
    logging.basicConfig(level=nivel, format=FORMATO)
