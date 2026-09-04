import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey
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
    repartidor_latitud = Column(String, nullable=True)
    repartidor_longitud = Column(String, nullable=True)

class Repartidor(Base):
    __tablename__ = "repartidores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    nombre = Column(String, nullable=False)
    dni = Column(String, nullable=False)
    estado_disponibilidad = Column(String, default="disponible")  # disponible, ocupado, desconectado
    fecha_registro = Column(DateTime, default=datetime.utcnow)


class EnvioRechazo(Base):
    """Historial completo de rechazos por envío — a diferencia del excluir_id puntual que ya
    existía (que solo recordaba el último repartidor dentro de una misma llamada), esta tabla
    persiste TODOS los rechazos, así que un envío rechazado por varios repartidores en secuencia
    nunca vuelve a ofrecerse a ninguno de ellos, sin importar cuánto tiempo pase entre intentos
    ni qué disparador (inmediato, periódico) sea el que reintente. Tabla nueva — no requiere
    ALTER TABLE, se crea sola con Base.metadata.create_all()."""
    __tablename__ = "envio_rechazos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    envio_id = Column(UUID(as_uuid=True), ForeignKey("envios.id"), nullable=False)
    repartidor_id = Column(UUID(as_uuid=True), ForeignKey("repartidores.id"), nullable=False)
    fecha_rechazo = Column(DateTime, default=datetime.utcnow)

