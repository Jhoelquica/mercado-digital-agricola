"""Fotos de las cosechas: la evidencia en origen que reemplazó al Verificador (ver completar_cosecha en main.py).

Bucket propio y de lectura pública, como el de las fotos de productos: la foto se muestra en la página del QR. Lo que
la protege no es ocultarla sino su huella SHA-256, que queda sellada en la cadena de Certificación: si alguien cambiara
el archivo después, la huella dejaría de coincidir. Escribir sigue exigiendo las credenciales de MinIO.
"""
import io
import json
import logging
import os
import uuid

from minio import Minio
from minio.error import S3Error

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "admin123")
MINIO_BUCKET = "cosechas-evidencias"
MINIO_PUBLIC_URL = os.getenv("MINIO_PUBLIC_URL", "http://localhost:9000")

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,
)

EXTENSIONES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

logger = logging.getLogger(__name__)


def asegurar_bucket_publico() -> None:
    """Crea el bucket si no existe y lo deja de lectura pública. Idempotente: se llama en cada arranque y, si un MinIO
    nuevo todavía no lo tiene, también en la primera subida."""
    try:
        if not client.bucket_exists(MINIO_BUCKET):
            client.make_bucket(MINIO_BUCKET)
    except S3Error as error:
        if error.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):  # otra réplica lo creó a la vez
            raise
    politica = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{MINIO_BUCKET}/*"],
        }],
    }
    client.set_bucket_policy(MINIO_BUCKET, json.dumps(politica))


def subir_foto_cosecha(datos: bytes, content_type: str) -> str:
    """Guarda la foto (ya validada, ver imagenes.py) y devuelve su URL pública."""
    nombre_archivo = f"{uuid.uuid4()}{EXTENSIONES.get(content_type, '.jpg')}"

    def _subir():
        client.put_object(MINIO_BUCKET, nombre_archivo, io.BytesIO(datos), length=len(datos), content_type=content_type)

    try:
        _subir()
    except S3Error as error:
        if error.code != "NoSuchBucket":
            raise
        asegurar_bucket_publico()
        _subir()
    return f"{MINIO_PUBLIC_URL}/{MINIO_BUCKET}/{nombre_archivo}"
