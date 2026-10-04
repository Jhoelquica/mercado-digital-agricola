"""Los textos que recibe el comprador. Un aviso dice qué pasó y qué tiene que hacer él, si algo: la clave del estado sola
("en_destino") no le dice a nadie que tiene que pagar su saldo para que descarguen su pedido.

Funciones puras (sin base ni RabbitMQ): reciben el evento tal como llega y devuelven el texto.
"""
from decimal import Decimal, InvalidOperation

GRACIAS = "¡Gracias por comprar en ChakraShop!"

ESTADOS_LEGIBLES = {
    "pendiente": "pendiente",
    "pendiente_asignacion": "buscando repartidor",
    "pendiente_entrega_directa": "lo entrega el productor",
    "propuesto": "propuesto a un repartidor",
    "asignado": "repartidor asignado",
    "en_camino": "en camino",
    "esperando_camion": "esperando que se llene el camión",
    "en_destino": "el camión llegó a tu ciudad",
    "en_agencia": "en la agencia de tu ciudad",
    "entregado": "entregado",
    "cancelado": "cancelado",
}


def soles(valor):
    """"S/ 1500.00" (mismo formato que el resto de la aplicación), o None si el evento no trae el monto."""
    if valor is None:
        return None
    try:
        return f"S/ {Decimal(str(valor)):.2f}"
    except (InvalidOperation, ValueError):
        return None


def _con_motivo(texto, motivo):
    motivo = (motivo or "").strip().rstrip(".")
    return f"{texto}: {motivo}." if motivo else f"{texto}."


def mensaje_pedido_creado(datos):
    nombre = (datos.get("comprador_nombre") or "").strip()
    modalidad = datos.get("modalidad_envio") or "local"
    if modalidad == "camion":
        cuerpo = ("registramos tu pedido en camión compartido. Lo subimos al camión apenas se apruebe tu adelanto (70%); "
                  "el 30% restante lo pagas cuando llegue a tu ciudad, antes de descargar.")
    elif modalidad == "agencia":
        cuerpo = "registramos tu pedido por agencia de carga. Lo confirmamos apenas se apruebe tu pago; el flete de la agencia lo pagas al recoger."
    else:
        cuerpo = "registramos tu pedido. Lo confirmamos apenas se apruebe tu pago."
    return f"Hola {nombre}, {cuerpo}" if nombre else cuerpo[0].upper() + cuerpo[1:]


def mensaje_envio(datos):
    estado = datos.get("estado") or ""
    modalidad = datos.get("modalidad_envio") or "local"
    ciudad = datos.get("destino_ciudad") or "tu ciudad"
    if modalidad == "camion":
        if estado == "en_camino":
            sacos = datos.get("sacos")
            carga = f"con tus {sacos} sacos" if sacos else "con tu pedido"
            return f"El camión salió de Huamanga rumbo a {ciudad} {carga}. Te avisamos cuando llegue."
        if estado == "en_destino":
            return (f"El camión llegó a {ciudad} con tu pedido. Paga tu saldo (30%) desde Mis Pedidos para que lo descarguen; "
                    "después dale tu código de entrega al chofer.")
        if estado == "entregado":
            return f"Descargaron y entregaron tu pedido en {ciudad}. {GRACIAS}"
    elif modalidad == "agencia":
        if estado == "asignado":
            return f"Un repartidor llevará tu pedido a la agencia de carga en Huamanga, con destino a {ciudad}."
        if estado == "en_camino":
            return "Tu pedido va camino a la agencia de carga en Huamanga."
        if estado == "en_agencia":
            agencia = datos.get("agencia_nombre") or "la agencia de carga"
            guia = datos.get("numero_guia")
            return (f"Tu pedido ya está en {agencia}{f' con la guía N.º {guia}' if guia else ''}. Recógelo en su oficina de "
                    f"{ciudad} con el DNI de quien recibe; el flete se paga al recoger. Luego confírmalo en Mis Pedidos.")
        if estado == "entregado":
            return f"Recogiste tu pedido en la agencia de {ciudad}. {GRACIAS}"
    else:
        if estado == "asignado":
            return "Un repartidor aceptó tu pedido y lo va a recoger del almacén."
        if estado == "en_camino":
            return "Tu pedido va en camino. Ten a mano tu código de entrega de 4 dígitos: el repartidor te lo pedirá."
        if estado == "entregado":
            return f"Tu pedido fue entregado. {GRACIAS}"
    legible = ESTADOS_LEGIBLES.get(estado, estado.replace("_", " "))
    return f"Tu envío cambió de estado: {legible}."


def mensaje_pago(datos):
    """El texto de un evento de Pagos, o None si no es uno que se le avise al comprador."""
    evento = datos.get("evento")
    monto = soles(datos.get("monto"))
    de_monto = f" de {monto}" if monto else ""
    if evento == "pago_confirmado":
        if datos.get("adelanto"):
            saldo = soles(datos.get("saldo"))
            return (f"Recibimos tu adelanto{de_monto}. Tu pedido ya está en un camión compartido: sale cuando junte su carga "
                    f"y te avisamos. El resto{f' ({saldo})' if saldo else ''} lo pagas al llegar, antes de descargar.")
        return f"Recibimos tu pago{de_monto}. Tu pedido está confirmado y ya buscamos quién lo lleve."
    if evento == "saldo_pagado":
        return f"Recibimos tu saldo{de_monto}. Ya pueden descargar tu pedido: dale tu código de entrega al chofer."
    if evento == "pago_rechazado":
        return _con_motivo("Tu pago no se completó", datos.get("motivo")) + " Puedes volver a pagar tu pedido desde Mis Pedidos."
    if evento == "saldo_rechazado":
        return (_con_motivo("No aprobamos la transferencia de tu saldo", datos.get("motivo"))
                + " Vuelve a pagarlo desde Mis Pedidos para que descarguen tu pedido.")
    return None
