"""Las reglas de plazos.py, sin base de datos: base de 15 min, transferencia a 2 h desde la creación, Culqi con tope en
45 min, y ninguna acorta un plazo."""
from datetime import datetime, timedelta

import plazos

CREACION = datetime(2026, 11, 22, 15, 0, 0)


def minutos(n):
    return timedelta(minutes=n)


def test_el_plazo_base_es_de_15_minutos_desde_la_creacion():
    assert plazos.PLAZO_PARA_PAGAR_MINUTOS == 15
    assert plazos.vence_inicial(CREACION) == CREACION + minutos(15)


def test_elegir_transferencia_da_dos_horas_desde_la_creacion_y_repetirlo_no_lo_alarga():
    vence = plazos.vence_con_transferencia(CREACION, CREACION + minutos(15))
    assert vence == CREACION + minutos(120)
    assert plazos.vence_con_transferencia(CREACION, vence) == vence  # cambiar de método y volver: lo mismo
    assert plazos.vence_con_transferencia(CREACION, CREACION + minutos(200)) == CREACION + minutos(200)  # nunca acorta


def test_abrir_culqi_con_menos_de_10_minutos_alarga_hasta_ahora_mas_10():
    vence = CREACION + minutos(15)
    a_los_12 = CREACION + minutos(12)
    assert plazos.vence_al_abrir_culqi(CREACION, vence, a_los_12) == a_los_12 + minutos(10)
    a_los_3 = CREACION + minutos(3)  # le quedan 12: no cambia
    assert plazos.vence_al_abrir_culqi(CREACION, vence, a_los_3) == vence


def test_abrir_culqi_muchas_veces_nunca_pasa_del_tope_de_45_minutos():
    vence = CREACION + minutos(15)
    for minuto in range(0, 70):
        ahora = CREACION + minutos(minuto)
        vence = plazos.vence_al_abrir_culqi(CREACION, vence, ahora)
        assert vence <= CREACION + minutos(45)
    assert vence == CREACION + minutos(45)


def test_pasado_el_tope_abrir_culqi_no_extiende_y_ningun_plazo_se_acorta():
    vence = CREACION + minutos(45)
    assert plazos.vence_al_abrir_culqi(CREACION, vence, CREACION + minutos(50)) == vence  # ya venció: vence
    con_transferencia = CREACION + minutos(120)
    assert plazos.vence_al_abrir_culqi(CREACION, con_transferencia, CREACION + minutos(115)) == con_transferencia
    de_antes = CREACION + minutos(60)  # un pago de antes, con 60 min: abrir Culqi a los 55 no lo baja al tope de 45
    assert plazos.vence_al_abrir_culqi(CREACION, de_antes, CREACION + minutos(55)) == de_antes
