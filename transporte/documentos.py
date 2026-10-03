"""Los documentos de un transportista de carga (licencia de conducir, tarjeta de propiedad del vehículo y SOAT), en
foto o PDF. Copia de usuarios/documentos.py: cada microservicio es autosuficiente.

Mismo criterio que imagenes.py en los demás servicios: tope de tamaño y el tipo se decide por la firma del contenido,
no por lo que declara el cliente (así no se cuela un HTML o un SVG con scripts llamándolo "application/pdf").
"""
from fastapi import HTTPException, UploadFile

TAMANO_MAXIMO_BYTES = 5 * 1024 * 1024
# El cuerpo completo de la petición: sus tres documentos más el "sobre" multipart y los datos del formulario. Se
# comprueba por Content-Length antes de leer nada (ver el middleware de main.py).
LIMITE_CUERPO_DOCUMENTOS_BYTES = 3 * TAMANO_MAXIMO_BYTES + 512 * 1024
MENSAJE_MUY_GRANDE = "Cada documento puede pesar hasta 5 MB."
MENSAJE_TIPO_NO_VALIDO = "El documento tiene que ser una foto (JPEG, PNG o WEBP) o un PDF."


def tipo_segun_el_contenido(datos: bytes):
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(datos) >= 12 and datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    if datos.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def leer_documento_validado(archivo: UploadFile):
    """(bytes, tipo detectado). 413 si pesa más del tope, 422 si no es una imagen ni un PDF de verdad."""
    datos = archivo.file.read(TAMANO_MAXIMO_BYTES + 1)
    if len(datos) > TAMANO_MAXIMO_BYTES:
        raise HTTPException(status_code=413, detail=MENSAJE_MUY_GRANDE)
    tipo = tipo_segun_el_contenido(datos)
    if tipo is None:
        raise HTTPException(status_code=422, detail=MENSAJE_TIPO_NO_VALIDO)
    return datos, tipo
