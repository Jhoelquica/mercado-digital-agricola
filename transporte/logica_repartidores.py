import models


def proponer_envio_a_repartidor(envio_id, db, excluir_id=None):
    query = db.query(models.Repartidor).filter(
        models.Repartidor.estado_disponibilidad == "disponible"
    )
    if excluir_id:
        query = query.filter(models.Repartidor.id != excluir_id)

    repartidor = query.first()

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        return

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