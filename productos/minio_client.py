import json
import logging
import os
import uuid
from minio import Minio
from minio.error import S3Error

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "admin123")
MINIO_BUCKET = "productos-imagenes"
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
    """Deja listo el bucket de imágenes: lo crea si no existe y lo pone de lectura pública (las fotos de los productos
    las ve cualquiera por su URL; escribir sigue exigiendo credenciales). Idempotente: se llama en cada arranque.
    Antes este bucket se preparaba a mano en la consola de MinIO, así que un MinIO nuevo (otro entorno) rechazaba toda
    subida con NoSuchBucket y, aunque se creara, sus fotos no se veían."""
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


def subir_imagen(archivo, content_type: str) -> str:
    nombre_archivo = f"{uuid.uuid4()}{EXTENSIONES.get(content_type, '.jpg')}"

    def _subir():
        client.put_object(
            MINIO_BUCKET,
            nombre_archivo,
            archivo,
            length=-1,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )

    try:
        _subir()
    except S3Error as error:
        if error.code != "NoSuchBucket":
            raise
        # MinIO nuevo o bucket borrado: se prepara y se reintenta una sola vez.
        asegurar_bucket_publico()
        archivo.seek(0)
        _subir()
    return f"{MINIO_PUBLIC_URL}/{MINIO_BUCKET}/{nombre_archivo}"