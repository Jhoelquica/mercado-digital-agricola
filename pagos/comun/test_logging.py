# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
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


def _sin_hora(resultado):
    return [re.sub(rf"^{HORA} ", "", linea) for linea in resultado.stderr.strip().splitlines()]


def test_las_librerias_ruidosas_solo_avisan(correr_python):
    # httpx escribía una línea por cada llamada entre servicios y pika una por cada conexión: ahora solo sus avisos.
    codigo = (
        "import logging\n"
        "from comun.logging import configurar_logging\n"
        "configurar_logging()\n"
        "logging.getLogger('httpx').info('HTTP Request: GET http://productos:8002/productos 200 OK')\n"
        "logging.getLogger('pika.adapters.blocking_connection').info('Created channel=1')\n"
        "logging.getLogger('google_genai.models').info('AFC is enabled')\n"
        "logging.getLogger('httpx').warning('reintento')\n"
        "logging.getLogger('pedidos.reintento_eventos').info('Bandeja de salida: 1 evento(s) reenviado(s)')\n"
    )
    resultado = correr_python(codigo, LOG_LEVEL="debug")
    assert resultado.returncode == 0, resultado.stderr
    assert _sin_hora(resultado) == ["WARNING httpx: reintento",
                                    "INFO pedidos.reintento_eventos: Bandeja de salida: 1 evento(s) reenviado(s)"]


def test_el_log_de_acceso_solo_escribe_las_respuestas_con_error(correr_python):
    # Como lo registra uvicorn: '%s - "%s %s HTTP/%s" %d', con el código de la respuesta al final.
    codigo = (
        "import logging\n"
        "from comun.logging import configurar_logging\n"
        "configurar_logging()\n"
        "acceso = logging.getLogger('uvicorn.access')\n"
        "for ruta, codigo in (('/salud', 200), ('/productos', 200), ('/pedidos/1', 404), ('/pagos', 503)):\n"
        "    acceso.info('%s - \"%s %s HTTP/%s\" %d', '10.0.0.1:5000', 'GET', ruta, '1.1', codigo)\n"
    )
    resultado = correr_python(codigo)
    assert resultado.returncode == 0, resultado.stderr
    assert _sin_hora(resultado) == ['INFO uvicorn.access: 10.0.0.1:5000 - "GET /pedidos/1 HTTP/1.1" 404',
                                    'INFO uvicorn.access: 10.0.0.1:5000 - "GET /pagos HTTP/1.1" 503']
