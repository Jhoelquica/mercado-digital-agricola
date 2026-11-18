"""Migraciones propias de Transporte: asegurar_indice_unico_de_envios, el índice único de envios.pedido_id (create_all
no agrega índices a una tabla que ya existe).

La de las columnas nuevas de una tabla que ya existe es común: agregar_columnas_faltantes, en comun/migraciones.py.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger("transporte.migraciones")


INDICE_UNICO_DE_ENVIOS = "uq_envios_pedido_id"


def asegurar_indice_unico_de_envios(engine) -> bool:
    """Crea el índice único de envios.pedido_id si falta (idempotente; varias réplicas pueden arrancar a la vez). Nunca
    lanza: si falla (por ejemplo porque ya hay dos envíos del mismo pedido, que hay que resolver a mano) se registra y el
    servicio arranca igual. Devuelve si el índice quedó garantizado."""
    try:
        with engine.begin() as conexion:
            conexion.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_UNICO_DE_ENVIOS} ON envios (pedido_id)"))
    except Exception:
        logger.exception("No se pudo crear el índice único %s sobre envios(pedido_id). Si hay pedidos con dos envíos "
                         "hay que resolverlos a mano; mientras tanto dos entregas a la vez pueden crear dos.",
                         INDICE_UNICO_DE_ENVIOS)
        return False
    return True
