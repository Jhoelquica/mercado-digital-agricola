import models


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

    # SKIP LOCKED (no bloqueante) sobre el repartidor: si el candidato que encontraríamos está
    # con su fila bloqueada por OTRA transacción concurrente que está decidiendo asignárselo a
    # un envío distinto, no nos quedamos esperando por él — pasamos al siguiente disponible. Sin
    # esto, dos envíos distintos podrían terminar asignados al mismo repartidor (el problema
    # espejo del que se protege arriba, ahora del lado del repartidor en vez del envío).
    query = (
        db.query(models.Repartidor)
        .filter(models.Repartidor.estado_disponibilidad == "disponible")
        .with_for_update(skip_locked=True)
    )
    if excluir_id:
        query = query.filter(models.Repartidor.id != excluir_id)

    repartidor = query.first()

    if not repartidor:
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
