import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Numeric
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
    # False hasta que Pagos confirme que ya creó la Liquidacion del repartidor para este envío
    # (ver logica_liquidacion_repartidor.py) — permite encontrar después "cuáles quedaron sin
    # liquidar" (reintento_liquidacion_repartidor.py). Mismo patrón que certificacion_confirmada
    # de abajo, aplicado primero acá.
    liquidacion_confirmada = Column(Boolean, nullable=False, default=False)
    # False hasta que Certificación confirme que ya registró este envío (ver
    # logica_certificacion.py) — mismo patrón que liquidacion_confirmada de arriba, para que
    # reintento_certificacion.py pueda encontrar "cuáles quedaron sin certificar".
    certificacion_confirmada = Column(Boolean, nullable=False, default=False)

class Repartidor(Base):
    __tablename__ = "repartidores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    nombre = Column(String, nullable=False)
    dni = Column(String, nullable=False)
    estado_disponibilidad = Column(String, default="disponible")  # disponible, ocupado, desconectado
    fecha_registro = Column(DateTime, default=datetime.utcnow)
    # Obligatorios para repartidores NUEVOS (ver RepartidorCrear/crear_repartidor en main.py) —
    # nullable acá solo porque los repartidores YA registrados antes de este campo no lo tienen
    # todavía. Pensados para el matching por capacidad de una sub-entrega siguiente.
    tipo_vehiculo = Column(String, nullable=True)  # "moto", "auto", "camioneta", "camion"
    # AUTODECLARADA por el repartidor al registrarse, sin ninguna verificación (no hay báscula ni
    # revisión de Admin de por medio) — limitación conocida y aceptada, no un descuido: el
    # registro es autoservicio de punta a punta (ver informe de la sub-entrega anterior), y este
    # dato sigue ese mismo criterio.
    capacidad_maxima_kg = Column(Numeric(10, 2), nullable=True)


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

