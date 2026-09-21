import os
import uuid
from minio import Minio

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "admin123")
MINIO_BUCKET = "transporte-entregas"
MINIO_PUBLIC_URL = os.getenv("MINIO_PUBLIC_URL", "http://localhost:9000")

client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,
)


def _asegurar_bucket():
    # A diferencia de "productos-imagenes" (creado a mano en MinIO), este bucket es nuevo: se crea
    # solo la primera vez para que la feature funcione sin un paso manual previo. Sin política
    # pública a propósito — es evidencia de entrega, no una imagen de catálogo.
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)


EXTENSIONES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def subir_foto_entrega(archivo, content_type: str) -> str:
    _asegurar_bucket()
    nombre_archivo = f"{uuid.uuid4()}{EXTENSIONES.get(content_type, '.jpg')}"
    client.put_object(
        MINIO_BUCKET,
        nombre_archivo,
        archivo,
        length=-1,
        part_size=10 * 1024 * 1024,
        content_type=content_type,
    )
    return f"{MINIO_PUBLIC_URL}/{MINIO_BUCKET}/{nombre_archivo}"
