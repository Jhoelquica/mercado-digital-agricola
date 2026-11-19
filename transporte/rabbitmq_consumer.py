import json
import logging
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from logica_repartidores import proponer_envio_a_repartidor, _obtener_pedido
import logica_viajes
import logica_recojos
from comun import rabbitmq
from comun.database import SessionLocal
import models


logger = logging.getLogger("transporte.rabbitmq_consumer")

COLA = "transporte_pagos"
# Cola de antes de este arreglo, ligada a eventos_pedidos: si se deja, RabbitMQ la sigue llenando con cada
# pedido_creado sin que nadie la lea. Se borra al conectarse.
COLA_ANTERIOR = "transporte_pedidos"


def _generar_pin_entrega():
    """4 dígitos, con ceros a la izquierda (ej. "0042") — el comprador lo ve en su pedido y se
    lo dicta al repartidor en la puerta, ver POST /envios/{id}/entregar."""
    return f"{secrets.randbelow(10000):04d}"


def crear_envio_si_falta(pedido_id, adelanto=False):
    """Crea el envío del pedido y lo propone a un repartidor (o, si va en camión, lo sube a un viaje compartido a su
    ciudad). Idempotente:
      - si el envío ya existe y pasó de "pendiente" (el mismo pago_confirmado entregado dos veces, o un pedido de antes
        de este arreglo, cuyo envío nació con pedido_creado) no hace nada;
      - si quedó en "pendiente" (un intento anterior se cortó después de crearlo y antes de asignarlo) sigue desde ahí,
        así nunca queda atascado: el job de huérfanos solo mira "pendiente_asignacion";
      - el índice único de envios.pedido_id impide que dos entregas a la vez creen dos: la segunda cede.
    Devuelve el envío que creó o retomó, o None si no hizo nada."""
    db = SessionLocal()
    try:
        envio = db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).first()
        if envio and envio.estado != "pendiente":
            logger.info("Pedido %s: ya tiene envío ('%s'); el pago_confirmado repetido se ignora", pedido_id, envio.estado)
            return None
        pedido = _obtener_pedido(pedido_id)
        if envio:
            print(f"[Transporte] El envío del pedido {pedido_id} había quedado en pendiente: se retoma su asignación")
        else:
            # Solo un pedido en camión se paga con adelanto: si Pedidos no responde, eso basta para no proponérselo a un
            # repartidor local (el job lo sube a un viaje cuando Pedidos vuelva).
            modalidad = (pedido or {}).get("modalidad_envio") or ("camion" if adelanto else "local")
            envio = models.Envio(
                pedido_id=pedido_id,
                estado="pendiente",
                pin_entrega=_generar_pin_entrega(),
                modalidad_envio=modalidad,
                destino_ciudad=(pedido or {}).get("destino_ciudad"),
                saldo_pagado=False if modalidad == "camion" else None,
            )
            db.add(envio)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                print(f"[Transporte] Otra entrega del mismo pago creó el envío del pedido {pedido_id} a la vez: lo sigue ella")
                return None
        if envio.modalidad_envio == "camion":
            envio.estado = "esperando_camion"
            if pedido:
                logica_viajes.sumar_a_viaje(db, envio, pedido)
            db.commit()
            print(f"[Transporte] Envío del pedido {pedido_id} subido a un viaje en camión")
            return envio
        proponer_envio_a_repartidor(envio.id, db)
        print(f"[Transporte] Envío creado para el pedido pagado {pedido_id}")
        return envio
    finally:
        db.close()


def marcar_saldo_pagado(pedido_id):
    """El comprador pagó el 30% de su pedido en camión: el chofer ya puede descargar (ver confirmar_entrega)."""
    db = SessionLocal()
    try:
        envio = db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).first()
        if not envio:
            logger.warning("Pedido %s: llegó saldo_pagado y no tiene envío; se ignora", pedido_id)
        elif envio.saldo_pagado:
            logger.info("Pedido %s: su saldo ya estaba pagado; el saldo_pagado repetido se ignora", pedido_id)
        else:
            envio.saldo_pagado = True
            db.commit()
    finally:
        db.close()


def _procesar_mensaje(ch, method, properties, body):
    datos = json.loads(body)

    # El envío nace cuando el pago se CONFIRMA, no cuando se crea el pedido: antes se proponía a un repartidor un
    # pedido que nadie había pagado, y podía aceptarlo, entregarlo y generar su liquidación. Un pago rechazado no
    # crea envío.
    if datos.get("evento") == "pago_confirmado":
        crear_envio_si_falta(datos["pedido_id"], adelanto=bool(datos.get("adelanto")))
    elif datos.get("evento") == "saldo_pagado":
        marcar_saldo_pagado(datos["pedido_id"])
    elif datos.get("evento") == "llegada_almacen" and datos.get("producto_id"):
        # El admin pesó la cosecha: se cierra (y se liquida) su recojo en la chacra, si lo hubo.
        db = SessionLocal()
        try:
            logica_recojos.cerrar_por_llegada(db, datos["producto_id"])
        finally:
            db.close()

    ch.basic_ack(delivery_tag=method.delivery_tag)


def _borrar_cola_anterior(connection):
    # En su propio canal: si RabbitMQ respondiera con un error, solo se cierra este canal y no el de consumo.
    try:
        canal = connection.channel()
        canal.queue_delete(queue=COLA_ANTERIOR)
        canal.close()
    except Exception as e:
        print(f"[Transporte] No se pudo borrar la cola anterior {COLA_ANTERIOR}: {e}")


# (exchange, cola, manejador). La conexión, los reintentos y las colas de fallidos están en comun/rabbitmq.py.
SUSCRIPCIONES = (
    ("eventos_pagos", COLA, _procesar_mensaje),
    ("eventos_productos", "transporte_productos", _procesar_mensaje),
)


def lanzar_consumidor_en_hilo():
    rabbitmq.lanzar_consumidor_en_hilo("Transporte", SUSCRIPCIONES, antes_de_consumir=_borrar_cola_anterior)
