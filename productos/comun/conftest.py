# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Ayudas para las pruebas de comun/ (corren dentro de la imagen de cada servicio, con su copia)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

CARPETA_DEL_SERVICIO = Path(__file__).resolve().parents[1]


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
