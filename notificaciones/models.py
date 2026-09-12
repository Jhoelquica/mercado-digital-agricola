import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Notificacion(Base):
    __tablename__ = "notificaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    # Una notificación cuelga de un pedido O de un producto, nunca de los dos a la vez — depende
    # del tipo (pedido_creado/envio_actualizado siguen usando pedido_id; producto_disponible, que
    # nace de una Reserva y no de un Pedido, usa producto_id con pedido_id en None). No hay
    # CHECK constraint que lo fuerce a nivel de base de datos: cada evento que llega acá ya trae
    # exactamente uno de los dos (ver rabbitmq_consumer._procesar_mensaje), así que alcanza con
    # la garantía de la aplicación.
    pedido_id = Column(UUID(as_uuid=True), nullable=True)
    producto_id = Column(UUID(as_uuid=True), nullable=True)
    tipo = Column(String, nullable=False)       # pedido_creado, envio_actualizado, producto_disponible
    mensaje = Column(String, nullable=False)
    canal = Column(String, default="email")     # simulado, no se envía de verdad
    fecha_envio = Column(DateTime, default=datetime.utcnow)