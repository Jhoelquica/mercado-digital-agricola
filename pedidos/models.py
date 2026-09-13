import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from database import Base

class Pedido(Base):
    __tablename__ = "pedidos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    comprador_nombre = Column(String, nullable=False)
    comprador_telefono = Column(String)
    estado = Column(String, default="pendiente")
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    items = relationship("PedidoItem", backref="pedido")
    # Antes eran String y opcionales (nullable=True) — ahora son obligatorios y numéricos: el
    # destino se valida contra la región de Ayacucho antes de crear el pedido (ver crear_pedido en
    # main.py), lo cual requiere poder compararlos como números, no como texto. Mismo nombre de
    # columna de siempre (no se renombra): tanto el frontend (Api.pedidos.crear) como el servicio
    # de Transporte (obtener_ruta, cálculo de distancia) ya leen/escriben destino_latitud/
    # destino_longitud tal cual — cambiar el nombre habría roto ambos sin necesidad.
    destino_latitud = Column(Float, nullable=False)
    destino_longitud = Column(Float, nullable=False)
    # Calculado y guardado al crear el pedido (ver calcular_costo_envio en main.py) — no se
    # recalcula después, así que un pedido conserva el costo que tenía al momento de crearse
    # aunque el Admin cambie la ubicación del almacén más tarde.
    costo_envio = Column(Numeric(10, 2), nullable=False, default=0)

class PedidoItem(Base):
    __tablename__ = "pedido_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), ForeignKey("pedidos.id"), nullable=False)
    producto_id = Column(UUID(as_uuid=True), nullable=False)
    # cantidad está siempre en la unidad en que compró el comprador (unidad si no es None, la
    # unidad base del producto si lo es) — NO en la unidad base convertida. La conversión a base
    # (cantidad * factor_a_base) se usa solo al validar stock y al llamar a descontar_stock en
    # Productos (ver crear_pedido en main.py); acá se guarda tal cual la eligió el comprador, para
    # que el historial del pedido tenga sentido ("3 sacos", no "30 kg").
    cantidad = Column(Integer, nullable=False)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    # None = compró en la unidad base del producto (comportamiento de siempre, sin cambio). Un
    # valor (ej. "saco") = compró en esa unidad alternativa — ver Producto.unidades_alternativas
    # en Productos, resuelto contra GET /productos/{id} al crear el pedido.
    unidad = Column(String, nullable=True)


class ConfiguracionSistema(Base):
    """Valores editables por el Admin sin tocar código — hoy solo la ubicación del almacén
    central (origen para calcular_costo_envio en main.py), pero la clave es genérica por si más
    adelante hace falta configurar algo más acá. Una fila por clave (UNIQUE), no una tabla
    clave-valor gigante con una sola fila — así cada valor tiene su propia fecha_actualizacion."""
    __tablename__ = "configuracion_sistema"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clave = Column(String, nullable=False, unique=True)
    valor_latitud = Column(Float, nullable=False)
    valor_longitud = Column(Float, nullable=False)
    fecha_actualizacion = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)