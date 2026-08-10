import hashlib
import models
from datetime import datetime

HASH_GENESIS = "0" * 64


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