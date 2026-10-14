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


def mensaje_cobro(datos):
    """El pago semanal que ChakraShop le hizo a un agricultor o repartidor."""
    monto = soles(datos.get("monto")) or "tu pago"
    medio = "Yape" if datos.get("metodo") == "yape" else "transferencia a tu cuenta"
    operacion = (datos.get("numero_operacion") or "").strip()
    que = "tus entregas" if datos.get("beneficiario_tipo") == "repartidor" else "lo que vendiste y ya se entregó"
    return (f"Te pagamos {monto} por {medio}{f' (operación {operacion})' if operacion else ''}: {que}. "
            "El detalle está en Mis ganancias.")


DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def fecha_y_hora(iso):
    """"jueves 16/10 a las 8:00" de "2026-10-16T08:00:00" (hora de Perú tal como la escribió el admin), o None."""
    from datetime import datetime
    try:
        f = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return f"{DIAS_SEMANA[f.weekday()]} {f.day:02d}/{f.month:02d} a las {f.hour}:{f.minute:02d}"


def mensaje_recojo(datos):
    """Cómo va el recojo de una cosecha en la chacra, contado al agricultor."""
    producto = datos.get("producto_nombre") or "cosecha"
    quien = datos.get("repartidor_nombre") or "El repartidor"
    estado = datos.get("estado") or ""
    if estado == "asignado" and datos.get("contratado"):
        # H8: el transporte que contrató el admin. Todo lo que el agricultor necesita, y que puede anularlo.
        cuando = fecha_y_hora(datos.get("fecha_programada"))
        placa = datos.get("transportista_placa")
        telefono = datos.get("transportista_telefono")
        flete = soles(datos.get("costo"))
        partes = [f"{quien} pasa a recoger tu {producto}{f' el {cuando}' if cuando else ''}"
                  f"{f' en el vehículo {placa}' if placa else ''}."]
        if telefono:
            partes.append(f"Su celular: {telefono}.")
        if flete:
            partes.append(f"El flete acordado es {flete}: se descuenta de lo que ganes cuando se venda. "
                          "Si no estás de acuerdo, anula el recojo antes de que pase.")
        return " ".join(partes)
    if estado == "asignado":
        return f"{quien} aceptó recoger tu {producto} en tu chacra. Te llamará antes de llegar."
    if estado == "recogido":
        return f"{quien} ya recogió tu {producto} y la lleva al almacén."
    if estado == "en_almacen":
        return f"Tu {producto} ya está en el almacén. Cuando la pesen, sale a la venta."
    if estado == "completado":
        costo = soles(datos.get("costo"))
        if datos.get("modalidad") == "repartidor" and costo:
            return f"El almacén pesó tu {producto} y ya está a la venta. El recojo ({costo}) se descontará de lo que ganes cuando se venda."
        return f"El almacén recibió y pesó tu {producto}: ya está a la venta."
    if estado == "cancelado":
        return f"Tu {producto} llegó al almacén antes de que la recogieran: anulamos el recojo y no se te cobra nada."
    legible = ESTADOS_LEGIBLES.get(estado, estado.replace("_", " "))
    return f"Tu recojo cambió de estado: {legible}."


def _kilos(valor):
    try:
        return f"{float(valor):,.1f}".rstrip("0").rstrip(".").replace(",", " ")
    except (TypeError, ValueError):
        return None


def mensaje_recojo_sin_vehiculo(datos):
    """H8, para los admins: un recojo que ningún vehículo registrado carga."""
    quien = (datos.get("productor_nombre") or "Un agricultor").strip()
    kilos = _kilos(datos.get("peso_kg"))
    sacos = datos.get("sacos")
    carga = f"{kilos} kg" + (f" ({sacos} sacos)" if sacos else "") if kilos else "su cosecha"
    producto = datos.get("producto_nombre")
    distancia = _kilos(datos.get("distancia_km"))
    return (f"{quien} pidió recojo de {carga}{f' de {producto}' if producto else ''}"
            f"{f', a {distancia} km del almacén' if distancia else ''}, y ningún vehículo registrado lo carga. "
            "Contrata un transporte y asígnalo en Productos en tránsito.")


def mensaje_recojo_anulado_contratado(datos):
    """H8, para los admins: el agricultor anuló un recojo que ya tenía un transporte contratado en camino."""
    producto = datos.get("producto_nombre") or "su cosecha"
    nombre = datos.get("transportista_nombre") or "el conductor"
    telefono = datos.get("transportista_telefono")
    return f"El agricultor anuló el recojo de {producto}: avísale a {nombre}{f' ({telefono})' if telefono else ''} que ya no vaya."


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
    if evento == "pago_reembolsado":
        motivo = (datos.get("motivo") or "").strip().rstrip(".")
        partes = [f"Cancelamos tu pedido{': ' + motivo if motivo else ''}."]
        devuelto = soles(datos.get("monto"))
        if devuelto and float(datos.get("monto")) > 0:
            partes.append(f"Te devolvimos {devuelto} al medio con que pagaste; puede tardar unos días en verse en tu cuenta.")
        manual = soles(datos.get("manual"))
        if manual:
            partes.append(f"Los {manual} que pagaste por transferencia te los devolvemos a la misma cuenta.")
        return " ".join(partes)
    if evento == "pago_rechazado" and datos.get("vencido"):
        return ("Venció el plazo para pagar tu pedido y los productos volvieron al catálogo. Si siguen disponibles, "
                "puedes pagarlo desde Mis Pedidos.")
    if evento == "pago_rechazado":
        return _con_motivo("Tu pago no se completó", datos.get("motivo")) + " Puedes volver a pagar tu pedido desde Mis Pedidos."
    if evento == "saldo_rechazado":
        return (_con_motivo("No aprobamos la transferencia de tu saldo", datos.get("motivo"))
                + " Vuelve a pagarlo desde Mis Pedidos para que descarguen tu pedido.")
    return None
