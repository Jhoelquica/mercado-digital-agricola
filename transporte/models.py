import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Numeric, Integer, Float
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Envio(Base):
    __tablename__ = "envios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repartidor_id = Column(UUID(as_uuid=True), nullable=True)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    estado = Column(String, default="pendiente")
    transportista = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    repartidor_latitud = Column(String, nullable=True)
    repartidor_longitud = Column(String, nullable=True)
    # False hasta que Pagos confirme que ya creó la Liquidacion del repartidor para este envío
    # (ver logica_liquidacion_repartidor.py) — permite encontrar después "cuáles quedaron sin
    # liquidar" (reintento_liquidacion_repartidor.py). Mismo patrón que certificacion_confirmada
    # de abajo, aplicado primero acá.
    liquidacion_confirmada = Column(Boolean, nullable=False, default=False)
    # False hasta que Certificación confirme que ya registró este envío (ver
    # logica_certificacion.py) — mismo patrón que liquidacion_confirmada de arriba, para que
    # reintento_certificacion.py pueda encontrar "cuáles quedaron sin certificar".
    certificacion_confirmada = Column(Boolean, nullable=False, default=False)
    # NO es un ForeignKey real (cross-servicio: Productor vive en Productores) — mismo patrón que
    # Producto.productor_id en productos/models.py. Null salvo cuando estado ==
    # "pendiente_entrega_directa": se puebla en proponer_envio_a_repartidor (ver
    # logica_repartidores.py) con el mismo productor_id que ya se resolvió para decidir esa
    # transición, así GET /envios/pendientes-entrega-directa puede filtrar server-side sin
    # repetir la resolución cruzada contra Productos en cada consulta.
    productor_id = Column(UUID(as_uuid=True), nullable=True)
    # Solo tiene sentido cuando estado == "pendiente_asignacion" — null en cualquier otro caso
    # (repartidor asignado, entrega directa, etc: ver proponer_envio_a_repartidor). Distingue las
    # dos causas posibles de esa espera para que el Admin pueda filtrar server-side (ver GET
    # /envios/pendientes-por-capacidad en main.py) los que de verdad necesitan un vehículo más
    # grande, no los que solo esperan que se libere cualquier repartidor:
    #   "sin_repartidor_disponible": nadie disponible en absoluto (con o sin filtro de capacidad).
    #   "sin_capacidad_suficiente": había candidatos disponibles, pero ninguno con capacidad
    #   suficiente para el peso del pedido.
    motivo_pendiente = Column(String, nullable=True)
    # Evidencia de entrega — código de 4 dígitos generado al crear el envío (ver
    # rabbitmq_consumer.py), que el comprador le dicta al repartidor en la puerta. Los otros 3
    # solo se llenan al confirmar la entrega vía POST /envios/{id}/entregar: la foto sube a MinIO
    # (mismo mecanismo que las fotos de producto), y la ubicación es una COPIA de
    # repartidor_latitud/longitud en ese instante — repartidor_latitud/longitud siguen
    # actualizándose después (el repartidor sigue moviéndose), así que sin esta copia se perdería
    # la posición real del momento de la entrega.
    pin_entrega = Column(String, nullable=True)
    foto_entrega_url = Column(String, nullable=True)
    entrega_latitud = Column(String, nullable=True)
    entrega_longitud = Column(String, nullable=True)
    # Intentos fallidos con el PIN (ver confirmar_entrega en main.py): tras MAX_INTENTOS_PIN el envío se
    # bloquea unos minutos, para que probar los 10.000 códigos posibles no sea viable. Null = 0 / sin
    # bloqueo. Nullable a propósito: migraciones.py agrega solas las columnas nuevas que aceptan NULL.
    pin_intentos_fallidos = Column(Integer, nullable=True)
    pin_bloqueado_hasta = Column(DateTime, nullable=True)
    # ---- Compradores lejanos (decisiones del usuario 2026-10-03) ----
    # "local", "agencia" (el repartidor lo deja en una agencia de carga en Huamanga; el comprador recoge en su ciudad y
    # confirma) o "camion" (viaje compartido; se descarga recién con el saldo pagado).
    modalidad_envio = Column(String, nullable=True)
    destino_ciudad = Column(String, nullable=True)
    sacos = Column(Integer, nullable=True)
    viaje_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    saldo_pagado = Column(Boolean, nullable=True)
    agencia_nombre = Column(String, nullable=True)
    numero_guia = Column(String, nullable=True)
    foto_guia_url = Column(String, nullable=True)
    fecha_despacho = Column(DateTime, nullable=True)
    fecha_recibido = Column(DateTime, nullable=True)


class Viaje(Base):
    """Un viaje compartido de camión a una ciudad lejana: junta envíos de varios compradores (ver logica_viajes.py)."""
    __tablename__ = "viajes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    destino_lejano_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    destino_ciudad = Column(String, nullable=True)
    capacidad_sacos = Column(Integer, nullable=False)
    sacos_para_salir = Column(Integer, nullable=False)
    estado = Column(String, nullable=False, default="abierto")  # abierto, listo, en_ruta, llegado, completado
    transportista_id = Column(UUID(as_uuid=True), nullable=True)  # Repartidor con documentos de carga verificados
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    fecha_salida = Column(DateTime, nullable=True)
    fecha_llegada = Column(DateTime, nullable=True)
    fecha_cierre = Column(DateTime, nullable=True)


class Repartidor(Base):
    __tablename__ = "repartidores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    nombre = Column(String, nullable=False)
    dni = Column(String, nullable=False)
    estado_disponibilidad = Column(String, default="disponible")  # disponible, ocupado, desconectado
    fecha_registro = Column(DateTime, default=datetime.utcnow)
    # Obligatorios para repartidores NUEVOS (ver RepartidorCrear/crear_repartidor en main.py) —
    # nullable acá solo porque los repartidores YA registrados antes de este campo no lo tienen
    # todavía. Pensados para el matching por capacidad de una sub-entrega siguiente.
    tipo_vehiculo = Column(String, nullable=True)  # "moto", "auto", "camioneta", "camion"
    # AUTODECLARADA por el repartidor al registrarse, sin ninguna verificación (no hay báscula ni
    # revisión de Admin de por medio) — limitación conocida y aceptada, no un descuido: el
    # registro es autoservicio de punta a punta (ver informe de la sub-entrega anterior), y este
    # dato sigue ese mismo criterio.
    capacidad_maxima_kg = Column(Numeric(10, 2), nullable=True)
    # ---- Transportista de carga (viajes largos en camión; decisión del usuario 2026-10-03) ----
    # Puede ser un camión de ChakraShop o de un tercero: sube su licencia, la tarjeta de propiedad del vehículo y el
    # SOAT (bucket PRIVADO transporte-documentos) y el admin los revisa. Solo uno "verificado" hace viajes.
    transportista_carga = Column(Boolean, nullable=True)
    placa = Column(String, nullable=True)
    licencia_numero = Column(String, nullable=True)
    doc_licencia_objeto = Column(String, nullable=True)
    doc_licencia_tipo = Column(String, nullable=True)
    doc_tarjeta_objeto = Column(String, nullable=True)
    doc_tarjeta_tipo = Column(String, nullable=True)
    doc_soat_objeto = Column(String, nullable=True)
    doc_soat_tipo = Column(String, nullable=True)
    verificacion_estado = Column(String, nullable=True)  # pendiente, verificado, rechazado
    verificacion_motivo = Column(String, nullable=True)
    fecha_documentos = Column(DateTime, nullable=True)
    verificado_por = Column(UUID(as_uuid=True), nullable=True)
    fecha_verificacion = Column(DateTime, nullable=True)


class EnvioRechazo(Base):
    """Historial completo de rechazos por envío — a diferencia del excluir_id puntual que ya
    existía (que solo recordaba el último repartidor dentro de una misma llamada), esta tabla
    persiste TODOS los rechazos, así que un envío rechazado por varios repartidores en secuencia
    nunca vuelve a ofrecerse a ninguno de ellos, sin importar cuánto tiempo pase entre intentos
    ni qué disparador (inmediato, periódico) sea el que reintente. Tabla nueva — no requiere
    ALTER TABLE, se crea sola con Base.metadata.create_all()."""
    __tablename__ = "envio_rechazos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    envio_id = Column(UUID(as_uuid=True), ForeignKey("envios.id"), nullable=False)
    repartidor_id = Column(UUID(as_uuid=True), ForeignKey("repartidores.id"), nullable=False)
    fecha_rechazo = Column(DateTime, default=datetime.utcnow)




class Recojo(Base):
    """El tramo chacra -> almacén de una cosecha publicada (ver logica_recojos.py). modalidad "repartidor": se le
    propone a un repartidor y lo cobra; "productor": la lleva el agricultor (fecha_estimada), sin costo."""
    __tablename__ = "recojos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    producto_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    productor_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    # El usuario del agricultor (quien lo pidió): a él le llegan los avisos de cómo va su recojo.
    productor_usuario_id = Column(UUID(as_uuid=True), nullable=True)
    producto_nombre = Column(String, nullable=True)
    modalidad = Column(String, nullable=False)  # repartidor | productor
    # repartidor: pendiente_asignacion -> propuesto -> asignado -> recogido -> en_almacen -> completado
    # productor: en_camino -> completado. Cualquiera: cancelado.
    estado = Column(String, nullable=False)
    latitud = Column(Float, nullable=True)   # dónde recoger (por defecto, la chacra)
    longitud = Column(Float, nullable=True)
    referencia = Column(String, nullable=True)  # cómo llegar ("al lado del puente")
    telefono = Column(String, nullable=True)    # el que deja el agricultor para este recojo
    fecha_estimada = Column(DateTime, nullable=True)  # productor: cuándo la lleva
    peso_kg = Column(Float, nullable=True)
    distancia_km = Column(Float, nullable=True)
    costo = Column(Numeric(10, 2), nullable=True)
    repartidor_id = Column(UUID(as_uuid=True), nullable=True)
    motivo_cancelacion = Column(String, nullable=True)
    liquidacion_confirmada = Column(Boolean, nullable=False, default=False)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    fecha_aceptado = Column(DateTime, nullable=True)
    fecha_recogido = Column(DateTime, nullable=True)
    fecha_en_almacen = Column(DateTime, nullable=True)
    fecha_completado = Column(DateTime, nullable=True)


class RecojoRechazo(Base):
    """Quién ya rechazó un recojo: no se le vuelve a ofrecer (mismo criterio que EnvioRechazo)."""
    __tablename__ = "recojo_rechazos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recojo_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    repartidor_id = Column(UUID(as_uuid=True), nullable=False)
    fecha = Column(DateTime, default=datetime.utcnow)
