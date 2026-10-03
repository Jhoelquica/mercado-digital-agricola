"""El DNI de quien recibe un pedido en otra ciudad, consultado en RENIEC (decisión del usuario, 2026-10-03): en el Perú
muchos compradores no tienen RUC pero sí DNI, y la agencia de carga o el camión entregan solo a quien muestra ese DNI.

RENIEC no tiene una API pública gratuita; se usa el mismo servicio que Usuarios usa para SUNAT (apis.net.pe), que el
2026-10-03 respondía sin clave. Se cambia sin tocar el código: RENIEC_DNI_URL, y RENIEC_TOKEN (opcional).

Al comprador solo se le muestra el nombre ENMASCARADO (nombre_para_confirmar): alcanza para que reconozca el suyo, pero
el endpoint no sirve para averiguar el nombre completo de cualquier DNI. El nombre completo se guarda en el pedido (lo
necesitan la agencia y el chofer) y no sale de Pedidos hacia el comprador.
"""
import os
import threading
import time

import httpx

URL_POR_DEFECTO = "https://api.apis.net.pe/v2/reniec/dni"
TIEMPO_MAXIMO_SEGUNDOS = 8
CACHE_SEGUNDOS = 24 * 3600
TAMANO_CACHE = 1000

_cache = {}
_candado = threading.Lock()


class ReniecNoDisponible(Exception):
    """El servicio no respondió, tardó o respondió algo que no se entiende: no se puede afirmar nada del DNI."""


def _nombre_de(cuerpo: dict) -> str:
    """El nombre completo, venga en el formato que venga (v2: nombres + apellidos; v1: "nombre"; otros: nombreCompleto
    o full_name)."""
    partes = [cuerpo.get("nombres") or cuerpo.get("first_name"),
              cuerpo.get("apellidoPaterno") or cuerpo.get("first_last_name"),
              cuerpo.get("apellidoMaterno") or cuerpo.get("second_last_name")]
    if partes[0] and (partes[1] or partes[2]):
        texto = " ".join(str(p) for p in partes if p)
    else:
        texto = cuerpo.get("nombreCompleto") or cuerpo.get("full_name") or cuerpo.get("nombre") or ""
    return " ".join(str(texto).split()).upper()


def consultar_dni(dni: str):
    """{"nombre": "NOMBRES APELLIDOS"} o None si RENIEC no tiene ese DNI. Lanza ReniecNoDisponible si no se pudo
    consultar."""
    ahora = time.monotonic()
    with _candado:
        guardado = _cache.get(dni)
        if guardado and ahora - guardado[0] < CACHE_SEGUNDOS:
            return guardado[1]

    cabeceras = {"Accept": "application/json"}
    token = os.getenv("RENIEC_TOKEN")
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(os.getenv("RENIEC_DNI_URL") or URL_POR_DEFECTO, params={"numero": dni}, headers=cabeceras,
                         timeout=TIEMPO_MAXIMO_SEGUNDOS)
    except httpx.HTTPError as error:
        raise ReniecNoDisponible(type(error).__name__) from error

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ReniecNoDisponible(f"HTTP {resp.status_code}")
    try:
        nombre = _nombre_de(resp.json())
    except (ValueError, AttributeError) as error:
        raise ReniecNoDisponible("respuesta que no es JSON") from error
    if not nombre:
        raise ReniecNoDisponible("respuesta sin nombre")

    datos = {"nombre": nombre}
    with _candado:
        if len(_cache) >= TAMANO_CACHE:
            _cache.pop(next(iter(_cache)))
        _cache[dni] = (ahora, datos)
    return datos


def nombre_para_confirmar(nombre: str) -> str:
    """ "ROSA ELENA QUISPE HUAMAN" -> "ROSA E. Q*** H***": el primer nombre entero y el resto solo con su inicial."""
    palabras = nombre.split()
    if not palabras:
        return ""
    resto = [f"{p[0]}." if i < len(palabras) - 2 else f"{p[0]}***" for i, p in enumerate(palabras[1:], start=1)]
    return " ".join([palabras[0], *resto])
