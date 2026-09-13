import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base
from sqlalchemy import Column, String, Numeric, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

class Producto(Base):
    __tablename__ = "productos"
    __table_args__ = (
        # Un solo producto por cosecha aprobada — evita duplicados si Productores reintenta
        # crear_producto_desde_cosecha (ej. tras un timeout de red que sí llegó a completarse
        # del lado de Productos). NULL no choca contra NULL en Postgres, así que los productos
        # creados a mano (sin registro_produccion_id) conviven sin problema entre sí.
        UniqueConstraint("registro_produccion_id", name="uq_producto_registro_produccion"),
    )

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
    # productor lo complete y publique (PATCH /productos/{id}/publicar). en_transito: publicado
    # por el productor pero todavía viajando al almacén central — visible en el catálogo, pero
    # solo reservable (POST /productos/{id}/reservar), no comprable. disponible (antes se
    # llamaba "publicado"): un Admin confirmó que llegó al almacén (PATCH
    # /productos/{id}/confirmar-llegada-almacen) — recién ahí es comprable de verdad. Mismo
    # patrón de nomenclatura que pagos.estado / transporte Envio.estado.
    estado = Column(String, nullable=False, default="borrador")  # borrador | en_transito | disponible
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

class Reserva(Base):
    """Registro de interés de un comprador en un producto "en_transito" — sin monto ni estado de
    pago, a propósito: no es una compra, solo sirve para saber a quién avisar (ver
    GET /productos/{id}/reservas) cuando el Admin confirme la llegada al almacén y el producto
    pase a "disponible" (ese aviso todavía no está conectado con Notificaciones, ver el TODO en
    confirmar_llegada_almacen en main.py)."""
    __tablename__ = "reservas"
    __table_args__ = (
        # Mismo criterio que uq_producto_registro_produccion: evitar duplicados es un constraint
        # de base de datos, no solo el SELECT-antes-de-insertar del endpoint (que por sí solo
        # sería vulnerable a una carrera entre el chequeo y el insert).
        UniqueConstraint("producto_id", "comprador_id", name="uq_reserva_producto_comprador"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # FK real (a diferencia de comprador_id): Reserva vive en la misma base que Producto.
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    # NO es un ForeignKey real: el comprador es un Usuario del servicio Usuarios, otra base
    # completamente distinta — mismo caso que Producto.productor_id.
    comprador_id = Column(UUID(as_uuid=True), nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

class UnidadAlternativa(Base):
    """Una unidad adicional en la que se puede comprar un Producto, con su propio precio — no
    reemplaza ni modifica precio/unidad_medida del producto (la unidad "base"), los complementa.
    Ej.: un producto base en "kg" a S/5/kg puede venderse TAMBIÉN por "saco" a S/45, con
    factor_a_base=10 (1 saco = 10 kg)."""
    __tablename__ = "unidades_alternativas"
    __table_args__ = (
        # Un producto no puede tener dos filas para la misma unidad alternativa — mismo criterio
        # que uq_producto_registro_produccion: constraint de base de datos, no solo el chequeo de
        # aplicación (que por sí solo sería vulnerable a una carrera entre el chequeo y el insert).
        UniqueConstraint("producto_id", "unidad", name="uq_unidad_alternativa_producto_unidad"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # FK real: UnidadAlternativa vive en la misma base que Producto.
    producto_id = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    unidad = Column(String, nullable=False)
    precio = Column(Numeric(10, 2), nullable=False)
    # "1 unidad de ESTA unidad alternativa = factor_a_base unidades de Producto.unidad_medida" —
    # mismo espíritu que RegistroProduccion.equivalencia_kg en Productores, pero generalizado a
    # cualquier unidad base (no siempre kg) y con precio propio, no solo para un cálculo interno.
    factor_a_base = Column(Numeric(10, 4), nullable=False)

    producto = relationship("Producto", backref="unidades_alternativas")

class BusquedaLog(Base):
    __tablename__ = "busqueda_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    termino = Column(String, nullable=False)
    fecha = Column(DateTime, default=datetime.utcnow)