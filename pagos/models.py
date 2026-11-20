import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, String, Numeric, DateTime, Integer, UniqueConstraint, Index, text
from sqlalchemy.dialects.postgresql import UUID
from comun.database import Base

class Pago(Base):
    __tablename__ = "pagos"
    # Un pago por pedido: los intentos fallidos (tarjeta rechazada, plazo vencido) reusan esta misma fila, que vuelve a
    # cobrarse. Dos entregas a la vez del mismo pedido_creado no crean dos. En una base de antes lo agrega
    # migraciones.asegurar_indice_unico_de_pagos (create_all no agrega índices a una tabla que ya existe).
    # Un cargo de Culqi nunca se asigna a dos pagos: los índices únicos del cargo (aquí y en IntentoPago) los agrega a una
    # base de antes migraciones.asegurar_indices_de_cargos.
    __table_args__ = (
        Index("uq_pagos_pedido_id", "pedido_id", unique=True),
        Index("uq_pagos_culqi_charge_id", "culqi_charge_id", unique=True,
              postgresql_where=text("culqi_charge_id IS NOT NULL")),
        Index("uq_pagos_culqi_charge_id_saldo", "culqi_charge_id_saldo", unique=True,
              postgresql_where=text("culqi_charge_id_saldo IS NOT NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    monto = Column(Numeric(10, 2), nullable=False)
    # pendiente, en_revision (transferencia), aprobado, rechazado; cancelado: el comprador lo canceló antes de pagar (ver
    # cancelar_pedido_sin_pagar en main.py), es final y ya no se cobra.
    estado = Column(String, default="pendiente")
    metodo = Column(String, default="tarjeta")     # tarjeta, yape, transferencia
    culqi_charge_id = Column(String, nullable=True)  # el id que devuelve Culqi
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    # JSON serializado: [{"producto_id", "cantidad", "unidad", "cantidad_base", "precio_unitario"}]. cantidad es lo
    # que compró el comprador (3 sacos) y cantidad_base lo que se descontó del stock (30 kg); los pagos creados
    # antes de que el evento trajera cantidad_base solo tienen cantidad.
    items = Column(String, nullable=True)
    # Transferencia bancaria (la alternativa a Culqi para montos que Yape no permite): el comprador sube la constancia
    # y el número de operación, el pago queda "en_revision" y un admin lo aprueba o lo rechaza. Columnas opcionales:
    # en una base ya creada las agrega migraciones.py.
    numero_operacion = Column(String, nullable=True)
    constancia_objeto = Column(String, nullable=True)  # nombre del archivo en el bucket privado pagos-constancias
    constancia_tipo = Column(String, nullable=True)
    fecha_constancia = Column(DateTime, nullable=True)
    motivo_rechazo = Column(String, nullable=True)
    revisado_por = Column(UUID(as_uuid=True), nullable=True)  # el admin que la aprobó o rechazó
    fecha_revision = Column(DateTime, nullable=True)
    # ---- Pedidos en camión (compradores lejanos, decisión del usuario 2026-10-03): adelanto al pedir, saldo al llegar ----
    # Con monto_adelanto, el primer cobro es ese adelanto (estado -> "adelanto_pagado": el pedido se confirma y sube al
    # camión) y el segundo es el saldo (estado -> "aprobado"). Sin él, un solo cobro por el total, como siempre.
    monto_adelanto = Column(Numeric(10, 2), nullable=True)
    fecha_adelanto = Column(DateTime, nullable=True)
    metodo_saldo = Column(String, nullable=True)
    culqi_charge_id_saldo = Column(String, nullable=True)
    fecha_saldo = Column(DateTime, nullable=True)
    # Qué parte está esperando la revisión de una transferencia: "total", "adelanto" o "saldo".
    parte_en_revision = Column(String, nullable=True)
    # Plazo para pagar: un pedido que nadie paga ya no retiene el stock para siempre (ver vencer_pagos_sin_pagar en
    # main.py). NULL en los pagos de antes, que no vencen solos.
    vence_en = Column(DateTime, nullable=True)
    vencido_en = Column(DateTime, nullable=True)
    # Cuándo lo canceló el comprador antes de pagar (estado "cancelado": el stock volvió en el acto).
    cancelado_en = Column(DateTime, nullable=True)
    # Devolución de un pedido pagado que se canceló (estado "reembolsado"): lo devuelto por Culqi, lo que queda por
    # devolver a mano (lo pagado por transferencia), los id de las devoluciones en Culqi (JSON) y el motivo.
    monto_reembolsado = Column(Numeric(10, 2), nullable=True)
    reembolso_manual = Column(Numeric(10, 2), nullable=True)
    reembolso_ids = Column(String, nullable=True)
    motivo_reembolso = Column(String, nullable=True)
    fecha_reembolso = Column(DateTime, nullable=True)
    # ---- Cobro y devolución en dos fases (ver IntentoPago y main.procesar_pago) ----
    # Mientras Culqi cobra, el pago está "procesando" (o "reembolso_procesando" mientras devuelve): ningún otro cobro, el
    # vencimiento ni la reposición de stock lo tocan. Aquí queda qué parte se cobra, de qué estado venía (al que vuelve
    # un saldo rechazado) y desde cuándo.
    parte_en_proceso = Column(String, nullable=True)
    estado_antes_de_procesar = Column(String, nullable=True)
    procesando_desde = Column(DateTime, nullable=True)


class IntentoPago(Base):
    """Cada llamada a Culqi, un cobro o una devolución, con una referencia propia (el id de esta fila) que viaja en la
    metadata y en la descripción. Se anota y se confirma ANTES de llamar a Culqi: si después el proceso muere o el commit
    del resultado falla, el intento queda "en_curso" y la conciliación averigua en Culqi qué pasó con esa referencia.
    También es el historial de intentos de cada pago."""
    __tablename__ = "intentos_pago"
    __table_args__ = (
        # Un solo intento en curso por pago: el UPDATE condicional del pago ya lo asegura; la base lo sostiene igual.
        Index("uq_intentos_pago_en_curso", "pago_id", unique=True,
              postgresql_where=text("tipo = 'cobro' AND estado = 'en_curso'")),
        # Un cargo se devuelve una sola vez: mientras su devolución está en curso o ya salió, no se puede empezar otra.
        Index("uq_intentos_pago_devolucion", "cargo_id", unique=True,
              postgresql_where=text("tipo = 'devolucion' AND estado IN ('en_curso', 'aprobado')")),
        # Un cargo, un solo cobro entre todos los pagos y sus partes (el adelanto de uno no puede ser el saldo de otro):
        # todo cargo asignado a un pago pasa por su intento.
        Index("uq_intentos_pago_cargo_cobro", "cargo_id", unique=True,
              postgresql_where=text("tipo = 'cobro' AND cargo_id IS NOT NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # la referencia propia que ve Culqi
    pago_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    pedido_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tipo = Column(String, nullable=False)  # cobro | devolucion
    parte = Column(String, nullable=False)  # total | adelanto | saldo
    monto = Column(Numeric(10, 2), nullable=False)
    metodo = Column(String, nullable=True)  # cobro: tarjeta | yape
    estado = Column(String, nullable=False, default="en_curso")  # en_curso | aprobado | rechazado
    estado_anterior = Column(String, nullable=True)  # el del pago al empezar
    cargo_id = Column(String, nullable=True)  # cobro: el cargo que creó Culqi; devolución: el cargo que se devuelve
    devolucion_id = Column(String, nullable=True)
    mensaje = Column(String, nullable=True)  # por qué se rechazó, o cómo se resolvió
    fecha_inicio = Column(DateTime, nullable=False, default=datetime.utcnow)
    fecha_fin = Column(DateTime, nullable=True)
    conciliaciones = Column(Integer, nullable=False, default=0)  # veces que la conciliación lo buscó en Culqi
    revision_manual = Column(Boolean, nullable=False, default=False)  # la conciliación no pudo: lo revisa una persona
    # Devolución: los céntimos que Culqi ya había devuelto de ese cargo al empezar (amount_refunded): la conciliación
    # compara lo acumulado (lo de antes + esta devolución), no solo esta.
    devuelto_antes = Column(Integer, nullable=True)
    # Lo resolvió a mano un admin desde el panel "Cobros por revisar en Culqi": quién, por qué y cuándo.
    resuelto_por = Column(UUID(as_uuid=True), nullable=True)
    motivo_resolucion = Column(String, nullable=True)
    fecha_resolucion = Column(DateTime, nullable=True)

class Liquidacion(Base):
    """Cuánto le corresponde a un beneficiario (productor hoy, repartidor en la sub-entrega
    siguiente) por su participación en un pedido ya pagado — una fila por beneficiario distinto,
    no una por pedido (ver crear_liquidaciones en main.py: un pedido con items de 2 productores
    genera 2 filas). Se crea en el mismo momento y la misma transacción en que Pagos confirma el
    cargo; el pago efectivo hacia el beneficiario (y quién lo dispara) es la sub-entrega
    siguiente — acá solo queda el registro en "pendiente"."""
    __tablename__ = "liquidaciones"
    __table_args__ = (
        # Un mismo pedido no liquida dos veces al mismo beneficiario — mismo criterio que
        # uq_producto_registro_produccion en Productos: constraint de base de datos, no solo
        # lógica de aplicación (fácil de saltarse en un reintento del webhook/cliente).
        UniqueConstraint("pedido_id", "beneficiario_tipo", "beneficiario_id", name="uq_liquidacion_pedido_beneficiario"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=False)
    beneficiario_tipo = Column(String, nullable=False)  # productor | repartidor
    # NO es un ForeignKey real: el productor vive en Productos/Productores y el repartidor en
    # Transporte — bases de datos completamente distintas a esta (mismo caso que Pago.pedido_id).
    beneficiario_id = Column(UUID(as_uuid=True), nullable=False)
    monto_bruto = Column(Numeric(10, 2), nullable=False)
    comision_plataforma = Column(Numeric(10, 2), nullable=False)
    monto_neto = Column(Numeric(10, 2), nullable=False)
    estado = Column(String, nullable=False, default="pendiente")  # pendiente | pagado (sub-entrega siguiente)
    # "recojo": lo que gana el repartidor por recoger una cosecha en la chacra (pedido_id lleva entonces el id del recojo).
    # NULL en las de siempre (la venta de un pedido o su envío).
    concepto = Column(String, nullable=True)
    # Lo que se le cobró al productor de esta venta por recojos de sus cosechas (ver DescuentoProductor): monto_neto ya
    # viene con el descuento hecho.
    descuento_recojo = Column(Numeric(10, 2), nullable=True)
    # El pago semanal (hecho a mano por el admin) que incluyó esta liquidación; ver PagoBeneficiario.
    pago_beneficiario_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    # Calculada en la aplicación como fecha_creacion + 24h (ver crear_liquidaciones), no con un
    # default de columna propio: dos defaults independientes llamarían datetime.utcnow() por
    # separado y podrían desincronizarse por microsegundos del valor real de fecha_creacion.
    fecha_limite = Column(DateTime, nullable=False)
    fecha_pago = Column(DateTime, nullable=True)


class DescuentoProductor(Base):
    """Lo que el agricultor debe por el recojo de una cosecha en su chacra (decisión del usuario 2026-10-03): ChakraShop
    le paga el recojo al repartidor y se lo descuenta al productor de sus siguientes liquidaciones, de a poco, sin dejar
    ninguna en negativo (ver logica_liquidaciones.crear_liquidaciones)."""
    __tablename__ = "descuentos_productor"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    productor_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    producto_id = Column(UUID(as_uuid=True), nullable=True)
    recojo_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    monto = Column(Numeric(10, 2), nullable=False)
    aplicado = Column(Numeric(10, 2), nullable=False, default=0)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)



class CuentaCobro(Base):
    """Cómo cobra un agricultor o un repartidor lo que gana. ChakraShop cobra todo con Culqi y les paga A MANO una vez por
    semana (decisión del usuario 2026-10-06): por Yape (hasta S/ 500 por operación) o por transferencia a su cuenta. La
    registra la misma persona en "Mis ganancias"; el admin la ve para pagarle."""
    __tablename__ = "cuentas_cobro"
    __table_args__ = (UniqueConstraint("beneficiario_tipo", "beneficiario_id", name="uq_cuenta_cobro_beneficiario"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiario_tipo = Column(String, nullable=False)  # productor | repartidor (igual que en Liquidacion)
    beneficiario_id = Column(UUID(as_uuid=True), nullable=False)
    usuario_id = Column(UUID(as_uuid=True), nullable=False)  # para avisarle cuando se le paga
    nombre = Column(String, nullable=True)  # su nombre de productor o repartidor (para la lista del admin)
    yape_celular = Column(String, nullable=True)
    yape_titular = Column(String, nullable=True)
    banco = Column(String, nullable=True)
    cuenta_titular = Column(String, nullable=True)
    cci = Column(String, nullable=True)
    fecha_actualizacion = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EntregaPedido(Base):
    """Cuándo se cumplió un pedido: se entregó al comprador o quedó en la agencia de su ciudad. Desde ahí el agricultor
    puede cobrar lo que vendió en ese pedido (antes, si el pedido se cancela, se le devuelve el dinero al comprador). La
    escribe el consumidor de eventos_envios (rabbitmq_consumer.py)."""
    __tablename__ = "entregas_pedido"

    pedido_id = Column(UUID(as_uuid=True), primary_key=True)
    estado = Column(String, nullable=False)  # entregado | en_agencia
    fecha = Column(DateTime, nullable=False, default=datetime.utcnow)


class PagoBeneficiario(Base):
    """Un pago semanal que el admin hizo a mano a un agricultor o repartidor (Yape o transferencia), con el número de
    operación. Cubre todas las liquidaciones que lo apuntan (Liquidacion.pago_beneficiario_id)."""
    __tablename__ = "pagos_beneficiarios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beneficiario_tipo = Column(String, nullable=False)
    beneficiario_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    nombre = Column(String, nullable=True)
    monto = Column(Numeric(10, 2), nullable=False)
    metodo = Column(String, nullable=False)  # yape | transferencia
    destino = Column(String, nullable=False)  # el celular o el banco y CCI al que se pagó (copia: la cuenta puede cambiar)
    numero_operacion = Column(String, nullable=False)
    cantidad = Column(Numeric(6, 0), nullable=False)  # cuántas liquidaciones cubre
    hasta = Column(DateTime, nullable=False)  # lo cumplido antes de este momento
    pagado_por = Column(UUID(as_uuid=True), nullable=True)  # el admin
    fecha = Column(DateTime, nullable=False, default=datetime.utcnow)


class DevolucionStock(Base):
    """Stock que Pagos le devuelve a Productos (pago vencido, rechazado o reembolsado) y que NO entró a la primera.
    El 2026-10-17 un pedido de maíz venció justo cuando Docker volvía a arrancar: Productos todavía no respondía, la
    devolución falló, y los 5 kg se perdieron (solo quedó un print en el log). Ahora la devolución queda anotada aquí y el
    ciclo de vencimientos la reintenta.
    - pendiente: seguro NO llegó a Productos (no conectó, o 502/503/504): se reintenta.
    - dudosa: pudo haber llegado (se cortó esperando la respuesta, u otro error): NO se reintenta, para no sumar dos veces;
      se revisa a mano.
    - devuelta: Productos la sumó."""
    __tablename__ = "devoluciones_stock"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pedido_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    producto_id = Column(String, nullable=False)
    cantidad = Column(Integer, nullable=False)  # en la unidad base del producto, ya redondeada como al descontar
    estado = Column(String, nullable=False, default="pendiente", index=True)  # pendiente | dudosa | devuelta
    intentos = Column(Integer, nullable=False, default=0)
    ultimo_error = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, nullable=False, default=datetime.utcnow)
    fecha_devuelta = Column(DateTime, nullable=True)
