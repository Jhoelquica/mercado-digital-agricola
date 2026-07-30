import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Producto(Base):
    __tablename__ = "productos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    productor_id = Column(UUID(as_uuid=True), nullable=False)
    productor_nombre = Column(String, nullable=False)  # copia replicada
    nombre = Column(String, nullable=False)
    imagen_url = Column(String, nullable=True)
    categoria = Column(String)
    precio = Column(Numeric(10, 2), nullable=False)
    stock = Column(Integer, default=0)
    unidad_medida = Column(String, default="kg")
    fecha_publicacion = Column(DateTime, default=datetime.utcnow)