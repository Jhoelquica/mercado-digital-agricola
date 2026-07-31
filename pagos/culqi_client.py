import os
import httpx

CULQI_SECRET_KEY = os.getenv("CULQI_SECRET_KEY")
CULQI_API_URL = "https://api.culqi.com/v2/charges"


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