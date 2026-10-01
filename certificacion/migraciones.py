"""Migración mínima de esquema para Certificación.

Este servicio no usa Alembic: Base.metadata.create_all() (main.py) crea las tablas que faltan, pero
no agrega restricciones a una tabla que ya existe. La restricción UNIQUE(producto_id, indice) de
`bloques` (uq_bloque_producto_indice, ver models.py) evita que dos escrituras simultáneas dejen
dos bloques con el mismo índice y la cadena bifurcada; en una base creada antes de que existiera,
hay que agregarla a mano: eso hace asegurar_indice_unico_de_bloques() al arrancar. Lo mismo con la
columna foto_url de `bloques` (asegurar_columna_foto_url()).
"""
import logging

from sqlalchemy import text

logger = logging.getLogger("certificacion.migraciones")

NOMBRE_INDICE = "uq_bloque_producto_indice"


def asegurar_indice_unico_de_bloques(engine) -> bool:
    """Crea el índice único si no existe (idempotente). Nunca lanza: si falla (por ejemplo porque la
    base ya tiene bloques duplicados, que hay que resolver a mano) se registra y el servicio arranca
    igual. Devuelve si el índice quedó garantizado."""
    try:
        with engine.begin() as conexion:
            conexion.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {NOMBRE_INDICE} ON bloques (producto_id, indice)"))
    except Exception:
        logger.exception(
            "No se pudo crear el índice único %s sobre bloques(producto_id, indice). Si la tabla tiene "
            "bloques con el mismo índice hay que resolverlos a mano; mientras tanto la cadena puede "
            "bifurcarse ante escrituras simultáneas.",
            NOMBRE_INDICE,
        )
        return False
    return True


def asegurar_columna_foto_url(engine) -> bool:
    """Agrega bloques.foto_url si falta (idempotente). Nunca lanza: si falla, el servicio arranca igual y lo
    registra (las escrituras de cosechas fallarían hasta resolverlo)."""
    try:
        with engine.begin() as conexion:
            conexion.execute(text("ALTER TABLE bloques ADD COLUMN IF NOT EXISTS foto_url VARCHAR"))
    except Exception:
        logger.exception("No se pudo agregar la columna foto_url a bloques")
        return False
    return True
