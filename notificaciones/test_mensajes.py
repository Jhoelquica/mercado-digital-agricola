"""Textos de los avisos (mensajes.py): funciones puras, sin base de datos."""
from mensajes import mensaje_envio, mensaje_pago, mensaje_pedido_creado, soles


def test_la_agencia_da_la_guia_donde_recoger_y_que_el_flete_se_paga_al_recoger():
    texto = mensaje_envio({"estado": "en_agencia", "modalidad_envio": "agencia", "destino_ciudad": "Lima",
                           "agencia_nombre": "Shalom", "numero_guia": "B001-123"})
    assert "Shalom" in texto and "B001-123" in texto and "Lima" in texto
    assert "DNI de quien recibe" in texto and "flete se paga al recoger" in texto


def test_el_camion_avisa_al_salir_y_al_llegar_que_toca_pagar_el_saldo():
    salio = mensaje_envio({"estado": "en_camino", "modalidad_envio": "camion", "destino_ciudad": "Lima", "sacos": 80})
    llego = mensaje_envio({"estado": "en_destino", "modalidad_envio": "camion", "destino_ciudad": "Lima"})
    assert "salió de Huamanga rumbo a Lima con tus 80 sacos" in salio
    assert "llegó a Lima" in llego and "Paga tu saldo (30%)" in llego and "código de entrega" in llego


def test_un_envio_local_pide_tener_a_mano_el_codigo():
    assert "código de entrega" in mensaje_envio({"estado": "en_camino"})
    assert mensaje_envio({"estado": "entregado", "modalidad_envio": "local"}).startswith("Tu pedido fue entregado")


def test_un_estado_sin_texto_propio_se_muestra_legible_y_no_como_clave():
    assert mensaje_envio({"estado": "pendiente_asignacion"}) == "Tu envío cambió de estado: buscando repartidor."
    assert mensaje_envio({"estado": "algo_nuevo"}) == "Tu envío cambió de estado: algo nuevo."


def test_los_pagos_dicen_el_monto_y_lo_que_sigue():
    assert mensaje_pago({"evento": "pago_confirmado", "monto": "45.5"}) == (
        "Recibimos tu pago de S/ 45.50. Tu pedido está confirmado y ya buscamos quién lo lleve.")
    adelanto = mensaje_pago({"evento": "pago_confirmado", "adelanto": True, "monto": "700.00", "saldo": "300.00"})
    assert "adelanto de S/ 700.00" in adelanto and "(S/ 300.00)" in adelanto and "antes de descargar" in adelanto
    assert "S/ 300.00" in mensaje_pago({"evento": "saldo_pagado", "monto": "300"})


def test_un_rechazo_lleva_su_motivo_sin_doble_punto_y_dice_como_seguir():
    texto = mensaje_pago({"evento": "saldo_rechazado", "motivo": "El monto no coincide."})
    assert texto.startswith("No aprobamos la transferencia de tu saldo: El monto no coincide. Vuelve a pagarlo")
    assert mensaje_pago({"evento": "pago_rechazado"}).startswith("Tu pago no se completó. Puedes volver a pagar")
    assert mensaje_pago({"evento": "otro"}) is None


def test_el_aviso_del_pedido_depende_de_como_viaja():
    assert mensaje_pedido_creado({"comprador_nombre": "Rosa"}).startswith("Hola Rosa, registramos tu pedido.")
    assert "flete de la agencia lo pagas al recoger" in mensaje_pedido_creado({"modalidad_envio": "agencia"})
    assert mensaje_pedido_creado({"modalidad_envio": "camion"}).startswith("Registramos tu pedido en camión compartido")
    assert soles(None) is None and soles("x") is None
