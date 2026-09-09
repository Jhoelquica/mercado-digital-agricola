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

class PedidoItem(Base):
    __tablename__ = "pedido_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), ForeignKey("pedidos.id"), nullable=False)
    producto_id = Column(UUID(as_uuid=True), nullable=False)
    cantidad = Column(Integer, nullable=False)
    precio_unitario = Column(Numeric(10, 2), nullable=False)