import json

from comun import inbox, rabbitmq
from comun.database import SessionLocal
import avisos_externos
import models
from mensajes import (mensaje_cobro, mensaje_envio, mensaje_pago, mensaje_pedido_creado, mensaje_recojo,
                      mensaje_recojo_anulado_contratado, mensaje_recojo_sin_vehiculo)


# Un evento llega por una sola de las 4 colas (cada uno se publica en un solo exchange): basta el nombre del servicio.
CONSUMIDOR = "notificaciones"


def _guardar_notificacion(tipo: str, mensaje: str, pedido_id: str = None, producto_id: str = None, usuario_id: str = None,
                          estado: str = None, rol_destino: str = None, evento_id=None) -> bool:
    """Guarda el aviso y lo manda fuera de la aplicación (push o correo). Devuelve si lo guardó: el mismo evento repetido
    (mismo evento_id) no guarda otro aviso ni vuelve a mandar el push o el correo."""
    db = SessionLocal()
    try:
        # La marca del evento y el aviso van en la misma transacción (comun/inbox.py).
        if inbox.ya_procesado(db, CONSUMIDOR, evento_id):
            print(f"[Notificaciones] El evento {evento_id} ya tiene su aviso: no se repite")
            return False
        # La inferencia por pedido_id (cuando el evento no trae usuario_id, ej.
        # envio_actualizado) solo tiene sentido si el evento es de un pedido — un evento de
        # producto siempre trae su propio usuario_id (la reserva ya lo tiene), así que acá no
        # hace falta ni tiene con qué inferirlo.
        if usuario_id is None and pedido_id is not None:
            anterior = db.query(models.Notificacion).filter(
                models.Notificacion.pedido_id == pedido_id
            ).first()
            usuario_id = anterior.usuario_id if anterior else None

        nueva = models.Notificacion(
            pedido_id=pedido_id, producto_id=producto_id, tipo=tipo, mensaje=mensaje, usuario_id=usuario_id, estado=estado,
            rol_destino=rol_destino,
        )
        db.add(nueva)
        db.commit()
        print(f"[Notificaciones] {mensaje}")
    finally:
        db.close()
    # H6: además, al celular (Web Push) o, de respaldo, por correo. En otro hilo y sin poder romper este consumo. Solo
    # llega aquí un aviso recién guardado.
    avisos_externos.despachar(usuario_id, tipo, mensaje, rol=rol_destino)
    return True


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)
    evento = datos.get("evento")
    evento_id = inbox.evento_id_de(datos)

    if evento == "pedido_creado":
        _guardar_notificacion(
            pedido_id=datos["pedido_id"],
            tipo="pedido_creado",
            mensaje=mensaje_pedido_creado(datos),
            usuario_id=datos.get("usuario_id"),
            evento_id=evento_id,
        )
    elif evento == "envio_actualizado":
        _guardar_notificacion(
            pedido_id=datos["pedido_id"],
            tipo="envio_actualizado",
            mensaje=mensaje_envio(datos),
            estado=datos.get("estado"),
            evento_id=evento_id,
        )
    elif evento == "producto_disponible":
        _guardar_notificacion(
            producto_id=datos["producto_id"],
            usuario_id=datos.get("usuario_id"),
            tipo="producto_disponible",
            mensaje=f"¡Buenas noticias! El producto \"{datos.get('nombre_producto')}\" que reservaste ya está disponible para comprar.",
            evento_id=evento_id,
        )
    elif evento == "recojo_actualizado" and datos.get("usuario_id") and datos.get("producto_id"):
        # El recojo de una cosecha en la chacra: el aviso es para el agricultor (Transporte manda su usuario).
        _guardar_notificacion(producto_id=datos["producto_id"], usuario_id=datos["usuario_id"], tipo="recojo_actualizado",
                              mensaje=mensaje_recojo(datos), estado=datos.get("estado"), evento_id=evento_id)
    elif evento == "recojo_sin_vehiculo" and datos.get("producto_id"):
        # H8: para todos los admins (y sus celulares): hay que contratar un transporte.
        _guardar_notificacion(producto_id=datos["producto_id"], rol_destino="admin", tipo=evento,
                              mensaje=mensaje_recojo_sin_vehiculo(datos), evento_id=evento_id)
    elif evento == "recojo_anulado_contratado" and datos.get("producto_id"):
        _guardar_notificacion(producto_id=datos["producto_id"], rol_destino="admin", tipo=evento,
                              mensaje=mensaje_recojo_anulado_contratado(datos), evento_id=evento_id)
    elif evento == "pago_a_beneficiario" and datos.get("usuario_id"):
        # El pago semanal a un agricultor o repartidor: Pagos manda a quién avisarle.
        _guardar_notificacion(usuario_id=datos["usuario_id"], tipo=evento, mensaje=mensaje_cobro(datos), evento_id=evento_id)
    elif evento in ("pago_confirmado", "saldo_pagado", "pago_rechazado", "saldo_rechazado", "pago_reembolsado") and datos.get("pedido_id"):
        # Pagos no manda el usuario: se toma del aviso de pedido_creado de ese pedido (ver _guardar_notificacion).
        _guardar_notificacion(pedido_id=datos["pedido_id"], tipo=evento, mensaje=mensaje_pago(datos), evento_id=evento_id)

    ch.basic_ack(delivery_tag=method.delivery_tag)


# (exchange, cola, manejador). La conexión, los reintentos y las colas de fallidos están en comun/rabbitmq.py.
SUSCRIPCIONES = (
    ("eventos_pedidos", "notificaciones_pedidos", _procesar_mensaje),
    ("eventos_envios", "notificaciones_envios", _procesar_mensaje),
    ("eventos_productos", "notificaciones_productos", _procesar_mensaje),
    ("eventos_pagos", "notificaciones_pagos", _procesar_mensaje),
)


def lanzar_consumidor_en_hilo():
    rabbitmq.lanzar_consumidor_en_hilo("Notificaciones", SUSCRIPCIONES)
