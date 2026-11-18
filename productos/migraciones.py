"""Migraciones propias de Productos: separar_fecha_de_creacion, que separa la fecha de creación de la de publicación
en los productos de antes.

La de las columnas nuevas de una tabla que ya existe es común: agregar_columnas_faltantes, en comun/migraciones.py.
"""
import logging

from sqlalchemy import text

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
