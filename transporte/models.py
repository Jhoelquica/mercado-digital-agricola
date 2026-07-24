import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Envio(Base):
    __tablename__ = "envios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    estado = Column(String, default="pendiente")
    transportista = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)