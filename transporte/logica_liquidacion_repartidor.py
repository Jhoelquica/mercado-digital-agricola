"""Lógica de liquidación de repartidor — separada de main.py por la misma razón que
logica_repartidores.py: tanto el endpoint que marca un Envio como "entregado"
(main.actualizar_estado) como el job de reintento en segundo plano
(reintento_liquidacion_repartidor.py) necesitan la misma función notificar_liquidacion_repartidor.
Si viviera en main.py, el job de reintento tendría que importarla DESDE main.py — un import
circular, porque main.py también importa el lanzador del hilo desde
reintento_liquidacion_repartidor.py (mismo problema, y misma solución, que ya resolvimos en
pagos/logica_liquidaciones.py).
"""
import os
import httpx
from sqlalchemy.orm import Session

import models

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
PAGOS_URL = os.getenv("PAGOS_URL", "http://localhost:8007")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
TRANSPORTE_A_PAGOS_SECRETO = os.getenv("TRANSPORTE_A_PAGOS_SECRETO")


def notificar_liquidacion_repartidor(db: Session, envio: models.Envio):
    """Avisa a Pagos que este Envio "entregado" ya puede liquidarse al repartidor — resuelve
    costo_envio consultando GET /pedidos/{pedido_id} (Transporte ya consume ese mismo endpoint
    para obtener_ruta) y se lo pasa a POST /pagos/interno/liquidar-repartidor junto con
    pedido_id/repartidor_id.

    La llaman dos lugares: main.actualizar_estado (justo después de notificar a Certificación,
    en su propio try/except) y reintento_liquidacion_repartidor.reintentar_liquidaciones_repartidor_pendientes
    (el job de fondo que reintenta los que fallaron la primera vez). Sin try/except acá adentro a
    propósito: cualquier falla (Pedidos o Pagos no responden, status != 2xx) debe propagar tal
    cual hacia quien llama, que ya decide qué hacer con ella. No hace su propio commit: quien
    llama decide cuándo (mismo criterio que crear_liquidaciones en Pagos).
    """
    with httpx.Client() as client:
        resp_pedido = client.get(f"{PEDIDOS_URL}/pedidos/{envio.pedido_id}", timeout=5)
        resp_pedido.raise_for_status()
        costo_envio = resp_pedido.json()["costo_envio"]

        resp_pagos = client.post(
            f"{PAGOS_URL}/pagos/interno/liquidar-repartidor",
            json={
                "pedido_id": str(envio.pedido_id),
                "repartidor_id": str(envio.repartidor_id),
                "costo_envio": float(costo_envio),
            },
            headers={"X-Servicio-Secreto": TRANSPORTE_A_PAGOS_SECRETO},
            timeout=5,
        )
        resp_pagos.raise_for_status()

    envio.liquidacion_confirmada = True


def envios_entregados_sin_liquidar(db: Session):
    """Envio "entregado" cuya liquidación de repartidor todavía no se confirmó en Pagos — la usa
    el job de reintento (reintento_liquidacion_repartidor.py)."""
    return (
        db.query(models.Envio)
        .filter(models.Envio.estado == "entregado", models.Envio.liquidacion_confirmada == False)
        .order_by(models.Envio.fecha_creacion.asc())
        .all()
    )
