"""Tipos validados de los formularios del productor (perfil, chacra, siembra y cosecha).

El formulario del frontend ya limita estos campos (min, max, maxlength), pero eso se salta con solo
llamar a la API: acá el servidor exige las mismas reglas y además números finitos (sin NaN ni
Infinity), textos sin caracteres nulos y topes que caben en las columnas de la base (Numeric(10,2)
revienta con un error 500 por encima de 99.999.999,99).

Los mensajes van en español; el frontend los muestra tal cual (ver mensajeDeDetalle en api.js).
Los límites inferiores que ya tienen su propio mensaje en cada endpoint ("debe ser mayor a 0")
no se repiten acá, para no cambiar esos mensajes.
"""
import math
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator

MONTO_MAXIMO = 9_999_999.99        # soles; cabe en Numeric(10,2) con margen
CANTIDAD_MAXIMA = 1_000_000        # kg/unidades/litros por cosecha; el stock del producto es un entero
EQUIVALENCIA_MAXIMA = 5_000        # kg por unidad
PARCELAS_MAXIMAS = 1_000

FECHA_SIEMBRA_MINIMA = datetime(2000, 1, 1)
SIEMBRA_FUTURA_MAXIMA_DIAS = 730   # se puede planificar una siembra con hasta dos años de anticipación
CICLO_MAXIMO_DIAS = 1_825          # de la siembra a la cosecha estimada: cinco años como máximo
COSECHA_FUTURA_TOLERANCIA_DIAS = 1  # margen por la diferencia de zona horaria con el navegador


def _finito(valor: float) -> float:
    if not math.isfinite(valor):
        raise ValueError("debe ser un número válido")
    return valor


def _monto(valor: float) -> float:
    _finito(valor)
    if valor < 0 or valor > MONTO_MAXIMO:
        raise ValueError(f"debe estar entre 0 y {MONTO_MAXIMO:.2f}")
    return valor


def _cantidad(valor: float) -> float:
    _finito(valor)
    if valor > CANTIDAD_MAXIMA:
        raise ValueError(f"no puede superar {CANTIDAD_MAXIMA}")
    return valor


def _equivalencia(valor: float) -> float:
    _finito(valor)
    if valor < 0:
        raise ValueError("no puede ser negativa")
    if valor > EQUIVALENCIA_MAXIMA:
        raise ValueError(f"no puede superar {EQUIVALENCIA_MAXIMA}")
    return valor


def _parcelas(valor: int) -> int:
    if valor > PARCELAS_MAXIMAS:
        raise ValueError(f"no puede superar {PARCELAS_MAXIMAS}")
    return valor


def _latitud(valor: float) -> float:
    _finito(valor)
    if not -90 <= valor <= 90:
        raise ValueError("la latitud debe estar entre -90 y 90")
    return valor


def _longitud(valor: float) -> float:
    _finito(valor)
    if not -180 <= valor <= 180:
        raise ValueError("la longitud debe estar entre -180 y 180")
    return valor


def _sin_zona(valor: datetime) -> datetime:
    """La base guarda fechas sin zona horaria: una fecha con zona ("...Z") se pasa a UTC y se le
    quita la zona, para poder compararla con las demás (comparar una con zona y otra sin zona
    lanza un error 500)."""
    if valor.tzinfo is not None:
        valor = valor.astimezone(timezone.utc).replace(tzinfo=None)
    return valor


def texto(maximo: int, *, minimo: int = 0):
    """Texto recortado, con largo acotado y sin caracteres nulos (Postgres los rechaza con un 500)."""
    def validar(valor: str) -> str:
        valor = valor.strip()
        if "\x00" in valor:
            raise ValueError("contiene caracteres no válidos")
        if len(valor) < minimo:
            raise ValueError("no puede estar vacío")
        if len(valor) > maximo:
            raise ValueError(f"no puede superar {maximo} caracteres")
        return valor
    return Annotated[str, AfterValidator(validar)]


def _coordenada_texto(nombre: str, limite: float):
    """Latitud/longitud del perfil de productor: se guardan como texto, pero deben ser un número."""
    def validar(valor: str) -> str:
        try:
            numero = float(valor)
        except ValueError:
            raise ValueError(f"{nombre} debe ser un número")
        if not math.isfinite(numero) or abs(numero) > limite:
            raise ValueError(f"{nombre} fuera de rango")
        return valor.strip()
    return Annotated[str, AfterValidator(validar)]


Monto = Annotated[float, AfterValidator(_monto)]
Cantidad = Annotated[float, AfterValidator(_cantidad)]
Equivalencia = Annotated[float, AfterValidator(_equivalencia)]
Parcelas = Annotated[int, AfterValidator(_parcelas)]
Latitud = Annotated[float, AfterValidator(_latitud)]
Longitud = Annotated[float, AfterValidator(_longitud)]
Fecha = Annotated[datetime, AfterValidator(_sin_zona)]

LatitudTexto = _coordenada_texto("la latitud", 90)
LongitudTexto = _coordenada_texto("la longitud", 180)

CodigoChacra = texto(30, minimo=1)
NombreChacra = texto(80)
Cultivo = texto(80, minimo=1)
Ubicacion = texto(200)
UnidadMedida = texto(20, minimo=1)
NombreProductor = texto(120, minimo=1)
Comunidad = texto(120)
Contacto = texto(60)
MotivoRechazo = texto(500)
