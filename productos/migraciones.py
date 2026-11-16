"""Migración mínima de esquema para Productos (copia de pedidos/migraciones.py: cada servicio es autosuficiente).

Este servicio no usa Alembic: Base.metadata.create_all() (main.py) crea las TABLAS que faltan, pero no agrega columnas
nuevas a una tabla que ya existe. Cuando models.py suma una columna (p. ej. la descripción en `productos`), toda base creada antes queda con errores "column ... does not exist" en cada consulta a esa
tabla.

agregar_columnas_faltantes() cierra ese hueco para el caso simple: columnas nuevas que aceptan NULL. Una columna NOT
NULL sin valor por defecto NO se agrega sola (las filas existentes quedarían inválidas): se avisa por log para
resolverla a mano.
"""
import logging

from sqlalchemy import inspect, text

logger = logging.getLogger("productos.migraciones")


def separar_fecha_de_creacion(engine):
    """2026-11-16: fecha_publicacion se ponía al crear el borrador. Ahora fecha_creacion guarda eso y
    fecha_publicacion es nula hasta publicar. Para las filas de antes:
    - fecha_creacion = la fecha_publicacion que tenían (la de su creación), en todas las que no la tengan;
    - los borradores quedan con fecha_publicacion nula;
    - los ya publicados se quedan con su fecha_publicacion (no hay un dato mejor de cuándo se publicaron).
    Idempotente (solo toca lo que falta): corre en cada arranque y con varias réplicas a la vez. Devuelve cuántas filas
    cambió en cada paso; nunca lanza."""
    try:
        with engine.begin() as conexion:
            creadas = conexion.execute(text(
                "UPDATE productos SET fecha_creacion = fecha_publicacion "
                "WHERE fecha_creacion IS NULL AND fecha_publicacion IS NOT NULL")).rowcount
            borradores = conexion.execute(text(
                "UPDATE productos SET fecha_publicacion = NULL "
                "WHERE estado = 'borrador' AND fecha_publicacion IS NOT NULL")).rowcount
    except Exception:
        logger.exception("No se pudo separar fecha_creacion de fecha_publicacion")
        return None
    if creadas or borradores:
        logger.warning("Migración de fechas: %s con fecha_creacion copiada, %s borradores sin fecha_publicacion",
                       creadas, borradores)
    return {"fecha_creacion": creadas, "borradores": borradores}


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
