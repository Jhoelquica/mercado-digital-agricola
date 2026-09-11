import uuid
import secrets
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    rol = Column(String, default="comprador")  # productor, comprador, admin, verificador
    fecha_registro = Column(DateTime, default=datetime.utcnow)


def _codigo_invitacion() -> str:
    # token_urlsafe(16) -> ~22 caracteres (128 bits de entropía): corto para pegar en un
    # WhatsApp/email, pero imposible de adivinar. No uuid4 (36 chars con guiones, más largo).
    return secrets.token_urlsafe(16)


def _expiracion_default() -> datetime:
    return datetime.utcnow() + timedelta(days=7)


class Invitacion(Base):
    """Código de un solo uso que habilita registrarse con un rol restringido. Lo genera un admin
    (POST /admin/invitaciones) y en una sub-entrega posterior lo consumirá /usuarios/registro.
    `rol_destino` es genérico a propósito — hoy se usará para "verificador", pero sirve para
    cualquier rol que en el futuro requiera invitación (incluido "admin"). Tabla nueva: la crea
    Base.metadata.create_all() al arrancar, igual que el resto de este proyecto."""
    __tablename__ = "invitaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    codigo = Column(String, unique=True, nullable=False, index=True, default=_codigo_invitacion)
    rol_destino = Column(String, nullable=False)
    usado = Column(Boolean, nullable=False, default=False)
    usado_por = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    creado_por = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False)
    fecha_creacion = Column(DateTime, nullable=False, default=datetime.utcnow)
    fecha_expiracion = Column(DateTime, nullable=False, default=_expiracion_default)
