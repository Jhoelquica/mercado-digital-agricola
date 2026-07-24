import os
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import jwt

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRACION_MINUTOS = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hashear_password(password: str) -> str:
    return pwd_context.hash(password)


def verificar_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def crear_token(usuario_id: str, rol: str) -> str:
    expira = datetime.utcnow() + timedelta(minutes=JWT_EXPIRACION_MINUTOS)
    payload = {"sub": usuario_id, "rol": rol, "exp": expira}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)