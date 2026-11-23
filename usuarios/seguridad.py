"""Contraseñas (bcrypt) y la firma de los tokens de sesión (RS256).

Solo Usuarios firma: la clave privada está en JWT_CLAVE_PRIVADA (un archivo del Secret jwt-firma, montado solo en sus
pods). Cada token lleva en su encabezado el kid de esa clave (comun/auth.kid_de) y los demás servicios lo verifican con
las claves públicas de JWT_CLAVES_PUBLICAS (comun/auth.py). Si la clave pública de la que firma no está publicada, el
servicio no arranca: ningún servicio aceptaría sus tokens.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from passlib.context import CryptContext

from comun import auth

JWT_EXPIRACION_MINUTOS = 60
RUTA_CLAVE_PRIVADA = os.getenv("JWT_CLAVE_PRIVADA") or sys.exit(
    "Falta la variable de entorno JWT_CLAVE_PRIVADA (el archivo con la clave privada que firma los tokens; solo "
    "Usuarios la tiene): el servicio no arranca sin ella.")


def _cargar_clave_privada(ruta) -> RSAPrivateKey:
    clave = serialization.load_pem_private_key(Path(ruta).read_bytes(), password=None)
    if not isinstance(clave, RSAPrivateKey) or clave.key_size < auth.BITS_MINIMOS:
        raise ValueError(f"no es una clave privada RSA de {auth.BITS_MINIMOS} bits o más")
    return clave


try:
    CLAVE_PRIVADA = _cargar_clave_privada(RUTA_CLAVE_PRIVADA)
except (OSError, ValueError, TypeError) as error:
    # Solo el tipo y la ruta: el mensaje nunca lleva nada de la clave.
    sys.exit(f"No se pudo leer la clave privada del JWT en {RUTA_CLAVE_PRIVADA} ({type(error).__name__}).")
KID = auth.kid_de(CLAVE_PRIVADA.public_key())
if KID not in auth.claves_publicas():
    sys.exit(f"La clave pública de la clave que firma (kid {KID}) no está entre las publicadas en {auth.CARPETA_CLAVES}: "
             "ningún servicio aceptaría los tokens. Publícala primero (README, \"Claves de firma de los tokens\").")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Hash de una contraseña que nadie tiene: el login lo compara cuando el correo no existe (o la cuenta es solo de
# Google) para gastar el mismo tiempo que con una cuenta real. Sin esto, responder más rápido a un correo
# desconocido delataba qué correos están registrados.
HASH_DE_RELLENO = pwd_context.hash("esta-contrasena-no-es-de-nadie")


def hashear_password(password: str) -> str:
    return pwd_context.hash(password)


def verificar_password(password: str, password_hash: str | None) -> bool:
    # None = cuenta creada por Google, sin contraseña propia (ver Usuario.password_hash en
    # models.py) — pwd_context.verify() no acepta None como hash, así que sin este guard el
    # login normal contra esa cuenta tiraría una excepción sin capturar en vez de un 401 claro.
    if password_hash is None:
        return False
    return pwd_context.verify(password, password_hash)


def crear_token(usuario_id: str, rol: str, version_credencial: int) -> str:
    """El token de la sesión, firmado con RS256 y el kid de la clave que firma. "ver" es la version_credencial de la
    cuenta al emitirlo: cuando sube (sesiones.py), el ForwardAuth del gateway rechaza este token en cualquier ruta."""
    ahora = datetime.now(timezone.utc)
    payload = {"sub": usuario_id, "rol": rol, "ver": int(version_credencial), "iss": auth.EMISOR, "aud": auth.AUDIENCIA,
               "iat": ahora, "exp": ahora + timedelta(minutes=JWT_EXPIRACION_MINUTOS)}
    return jwt.encode(payload, CLAVE_PRIVADA, algorithm=auth.ALGORITMO, headers={"kid": KID})
