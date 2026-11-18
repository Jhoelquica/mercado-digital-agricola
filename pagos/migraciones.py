"""Migraciones propias de Pagos: asegurar_indice_unico_de_pagos, el índice único de pagos.pedido_id (create_all no
agrega índices a una tabla que ya existe).

La de las columnas nuevas de una tabla que ya existe es común: agregar_columnas_faltantes, en comun/migraciones.py.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger("pagos.migraciones")


INDICE_UNICO_DE_PAGOS = "uq_pagos_pedido_id"


def asegurar_indice_unico_de_pagos(engine) -> bool:
    """Crea el índice único de pagos.pedido_id si falta (idempotente; varias réplicas pueden arrancar a la vez). Nunca
    lanza: si falla (por ejemplo porque ya hay dos pagos del mismo pedido, que hay que resolver a mano) se registra y el
    servicio arranca igual. Devuelve si el índice quedó garantizado."""
    try:
        with engine.begin() as conexion:
            conexion.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_UNICO_DE_PAGOS} ON pagos (pedido_id)"))
    except Exception:
        logger.exception("No se pudo crear el índice único %s sobre pagos(pedido_id). Si hay pedidos con dos pagos hay "
                         "que resolverlos a mano; mientras tanto dos entregas a la vez pueden crear dos.",
                         INDICE_UNICO_DE_PAGOS)
        return False
    return True
