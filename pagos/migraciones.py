"""Migraciones propias de Pagos: asegurar_indice_unico_de_pagos, el índice único de pagos.pedido_id, y
asegurar_indices_de_cargos, los del id del cargo de Culqi (create_all no agrega índices a una tabla que ya existe).

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


# Un cargo de Culqi nunca se asigna a dos pagos ni a dos partes (los mismos índices que declaran los modelos).
INDICES_DE_CARGOS = {
    "uq_pagos_culqi_charge_id": "ON pagos (culqi_charge_id) WHERE culqi_charge_id IS NOT NULL",
    "uq_pagos_culqi_charge_id_saldo": "ON pagos (culqi_charge_id_saldo) WHERE culqi_charge_id_saldo IS NOT NULL",
    "uq_intentos_pago_cargo_cobro": "ON intentos_pago (cargo_id) WHERE tipo = 'cobro' AND cargo_id IS NOT NULL",
}


def asegurar_indices_de_cargos(engine) -> list:
    """Crea los índices únicos del id del cargo si faltan (idempotente). Nunca lanza: si uno falla (un cargo ya asignado a
    dos pagos, que hay que resolver a mano) se registra y el servicio arranca igual. Devuelve los que no quedaron."""
    faltan = []
    for nombre, definicion in INDICES_DE_CARGOS.items():
        try:
            with engine.begin() as conexion:
                conexion.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {nombre} {definicion}"))
        except Exception:
            logger.exception("No se pudo crear el índice único %s: ¿un cargo de Culqi asignado a dos pagos? Hay que "
                             "resolverlo a mano; mientras tanto la base no lo impide.", nombre)
            faltan.append(nombre)
    return faltan
