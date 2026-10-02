"""Constancias de las transferencias bancarias (la foto o captura del comprobante que sube el comprador).

Bucket PRIVADO (sin política de lectura pública, a diferencia de las fotos de productos y de cosechas): una constancia
muestra datos bancarios del comprador. Nadie la ve por una URL de MinIO: el admin la pide a Pagos
(GET /pagos/admin/transferencias/{pedido_id}/constancia), que la lee con sus credenciales y la devuelve.
"""
import io
import os
import uuid

from minio import Minio
from minio.error import S3Error

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "admin123")
MINIO_BUCKET = "pagos-constancias"

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,
)

EXTENSIONES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _asegurar_bucket() -> None:
    try:
        if not client.bucket_exists(MINIO_BUCKET):
            client.make_bucket(MINIO_BUCKET)
    except S3Error as error:
        if error.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):  # otra réplica lo creó a la vez
            raise


def subir_constancia(datos: bytes, content_type: str) -> str:
    """Guarda la constancia (ya validada, ver imagenes.py) y devuelve el nombre del objeto en el bucket."""
    _asegurar_bucket()
    nombre = f"{uuid.uuid4()}{EXTENSIONES.get(content_type, '.jpg')}"
    client.put_object(MINIO_BUCKET, nombre, io.BytesIO(datos), length=len(datos), content_type=content_type)
    return nombre


def leer_constancia(nombre: str) -> bytes:
    respuesta = client.get_object(MINIO_BUCKET, nombre)
    try:
        return respuesta.read()
    finally:
        respuesta.close()
        respuesta.release_conn()
