import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Pago(Base):
    __tablename__ = "pagos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    monto = Column(Numeric(10, 2), nullable=False)
    estado = Column(String, default="pendiente")  # pendiente, aprobado, rechazado
    metodo = Column(String, default="tarjeta")     # tarjeta, yape
    culqi_charge_id = Column(String, nullable=True)  # el id que devuelve Culqi
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    items = Column(String, nullable=True)  # JSON serializado: [{"producto_id": ..., "cantidad": ...}]