import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Bloque(Base):
    __tablename__ = "bloques"
    # Un solo bloque por posición dentro de la cadena de cada producto: sin esto, dos escrituras
    # simultáneas dejaban dos bloques con el mismo índice y verificar_cadena marcaba la cadena como
    # alterada. En una base ya creada la restricción la agrega migraciones.py al arrancar.
    __table_args__ = (UniqueConstraint("producto_id", "indice", name="uq_bloque_producto_indice"),)

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