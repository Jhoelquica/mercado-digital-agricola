"""Las llamadas a Culqi (API v2). Montos en CÉNTIMOS (S/ 10.00 = 1000). Las respuestas se devuelven tal cual
({"status_code", "data"}): quien llama decide qué hacer con un rechazo.

Cada cobro y cada devolución llevan nuestra referencia (la del IntentoPago) en la metadata, y la del cobro también en la
descripción: así la conciliación los encuentra en Culqi aunque se haya perdido la respuesta (ver buscar_por_referencia),
y una persona los encuentra en el panel de Culqi.

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
PAGINAS_MAXIMAS = 5  # hasta 500 cargos (o devoluciones) hacia atrás por búsqueda


def _cabeceras() -> dict:
    return {"Authorization": f"Bearer {CULQI_SECRET_KEY}", "Content-Type": "application/json"}


def crear_cargo(token: str, monto_soles: float, email: str, descripcion: str, metadata: dict | None = None) -> dict:
    """Envía un cargo a Culqi usando un token generado en el frontend."""
    payload = {
        "amount": int(round(monto_soles * 100)),
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
    payload = {"amount": int(round(monto_soles * 100)), "charge_id": charge_id, "reason": razon}
    if metadata:
        payload["metadata"] = {clave: str(valor) for clave, valor in metadata.items()}
    with httpx.Client() as client:
        resp = client.post(f"{CULQI_API_BASE}/refunds", json=payload, headers=_cabeceras(), timeout=15)
    return {"status_code": resp.status_code, "data": resp.json()}


def _milisegundos(momento: datetime) -> int:
    """Una fecha UTC sin zona (como las de la base) en milisegundos desde 1970, como creation_date de Culqi."""
    return int(momento.replace(tzinfo=timezone.utc).timestamp() * 1000)


def buscar_por_referencia(recurso: str, referencia: str, desde: datetime) -> dict:
    """Busca en Culqi el cargo ("charges") o la devolución ("refunds") que lleva nuestra referencia en la metadata.

    La API no filtra por metadata: se recorren los más nuevos primero, de a POR_PAGINA, hasta pasar `desde` (la hora en
    que empezó el intento, con margen). Devuelve {"encontrado": el objeto o None, "completo": bool}:
    - completo=True: se miró todo lo creado desde `desde`; si no apareció, no existe;
    - completo=False: se cortó antes (demasiados, o la paginación no siguió): no se puede concluir nada.
    Si Culqi no responde, lanza (httpx.HTTPError): quien llama reintenta en la pasada siguiente."""
    limite = _milisegundos(desde)
    parametros = {"limit": POR_PAGINA}
    with httpx.Client() as client:
        for _ in range(PAGINAS_MAXIMAS):
            resp = client.get(f"{CULQI_API_BASE}/{recurso}", params=parametros, headers=_cabeceras(), timeout=15)
            resp.raise_for_status()
            cuerpo = resp.json()
            objetos = cuerpo.get("data") or []
            for objeto in objetos:
                if str((objeto.get("metadata") or {}).get("referencia")) == str(referencia):
                    return {"encontrado": objeto, "completo": True}
            fechas = [o["creation_date"] for o in objetos if isinstance(o.get("creation_date"), (int, float))]
            if not objetos or len(objetos) < POR_PAGINA or (fechas and min(fechas) < limite):
                return {"encontrado": None, "completo": True}
            cursor = ((cuerpo.get("paging") or {}).get("cursors") or {}).get("before") or objetos[-1].get("id")
            if not cursor:
                return {"encontrado": None, "completo": False}
            parametros = {"limit": POR_PAGINA, "before": cursor}
    return {"encontrado": None, "completo": False}


def cargo_exitoso(cargo: dict) -> bool:
    """Un cargo que Culqi cobró de verdad ("venta_exitosa")."""
    return ((cargo.get("outcome") or {}).get("type")) == "venta_exitosa"
