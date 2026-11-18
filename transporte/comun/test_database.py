# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Pruebas de comun/database.py: las variables de la conexión, el pool y la sesión de cada petición."""
import pytest

from comun import database

LEER_CONEXION = (
    "from comun.database import engine\n"
    "_, datos = engine.dialect.create_connect_args(engine.url)\n"
    "print(repr((datos['user'], datos['password'], datos['host'], datos['port'], datos['dbname'])))\n"
)
LEER_POOL = "from comun.database import engine\nprint(engine.pool.size(), engine.pool._max_overflow)\n"


@pytest.mark.parametrize("variable", ["DB_NAME", "DB_USER", "DB_PASSWORD"])
def test_sin_una_variable_obligatoria_el_servicio_no_arranca(correr_python, variable):
    resultado = correr_python("import comun.database", **{variable: None})
    assert resultado.returncode != 0
    assert f"Falta la variable de entorno {variable}" in resultado.stderr


def test_la_conexion_usa_las_variables_y_la_contrasena_llega_entera(correr_python):
    # Con la URL armada a mano, una contraseña con @ : / # ? % partía la URL y llegaba otro usuario, otro host o nada.
    resultado = correr_python(LEER_CONEXION, DB_USER="usuario", DB_PASSWORD="cl@ve:/#?%x", DB_HOST="servidor",
                              DB_PORT="6543", DB_NAME="otra_base")
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == repr(("usuario", "cl@ve:/#?%x", "servidor", 6543, "otra_base"))


def test_host_puerto_y_pool_por_defecto(correr_python):
    codigo = ("from comun.database import engine\n"
              "print(engine.url.host, engine.url.port, engine.pool.size(), engine.pool._max_overflow)\n")
    resultado = correr_python(codigo, DB_HOST=None, DB_PORT=None, DB_POOL_SIZE=None, DB_MAX_OVERFLOW=None)
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.split() == ["localhost", "5432", "5", "10"]


def test_el_pool_sale_de_las_variables(correr_python):
    resultado = correr_python(LEER_POOL, DB_POOL_SIZE="2", DB_MAX_OVERFLOW="3")
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.split() == ["2", "3"]


@pytest.mark.parametrize("variable", ["DB_PORT", "DB_POOL_SIZE", "DB_MAX_OVERFLOW"])
def test_un_numero_mal_escrito_no_deja_arrancar(correr_python, variable):
    resultado = correr_python("import comun.database", **{variable: "cinco"})
    assert resultado.returncode != 0
    assert f"{variable} debe ser un número entero" in resultado.stderr


def test_una_conexion_nueva_se_rinde_a_los_5_segundos(correr_python):
    # Se mira qué recibe el driver al conectar, sin conectar de verdad.
    codigo = (
        "from sqlalchemy import event\n"
        "from comun.database import engine\n"
        "@event.listens_for(engine, 'do_connect')\n"
        "def mirar(dialecto, registro, argumentos, parametros):\n"
        "    print('connect_timeout =', parametros.get('connect_timeout'), flush=True)\n"
        "    raise RuntimeError('sin conectar de verdad')\n"
        "try:\n"
        "    engine.connect()\n"
        "except Exception:\n"
        "    pass\n"
    )
    resultado = correr_python(codigo)
    assert "connect_timeout = 5" in resultado.stdout, resultado.stderr


def test_get_db_cierra_la_sesion_aunque_la_peticion_falle(monkeypatch):
    cerradas = []

    class Sesion:
        def close(self):
            cerradas.append(self)

    monkeypatch.setattr(database, "SessionLocal", Sesion)
    peticion = database.get_db()
    sesion = next(peticion)
    with pytest.raises(RuntimeError):
        peticion.throw(RuntimeError("la petición falló"))
    assert cerradas == [sesion]


class _SesionQueFalla:
    """Una sesión cuyo rollback y cierre también fallan: lo peor que le puede pasar a un hilo de fondo."""
    deshechas = cerradas = 0

    def rollback(self):
        _SesionQueFalla.deshechas += 1
        raise RuntimeError("tampoco se pudo deshacer")

    def close(self):
        _SesionQueFalla.cerradas += 1
        raise RuntimeError("ni cerrar")


def test_en_su_sesion_sin_lanzar_corre_el_trabajo_y_cierra():
    import logging
    usadas = []

    class Sesion:
        cerrada = False

        def close(self):
            Sesion.cerrada = True

    assert database.en_su_sesion_sin_lanzar(usadas.append, "Una prueba", logging.getLogger("prueba"), Sesion) is True
    assert len(usadas) == 1 and Sesion.cerrada


def test_en_su_sesion_sin_lanzar_no_lanza_aunque_fallen_el_rollback_y_el_cierre(caplog):
    import logging

    def trabajo(_sesion):
        raise RuntimeError("la base se cayó a mitad")

    _SesionQueFalla.deshechas = _SesionQueFalla.cerradas = 0
    with caplog.at_level(logging.ERROR, logger="prueba"):
        resultado = database.en_su_sesion_sin_lanzar(trabajo, "Una prueba", logging.getLogger("prueba"), _SesionQueFalla)
    assert resultado is False
    assert (_SesionQueFalla.deshechas, _SesionQueFalla.cerradas) == (1, 1)
    assert "Una prueba falló" in caplog.text and "tampoco se pudo deshacer" in caplog.text
