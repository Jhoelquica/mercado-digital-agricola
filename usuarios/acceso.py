"""Quién puede registrarse con qué contraseña y cuántas veces se puede fallar al iniciar sesión.

- Contraseña nueva (registro): al menos 8 caracteres, como máximo 72 bytes (bcrypt ignora en silencio lo que
  pase de ahí), no solo espacios, no una de las contraseñas más comunes y no igual al correo. Las cuentas que
  ya existen con una contraseña más corta siguen entrando: la regla se aplica solo al registrarse.
- Login: tras MAX_INTENTOS_LOGIN contraseñas incorrectas seguidas para un mismo correo, ese correo no puede
  iniciar sesión durante BLOQUEO_LOGIN_MINUTOS (ni siquiera con la contraseña correcta). Se cuenta por correo y
  también para correos que NO existen, con la misma respuesta: si solo se bloquearan los que existen, el
  bloqueo serviría para averiguar qué correos están registrados. Un inicio de sesión correcto reinicia la
  cuenta, y los fallos de hace más de una hora ya no cuentan (errores sueltos de otro día no bloquean a nadie).
  La tabla intentos_login es nueva: la crea Base.metadata.create_all() al arrancar, sin migración.
"""
import math
import os
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import case, delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

import models

# Ajustables sin tocar código (ver k8s/usuarios.yaml).
MAX_INTENTOS_LOGIN = int(os.getenv("MAX_INTENTOS_LOGIN", "8"))
BLOQUEO_LOGIN_MINUTOS = int(os.getenv("BLOQUEO_LOGIN_MINUTOS", "10"))
OLVIDO_DE_FALLOS_MINUTOS = 60
AVISAR_CUANDO_QUEDEN = 3  # a partir de cuántos intentos restantes el mensaje avisa del bloqueo

PASSWORD_MINIMO = 8
PASSWORD_MAXIMO_BYTES = 72

# Las contraseñas de 8 o más caracteres que más se repiten en las filtraciones (y las del propio proyecto).
CONTRASENAS_COMUNES = frozenset({
    "12345678", "123456789", "1234567890", "12345678910", "11111111", "00000000", "87654321", "123123123",
    "password", "password1", "password12", "password123", "passw0rd", "contrasena", "contraseña", "contrasena1",
    "qwertyui", "qwerty123", "qwertyuiop", "1q2w3e4r", "abc12345", "abcd1234", "iloveyou", "chakrashop",
    "admin123", "administrador", "bienvenido", "bienvenida", "letmein1", "trustno1",
})


def normalizar_email(email) -> str:
    return str(email).strip().lower()[:254]


def validar_password_nueva(password: str, email: str | None = None) -> str:
    """Devuelve la contraseña si es aceptable; si no, lanza ValueError con el motivo en español."""
    if len(password) < PASSWORD_MINIMO:
        raise ValueError(f"La contraseña debe tener al menos {PASSWORD_MINIMO} caracteres")
    if len(password.encode("utf-8")) > PASSWORD_MAXIMO_BYTES:
        raise ValueError(f"La contraseña es demasiado larga (máximo {PASSWORD_MAXIMO_BYTES} caracteres)")
    if not password.strip():
        raise ValueError("La contraseña no puede ser solo espacios")
    if password.lower() in CONTRASENAS_COMUNES:
        raise ValueError("Esa contraseña es demasiado común: elige otra")
    if email and password.strip().lower() == str(email).strip().lower():
        raise ValueError("La contraseña no puede ser igual a tu correo")
    return password


def comprobar_que_no_esta_bloqueado(db, email) -> None:
    fila = db.get(models.IntentoLogin, normalizar_email(email))
    ahora = datetime.utcnow()
    if fila is not None and fila.bloqueado_hasta is not None and fila.bloqueado_hasta > ahora:
        segundos = (fila.bloqueado_hasta - ahora).total_seconds()
        minutos = max(math.ceil(segundos / 60), 1)
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos fallidos. Vuelve a intentarlo en {minutos} minuto(s).",
            headers={"Retry-After": str(math.ceil(segundos))},
        )


def registrar_fallo(db, email) -> int:
    """Cuenta un intento fallido y devuelve cuántos quedan antes del bloqueo. Si este agota el tope, bloquea y
    responde 429 en el mismo intento (el atacante y el usuario lo ven al momento, no en el siguiente)."""
    clave = normalizar_email(email)
    ahora = datetime.utcnow()

    # Limpieza oportunista: una fila sin bloqueo vigente y sin fallos recientes ya no le sirve a nadie.
    db.execute(delete(models.IntentoLogin).where(
        models.IntentoLogin.ultimo_fallo < ahora - timedelta(hours=1),
        (models.IntentoLogin.bloqueado_hasta.is_(None)) | (models.IntentoLogin.bloqueado_hasta < ahora),
    ))

    # Un solo INSERT ... ON CONFLICT DO UPDATE: dos fallos simultáneos del mismo correo se suman uno tras otro
    # (la fila se bloquea), no se pisan.
    olvido = ahora - timedelta(minutes=OLVIDO_DE_FALLOS_MINUTOS)
    sentencia = pg_insert(models.IntentoLogin).values(email=clave, fallos=1, ultimo_fallo=ahora)
    sentencia = sentencia.on_conflict_do_update(
        index_elements=[models.IntentoLogin.email],
        set_={
            "fallos": case((models.IntentoLogin.ultimo_fallo < olvido, 1), else_=models.IntentoLogin.fallos + 1),
            "ultimo_fallo": ahora,
        },
    ).returning(models.IntentoLogin.fallos)
    fallos = db.execute(sentencia).scalar_one()

    if fallos >= MAX_INTENTOS_LOGIN:
        db.execute(update(models.IntentoLogin).where(models.IntentoLogin.email == clave).values(
            fallos=0, bloqueado_hasta=ahora + timedelta(minutes=BLOQUEO_LOGIN_MINUTOS),
        ))
        db.commit()
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos fallidos. Por seguridad, este acceso se bloqueó {BLOQUEO_LOGIN_MINUTOS} minutos.",
            headers={"Retry-After": str(BLOQUEO_LOGIN_MINUTOS * 60)},
        )
    db.commit()
    return MAX_INTENTOS_LOGIN - fallos


def limpiar_fallos(db, email) -> None:
    db.execute(delete(models.IntentoLogin).where(models.IntentoLogin.email == normalizar_email(email)))
    db.commit()
