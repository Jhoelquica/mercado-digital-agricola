"""Migraciones propias de Productores: pasar_entero_a_decimal, para cambiar el tipo de una columna que ya existe
(p. ej. gastos_produccion.dias).

La de las columnas nuevas de una tabla que ya existe es común: agregar_columnas_faltantes, en comun/migraciones.py.
"""
import logging

from sqlalchemy import inspect, text

logger = logging.getLogger("productores.migraciones")


def pasar_entero_a_decimal(engine, tabla: str, columna: str, tipo: str) -> bool:
    """Cambia una columna entera a decimal (p. ej. los días de la mano de obra, que ahora aceptan medio día). Solo si la
    columna existe y todavía es entera: las siguientes veces no hace nada. Los valores enteros se conservan tal cual.

    Devuelve True si la cambió. Nunca lanza, como agregar_columnas_faltantes. Solo en Postgres (SQLite no cambia tipos
    con ALTER, y ahí la columna nace ya con el tipo del modelo)."""
    if engine.dialect.name != "postgresql":
        return False
    try:
        inspector = inspect(engine)
        if not inspector.has_table(tabla):
            return False
        actual = next((c for c in inspector.get_columns(tabla) if c["name"] == columna), None)
        if actual is None or actual["type"].python_type is not int:
            return False
        with engine.begin() as conexion:
            conexion.execute(text(f'ALTER TABLE "{tabla}" ALTER COLUMN "{columna}" TYPE {tipo} USING "{columna}"::numeric'))
    except Exception:
        logger.exception("No se pudo pasar %s.%s a %s", tabla, columna, tipo)
        return False
    logger.warning("Migración: %s.%s pasó de entero a %s", tabla, columna, tipo)
    return True
