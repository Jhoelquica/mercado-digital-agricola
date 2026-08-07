import os
import uuid
from minio import Minio

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


def subir_imagen(archivo, content_type: str) -> str:
    nombre_archivo = f"{uuid.uuid4()}.jpg"
    client.put_object(
        MINIO_BUCKET,
        nombre_archivo,
        archivo,
        length=-1,
        part_size=10 * 1024 * 1024,
        content_type=content_type,
    )
    return f"{MINIO_PUBLIC_URL}/{MINIO_BUCKET}/{nombre_archivo}"