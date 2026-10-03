"""Verificación del RUC de un comprador "negocio" en el padrón público de SUNAT: sin esto cualquiera se registraba como
negocio con un RUC inventado o de una empresa dada de baja.

SUNAT no ofrece una API pública gratuita (su consulta web tiene captcha y su servicio oficial pide credenciales de
empresa), así que se consulta un servicio que lee el padrón público de SUNAT: apis.net.pe, que el 2026-10-03 respondía
sin clave. Se cambia sin tocar el código: SUNAT_RUC_URL, y SUNAT_RUC_TOKEN (opcional) si el servicio pasa a pedir una.

Comprueba que el RUC exista y diga su razón social, estado (ACTIVO, BAJA...) y condición (HABIDO, NO HABIDO...). No
prueba que quien se registra sea el dueño de ese negocio.
"""
import os
import threading
import time

import httpx

URL_POR_DEFECTO = "https://api.apis.net.pe/v2/sunat/ruc"
TIEMPO_MAXIMO_SEGUNDOS = 8
CACHE_SEGUNDOS = 24 * 3600  # el estado de un RUC no cambia de un minuto a otro
TAMANO_CACHE = 1000

_cache = {}
_candado = threading.Lock()


class SunatNoDisponible(Exception):
    """El servicio no respondió, tardó, limitó las consultas o respondió algo que no se entiende: no se puede afirmar
    nada sobre el RUC."""


def consultar_ruc(ruc: str):
    """{"razon_social", "estado", "condicion"} (estado y condición en mayúsculas) o None si el RUC no está en el padrón.
    Lanza SunatNoDisponible si no se pudo consultar."""
    ahora = time.monotonic()
    with _candado:
        guardado = _cache.get(ruc)
        if guardado and ahora - guardado[0] < CACHE_SEGUNDOS:
            return guardado[1]

    cabeceras = {"Accept": "application/json"}
    token = os.getenv("SUNAT_RUC_TOKEN")
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(os.getenv("SUNAT_RUC_URL") or URL_POR_DEFECTO, params={"numero": ruc}, headers=cabeceras,
                         timeout=TIEMPO_MAXIMO_SEGUNDOS)
    except httpx.HTTPError as error:
        raise SunatNoDisponible(type(error).__name__) from error

    if resp.status_code == 404:
        return None  # no se guarda: un RUC recién inscrito puede aparecer pronto
    if resp.status_code != 200:
        raise SunatNoDisponible(f"HTTP {resp.status_code}")
    try:
        cuerpo = resp.json()
        datos = {
            # "razonSocial" en la v2 del servicio, "nombre" en la v1
            "razon_social": " ".join(str(cuerpo.get("razonSocial") or cuerpo.get("nombre") or "").split()),
            "estado": str(cuerpo.get("estado") or "").strip().upper(),
            "condicion": str(cuerpo.get("condicion") or "").strip().upper(),
        }
    except (ValueError, AttributeError) as error:
        raise SunatNoDisponible("respuesta que no es JSON") from error
    if not datos["razon_social"] or not datos["estado"]:
        raise SunatNoDisponible("respuesta sin razón social o sin estado")

    with _candado:
        if len(_cache) >= TAMANO_CACHE:
            _cache.pop(next(iter(_cache)))
        _cache[ruc] = (ahora, datos)
    return datos
