"""Cobros de agricultores y repartidores.

ChakraShop cobra todo con Culqi y les paga A MANO una vez por semana (decisión del usuario 2026-10-06): lo que se cumplió
hasta el domingo se paga el lunes, por Yape (hasta S/ 500 por operación) o por transferencia a su cuenta. Qué es "cumplido":
- repartidor: su liquidación nace cuando entrega (o deja el pedido en la agencia) o cuando el recojo llega al almacén, así
  que se puede cobrar desde que existe;
- agricultor: su liquidación nace cuando el comprador paga, pero se cobra recién cuando el pedido se entrega (o queda en
  la agencia de la ciudad del comprador): antes, el pedido todavía se puede cancelar y devolver (ver EntregaPedido).
Este módulo solo decide qué se puede cobrar y cuándo se paga; las rutas están en main.py.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import models

HORAS_PERU = timedelta(hours=5)  # Perú está en UTC-5 todo el año (no tiene horario de verano)
ESTADOS_CUMPLIDOS = ("entregado", "en_agencia")


def corte_de_la_semana(ahora: datetime | None = None) -> datetime:
    """El último corte, en UTC: el lunes 00:00 de esta semana en hora de Perú. Lo cumplido antes entra en el pago de ese
    lunes; lo cumplido después, en el del lunes siguiente."""
    local = (ahora or datetime.utcnow()) - HORAS_PERU
    lunes = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return lunes + HORAS_PERU


def dia_de_pago(cumplido_en: datetime) -> date:
    """El lunes (fecha de Perú) en que se paga algo cumplido en ese momento: el de la semana siguiente."""
    local = cumplido_en - HORAS_PERU
    return (local + timedelta(days=7 - local.weekday())).date()


def entregas_de(db, pedido_ids) -> dict:
    """{pedido_id: fecha en que se cumplió} de los pedidos que ya se cumplieron."""
    if not pedido_ids:
        return {}
    filas = db.query(models.EntregaPedido).filter(models.EntregaPedido.pedido_id.in_(list(pedido_ids))).all()
    return {f.pedido_id: f.fecha for f in filas}


def cumplido_en(liquidacion, entregas: dict) -> datetime | None:
    if liquidacion.beneficiario_tipo == "repartidor":
        return liquidacion.fecha_creacion
    return entregas.get(liquidacion.pedido_id)


def estado_de_cobro(liquidacion, entregas: dict) -> str:
    """pagado | anulada | por_cobrar | esperando_entrega"""
    if liquidacion.estado in ("pagado", "anulada"):
        return liquidacion.estado
    return "por_cobrar" if cumplido_en(liquidacion, entregas) else "esperando_entrega"


def concepto_de(liquidacion) -> str:
    """venta (agricultor) | envio | recojo (repartidor)"""
    if liquidacion.beneficiario_tipo == "productor":
        return "venta"
    return "recojo" if liquidacion.concepto == "recojo" else "envio"


def pendientes(db, tipo=None, beneficiario_id=None, bloquear=False):
    """Las liquidaciones todavía sin pagar (de un beneficiario o de todos) y las entregas de sus pedidos."""
    consulta = db.query(models.Liquidacion).filter(models.Liquidacion.estado == "pendiente")
    if tipo:
        consulta = consulta.filter(models.Liquidacion.beneficiario_tipo == tipo,
                                   models.Liquidacion.beneficiario_id == beneficiario_id)
    if bloquear:
        consulta = consulta.with_for_update()
    liquidaciones = consulta.order_by(models.Liquidacion.fecha_creacion).all()
    entregas = entregas_de(db, {l.pedido_id for l in liquidaciones if l.beneficiario_tipo == "productor"})
    return liquidaciones, entregas


def cumplidas_antes(liquidaciones, entregas: dict, hasta: datetime) -> list:
    return [l for l in liquidaciones if (c := cumplido_en(l, entregas)) is not None and c < hasta]


def neto(liquidaciones) -> Decimal:
    return sum((Decimal(str(l.monto_neto)) for l in liquidaciones), Decimal("0"))
