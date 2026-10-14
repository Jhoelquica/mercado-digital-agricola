"""Claves VAPID para Web Push (H6): un par P-256 en el formato que esperan el navegador y pywebpush.

  - pública: el punto sin comprimir (65 bytes) en base64url sin "=": es la applicationServerKey del navegador.
  - privada: el número de 32 bytes en base64url sin "=" (py_vapid.Vapid.from_string la acepta así).

Solo usa `cryptography`, para que scripts/generar_claves_avisos.py corra fuera de la imagen del servicio.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def generar() -> tuple[str, str]:
    """Devuelve (pública, privada)."""
    privada = ec.generate_private_key(ec.SECP256R1())
    publica = privada.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64url(publica), _b64url(privada.private_numbers().private_value.to_bytes(32, "big"))
