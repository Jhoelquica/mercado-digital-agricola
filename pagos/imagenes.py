"""Validación de las imágenes que suben los usuarios (foto de un producto, de entrega, de la cosecha, constancia de pago).

Antes solo se miraba el content-type que DECLARA el cliente y no había tope de tamaño: cualquiera con sesión de
productor o repartidor podía subir un archivo cualquiera (un HTML, un SVG con scripts, un vídeo de gigas) con
solo ponerle "image/png". Ahora se limita el tamaño y se comprueba el contenido real por su firma; lo que se
guarda lleva el tipo que se detectó, no el que dijo el cliente. Cada microservicio es autosuficiente (no hay
librería común): el mismo módulo vive en productos/, transporte/, productores/ y pagos/.
"""
from fastapi import HTTPException, UploadFile

TAMANO_MAXIMO_BYTES = 5 * 1024 * 1024
# Lo máximo que se acepta del cuerpo completo de la petición: la imagen más el "sobre" multipart (nombres de
# campo y cabeceras). Se comprueba por Content-Length antes de leer nada (ver el middleware de main.py).
LIMITE_CUERPO_BYTES = TAMANO_MAXIMO_BYTES + 512 * 1024
MENSAJE_MUY_GRANDE = "La imagen no puede pesar más de 5 MB."
MENSAJE_NO_ES_IMAGEN = "El archivo no es una imagen JPEG, PNG o WEBP válida."


def tipo_segun_el_contenido(datos: bytes):
    """El tipo real de la imagen por su firma, o None si no es JPEG, PNG ni WEBP."""
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(datos) >= 12 and datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


def leer_imagen_validada(archivo: UploadFile):
    """Lee el archivo (como mucho el tope + 1 byte) y devuelve (bytes, tipo detectado). 413 si pesa más del
    tope, 422 si no es una imagen de verdad."""
    datos = archivo.file.read(TAMANO_MAXIMO_BYTES + 1)
    if len(datos) > TAMANO_MAXIMO_BYTES:
        raise HTTPException(status_code=413, detail=MENSAJE_MUY_GRANDE)
    tipo = tipo_segun_el_contenido(datos)
    if tipo is None:
        raise HTTPException(status_code=422, detail=MENSAJE_NO_ES_IMAGEN)
    return datos, tipo
