"""Documentos de los reclamos de RUC (la Ficha RUC que sube quien dice ser el dueño del negocio).

Bucket PRIVADO, como las constancias de transferencia de Pagos: un documento así trae datos del negocio y de su
representante. Nadie lo ve por una URL de MinIO: el admin lo pide a Usuarios
(GET /usuarios/admin/reclamos-ruc/{id}/documento), que lo lee con sus credenciales y lo devuelve.
"""
import io
import os
import uuid

from minio import Minio
from minio.error import S3Error

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "admin123")
MINIO_BUCKET = "usuarios-reclamos-ruc"

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,
)

EXTENSIONES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}


def _asegurar_bucket() -> None:
    try:
        if not client.bucket_exists(MINIO_BUCKET):
            client.make_bucket(MINIO_BUCKET)
    except S3Error as error:
        if error.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):  # otra réplica lo creó a la vez
            raise


def subir_documento_reclamo(datos: bytes, content_type: str) -> str:
    """Guarda el documento (ya validado, ver documentos.py) y devuelve el nombre del objeto en el bucket."""
    _asegurar_bucket()
    nombre = f"{uuid.uuid4()}{EXTENSIONES.get(content_type, '.bin')}"
    client.put_object(MINIO_BUCKET, nombre, io.BytesIO(datos), length=len(datos), content_type=content_type)
    return nombre


def leer_documento_reclamo(nombre: str) -> bytes:
    respuesta = client.get_object(MINIO_BUCKET, nombre)
    try:
        return respuesta.read()
    finally:
        respuesta.close()
        respuesta.release_conn()
