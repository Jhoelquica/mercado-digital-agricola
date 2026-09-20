"""Acceso de Transporte al servicio de Pedidos por la vía INTERNA (con secreto de servicio).

GET /pedidos/{id} dejó de ser público: ahora exige sesión y solo lo ven el comprador y los roles de
entrega o administración. Transporte necesita el pedido en procesos que no tienen a ningún usuario
detrás (proponer envíos, liquidar al repartidor, certificar la entrega) y para comprobar de quién es
un pedido antes de mostrar un envío, así que usa GET /pedidos/interno/{id}, que se protege con el
secreto TRANSPORTE_A_PEDIDOS_SECRETO (mismo patrón que los demás pares de servicios).
"""
import os

TRANSPORTE_A_PEDIDOS_SECRETO = os.getenv("TRANSPORTE_A_PEDIDOS_SECRETO")


def cabecera_pedidos() -> dict:
    return {"X-Servicio-Secreto": TRANSPORTE_A_PEDIDOS_SECRETO or ""}
