import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base
from sqlalchemy import Column, String, Numeric, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship

class Producto(Base):
    __tablename__ = "productos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    productor_id = Column(UUID(as_uuid=True), nullable=False)
    productor_nombre = Column(String, nullable=False)  # copia replicada
    nombre = Column(String, nullable=False)
    imagen_url = Column(String, nullable=True)
    categoria = Column(String)
    # Nullable: un producto en estado "borrador" puede no tener precio todavía (ver estado).
    precio = Column(Numeric(10, 2), nullable=True)
    stock = Column(Integer, default=0)
    unidad_medida = Column(String, default="kg")
    fecha_publicacion = Column(DateTime, default=datetime.utcnow)
    # borrador: recién creado (a mano vía POST /productos, o automático vía
    # /productos/interno/crear-desde-cosecha), no aparece en GET /productos hasta que el
    # productor lo complete y publique (PATCH /productos/{id}/publicar). publicado: visible en
    # el catálogo. Mismo patrón de nomenclatura que pagos.estado / transporte Envio.estado.
    estado = Column(String, nullable=False, default="borrador")  # borrador | publicado
    # NO es un ForeignKey real a pesar del nombre: RegistroProduccion vive en la base de datos
    # del servicio Productores, una BD completamente distinta — mismo caso que
    # HistorialVerificacionCosecha.verificador_id en ese servicio. Nulo para productos creados
    # a mano (el flujo de siempre); solo se llena cuando el producto nace automáticamente a
    # partir de una cosecha aprobada por un Verificador.
    registro_produccion_id = Column(UUID(as_uuid=True), nullable=True)

class ProductoImagen(Base):
    __tablename__ = "producto_imagenes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    url = Column(String, nullable=False)
    orden = Column(Integer, default=0)

    producto = relationship("Producto", backref="imagenes")

class Resena(Base):
    __tablename__ = "resenas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=False)
    usuario_nombre = Column(String, nullable=False)
    calificacion = Column(Integer, nullable=False)  # 1 a 5
    comentario = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

class BusquedaLog(Base):
    __tablename__ = "busqueda_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    termino = Column(String, nullable=False)
    fecha = Column(DateTime, default=datetime.utcnow)