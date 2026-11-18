"""Pruebas de comun/logging.py: el formato y el nivel de los logs."""
import re

REGISTRAR = (
    "import logging\n"
    "from comun.logging import configurar_logging\n"
    "configurar_logging()\n"
    "logging.getLogger('servicio.prueba').debug('detalle')\n"
    "logging.getLogger('servicio.prueba').info('bandeja al día')\n"
)
HORA = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}"


def test_por_defecto_sale_info_con_hora_nivel_y_nombre(correr_python):
    resultado = correr_python(REGISTRAR, LOG_LEVEL=None)
    assert resultado.returncode == 0, resultado.stderr
    assert re.fullmatch(rf"{HORA} INFO servicio\.prueba: bandeja al día", resultado.stderr.strip()), resultado.stderr


def test_log_level_cambia_el_nivel(correr_python):
    resultado = correr_python(REGISTRAR, LOG_LEVEL="debug")
    lineas = resultado.stderr.strip().splitlines()
    assert [re.sub(rf"^{HORA} ", "", linea) for linea in lineas] == [
        "DEBUG servicio.prueba: detalle", "INFO servicio.prueba: bandeja al día"]


def test_un_log_level_que_no_existe_no_deja_arrancar(correr_python):
    resultado = correr_python(REGISTRAR, LOG_LEVEL="ruidoso")
    assert resultado.returncode != 0
    assert "LOG_LEVEL vale 'RUIDOSO'" in resultado.stderr
