import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey, Float, Boolean
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
    # Suma de PedidoItem.peso_kg de todas las líneas — null si CUALQUIER línea tiene peso
    # desconocido (su producto no tiene unidad base "kg"; ver PedidoItem.peso_kg más abajo). No se
    # aproxima ni se ignora la línea faltante: si falta un dato no podemos afirmar el total.
    peso_total_kg = Column(Numeric(10, 3), nullable=True)
    # True solo si peso_total_kg no es null Y superó el umbral configurado (ConfiguracionSistema,
    # clave "umbral_pedido_grande_kg") AL MOMENTO de crear el pedido — igual que costo_envio, un
    # cambio de umbral posterior no recalcula pedidos ya creados.
    requiere_vehiculo_grande = Column(Boolean, nullable=False, default=False)

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
    # Peso real de esta línea en kg (cantidad ya convertida a unidad base — ver crear_pedido en
    # main.py) — null si la unidad base del producto no es "kg". Por ahora "unidad"/"litro" no
    # tienen forma de estimar peso (RegistroProduccion.equivalencia_kg existe en Productores pero
    # no llega hasta Producto ni hasta acá); eso se resuelve en una sub-entrega aparte.
    peso_kg = Column(Numeric(10, 3), nullable=True)


class ConfiguracionSistema(Base):
    """Valores editables por el Admin sin tocar código — ubicación del almacén central (origen
    para calcular_costo_envio) y el umbral de peso desde el que un pedido requiere vehículo grande
    (ver crear_pedido), pero la clave es genérica por si más adelante hace falta configurar algo
    más acá. Una fila por clave (UNIQUE), no una tabla clave-valor gigante con una sola fila — así
    cada valor tiene su propia fecha_actualizacion.

    valor_latitud/valor_longitud y valor_numerico son mutuamente excluyentes según el tipo de
    configuración que sea esa clave (coordenadas vs. un número suelto) — ambos pares son nullable
    porque ninguna fila usa las tres cosas; forzar un 0.0 en las columnas que no aplican sería un
    dato inventado, no "sin valor"."""
    __tablename__ = "configuracion_sistema"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clave = Column(String, nullable=False, unique=True)
    valor_latitud = Column(Float, nullable=True)
    valor_longitud = Column(Float, nullable=True)
    # Config numérica genérica (ej. umbral_pedido_grande_kg) — no atada a coordenadas, para no
    # forzar futuras configuraciones que no sean una ubicación a vivir en valor_latitud.
    valor_numerico = Column(Float, nullable=True)
    fecha_actualizacion = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)