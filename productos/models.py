import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base
from sqlalchemy import Column, String, Numeric, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship

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

class ProductoImagen(Base):
    __tablename__ = "producto_imagenes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    url = Column(String, nullable=False)
    orden = Column(Integer, default=0)

    producto = relationship("Producto", backref="imagenes")

class Resena(Base):
    __tablename__ = "resenas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=False)
    usuario_nombre = Column(String, nullable=False)
    calificacion = Column(Integer, nullable=False)  # 1 a 5
    comentario = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)