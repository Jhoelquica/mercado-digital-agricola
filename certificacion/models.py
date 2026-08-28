import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Bloque(Base):
    __tablename__ = "bloques"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    verificador_id = Column(UUID(as_uuid=True), nullable=True)
    producto_id = Column(UUID(as_uuid=True), nullable=False)
    indice = Column(Integer, nullable=False)  # posición dentro de la cadena de ESE producto
    evento = Column(String, nullable=False)   # cosecha_registrada, certificado_productor, verificado_punto_venta
    datos = Column(String, nullable=True)     # descripción libre del evento
    fecha = Column(DateTime, default=datetime.utcnow)
    hash_anterior = Column(String, nullable=False)
    hash_actual = Column(String, nullable=False)

class Verificador(Base):
    __tablename__ = "verificadores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    nombre = Column(String, nullable=False)
    credencial = Column(String, nullable=False)  # ej. número de colegiatura o código interno
    fecha_registro = Column(DateTime, default=datetime.utcnow)

class CertificadoPedido(Base):
    __tablename__ = "certificados_pedido"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    merkle_root = Column(String, nullable=False)
    detalle = Column(String, nullable=False)  # JSON serializado: [{"producto_id": ..., "hash": ...}, ...]
    fecha_generado = Column(DateTime, default=datetime.utcnow)