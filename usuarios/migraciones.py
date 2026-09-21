"""Cambios de esquema que Base.metadata.create_all() no aplica a una tabla que ya existe."""
from sqlalchemy import text


def asegurar_columna_credencial_en_invitaciones(engine) -> None:
    """`Invitacion.credencial` (la que el admin le asigna a un verificador al invitarlo) es posterior a la tabla:
    en una base que ya tenía `invitaciones` hay que agregarla. Idempotente (no falla si ya está)."""
    with engine.begin() as conexion:
        conexion.execute(text("ALTER TABLE invitaciones ADD COLUMN IF NOT EXISTS credencial VARCHAR"))
