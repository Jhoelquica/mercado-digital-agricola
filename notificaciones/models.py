import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Notificacion(Base):
    __tablename__ = "notificaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    tipo = Column(String, nullable=False)       # pedido_creado, envio_actualizado
    mensaje = Column(String, nullable=False)
    canal = Column(String, default="email")     # simulado, no se envía de verdad
    fecha_envio = Column(DateTime, default=datetime.utcnow)