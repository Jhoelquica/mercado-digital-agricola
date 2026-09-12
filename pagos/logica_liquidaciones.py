"""Lógica de liquidaciones — separada de main.py por la misma razón que logica_repartidores.py
en Transporte: tanto el endpoint que confirma un pago (main.procesar_pago) como el job de
reintento en segundo plano (reintento_liquidaciones.py) necesitan la misma función
crear_liquidaciones. Si viviera en main.py, reintento_liquidaciones.py tendría que importarla
DESDE main.py — un import circular, porque main.py también importa el lanzador del hilo desde
reintento_liquidaciones.py.
"""
import os
import json
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import httpx
from sqlalchemy import exists
from sqlalchemy.orm import Session

import models

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# 10% para la plataforma, 90% neto para el beneficiario — ver Liquidacion en models.py.
COMISION_PLATAFORMA_PORCENTAJE = Decimal("0.10")


def crear_liquidaciones(db: Session, pago: models.Pago):
    """Una Liquidacion por cada productor distinto involucrado en el pedido de este pago — se
    calcula acá porque ningún otro servicio tiene a la vez "cuánto" (precio_unitario/cantidad,
    que vive en Pedidos y viajó hasta acá en pago.items) y "de quién" (productor_id, que solo
    conoce Productos).

    La llaman dos lugares: main.procesar_pago (justo después de confirmar el pago, en su propio
    try/except) y reintento_liquidaciones.reintentar_liquidaciones_pendientes (el job de fondo
    que reintenta los que fallaron la primera vez). En ambos casos se llama DESPUÉS de que
    pago.estado ya quedó "aprobado" — no antes: el cargo en Culqi ya sucedió para cuando se llega
    acá, así que un fallo (ej. Productos no responde) NO debe impedir ni deshacer esa
    confirmación. Bloquearla dejaría al sistema mintiendo (dinero cobrado de verdad, pago marcado
    "pendiente"), que es peor que el problema que esto resuelve. Si esta función falla, el pago
    queda aprobado sin sus liquidaciones — un problema menor y recuperable (el job de fondo lo
    reintenta cada INTERVALO_SEGUNDOS, sin límite de reintentos: Productos eventualmente va a
    estar arriba de nuevo), no uno que amerite ocultar el cobro. No hace su propio commit: quien
    llama decide cuándo.
    """
    items = json.loads(pago.items) if pago.items else []
    if not items:
        return

    # 1) Agrupa por producto_id único — evita pedirle a Productos el mismo producto dos veces
    # si por algún motivo aparece repetido en los items.
    subtotal_por_producto = {}
    for item in items:
        producto_id = item["producto_id"]
        monto_item = Decimal(str(item["precio_unitario"])) * item["cantidad"]
        subtotal_por_producto[producto_id] = subtotal_por_producto.get(producto_id, Decimal("0")) + monto_item

    # 2) Resuelve productor_id de cada producto y agrupa el subtotal por productor distinto.
    # Sin try/except acá a propósito: cualquier falla (RequestError, status != 2xx, respuesta sin
    # "productor_id") debe propagar tal cual hacia quien llama, que ya decide qué hacer con ella.
    subtotal_por_productor = {}
    with httpx.Client() as client:
        for producto_id, subtotal in subtotal_por_producto.items():
            resp = client.get(f"{PRODUCTOS_URL}/productos/{producto_id}", timeout=5)
            resp.raise_for_status()
            productor_id = resp.json()["productor_id"]
            subtotal_por_productor[productor_id] = subtotal_por_productor.get(productor_id, Decimal("0")) + subtotal

    ahora = datetime.utcnow()
    for productor_id, monto_bruto in subtotal_por_productor.items():
        monto_bruto = monto_bruto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        comision = (monto_bruto * COMISION_PLATAFORMA_PORCENTAJE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        monto_neto = monto_bruto - comision
        db.add(models.Liquidacion(
            pedido_id=pago.pedido_id,
            beneficiario_tipo="productor",
            beneficiario_id=productor_id,
            monto_bruto=monto_bruto,
            comision_plataforma=comision,
            monto_neto=monto_neto,
            estado="pendiente",
            fecha_creacion=ahora,
            fecha_limite=ahora + timedelta(hours=24),
        ))


def pagos_sin_liquidaciones(db: Session):
    """Pago "aprobado" que todavía no tiene ninguna Liquidacion asociada — la usan tanto el job
    de reintento (reintento_liquidaciones.py) como GET /pagos/sin-liquidaciones (main.py), para
    no duplicar el mismo query en dos lugares."""
    ya_tiene_liquidacion = exists().where(models.Liquidacion.pedido_id == models.Pago.pedido_id)
    return (
        db.query(models.Pago)
        .filter(models.Pago.estado == "aprobado", ~ya_tiene_liquidacion)
        .order_by(models.Pago.fecha_creacion.asc())
        .all()
    )
