import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey, Float, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from database import Base

class EventoSaliente(Base):
    """Bandeja de salida (patrón outbox): el evento pedido_creado se guarda en la MISMA transacción que el pedido y se
    publica después. Si RabbitMQ no responde, el pedido queda creado igual y reintento_eventos.py lo reenvía; antes el
    pedido quedaba guardado pero Pagos nunca se enteraba (y el comprador no podía pagarlo)."""
    __tablename__ = "eventos_salientes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tipo = Column(String, nullable=False)
    cuerpo = Column(Text, nullable=False)  # el evento en JSON, tal cual se publica
    fecha_creacion = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    fecha_envio = Column(DateTime, nullable=True)  # NULL = todavía no llegó a RabbitMQ
    intentos = Column(Integer, nullable=False, default=0)


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
    # Parte de costo_envio que se cobró por los kg que pasan el umbral (compra por mayor; ver calcular_recargo_por_peso
    # en main.py). costo_envio ya la incluye: esto es solo para mostrar el desglose. Null en los pedidos de antes.
    recargo_envio_peso = Column(Numeric(10, 2), nullable=True)
    # ---- Compradores lejanos (decisiones del usuario, 2026-10-03; ver DestinoLejano) ----
    # "local" (reparto en Ayacucho, como siempre), "agencia" (el repartidor lo deja en una agencia de carga en Huamanga
    # y el flete de la agencia se paga en destino) o "camion" (viaje compartido de un camión, con adelanto).
    # Nullable para que la migración la agregue a la tabla ya creada: NULL (pedidos de antes) = "local".
    modalidad_envio = Column(String, nullable=True, default="local")
    destino_lejano_id = Column(UUID(as_uuid=True), nullable=True)
    destino_ciudad = Column(String, nullable=True)  # copia del nombre: el destino se puede editar después
    # Quien recibe en la otra ciudad, con su DNI consultado en RENIEC (la agencia o el chofer entregan contra ese DNI).
    receptor_dni = Column(String(8), nullable=True)
    receptor_nombre = Column(String, nullable=True)
    sacos = Column(Integer, nullable=True)  # camión: peso / 50 kg, redondeado hacia arriba
    flete_destino_estimado = Column(Numeric(10, 2), nullable=True)  # agencia: aproximado, se paga en destino
    monto_adelanto = Column(Numeric(10, 2), nullable=True)  # camión: lo que se paga al pedir (el resto al llegar)
    porcentaje_adelanto = Column(Integer, nullable=True)

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


class DestinoLejano(Base):
    """Una ciudad fuera de Ayacucho a la que se vende (lista que edita el admin: solo donde sea rentable llegar). Un punto
    de entrega dentro de `radio_km` de la ciudad es de esa ciudad. Cada una dice si se llega por agencia de carga (el
    flete lo cobra la agencia en destino; aquí solo va una estimación) y/o en camión compartido (precio por saco, mínimo
    por comprador, capacidad y porcentaje con el que sale). Los valores iniciales son de ejemplo: ver el informe de
    rentabilidad en la memoria del proyecto (viaje redondo Ayacucho-Lima ~S/ 2 400–2 900 en un camión de 5 t)."""
    __tablename__ = "destinos_lejanos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ciudad = Column(String, nullable=False)
    departamento = Column(String, nullable=True)
    latitud = Column(Float, nullable=False)
    longitud = Column(Float, nullable=False)
    radio_km = Column(Float, nullable=False, default=40)
    activo = Column(Boolean, nullable=False, default=True)
    agencia_disponible = Column(Boolean, nullable=False, default=True)
    agencia_nombre = Column(String, nullable=True)
    agencia_flete_kg = Column(Numeric(10, 2), nullable=True)
    agencia_dias = Column(Integer, nullable=True)
    camion_disponible = Column(Boolean, nullable=False, default=True)
    camion_precio_saco = Column(Numeric(10, 2), nullable=True)
    camion_minimo_sacos = Column(Integer, nullable=False, default=50)
    camion_capacidad_sacos = Column(Integer, nullable=False, default=100)
    camion_porcentaje_salida = Column(Integer, nullable=False, default=80)
    camion_dias = Column(Integer, nullable=True)
    nota = Column(String, nullable=True)
    fecha_actualizacion = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
