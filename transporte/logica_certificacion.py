"""Lógica de notificación a Certificación al entregar un envío — separada de main.py por la misma
razón que logica_liquidacion_repartidor.py: tanto el endpoint que marca un Envio como "entregado"
(main.actualizar_estado) como el job de reintento en segundo plano (reintento_certificacion.py)
necesitan la misma función notificar_entrega_a_certificacion. Si viviera en main.py, el job de
reintento tendría que importarla DESDE main.py — un import circular, mismo problema (y misma
solución) que ya resolvimos con la liquidación del repartidor en este servicio y con las
liquidaciones en Pagos.

A diferencia de la versión anterior de esta función (que vivía en main.py, y atrapaba cada
llamada a Certificación por separado con su propio try/except, tolerando fallas parciales sin
dejar ningún rastro reintentable), acá cualquier falla en cualquiera de sus llamadas propaga tal
cual — la función pasa a ser una unidad atómica: o notificó todo bien, o queda pendiente para el
próximo reintento completo (mismo criterio que notificar_liquidacion_repartidor).

LIMITACIÓN CONOCIDA: un reintento completo vuelve a mandar eventos-internos incluso para
productos que ya se habían registrado bien en un intento previo parcial — depende de que
crear_bloque en Certificación tolere esa repetición sin problema; no se verificó eso acá, mismo
tipo de límite aceptado que el cargo de Culqi no reversible en Pagos.
"""
import os
import httpx
from sqlalchemy.orm import Session

import models

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
CERTIFICACION_URL = os.getenv("CERTIFICACION_URL", "http://certificacion:8008")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
TRANSPORTE_A_CERTIFICACION_SECRETO = os.getenv("TRANSPORTE_A_CERTIFICACION_SECRETO")


def notificar_entrega_a_certificacion(db: Session, envio: models.Envio):
    """Certifica en el blockchain interno cada producto del pedido de este envío, y genera el
    certificado Merkle del pedido completo.

    La llaman dos lugares: main.actualizar_estado (justo antes de notificar la liquidación del
    repartidor, en su propio try/except) y
    reintento_certificacion.reintentar_certificaciones_pendientes (el job de fondo que reintenta
    las que fallaron la primera vez). Sin try/except acá adentro a propósito (ver docstring del
    módulo): cualquier falla debe propagar tal cual hacia quien llama, que ya decide qué hacer
    con ella. No hace su propio commit: quien llama decide cuándo (mismo criterio que
    notificar_liquidacion_repartidor)."""
    with httpx.Client() as client:
        resp_pedido = client.get(f"{PEDIDOS_URL}/pedidos/{envio.pedido_id}", timeout=5)
        resp_pedido.raise_for_status()
        pedido = resp_pedido.json()

        productos_ids = [item["producto_id"] for item in pedido.get("items", []) if item.get("producto_id")]

        for producto_id in productos_ids:
            resp = client.post(
                f"{CERTIFICACION_URL}/certificacion/{producto_id}/eventos-internos",
                json={"evento": "verificado_punto_venta", "datos": f"pedido {envio.pedido_id} entregado"},
                headers={"X-Servicio-Secreto": TRANSPORTE_A_CERTIFICACION_SECRETO},
                timeout=5,
            )
            resp.raise_for_status()

        if productos_ids:
            resp = client.post(
                f"{CERTIFICACION_URL}/certificacion/pedido/{envio.pedido_id}/certificado",
                json={"productos_ids": productos_ids},
                headers={"X-Servicio-Secreto": TRANSPORTE_A_CERTIFICACION_SECRETO},
                timeout=5,
            )
            resp.raise_for_status()

    envio.certificacion_confirmada = True


def envios_entregados_sin_certificar(db: Session):
    """Envio "entregado" cuya notificación a Certificación todavía no se confirmó — la usa el job
    de reintento (reintento_certificacion.py)."""
    return (
        db.query(models.Envio)
        .filter(models.Envio.estado == "entregado", models.Envio.certificacion_confirmada == False)
        .order_by(models.Envio.fecha_creacion.asc())
        .all()
    )
