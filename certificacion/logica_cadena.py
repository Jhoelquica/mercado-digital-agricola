import hashlib
import models
from datetime import datetime
import math
import httpx
import os
from fastapi import HTTPException

HASH_GENESIS = "0" * 64

RADIO_GEOFENCING_KM = 1.5
PRODUCTORES_URL = os.getenv("PRODUCTORES_URL", "http://productores:8001")


def calcular_distancia_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def validar_geofencing(token: str, latitud_evento: str, longitud_evento: str):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        respuesta = httpx.get(f"{PRODUCTORES_URL}/productores/me", headers=headers, timeout=5)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="No se pudo validar la ubicación con el servicio de Productores")

    if respuesta.status_code != 200:
        raise HTTPException(status_code=422, detail="No se encontró tu perfil de productor para validar ubicación")

    productor = respuesta.json()
    lat_registrada = productor.get("latitud")
    lon_registrada = productor.get("longitud")

    if not lat_registrada or not lon_registrada:
        raise HTTPException(status_code=422, detail="Tu perfil de productor no tiene ubicación registrada")

    distancia = calcular_distancia_km(lat_registrada, lon_registrada, latitud_evento, longitud_evento)

    if distancia > RADIO_GEOFENCING_KM:
        raise HTTPException(
            status_code=422,
            detail=f"La ubicación registrada está a {distancia:.2f} km de tu parcela (máximo permitido: {RADIO_GEOFENCING_KM} km)"
        )

def calcular_hash(indice, producto_id, evento, datos, fecha, hash_anterior) -> str:
    contenido = f"{indice}{producto_id}{evento}{datos}{fecha}{hash_anterior}"
    return hashlib.sha256(contenido.encode()).hexdigest()


def crear_bloque(db, producto_id: str, evento: str, datos: str = None) -> models.Bloque:
    ultimo = db.query(models.Bloque).filter(
        models.Bloque.producto_id == producto_id
    ).order_by(models.Bloque.indice.desc()).first()

    if ultimo:
        indice = ultimo.indice + 1
        hash_anterior = ultimo.hash_actual
    else:
        indice = 0
        hash_anterior = HASH_GENESIS

    fecha = datetime.utcnow()
    hash_actual = calcular_hash(indice, producto_id, evento, datos, str(fecha), hash_anterior)

    nuevo_bloque = models.Bloque(
        producto_id=producto_id,
        indice=indice,
        evento=evento,
        datos=datos,
        fecha=fecha,
        hash_anterior=hash_anterior,
        hash_actual=hash_actual,
    )
    db.add(nuevo_bloque)
    db.commit()
    db.refresh(nuevo_bloque)
    return nuevo_bloque


def verificar_cadena(db, producto_id: str) -> dict:
    bloques = db.query(models.Bloque).filter(
        models.Bloque.producto_id == producto_id
    ).order_by(models.Bloque.indice).all()

    if not bloques:
        return {"valido": True, "total_bloques": 0, "mensaje": "Sin historial de certificación aún"}

    hash_esperado = HASH_GENESIS
    for bloque in bloques:
        recalculado = calcular_hash(
            bloque.indice, str(bloque.producto_id), bloque.evento,
            bloque.datos, str(bloque.fecha), hash_esperado,
        )
        if bloque.hash_anterior != hash_esperado or bloque.hash_actual != recalculado:
            return {
                "valido": False,
                "total_bloques": len(bloques),
                "mensaje": f"Alteración detectada en el bloque #{bloque.indice} ({bloque.evento})",
            }
        hash_esperado = bloque.hash_actual

    return {"valido": True, "total_bloques": len(bloques), "mensaje": "Cadena íntegra, sin alteraciones"}