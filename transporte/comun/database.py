# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""La conexión del servicio a su base de datos (SQLAlchemy) y la sesión de cada petición.

Todo sale de variables de entorno:
  DB_USER, DB_PASSWORD, DB_NAME   obligatorias: sin una de ellas el servicio no arranca.
  DB_HOST                         por defecto localhost.
  DB_PORT                         por defecto 5432.
  DB_POOL_SIZE, DB_MAX_OVERFLOW   conexiones por proceso: las que el pool mantiene abiertas y las que puede abrir de más
                                  en un momento de carga. Por defecto 5 y 10 (los valores de SQLAlchemy).
"""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()


def _obligatoria(nombre: str) -> str:
    return os.getenv(nombre) or sys.exit(f"Falta la variable de entorno {nombre}: el servicio no arranca sin ella.")


def _entero(nombre: str, por_defecto: int) -> int:
    valor = os.getenv(nombre)
    if not valor:
        return por_defecto
    try:
        return int(valor)
    except ValueError:
        sys.exit(f"La variable de entorno {nombre} debe ser un número entero, no {valor!r}: el servicio no arranca.")


# URL.create y no un f-string: escapa la contraseña. Con el f-string, una contraseña con @ : / # o ? rompía la URL.
URL_DE_LA_BASE = URL.create(
    "postgresql",
    username=_obligatoria("DB_USER"),
    password=_obligatoria("DB_PASSWORD"),
    host=os.getenv("DB_HOST") or "localhost",
    port=_entero("DB_PORT", 5432),
    database=_obligatoria("DB_NAME"),
)

# pool_pre_ping: antes de usar una conexión del pool comprueba que siga viva. Sin esto, cuando la base se reinicia (una
# actualización, el mantenimiento del nodo) cada conexión vieja del pool falla UNA vez con un 500 antes de renovarse.
# connect_timeout: una base que no contesta (en vez de rechazar) dejaba colgada la petición hasta el límite de TCP, unos
# 2 minutos; ahora una conexión nueva se rinde a los 5 segundos.
engine = create_engine(
    URL_DE_LA_BASE,
    pool_pre_ping=True,
    pool_size=_entero("DB_POOL_SIZE", 5),
    max_overflow=_entero("DB_MAX_OVERFLOW", 10),
    connect_args={"connect_timeout": 5},
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_db():
    """La sesión de una petición (`db: Session = Depends(get_db)`). Se cierra al terminar la petición, y al cerrarse se
    deshace lo que no se haya confirmado con commit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
