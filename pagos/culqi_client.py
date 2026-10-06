import os
import httpx

CULQI_SECRET_KEY = os.getenv("CULQI_SECRET_KEY")
CULQI_API_URL = "https://api.culqi.com/v2/charges"
CULQI_DEVOLUCIONES_URL = "https://api.culqi.com/v2/refunds"


def crear_cargo(token: str, monto_soles: float, email: str, descripcion: str) -> dict:
    """
    Envía un cargo a Culqi usando un token generado en el frontend.
    Culqi trabaja los montos en CÉNTIMOS (ej. S/10.00 = 1000).
    """
    headers = {
        "Authorization": f"Bearer {CULQI_SECRET_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "amount": int(round(monto_soles * 100)),
        "currency_code": "PEN",
        "email": email,
        "source_id": token,
        "description": descripcion,
    }

    with httpx.Client() as client:
        resp = client.post(CULQI_API_URL, json=payload, headers=headers, timeout=10)

    return {"status_code": resp.status_code, "data": resp.json()}


def crear_devolucion(charge_id: str, monto_soles: float, razon: str = "solicitud_comprador") -> dict:
    """Devuelve (total o en parte) un cargo de Culqi. Mismo trato que crear_cargo: montos en céntimos y la respuesta tal
    cual, para que quien llama decida qué hacer con un rechazo."""
    headers = {"Authorization": f"Bearer {CULQI_SECRET_KEY}", "Content-Type": "application/json"}
    payload = {"amount": int(round(monto_soles * 100)), "charge_id": charge_id, "reason": razon}
    with httpx.Client() as client:
        resp = client.post(CULQI_DEVOLUCIONES_URL, json=payload, headers=headers, timeout=15)
    return {"status_code": resp.status_code, "data": resp.json()}
