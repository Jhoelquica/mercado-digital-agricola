import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Productor(Base):
    __tablename__ = "productores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=True, unique=True)
    nombre = Column(String, nullable=False)
    comunidad = Column(String)
    contacto = Column(String)
    ubicacion = Column(String, nullable=True)
    fecha_registro = Column(DateTime, default=datetime.utcnow)
