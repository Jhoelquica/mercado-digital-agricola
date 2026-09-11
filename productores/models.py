import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Numeric, Float, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Productor(Base):
    __tablename__ = "productores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=True, unique=True)
    nombre = Column(String, nullable=False)
    comunidad = Column(String)
    contacto = Column(String)
    ubicacion = Column(String, nullable=True)
    fecha_registro = Column(DateTime, default=datetime.utcnow)
    latitud = Column(String, nullable=True)
    longitud = Column(String, nullable=True)


class Chacra(Base):
    """Parcela/finca que el productor registra y a la que asocia cada RegistroProduccion.
    Tabla nueva — la crea Base.metadata.create_all() al arrancar, igual que las demás
    (ver comentario en RegistroProduccion)."""
    __tablename__ = "chacras"
    __table_args__ = (
        # El código lo elige el productor y solo tiene que ser único DENTRO de su cuenta:
        # dos productores distintos pueden tener ambos, p. ej., "CH-01". Lo que no se repite
        # es la combinación (productor_id, codigo).
        UniqueConstraint("productor_id", "codigo", name="uq_chacra_productor_codigo"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    productor_id = Column(UUID(as_uuid=True), ForeignKey("productores.id"), nullable=False)
    codigo = Column(String, nullable=False)
    nombre = Column(String, nullable=True)  # nombre descriptivo opcional, ej. "Chacra del cerro"
    ubicacion_latitud = Column(Float, nullable=False)
    ubicacion_longitud = Column(Float, nullable=False)
    fecha_registro = Column(DateTime, default=datetime.utcnow)


class RegistroProduccion(Base):
    """Módulo 1 de Gestión Económica: una siembra planificada que eventualmente se cosecha.
    Tabla nueva — no requiere ALTER TABLE, Base.metadata.create_all() (ya se llama al arrancar
    este servicio) la crea sola la primera vez que no exista, igual que las demás tablas de
    este proyecto."""
    __tablename__ = "registros_produccion"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    productor_id = Column(UUID(as_uuid=True), ForeignKey("productores.id"), nullable=False)
    chacra_id = Column(UUID(as_uuid=True), ForeignKey("chacras.id"), nullable=False)

    cultivo = Column(String, nullable=False)
    numero_parcelas = Column(Integer, nullable=False)
    ubicacion_cosecha = Column(String, nullable=True)

    # Costos de siembra (semillas/insumos, conocidos de entrada) vs. costos "finales" que solo
    # se conocen con certeza al cosechar (mano de obra acumulada, envío) — por eso mano_obra y
    # envío arrancan en 0 y se completan recién en completar-cosecha, no en la creación.
    costo_semillas = Column(Numeric(10, 2), nullable=False, default=0)
    costo_mano_obra = Column(Numeric(10, 2), nullable=False, default=0)
    costo_insumos = Column(Numeric(10, 2), nullable=False, default=0)
    costo_envio = Column(Numeric(10, 2), nullable=False, default=0)

    cantidad_cosechada = Column(Numeric(10, 2), nullable=True)
    unidad_medida = Column(String, nullable=False, default="kg")
    # "1 unidad de unidad_medida = equivalencia_kg kilogramos" — obligatorio solo si
    # unidad_medida != "kg" (validado en main.py, no acá).
    equivalencia_kg = Column(Numeric(10, 4), nullable=True)

    fecha_siembra = Column(DateTime, nullable=False)
    fecha_cosecha_estimada = Column(DateTime, nullable=False)
    fecha_cosecha_real = Column(DateTime, nullable=True)

    # Máquina de estados (ampliada — ver HistorialVerificacionCosecha):
    #   planificado -> pendiente_verificacion -> aprobado
    #                                          -> rechazado -> pendiente_verificacion (reenvío)
    # "cosechado" sigue en el vocabulario (compatibilidad con datos/tests viejos) pero ya no
    # queda como valor de reposo: completar_cosecha() en main.py transiciona directo a
    # pendiente_verificacion en la misma llamada — desde la perspectiva del productor "marcar
    # cosechado" es un solo paso, no dos.
    estado = Column(String, nullable=False, default="planificado")  # planificado | cosechado | pendiente_verificacion | aprobado | rechazado
    fecha_registro = Column(DateTime, default=datetime.utcnow)


class HistorialVerificacionCosecha(Base):
    """Historial de decisiones del Verificador sobre una cosecha — calcado de EnvioRechazo en
    Transporte (transporte/models.py): guarda CADA decisión (no solo la última), así que un
    reenvío tras un rechazo no borra el rastro de por qué se rechazó la primera vez.

    verificador_id NO es un ForeignKey real a pesar del nombre: Verificador vive en la base de
    datos del servicio Certificación, una BD completamente distinta a la de este servicio — no
    hay integridad referencial posible entre bases de datos separadas. Mismo caso que
    Bloque.verificador_id en Certificación (certificacion/models.py) o Envio.pedido_id acá mismo
    (referencia a Pedidos). Se guarda el usuario_id del verificador autenticado (claim `sub` del
    JWT), no se busca su fila real de Verificador — requiere_rol("verificador") ya garantiza que
    ese usuario tiene el rol, sin necesidad de otra llamada a Certificación para confirmarlo."""
    __tablename__ = "historial_verificacion_cosecha"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registro_produccion_id = Column(UUID(as_uuid=True), ForeignKey("registros_produccion.id"), nullable=False)
    verificador_id = Column(UUID(as_uuid=True), nullable=False)
    accion = Column(String, nullable=False)  # aprobado | rechazado
    motivo = Column(String, nullable=True)  # obligatorio en la práctica solo para "rechazado" (validado en main.py)
    fecha = Column(DateTime, default=datetime.utcnow)
