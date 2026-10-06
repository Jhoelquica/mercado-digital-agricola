"""Recojo de cosechas en la chacra: el tramo chacra -> almacén (decisión del usuario, 2026-10-03).

Al publicar su cosecha el agricultor elige cómo llega al almacén: la lleva él (dice cuándo) o pide un recojo. El recojo
se cotiza con la misma tarifa que un envío (Pedidos, GET /pedidos/costo-recojo), se le propone a un repartidor con
capacidad para ese peso (como un envío) y recorre:

    pendiente_asignacion -> propuesto -> asignado -> recogido -> en_almacen -> completado

Se completa recién cuando el admin pesa la cosecha en el almacén (evento llegada_almacen de Productos): ahí se le
liquida al repartidor y el agricultor queda debiendo el recojo, que Pagos le descuenta de sus siguientes ventas. Si la
cosecha llega sin que nadie la haya recogido (el agricultor la trajo igual), el recojo se anula sin cobrar nada.
"""
import logging
import os
from datetime import datetime

import httpx
from fastapi import HTTPException

import models
from rabbitmq_publisher import publicar_evento

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
PAGOS_URL = os.getenv("PAGOS_URL", "http://localhost:8007")
TRANSPORTE_A_PAGOS_SECRETO = os.getenv("TRANSPORTE_A_PAGOS_SECRETO")

# Los que todavía están en curso: un producto no puede tener dos a la vez.
ESTADOS_EN_CURSO = ("pendiente_asignacion", "propuesto", "asignado", "recogido", "en_almacen", "en_camino")
# Hasta acá el agricultor puede anularlo (todavía nadie cargó su cosecha).
ESTADOS_CANCELABLES = ("pendiente_asignacion", "propuesto", "asignado", "en_camino")

logger = logging.getLogger("transporte.recojos")


def pedir_producto(producto_id) -> dict:
    """El producto tal como lo publica Productos (estado, dueño, stock y su unidad)."""
    try:
        with httpx.Client() as client:
            resp = client.get(f"{PRODUCTOS_URL}/productos/{producto_id}", timeout=5)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="El catálogo no responde. Intenta de nuevo en unos momentos.")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    if resp.status_code != 200:
        raise HTTPException(status_code=503, detail="El catálogo no responde. Intenta de nuevo en unos momentos.")
    return resp.json()


def peso_kg_de(producto: dict):
    """Lo que pesa la cosecha publicada: el stock en su unidad pasado a kg (None si la unidad no tiene equivalencia)."""
    stock = float(producto.get("stock") or 0)
    if (producto.get("unidad_medida") or "kg") == "kg":
        return stock
    equivalencia = producto.get("equivalencia_kg")
    return stock * float(equivalencia) if equivalencia else None


def cotizar(latitud: float, longitud: float, peso_kg: float) -> dict:
    """Costo del recojo según Pedidos (misma tarifa que un envío de ese peso a esa distancia)."""
    try:
        with httpx.Client() as client:
            resp = client.get(f"{PEDIDOS_URL}/pedidos/costo-recojo",
                              params={"latitud": latitud, "longitud": longitud, "peso_kg": peso_kg}, timeout=5)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="No se pudo calcular el costo del recojo. Intenta de nuevo en unos momentos.")
    if resp.status_code == 422:
        detalle = resp.json().get("detail")
        raise HTTPException(status_code=422, detail=detalle if isinstance(detalle, str) else "Revisa la ubicación de tu chacra.")
    if resp.status_code != 200:
        raise HTTPException(status_code=503, detail="No se pudo calcular el costo del recojo. Intenta de nuevo en unos momentos.")
    return resp.json()


def proponer_recojo(db, recojo: models.Recojo, excluir_id=None) -> None:
    """Se lo propone al repartidor disponible de menor capacidad que cargue el peso (mismo criterio que un envío), sin
    volver a ofrecérselo a quien ya lo rechazó. Si no hay nadie, queda "pendiente_asignacion" y el job lo reintenta."""
    rechazaron = db.query(models.RecojoRechazo.repartidor_id).filter(models.RecojoRechazo.recojo_id == recojo.id)
    candidatos = (
        db.query(models.Repartidor)
        .filter(models.Repartidor.estado_disponibilidad == "disponible", ~models.Repartidor.id.in_(rechazaron))
    )
    if excluir_id:
        candidatos = candidatos.filter(models.Repartidor.id != excluir_id)
    if recojo.peso_kg is not None:
        candidatos = candidatos.filter(models.Repartidor.capacidad_maxima_kg >= recojo.peso_kg)
    elegido = candidatos.order_by(models.Repartidor.capacidad_maxima_kg.asc()).with_for_update(skip_locked=True).first()
    if elegido:
        recojo.estado = "propuesto"
        recojo.repartidor_id = elegido.id
        elegido.estado_disponibilidad = "ocupado"
    else:
        recojo.estado = "pendiente_asignacion"
        recojo.repartidor_id = None
    db.commit()


def asignar_recojos_pendientes(db) -> None:
    """Red de seguridad del job periódico: vuelve a buscar repartidor para los recojos que se quedaron sin uno."""
    for recojo in (db.query(models.Recojo).filter(models.Recojo.estado == "pendiente_asignacion")
                   .order_by(models.Recojo.fecha_creacion).all()):
        proponer_recojo(db, recojo)


def liberar_repartidor(db, repartidor_id) -> None:
    repartidor = db.query(models.Repartidor).filter(models.Repartidor.id == repartidor_id).first() if repartidor_id else None
    if repartidor and repartidor.estado_disponibilidad == "ocupado":
        repartidor.estado_disponibilidad = "disponible"


def avisar_al_agricultor(db, recojo: models.Recojo) -> None:
    """Le cuenta al agricultor cómo va su recojo (Notificaciones arma el texto). Nunca rompe el paso que lo dispara: si
    RabbitMQ no responde, el agricultor igual lo ve en su panel."""
    if not recojo.productor_usuario_id:
        return
    repartidor = (db.query(models.Repartidor).filter(models.Repartidor.id == recojo.repartidor_id).first()
                  if recojo.repartidor_id else None)
    try:
        publicar_evento({
            "evento": "recojo_actualizado",
            "recojo_id": str(recojo.id),
            "producto_id": str(recojo.producto_id),
            "usuario_id": str(recojo.productor_usuario_id),
            "estado": recojo.estado,
            "modalidad": recojo.modalidad,
            "producto_nombre": recojo.producto_nombre,
            "repartidor_nombre": repartidor.nombre if repartidor else None,
            "costo": float(recojo.costo) if recojo.costo is not None else None,
        })
    except Exception:
        logger.warning("No se pudo avisar al agricultor del recojo %s (%s)", recojo.id, recojo.estado, exc_info=True)


def liquidar(recojo: models.Recojo) -> None:
    """Le pide a Pagos que liquide el recojo al repartidor y lo anote como deuda del agricultor. Propaga cualquier falla:
    quien llama marca liquidacion_confirmada solo si salió bien (y el job lo reintenta)."""
    with httpx.Client() as client:
        resp = client.post(
            f"{PAGOS_URL}/pagos/interno/liquidar-recojo",
            json={"recojo_id": str(recojo.id), "repartidor_id": str(recojo.repartidor_id), "productor_id": str(recojo.productor_id),
                  "producto_id": str(recojo.producto_id), "costo": float(recojo.costo)},
            headers={"X-Servicio-Secreto": TRANSPORTE_A_PAGOS_SECRETO or ""},
            timeout=5,
        )
    resp.raise_for_status()


def _liquidar_sin_romper(db, recojo) -> None:
    try:
        liquidar(recojo)
        recojo.liquidacion_confirmada = True
        db.commit()
    except Exception:
        db.rollback()
        logger.error("No se pudo liquidar el recojo %s (queda para el reintento)", recojo.id, exc_info=True)


def cerrar_por_llegada(db, producto_id) -> None:
    """La cosecha ya se pesó en el almacén. Con repartidor de por medio (la cargó: asignado, recogido o en_almacen) el
    recojo se completa y se liquida; si nadie la había recogido todavía (el agricultor la trajo igual) se anula sin
    cobrar. Si la llevaba el agricultor, se completa. Idempotente: un segundo aviso no encuentra nada en curso."""
    recojo = (db.query(models.Recojo)
              .filter(models.Recojo.producto_id == producto_id, models.Recojo.estado.in_(ESTADOS_EN_CURSO))
              .with_for_update().first())
    if not recojo:
        return
    ahora = datetime.utcnow()
    if recojo.modalidad == "repartidor" and recojo.estado in ("asignado", "recogido", "en_almacen"):
        if recojo.estado != "en_almacen":
            liberar_repartidor(db, recojo.repartidor_id)
        recojo.estado = "completado"
        recojo.fecha_completado = ahora
        db.commit()
        avisar_al_agricultor(db, recojo)
        _liquidar_sin_romper(db, recojo)
        return
    if recojo.modalidad == "repartidor":
        liberar_repartidor(db, recojo.repartidor_id)
        recojo.estado = "cancelado"
        recojo.motivo_cancelacion = "La cosecha llegó al almacén antes de que la recogieran."
    else:
        recojo.estado = "completado"
    recojo.fecha_completado = ahora
    db.commit()
    avisar_al_agricultor(db, recojo)


def reintentar_liquidaciones(db) -> None:
    for recojo in (db.query(models.Recojo)
                   .filter(models.Recojo.estado == "completado", models.Recojo.modalidad == "repartidor",
                           models.Recojo.liquidacion_confirmada.is_(False))
                   .all()):
        _liquidar_sin_romper(db, recojo)


def recojo_para_mostrar(db, recojo: models.Recojo) -> dict:
    repartidor = (db.query(models.Repartidor).filter(models.Repartidor.id == recojo.repartidor_id).first()
                  if recojo.repartidor_id else None)
    return {
        "id": str(recojo.id),
        "producto_id": str(recojo.producto_id),
        "producto_nombre": recojo.producto_nombre,
        "productor_id": str(recojo.productor_id),
        "modalidad": recojo.modalidad,
        "estado": recojo.estado,
        "latitud": recojo.latitud,
        "longitud": recojo.longitud,
        "referencia": recojo.referencia,
        "telefono": recojo.telefono,
        "fecha_estimada": recojo.fecha_estimada,
        "peso_kg": recojo.peso_kg,
        "distancia_km": recojo.distancia_km,
        "costo": float(recojo.costo) if recojo.costo is not None else None,
        "repartidor": {"id": str(repartidor.id), "nombre": repartidor.nombre, "tipo_vehiculo": repartidor.tipo_vehiculo} if repartidor else None,
        "motivo_cancelacion": recojo.motivo_cancelacion,
        "fecha_creacion": recojo.fecha_creacion,
        "fecha_recogido": recojo.fecha_recogido,
        "fecha_en_almacen": recojo.fecha_en_almacen,
        "fecha_completado": recojo.fecha_completado,
    }
