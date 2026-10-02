import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Pago(Base):
    __tablename__ = "pagos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    monto = Column(Numeric(10, 2), nullable=False)
    estado = Column(String, default="pendiente")  # pendiente, en_revision (transferencia), aprobado, rechazado
    metodo = Column(String, default="tarjeta")     # tarjeta, yape, transferencia
    culqi_charge_id = Column(String, nullable=True)  # el id que devuelve Culqi
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    # JSON serializado: [{"producto_id", "cantidad", "unidad", "cantidad_base", "precio_unitario"}]. cantidad es lo
    # que compró el comprador (3 sacos) y cantidad_base lo que se descontó del stock (30 kg); los pagos creados
    # antes de que el evento trajera cantidad_base solo tienen cantidad.
    items = Column(String, nullable=True)
    # Transferencia bancaria (la alternativa a Culqi para montos que Yape no permite): el comprador sube la constancia
    # y el número de operación, el pago queda "en_revision" y un admin lo aprueba o lo rechaza. Columnas opcionales:
    # en una base ya creada las agrega migraciones.py.
    numero_operacion = Column(String, nullable=True)
    constancia_objeto = Column(String, nullable=True)  # nombre del archivo en el bucket privado pagos-constancias
    constancia_tipo = Column(String, nullable=True)
    fecha_constancia = Column(DateTime, nullable=True)
    motivo_rechazo = Column(String, nullable=True)
    revisado_por = Column(UUID(as_uuid=True), nullable=True)  # el admin que la aprobó o rechazó
    fecha_revision = Column(DateTime, nullable=True)

class Liquidacion(Base):
    """Cuánto le corresponde a un beneficiario (productor hoy, repartidor en la sub-entrega
    siguiente) por su participación en un pedido ya pagado — una fila por beneficiario distinto,
    no una por pedido (ver crear_liquidaciones en main.py: un pedido con items de 2 productores
    genera 2 filas). Se crea en el mismo momento y la misma transacción en que Pagos confirma el
    cargo; el pago efectivo hacia el beneficiario (y quién lo dispara) es la sub-entrega
    siguiente — acá solo queda el registro en "pendiente"."""
    __tablename__ = "liquidaciones"
    __table_args__ = (
        # Un mismo pedido no liquida dos veces al mismo beneficiario — mismo criterio que
        # uq_producto_registro_produccion en Productos: constraint de base de datos, no solo
        # lógica de aplicación (fácil de saltarse en un reintento del webhook/cliente).
        UniqueConstraint("pedido_id", "beneficiario_tipo", "beneficiario_id", name="uq_liquidacion_pedido_beneficiario"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    beneficiario_tipo = Column(String, nullable=False)  # productor | repartidor
    # NO es un ForeignKey real: el productor vive en Productos/Productores y el repartidor en
    # Transporte — bases de datos completamente distintas a esta (mismo caso que Pago.pedido_id).
    beneficiario_id = Column(UUID(as_uuid=True), nullable=False)
    monto_bruto = Column(Numeric(10, 2), nullable=False)
    comision_plataforma = Column(Numeric(10, 2), nullable=False)
    monto_neto = Column(Numeric(10, 2), nullable=False)
    estado = Column(String, nullable=False, default="pendiente")  # pendiente | pagado (sub-entrega siguiente)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    # Calculada en la aplicación como fecha_creacion + 24h (ver crear_liquidaciones), no con un
    # default de columna propio: dos defaults independientes llamarían datetime.utcnow() por
    # separado y podrían desincronizarse por microsegundos del valor real de fecha_creacion.
    fecha_limite = Column(DateTime, nullable=False)
    fecha_pago = Column(DateTime, nullable=True)