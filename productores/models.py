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

    # Costos de siembra (semillas/insumos, conocidos de entrada) vs. el costo "final" que solo
    # se conoce con certeza al cosechar (mano de obra acumulada) — por eso mano_obra arranca en 0
    # y se completa recién en completar-cosecha, no en la creación.
    costo_semillas = Column(Numeric(10, 2), nullable=False, default=0)
    costo_mano_obra = Column(Numeric(10, 2), nullable=False, default=0)
    costo_insumos = Column(Numeric(10, 2), nullable=False, default=0)
    # Ya no se pide ni se usa (se quitó del formulario de completar cosecha): se conserva solo para no tocar las
    # bases ya creadas. Las filas nuevas quedan en 0.
    costo_envio = Column(Numeric(10, 2), nullable=False, default=0)

    cantidad_cosechada = Column(Numeric(10, 2), nullable=True)
    unidad_medida = Column(String, nullable=False, default="kg")
    # "1 unidad de unidad_medida = equivalencia_kg kilogramos" — obligatorio solo si
    # unidad_medida != "kg" (validado en main.py, no acá).
    equivalencia_kg = Column(Numeric(10, 4), nullable=True)

    fecha_siembra = Column(DateTime, nullable=False)
    fecha_cosecha_estimada = Column(DateTime, nullable=False)
    fecha_cosecha_real = Column(DateTime, nullable=True)

    # Máquina de estados: planificado -> cosechado. Al completar la cosecha (con su foto) el producto nace como
    # borrador en el catálogo, sin pasar por nadie: el rol Verificador se retiró (presentación del 2026-10-01) y su
    # lugar lo ocupan la evidencia en origen de abajo y la pesada en el almacén. "pendiente_verificacion", "aprobado"
    # y "rechazado" quedan solo en registros de antes: uno "aprobado" ya tiene su producto, y uno
    # "pendiente_verificacion" o "rechazado" el agricultor lo completa con foto, igual que uno planificado.
    estado = Column(String, nullable=False, default="planificado")  # planificado | cosechado | (antes: pendiente_verificacion | aprobado | rechazado)
    fecha_registro = Column(DateTime, default=datetime.utcnow)

    # Evidencia en origen. Dónde estaba el agricultor (GPS del navegador) al registrar la siembra y al completar la
    # cosecha, y a cuántos km quedó de su chacra: si quedó fuera del radio, se permite igual y queda como advertencia
    # en la cadena (el GPS falla seguido en el campo). La foto de la cosecha es obligatoria y su huella SHA-256 se sella
    # en la cadena de Certificación. Columnas opcionales: en una base ya creada las agrega migraciones.py.
    siembra_latitud = Column(Float, nullable=True)
    siembra_longitud = Column(Float, nullable=True)
    siembra_distancia_km = Column(Float, nullable=True)
    cosecha_latitud = Column(Float, nullable=True)
    cosecha_longitud = Column(Float, nullable=True)
    cosecha_distancia_km = Column(Float, nullable=True)
    foto_cosecha_url = Column(String, nullable=True)
    foto_cosecha_sha256 = Column(String, nullable=True)
    foto_cosecha_fecha = Column(DateTime, nullable=True)
    # Cómo se obtiene este alimento y sus etapas (ver plantillas_produccion.py): la clave del alimento ("papa",
    # "huevo", "generica_temporada"...) y su tipo (temporada | frutal | crianza | elaboracion). None en los registros
    # de antes (decisión del usuario 2026-10-07: esos se quedan con sus 3 costos). Con plantilla, costo_semillas,
    # costo_insumos y costo_mano_obra son la suma de sus gastos (GastoProduccion) por rubro.
    plantilla = Column(String, nullable=True)
    tipo_produccion = Column(String, nullable=True)


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



class GastoProduccion(Base):
    """Un gasto de una siembra (o de un periodo de crianza, o de una elaboración) en una de sus etapas: "Aporque: 4
    jornales × S/ 50", "Siembra: semilla S/ 600". Se anota cuando ocurre, no solo al sembrar y al cosechar."""
    __tablename__ = "gastos_produccion"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registro_id = Column(UUID(as_uuid=True), ForeignKey("registros_produccion.id"), nullable=False, index=True)
    etapa = Column(String, nullable=False)
    rubro = Column(String, nullable=False)
    descripcion = Column(String, nullable=True)
    cantidad = Column(Numeric(12, 2), nullable=True)
    unidad = Column(String, nullable=True)
    precio_unitario = Column(Numeric(12, 2), nullable=True)
    # Mano de obra como se cuenta en el campo (validación con usuarios del 2026-10-14, A04): cuántos peones y cuántos
    # días (medio día = 0.5); los jornales (cantidad) son peones × días. Nulos en los gastos de antes, anotados en jornales.
    peones = Column(Integer, nullable=True)
    dias = Column(Numeric(6, 2), nullable=True)
    monto = Column(Numeric(12, 2), nullable=False)
    fecha = Column(DateTime, nullable=False, default=datetime.utcnow)
    fecha_registro = Column(DateTime, nullable=False, default=datetime.utcnow)
