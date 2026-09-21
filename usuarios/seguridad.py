import os
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import jwt

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRACION_MINUTOS = 60

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


def crear_token(usuario_id: str, rol: str) -> str:
    expira = datetime.utcnow() + timedelta(minutes=JWT_EXPIRACION_MINUTOS)
    payload = {"sub": usuario_id, "rol": rol, "exp": expira}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)