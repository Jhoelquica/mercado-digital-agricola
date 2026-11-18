"""Migraciones propias de Usuarios: crear_indices_faltantes, porque create_all() no agrega los índices nuevos de una
tabla que ya existe (p. ej. el único del RUC en `usuarios`).

La de las columnas nuevas de una tabla que ya existe es común: agregar_columnas_faltantes, en comun/migraciones.py.
"""
import logging

from sqlalchemy import text
from sqlalchemy.schema import CreateIndex

logger = logging.getLogger("usuarios.migraciones")


def crear_indices_faltantes(engine, base):
    """create_all() tampoco agrega los índices nuevos de una tabla que ya existe (p. ej. el único del RUC en `usuarios`):
    se crean aquí con IF NOT EXISTS (varias réplicas pueden arrancar a la vez). Nunca lanza: si uno no se puede crear
    (p. ej. un índice único con datos repetidos) se avisa por log y el servicio arranca igual."""
    for tabla in base.metadata.sorted_tables:
        for indice in tabla.indexes:
            try:
                with engine.begin() as conexion:
                    conexion.execute(CreateIndex(indice, if_not_exists=True))
            except Exception:
                logger.exception("No se pudo crear el índice %s de %s", indice.name, tabla.name)
