"""Migración mínima de esquema para Productores (copia de transporte/migraciones.py: cada servicio es autosuficiente).

Este servicio no usa Alembic: Base.metadata.create_all() (main.py) crea las TABLAS que faltan, pero no agrega columnas
nuevas a una tabla que ya existe. Cuando models.py suma una columna (p. ej. la ubicación GPS y la foto de la cosecha en
`registros_produccion`), toda base creada antes queda con errores "column ... does not exist" en cada consulta a esa
tabla.

agregar_columnas_faltantes() cierra ese hueco para el caso simple: columnas nuevas que aceptan NULL. Una columna NOT
NULL sin valor por defecto NO se agrega sola (las filas existentes quedarían inválidas): se avisa por log para
resolverla a mano.
"""
import logging

from sqlalchemy import inspect, text

logger = logging.getLogger("productores.migraciones")


def agregar_columnas_faltantes(engine, base):
    """Agrega a cada tabla existente las columnas del modelo que la BD todavía no tiene.

    Devuelve la lista de "tabla.columna" agregadas. Nunca lanza: un fallo se registra y el servicio
    arranca igual (los errores de columna faltante seguirían apareciendo en los logs de siempre).
    """
    agregadas = []
    try:
        inspector = inspect(engine)
        pendientes = []
        for tabla in base.metadata.sorted_tables:
            if not inspector.has_table(tabla.name):
                continue  # create_all() la crea completa
            existentes = {c["name"] for c in inspector.get_columns(tabla.name)}
            pendientes.extend((tabla, c) for c in tabla.columns if c.name not in existentes)
    except Exception:
        logger.exception("No se pudo inspeccionar el esquema; se omite la migración de columnas")
        return agregadas

    # Con varias réplicas arrancando a la vez, IF NOT EXISTS (Postgres) evita que la segunda falle
    # porque la primera ya agregó la columna.
    si_no_existe = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
    for tabla, columna in pendientes:
        if not columna.nullable:
            logger.error(
                "La columna %s.%s es NOT NULL y no existe en la base: no se agrega sola (las filas "
                "existentes quedarían inválidas). Agrégala a mano con un valor por defecto.",
                tabla.name, columna.name,
            )
            continue
        tipo = columna.type.compile(dialect=engine.dialect)
        try:
            with engine.begin() as conexion:
                conexion.execute(text(f'ALTER TABLE "{tabla.name}" ADD COLUMN {si_no_existe}"{columna.name}" {tipo}'))
        except Exception:
            logger.exception("No se pudo agregar la columna %s.%s", tabla.name, columna.name)
            continue
        agregadas.append(f"{tabla.name}.{columna.name}")
        logger.warning("Migración: se agregó la columna %s.%s (%s)", tabla.name, columna.name, tipo)
    return agregadas


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
