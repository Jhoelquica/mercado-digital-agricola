"""Viajes compartidos de camión a una ciudad lejana (decisiones del usuario, 2026-10-03).

Cada comprador pide desde el mínimo de sacos de su ciudad (50 por defecto) y su envío se suma a un viaje abierto a esa
ciudad que todavía tenga lugar. El viaje queda "listo" para salir cuando junta el porcentaje de su capacidad que fijó el
admin (80% por defecto: 80 sacos en un camión de 100), porque con menos el flete por saco no conviene (ver el informe de
rentabilidad en la memoria del proyecto). El admin le asigna un transportista de carga verificado y el viaje sale.

Estados del viaje: abierto -> listo -> en_ruta -> llegado -> completado.
Estados del envío en un viaje: esperando_camion -> en_camino -> en_destino (esperando el saldo) -> entregado.
"""
import os

import httpx

import models

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
CAPACIDAD_POR_DEFECTO = 100
PORCENTAJE_SALIDA_POR_DEFECTO = 80
ESTADOS_CON_LUGAR = ("abierto", "listo")


def _destino(destino_lejano_id) -> dict | None:
    """El destino tal como lo publica Pedidos (GET /destinos-lejanos, público), o None si no responde o ya no está."""
    try:
        with httpx.Client() as client:
            resp = client.get(f"{PEDIDOS_URL}/destinos-lejanos", timeout=5)
    except httpx.RequestError:
        return None
    if resp.status_code != 200:
        return None
    return next((d for d in resp.json() if str(d.get("id")) == str(destino_lejano_id)), None)


def sacos_cargados(db, viaje) -> int:
    # Un pedido cancelado deja de ocupar lugar en el camión.
    return sum(e.sacos or 0 for e in db.query(models.Envio).filter(models.Envio.viaje_id == viaje.id,
                                                                   models.Envio.estado != "cancelado").all())


def sumar_a_viaje(db, envio, pedido: dict) -> "models.Viaje | None":
    """Sube el envío (de un pedido en camión, ya con su adelanto pagado) a un viaje abierto a su ciudad con lugar para sus
    sacos, o abre uno nuevo. No hace commit: lo hace quien llama. None si faltan datos del pedido (lo reintenta el job)."""
    destino_id, sacos = pedido.get("destino_lejano_id"), pedido.get("sacos")
    if not destino_id or not sacos:
        return None
    envio.sacos = sacos
    envio.estado = "esperando_camion"
    abiertos = (
        db.query(models.Viaje)
        .filter(models.Viaje.destino_lejano_id == destino_id, models.Viaje.estado.in_(ESTADOS_CON_LUGAR))
        .order_by(models.Viaje.fecha_creacion.asc())
        .with_for_update()
        .all()
    )
    viaje = next((v for v in abiertos if sacos_cargados(db, v) + sacos <= v.capacidad_sacos), None)
    if viaje is None:
        destino = _destino(destino_id) or {}
        camion = destino.get("camion") or {}
        capacidad = camion.get("capacidad_sacos") or CAPACIDAD_POR_DEFECTO
        viaje = models.Viaje(
            destino_lejano_id=destino_id,
            destino_ciudad=pedido.get("destino_ciudad") or destino.get("ciudad"),
            capacidad_sacos=capacidad,
            sacos_para_salir=camion.get("sacos_para_salir") or -(-capacidad * PORCENTAJE_SALIDA_POR_DEFECTO // 100),
            estado="abierto",
        )
        db.add(viaje)
        db.flush()
    envio.viaje_id = viaje.id
    db.flush()
    if viaje.estado == "abierto" and sacos_cargados(db, viaje) >= viaje.sacos_para_salir:
        viaje.estado = "listo"
    return viaje


def resumen_de_viaje(db, viaje) -> dict:
    """Lo que ve el comprador de su viaje: cuánto falta para que salga."""
    return {
        "id": str(viaje.id),
        "estado": viaje.estado,
        "destino_ciudad": viaje.destino_ciudad,
        "capacidad_sacos": viaje.capacidad_sacos,
        "sacos_para_salir": viaje.sacos_para_salir,
        "sacos_cargados": sacos_cargados(db, viaje),
        "fecha_salida": viaje.fecha_salida.isoformat() if viaje.fecha_salida else None,
        "fecha_llegada": viaje.fecha_llegada.isoformat() if viaje.fecha_llegada else None,
    }


def asignar_envios_de_camion_sin_viaje(db, obtener_pedido) -> int:
    """Red de seguridad del job periódico: envíos en camión que no se pudieron subir a un viaje (Pedidos no respondió
    al crearlos). Devuelve cuántos subió."""
    sueltos = (
        db.query(models.Envio)
        .filter(models.Envio.modalidad_envio == "camion", models.Envio.viaje_id.is_(None),
                models.Envio.estado == "esperando_camion")
        .all()
    )
    subidos = 0
    for envio in sueltos:
        pedido = obtener_pedido(envio.pedido_id)
        if pedido and sumar_a_viaje(db, envio, pedido):
            db.commit()
            subidos += 1
    return subidos
