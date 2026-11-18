"""El logging de los servicios: un solo formato (hora, nivel y nombre del logger) y el nivel desde LOG_LEVEL.

Sin configurar, Python usa su "handler de último recurso": los logger.info() no salen en `kubectl logs` y los avisos
salen sin hora ni nivel. uvicorn configura solo sus propios loggers, no el de la raíz.
"""
import logging
import os
import sys

FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"
NIVELES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# Librerías que en INFO escriben una línea por cada petición o conexión: httpx y google_genai (Gemini) por cada llamada,
# pika por cada conexión y canal con RabbitMQ. Quedan en WARNING (o en LOG_LEVEL, si es más alto): sus avisos y errores
# siguen saliendo. Los loggers del proyecto siguen a LOG_LEVEL.
LIBRERIAS_RUIDOSAS = ("httpx", "httpcore", "pika", "urllib3", "google_genai")


class SoloRespuestasConError(logging.Filter):
    """El log de acceso de uvicorn escribía una línea por cada petición: las sondas de Kubernetes cada pocos segundos,
    /metrics y cada llamada entre servicios. Con este filtro solo escribe las respuestas con error (4xx y 5xx)."""

    def filter(self, registro: logging.LogRecord) -> bool:
        # uvicorn registra el acceso como '%s - "%s %s HTTP/%s" %d': el último argumento es el código de la respuesta.
        codigo = registro.args[-1] if isinstance(registro.args, tuple) and registro.args else None
        return not isinstance(codigo, int) or codigo >= 400


def configurar_logging() -> None:
    """Se llama una vez, lo primero en main.py: así lo que se registre al importar el resto ya sale con este formato.
    LOG_LEVEL elige el nivel (por defecto INFO); uno que no existe no deja arrancar el servicio."""
    nivel = (os.getenv("LOG_LEVEL") or "INFO").upper()
    if nivel not in NIVELES:
        sys.exit(f"La variable de entorno LOG_LEVEL vale {nivel!r}: tiene que ser uno de {', '.join(NIVELES)}.")
    logging.basicConfig(level=nivel, format=FORMATO)
    nivel_de_librerias = max(logging.WARNING, logging.getLevelNamesMapping()[nivel])
    for nombre in LIBRERIAS_RUIDOSAS:
        logging.getLogger(nombre).setLevel(nivel_de_librerias)
    acceso = logging.getLogger("uvicorn.access")
    if not any(isinstance(filtro, SoloRespuestasConError) for filtro in acceso.filters):
        acceso.addFilter(SoloRespuestasConError())
