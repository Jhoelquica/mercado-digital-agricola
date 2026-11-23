"""Ayudas para las pruebas de comun/ (corren dentro de la imagen de cada servicio, con su copia)."""
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

CARPETA_DEL_SERVICIO = Path(__file__).resolve().parents[1]


def token_de_prueba(**claims) -> str:
    """Un token firmado como lo firma Usuarios (RS256, con el kid de la clave), con la clave privada de PRUEBA que genera
    scripts/correr_pruebas.sh (JWT_CLAVE_PRIVADA; su clave pública es la única de JWT_CLAVES_PUBLICAS). Lo que no se
    pase va con valores válidos; un claim con None no va (para probar uno que falta)."""
    import time

    import jwt
    from cryptography.hazmat.primitives import serialization

    from comun import auth

    clave = serialization.load_pem_private_key(Path(os.environ["JWT_CLAVE_PRIVADA"]).read_bytes(), password=None)
    ahora = int(time.time())
    carga = {"sub": "11111111-1111-1111-1111-111111111111", "rol": "comprador", "ver": 1, "iss": auth.EMISOR,
             "aud": auth.AUDIENCIA, "iat": ahora, "exp": ahora + 900, **claims}
    return jwt.encode({k: v for k, v in carga.items() if v is not None}, clave, algorithm=auth.ALGORITMO,
                      headers={"kid": auth.kid_de(clave.public_key())})


@pytest.fixture
def correr_python():
    """Corre código en un Python nuevo, desde la carpeta del servicio y con las variables de entorno cambiadas (None la
    quita). Así se prueba lo que pasa al arrancar sin tocar los módulos que ya importaron las demás pruebas."""

    def correr(codigo: str, **variables) -> subprocess.CompletedProcess:
        entorno = dict(os.environ)
        for nombre, valor in variables.items():
            if valor is None:
                entorno.pop(nombre, None)
            else:
                entorno[nombre] = valor
        return subprocess.run([sys.executable, "-c", codigo], cwd=CARPETA_DEL_SERVICIO, env=entorno,
                              capture_output=True, text=True, timeout=60)

    return correr


def _ultima_secuencia():
    from sqlalchemy import func

    from comun.bandeja_salida import EventoSaliente
    from comun.database import SessionLocal

    db = SessionLocal()
    try:
        return db.query(func.max(EventoSaliente.secuencia)).scalar() or 0
    finally:
        db.close()


def _mensajes_entre(desde, hasta=None):
    from comun.bandeja_salida import EventoSaliente
    from comun.database import SessionLocal

    db = SessionLocal()
    try:
        consulta = db.query(EventoSaliente).filter(EventoSaliente.secuencia > desde)
        if hasta is not None:
            consulta = consulta.filter(EventoSaliente.secuencia <= hasta)
        return [fila.mensaje() for fila in consulta.order_by(EventoSaliente.secuencia).all()]
    finally:
        db.close()


def anotar_eventos(monkeypatch, modulo, eventos: list) -> None:
    """Anota en `eventos` cada evento que `modulo` deja en la bandeja de salida con su registrar_evento, tal como se
    publicará (sin el evento_id, que es nuevo cada vez), y lo deja también en la bandeja, como en producción."""
    registrar = modulo.registrar_evento

    def anotar(sesion, tipo, datos):
        eventos.append({"evento": tipo, **{clave: valor for clave, valor in datos.items() if clave != "evento"}})
        return registrar(sesion, tipo, datos)

    monkeypatch.setattr(modulo, "registrar_evento", anotar)


@contextmanager
def bandeja_como_mock():
    """Para las pruebas de antes de la bandeja de salida, que sustituían publicar_evento por un mock: da un MagicMock
    "llamado" una vez por cada evento que quedó confirmado en la bandeja mientras duró el `with`, en orden (call_args,
    assert_called_once, assert_not_called... siguen valiendo). Lo que una petición deshizo no cuenta."""
    desde = _ultima_secuencia()
    simulado = MagicMock()
    try:
        yield simulado
    finally:
        for mensaje in _mensajes_entre(desde, _ultima_secuencia()):
            simulado(mensaje)


@pytest.fixture
def bandeja():
    """Los mensajes que la prueba dejó en la bandeja de salida (comun/bandeja_salida.py), en el orden en que se
    publicarán: bandeja() da todos y bandeja("pago_confirmado") solo los de ese tipo. Solo cuenta lo confirmado en la base
    desde que empezó la prueba: lo que una petición deshizo no aparece."""
    desde = _ultima_secuencia()

    def mensajes(tipo=None):
        return [mensaje for mensaje in _mensajes_entre(desde) if tipo is None or mensaje["evento"] == tipo]

    return mensajes
