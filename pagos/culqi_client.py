"""Las llamadas a Culqi (API v2). Montos en CÉNTIMOS (S/ 10.00 = 1000). Las respuestas se devuelven tal cual
({"status_code", "data"}): quien llama decide qué hacer con un rechazo.

Cada cobro lleva nuestra referencia (la del IntentoPago) en la metadata y en la descripción: así la conciliación lo
encuentra en Culqi aunque se haya perdido la respuesta (ver buscar_cargo), y una persona lo encuentra en el panel de
Culqi. La devolución también la manda en la metadata, pero Culqi no la guarda: la conciliación mira el cargo que se
devolvía (ver buscar_devolucion).

Lo que se comprobó contra el sandbox (2026-11-19) y en lo que se apoyan las búsquedas:
- la lista de cargos (o devoluciones) NO viene en orden de fecha (va por id) y before/after no la recorren en orden:
  no se pagina;
- creation_date_from y creation_date_to (en ms) sí filtran; un parámetro que Culqi no conoce se ignora en silencio;
- el cargo dice cuánto se le devolvió (amount_refunded): 0 si nada.

CULQI_API_BASE se cambia solo para las pruebas (un doble de Culqi en Docker); por defecto es la API real.
"""
import os
import sys
from datetime import datetime, timezone

import httpx

CULQI_SECRET_KEY = os.getenv("CULQI_SECRET_KEY") or sys.exit("Falta la variable de entorno CULQI_SECRET_KEY: el servicio no arranca sin ella.")
CULQI_API_BASE = (os.getenv("CULQI_API_BASE") or "https://api.culqi.com/v2").rstrip("/")
LARGO_MAXIMO_DESCRIPCION = 80  # el de Culqi
POR_PAGINA = 100  # el máximo que deja listar Culqi


def _cabeceras() -> dict:
    return {"Authorization": f"Bearer {CULQI_SECRET_KEY}", "Content-Type": "application/json"}


def centimos(monto_soles) -> int:
    return int(round(float(monto_soles) * 100))


def crear_cargo(token: str, monto_soles: float, email: str, descripcion: str, metadata: dict | None = None) -> dict:
    """Envía un cargo a Culqi usando un token generado en el frontend."""
    payload = {
        "amount": centimos(monto_soles),
        "currency_code": "PEN",
        "email": email,
        "source_id": token,
        "description": descripcion[:LARGO_MAXIMO_DESCRIPCION],
    }
    if metadata:
        payload["metadata"] = {clave: str(valor) for clave, valor in metadata.items()}
    with httpx.Client() as client:
        resp = client.post(f"{CULQI_API_BASE}/charges", json=payload, headers=_cabeceras(), timeout=10)
    return {"status_code": resp.status_code, "data": resp.json()}


def crear_devolucion(charge_id: str, monto_soles: float, razon: str = "solicitud_comprador",
                     metadata: dict | None = None) -> dict:
    """Devuelve (total o en parte) un cargo de Culqi."""
    payload = {"amount": centimos(monto_soles), "charge_id": charge_id, "reason": razon}
    if metadata:
        payload["metadata"] = {clave: str(valor) for clave, valor in metadata.items()}
    with httpx.Client() as client:
        resp = client.post(f"{CULQI_API_BASE}/refunds", json=payload, headers=_cabeceras(), timeout=15)
    return {"status_code": resp.status_code, "data": resp.json()}


def obtener_cargo(cargo_id: str) -> dict:
    """El cargo, tal cual ({"status_code", "data"}): con amount_refunded (lo devuelto) y la metadata."""
    with httpx.Client() as client:
        resp = client.get(f"{CULQI_API_BASE}/charges/{cargo_id}", headers=_cabeceras(), timeout=15)
    return {"status_code": resp.status_code, "data": resp.json()}


def obtener_devolucion(devolucion_id: str) -> dict:
    """La devolución, tal cual ({"status_code", "data"}): charge_id, amount y status."""
    with httpx.Client() as client:
        resp = client.get(f"{CULQI_API_BASE}/refunds/{devolucion_id}", headers=_cabeceras(), timeout=15)
    return {"status_code": resp.status_code, "data": resp.json()}


def devuelto_del_cargo(cargo_id: str) -> int:
    """Los céntimos que Culqi ya devolvió de ese cargo (amount_refunded). Si no se puede saber, lanza (httpx.HTTPError
    o ValueError): quien llama no sigue sin ese dato."""
    resultado = obtener_cargo(cargo_id)
    devuelto = resultado["data"].get("amount_refunded") if resultado["status_code"] == 200 else None
    if isinstance(devuelto, bool) or not isinstance(devuelto, (int, float)):
        raise ValueError(f"Culqi no dijo cuánto se devolvió del cargo (HTTP {resultado['status_code']})")
    return int(devuelto)


def _milisegundos(momento: datetime) -> int:
    """Una fecha UTC sin zona (como las de la base) en milisegundos desde 1970, como creation_date de Culqi."""
    return int(momento.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _listar_en_ventana(recurso: str, desde: datetime, hasta: datetime) -> list | None:
    """Los cargos ("charges") o devoluciones ("refunds") creados entre desde y hasta, en UNA consulta (no se pagina: ver
    arriba). Si algo cae fuera de la ventana, Culqi no aplicó el filtro y devuelve None: con eso no se concluye nada.
    Si Culqi no responde o responde con error, lanza (httpx.HTTPError)."""
    inicio, fin = _milisegundos(desde), _milisegundos(hasta)
    with httpx.Client() as client:
        resp = client.get(f"{CULQI_API_BASE}/{recurso}", headers=_cabeceras(), timeout=15,
                          params={"limit": POR_PAGINA, "creation_date_from": inicio, "creation_date_to": fin})
    resp.raise_for_status()
    objetos = resp.json().get("data") or []
    if any(not isinstance(o.get("creation_date"), (int, float)) or not inicio <= o["creation_date"] <= fin for o in objetos):
        return None
    return objetos


def buscar_cargo(referencia: str, desde: datetime, hasta: datetime) -> dict:
    """El cargo que lleva nuestra referencia en la metadata, creado entre desde y hasta (la hora del intento con margen).
    Devuelve {"encontrado": el cargo o None, "completo": bool}:
    - completo=True: llegó todo lo creado en la ventana; si no apareció, Culqi no lo cobró;
    - completo=False: no se puede concluir (Culqi no filtró por fecha, o la ventana trae POR_PAGINA o más).
    Si Culqi no responde, lanza: quien llama reintenta en la pasada siguiente."""
    objetos = _listar_en_ventana("charges", desde, hasta)
    if objetos is None:
        return {"encontrado": None, "completo": False}
    hallado = next((o for o in objetos if str((o.get("metadata") or {}).get("referencia")) == str(referencia)), None)
    return {"encontrado": hallado, "completo": hallado is not None or len(objetos) < POR_PAGINA}


def buscar_devolucion(cargo_id: str, monto_soles, desde: datetime, hasta: datetime, devuelto_antes=0) -> dict:
    """Si esta devolución del cargo se hizo, mirando cuánto dice Culqi que lleva devuelto (amount_refunded), ACUMULADO:
    lo que ya estaba devuelto al empezar (devuelto_antes, en céntimos) más esta devolución. Devuelve
    {"encontrado", "completo"}:
    - amount_refunded >= lo de antes + el monto: se hizo. La devolución se busca entre las de la ventana (por su
      charge_id) para tener su id; si no aparece, va {"id": None, "charge_id": ...};
    - amount_refunded == lo de antes: no se hizo (completo);
    - otro valor, o sin el campo: no se puede concluir.
    Si Culqi no responde bien al pedir el cargo, lanza (httpx.HTTPError o ValueError): quien llama reintenta en la pasada
    siguiente."""
    resultado = obtener_cargo(cargo_id)
    if resultado["status_code"] != 200:
        raise ValueError(f"Culqi respondió {resultado['status_code']} al pedir el cargo")
    devuelto = resultado["data"].get("amount_refunded")
    if isinstance(devuelto, bool) or not isinstance(devuelto, (int, float)):
        return {"encontrado": None, "completo": False}
    antes = int(devuelto_antes or 0)
    if devuelto < antes + centimos(monto_soles):
        return {"encontrado": None, "completo": devuelto == antes}
    try:
        devoluciones = _listar_en_ventana("refunds", desde, hasta) or []
    except httpx.HTTPError:  # ya se sabe que se devolvió: el id es un dato más
        devoluciones = []
    hallada = next((o for o in devoluciones if o.get("charge_id") == cargo_id), None)
    return {"encontrado": hallada or {"id": None, "charge_id": cargo_id}, "completo": True}


def cargo_exitoso(cargo: dict) -> bool:
    """Un cargo que Culqi cobró de verdad ("venta_exitosa")."""
    return ((cargo.get("outcome") or {}).get("type")) == "venta_exitosa"
