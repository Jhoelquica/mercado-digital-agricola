import os
import httpx

import models

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")


def _obtener_pedido(pedido_id):
    """GET /pedidos/{id} (Transporte ya consume este mismo endpoint en obtener_ruta y en
    logica_liquidacion_repartidor/logica_certificacion) envuelto en su propio try/except: una
    falla acá (Pedidos caído, timeout, 404) degrada a "no se pudo determinar" — devuelve None, y
    quien llama lo trata exactamente igual que un pedido con peso_total_kg desconocido (ver
    proponer_envio_a_repartidor). Nunca debe bloquear ni hacer fallar la asignación del envío."""
    try:
        with httpx.Client() as client:
            resp = client.get(f"{PEDIDOS_URL}/pedidos/{pedido_id}", timeout=5)
    except httpx.RequestError:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def _resolver_productor_unico(pedido):
    """Devuelve el productor_id (string) si TODOS los items del pedido pertenecen al mismo
    productor, o None si hay más de uno o no se pudo resolver con certeza. Se resuelve pidiendo
    producto_id -> productor_id a Productos, un producto por llamada (no existe un endpoint de
    lote en Productos) — mismo patrón que pagos/logica_liquidaciones.py::crear_liquidaciones al
    agrupar liquidaciones por productor, incluyendo deduplicar producto_id antes de llamar.

    A diferencia de esa función (que deja propagar cualquier falla porque ahí resolver el
    productor ES el objetivo del paso), acá cualquier falla se traga y devuelve None: esto es
    solo un refinamiento para decidir un estado sobre una transición que de todos modos tiene que
    pasar — ante la duda, el envío cae al camino ya existente (pendiente_asignacion), nunca a uno
    nuevo sin poder confirmarlo. El productor_id devuelto se guarda en Envio.productor_id (ver
    proponer_envio_a_repartidor) para que GET /envios/pendientes-entrega-directa (main.py) pueda
    filtrar server-side sin repetir esta misma resolución en cada consulta."""
    producto_ids = {item["producto_id"] for item in pedido.get("items", []) if item.get("producto_id")}
    if not producto_ids:
        return None

    productores_ids = set()
    try:
        with httpx.Client() as client:
            for producto_id in producto_ids:
                resp = client.get(f"{PRODUCTOS_URL}/productos/{producto_id}", timeout=5)
                resp.raise_for_status()
                productores_ids.add(resp.json()["productor_id"])
    except (httpx.RequestError, httpx.HTTPStatusError, KeyError):
        return None

    if len(productores_ids) == 1:
        return productores_ids.pop()
    return None


def proponer_envio_a_repartidor(envio_id, db, excluir_id=None):
    # SELECT ... FOR UPDATE sobre el envío: si dos disparadores (el inmediato al liberarse un
    # repartidor, y el job periódico de envíos huérfanos) intentan asignar el MISMO envío casi
    # al mismo tiempo, el segundo se queda esperando en esta línea hasta que el primero termine
    # (commit/rollback) y libere el lock — recién ahí sigue, con el estado ya actualizado.
    envio = (
        db.query(models.Envio)
        .filter(models.Envio.id == envio_id)
        .with_for_update()
        .first()
    )
    if not envio:
        return

    # Con el lock ya en mano, se vuelve a verificar el estado: si mientras esperábamos el otro
    # disparador ya lo dejó "propuesto"/"asignado"/lo que sea, acá no hay nada que hacer — se
    # retira con gracia en vez de pisar una asignación que ya se hizo. "pendiente" es el estado
    # inicial de un envío recién creado (rabbitmq_consumer.py); "pendiente_asignacion" es el de
    # un envío huérfano en reintento (rechazar_propuesta, disparador inmediato, job periódico).
    if envio.estado not in ("pendiente", "pendiente_asignacion"):
        db.rollback()
        print(f"[Transporte] Envío {envio_id} ya no está huérfano (estado actual: {envio.estado}), no se toca")
        return

    # Historial completo de rechazos de ESTE envío (subconsulta, no una lista en memoria que se
    # pierda entre llamadas): a diferencia de excluir_id (que solo cubre un repartidor puntual
    # dentro de esta misma llamada), esto excluye a TODOS los que ya rechazaron este envío en
    # cualquier momento pasado, sin importar qué disparador reintente después (inmediato,
    # periódico, o este mismo si se llama de nuevo más tarde).
    rechazos_previos = db.query(models.EnvioRechazo.repartidor_id).filter(
        models.EnvioRechazo.envio_id == envio_id
    )

    # Peso del pedido asociado a este envío — determina si se puede filtrar por capacidad de
    # vehículo. None = "no se pudo determinar" (Pedidos no respondió, o el pedido en sí tiene
    # peso_total_kg null porque alguna línea es de unidad/litro sin equivalencia conocida) — en
    # ambos casos el comportamiento de acá abajo es idéntico al de antes de esta sub-entrega:
    # matching sin ningún filtro de capacidad, porque no podemos exigir una capacidad sobre un
    # peso que no conocemos con certeza.
    pedido = _obtener_pedido(envio.pedido_id)
    peso_total_kg = pedido.get("peso_total_kg") if pedido else None

    # SKIP LOCKED (no bloqueante) sobre el repartidor: si el candidato que encontraríamos está
    # con su fila bloqueada por OTRA transacción concurrente que está decidiendo asignárselo a
    # un envío distinto, no nos quedamos esperando por él — pasamos al siguiente disponible. Sin
    # esto, dos envíos distintos podrían terminar asignados al mismo repartidor (el problema
    # espejo del que se protege arriba, ahora del lado del repartidor en vez del envío).
    query = (
        db.query(models.Repartidor)
        .filter(models.Repartidor.estado_disponibilidad == "disponible")
        .filter(~models.Repartidor.id.in_(rechazos_previos))
    )
    if peso_total_kg is not None:
        # .isnot(None) es redundante en SQL (NULL >= peso_total_kg ya es falso de por sí, así que
        # un repartidor sin capacidad declarada nunca calificaría igual) — se deja explícito por
        # claridad para quien lea esto después, no porque haga falta funcionalmente.
        # ORDER BY ASC: prioriza el vehículo más chico que alcance, dejando los grandes libres
        # para pedidos que de verdad los necesiten.
        query = (
            query
            .filter(models.Repartidor.capacidad_maxima_kg.isnot(None))
            .filter(models.Repartidor.capacidad_maxima_kg >= peso_total_kg)
            .order_by(models.Repartidor.capacidad_maxima_kg.asc())
        )
    query = query.with_for_update(skip_locked=True)
    # excluir_id se mantiene y se COMBINA con el historial de arriba (no lo reemplaza): sigue
    # haciendo falta para el caso en que todavía no hay un EnvioRechazo que cubra la exclusión
    # —p. ej. intentar_resolver_envio_huerfano puede terminar mirando un envío DISTINTO al que
    # el repartidor actual acaba de rechazar (ver ese mismo excluir_id en rechazar_propuesta),
    # y para ese otro envío no existe ningún rechazo registrado todavía.
    if excluir_id:
        query = query.filter(models.Repartidor.id != excluir_id)

    repartidor = query.first()

    if not repartidor:
        # Entrega directa SOLO si: el peso se conoce (si no, ni siquiera se filtró por capacidad
        # arriba, así que tampoco corresponde preguntarlo acá), el pedido de verdad requiere un
        # vehículo grande (no cualquier "no alcanzó" amerita este camino — ver
        # Pedido.requiere_vehiculo_grande en pedidos/main.py), y es de un solo productor (si no
        # se puede resolver con certeza, se cae al comportamiento de siempre).
        productor_id_unico = None
        if peso_total_kg is not None and pedido.get("requiere_vehiculo_grande"):
            productor_id_unico = _resolver_productor_unico(pedido)

        if productor_id_unico:
            envio.estado = "pendiente_entrega_directa"
            envio.productor_id = productor_id_unico
            db.commit()
            print(f"[Transporte] Envío {envio_id} sin repartidor con capacidad suficiente — un solo productor, marcado para entrega directa")
            return

        envio.estado = "pendiente_asignacion"
        db.commit()
        print(f"[Transporte] Sin repartidores disponibles para el envío {envio_id}")
        return

    envio.repartidor_id = repartidor.id
    envio.estado = "propuesto"
    repartidor.estado_disponibilidad = "ocupado"
    db.commit()
    print(f"[Transporte] Envío {envio_id} propuesto a repartidor {repartidor.nombre}")


def intentar_resolver_envio_huerfano(db, excluir_id=None):
    """Disparador inmediato: se llama justo después de que un repartidor pasa a estar
    "disponible" (entrega completada, propuesta rechazada, o repartidor recién registrado —
    ver los call sites en main.py). Busca el envío huérfano MÁS ANTIGUO (estado
    "pendiente_asignacion", ordenado por fecha_creacion) y reutiliza proponer_envio_a_repartidor
    para intentar asignarlo ahora que hay (al menos) un repartidor libre — esa función hace su
    propia selección de candidato con el lock correspondiente, así que acá no se fuerza a que
    sea justo el repartidor recién liberado, solo se dispara el intento.

    excluir_id: usado por rechazar_propuesta para no ofrecerle ningún envío (ni el que acaba de
    rechazar ni ningún otro huérfano) de vuelta al mismo repartidor que acaba de rechazar uno,
    dentro de esa misma petición — evita el caso borde de "rechaza X, no hay nadie más
    disponible, X vuelve a quedar huérfano, y este mismo disparador se lo re-ofrece
    inmediatamente al mismo repartidor que lo acaba de rechazar".
    """
    huerfano = (
        db.query(models.Envio)
        .filter(models.Envio.estado == "pendiente_asignacion")
        .order_by(models.Envio.fecha_creacion.asc())
        .first()
    )
    if huerfano:
        proponer_envio_a_repartidor(huerfano.id, db, excluir_id=excluir_id)
