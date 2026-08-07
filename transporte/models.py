import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Envio(Base):
    __tablename__ = "envios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repartidor_id = Column(UUID(as_uuid=True), nullable=True)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    estado = Column(String, default="pendiente")
    transportista = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

class Repartidor(Base):
    __tablename__ = "repartidores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    nombre = Column(String, nullable=False)
    dni = Column(String, nullable=False)
    estado_disponibilidad = Column(String, default="disponible")  # disponible, ocupado, desconectado
    fecha_registro = Column(DateTime, default=datetime.utcnow)

