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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# 10% para la plataforma, 90% neto para el beneficiario — ver Liquidacion en models.py. Mismo
# porcentaje para cualquier tipo de beneficiario (productor o repartidor).
COMISION_PLATAFORMA_PORCENTAJE = Decimal("0.10")


def calcular_comision_y_neto(monto_bruto: Decimal):
    """Redondea monto_bruto a 2 decimales y calcula (comision_plataforma, monto_neto) sobre él —
    usado tanto por crear_liquidaciones (productor) como por crear_liquidacion_repartidor, para
    no repetir el redondeo con ROUND_HALF_UP en dos lugares."""
    monto_bruto = monto_bruto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    comision = (monto_bruto * COMISION_PLATAFORMA_PORCENTAJE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    monto_neto = monto_bruto - comision
    return monto_bruto, comision, monto_neto


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
        monto_bruto, comision, monto_neto = calcular_comision_y_neto(monto_bruto)
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


def crear_liquidacion_repartidor(db: Session, pedido_id: str, repartidor_id: str, costo_envio) -> None:
    """Crea la Liquidacion del repartidor para un pedido ya entregado — la llama
    POST /pagos/interno/liquidar-repartidor (ver main.py), a su vez llamado por Transporte cuando
    un Envio pasa a "entregado" (y por su propio job de reintento si la primera llamada falló).

    A diferencia de crear_liquidaciones (productor), acá no hace falta ninguna llamada cruzada
    para calcular el monto: costo_envio ya viene resuelto — lo calculó Transporte contra la
    ubicación del almacén al crear el pedido originalmente (ver calcular_costo_envio en
    pedidos/main.py).

    Idempotente por construcción: el mismo UniqueConstraint(pedido_id, beneficiario_tipo,
    beneficiario_id) de Liquidacion que ya usa crear_liquidaciones es quien de verdad evita un
    duplicado si Transporte reintenta tras un fallo de red que sí había llegado a completarse acá
    — se traduce ese choque en un no-op silencioso, no en un error: un reintento
    exitoso-pero-tarde no es una falla del lado de Transporte, mismo criterio que
    crear_producto_desde_cosecha en Productos (409 tratado ahí; acá ni eso, éxito silencioso,
    porque quien llama no tiene por qué distinguir "ya estaba" de "se acaba de crear")."""
    monto_bruto, comision, monto_neto = calcular_comision_y_neto(Decimal(str(costo_envio)))
    ahora = datetime.utcnow()
    db.add(models.Liquidacion(
        pedido_id=pedido_id,
        beneficiario_tipo="repartidor",
        beneficiario_id=repartidor_id,
        monto_bruto=monto_bruto,
        comision_plataforma=comision,
        monto_neto=monto_neto,
        estado="pendiente",
        fecha_creacion=ahora,
        fecha_limite=ahora + timedelta(hours=24),
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


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
