from prometheus_fastapi_instrumentator import Instrumentator
import os
import uuid

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
import qrcode
import io
from fastapi.responses import StreamingResponse

from database import Base, engine, SessionLocal
import models
from migraciones import asegurar_indice_unico_de_bloques, asegurar_columna_foto_url
from logica_cadena import crear_bloque, verificar_cadena, generar_certificado_pedido
from auth import secreto_de_servicio_valido
from fastapi import Header
import json

Base.metadata.create_all(bind=engine)
# create_all() no agrega restricciones a una tabla que ya existe: ver migraciones.py.
asegurar_indice_unico_de_bloques(engine)
asegurar_columna_foto_url(engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
TRANSPORTE_A_CERTIFICACION_SECRETO = os.getenv("TRANSPORTE_A_CERTIFICACION_SECRETO")  # crear_certificado_pedido, registrar_evento_interno (los llama Transporte)
PRODUCTORES_A_CERTIFICACION_SECRETO = os.getenv("PRODUCTORES_A_CERTIFICACION_SECRETO")  # registrar_cosecha_aprobada (lo llama Productores)
PRODUCTOS_A_CERTIFICACION_SECRETO = os.getenv("PRODUCTOS_A_CERTIFICACION_SECRETO")  # registrar_llegada_almacen (lo llama Productos)

class EventoInterno(BaseModel):
    evento: str
    datos: str | None = None

class CertificadoPedidoCrear(BaseModel):
    productos_ids: list[str]

DATOS_LLEGADA_MAXIMO = 600


class LlegadaAlmacenInterno(BaseModel):
    datos: str

    @field_validator("datos")
    @classmethod
    def _datos_validos(cls, valor: str) -> str:
        limpio = (valor or "").strip()
        if not limpio:
            raise ValueError("Indica qué llegó al almacén")
        if "\x00" in limpio:
            raise ValueError("El texto contiene caracteres no válidos")
        if len(limpio) > DATOS_LLEGADA_MAXIMO:
            raise ValueError(f"El texto no puede superar {DATOS_LLEGADA_MAXIMO} caracteres")
        return limpio


FOTO_URL_MAXIMO = 500


class CosechaAprobadaInterno(BaseModel):
    datos_cosecha: str | None = None
    # La foto de la cosecha (su huella SHA-256 viene dentro de datos_cosecha). Ver Bloque.foto_url.
    foto_url: str | None = None

    @field_validator("foto_url")
    @classmethod
    def _foto_url_valida(cls, valor):
        if valor is None:
            return None
        if not valor.startswith(("http://", "https://")) or len(valor) > FOTO_URL_MAXIMO:
            raise ValueError("La URL de la foto no es válida")
        return valor

@app.post("/certificacion/pedido/{pedido_id}/certificado")
def crear_certificado_pedido(
        pedido_id: str,
        datos: CertificadoPedidoCrear,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    existente = db.query(models.CertificadoPedido).filter(
        models.CertificadoPedido.pedido_id == pedido_id
    ).first()
    if existente:
        return existente  # idempotente: si ya se generó, se devuelve el mismo

    resultado = generar_certificado_pedido(db, pedido_id, datos.productos_ids)

    nuevo_certificado = models.CertificadoPedido(
        pedido_id=pedido_id,
        merkle_root=resultado["merkle_root"],
        detalle=json.dumps(resultado["detalle"]),
    )
    db.add(nuevo_certificado)
    db.commit()
    db.refresh(nuevo_certificado)
    return nuevo_certificado


@app.get("/certificacion/pedido/{pedido_id}/certificado")
def obtener_certificado_pedido(pedido_id: str, db: Session = Depends(get_db)):
    certificado = db.query(models.CertificadoPedido).filter(
        models.CertificadoPedido.pedido_id == pedido_id
    ).first()
    if not certificado:
        raise HTTPException(status_code=404, detail="Este pedido aún no tiene certificado generado")

    return {
        "pedido_id": str(certificado.pedido_id),
        "merkle_root": certificado.merkle_root,
        "detalle": json.loads(certificado.detalle),
        "fecha_generado": certificado.fecha_generado,
    }

@app.post("/certificacion/{producto_id}/eventos-internos")
def registrar_evento_interno(
        producto_id: str,
        datos: EventoInterno,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    if datos.evento != "verificado_punto_venta":
        raise HTTPException(status_code=422, detail="Este endpoint solo acepta el evento verificado_punto_venta")

    bloque = crear_bloque(db, producto_id, datos.evento, datos.datos)
    return bloque

@app.post("/certificacion/{producto_id}/cosecha-aprobada")
def registrar_cosecha_aprobada(
        producto_id: str,
        datos: CosechaAprobadaInterno,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    """Llamado por Productores al completar una cosecha (completar_cosecha): registra el primer evento de la cadena
    del producto recién creado, cosecha_registrada, con la evidencia en origen (distancia a la chacra al sembrar y al
    cosechar, y la huella de la foto) y la foto. Antes, además, un certificado_productor firmado por el Verificador:
    ese rol se retiró (presentación del 2026-10-01) y los bloques que ya existen se conservan tal cual. La ruta
    conserva su nombre para no cambiar el contrato entre servicios.

    Idempotente: un reintento no duplica el bloque. Falla cerrado: si el secreto no está configurado en este
    servicio, rechaza todo (a diferencia de comparar None == None)."""
    if not secreto_de_servicio_valido(x_servicio_secreto, PRODUCTORES_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    existentes = {
        b.evento for b in db.query(models.Bloque).filter(models.Bloque.producto_id == producto_id).all()
    }
    creados = []
    if "cosecha_registrada" not in existentes:
        creados.append(crear_bloque(db, producto_id, "cosecha_registrada", datos.datos_cosecha, foto_url=datos.foto_url))

    total = db.query(models.Bloque).filter(models.Bloque.producto_id == producto_id).count()
    return {"creados": len(creados), "total_bloques": total}

@app.post("/certificacion/{producto_id}/llegada-almacen")
def registrar_llegada_almacen(
        producto_id: uuid.UUID,
        datos: LlegadaAlmacenInterno,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    """Llamado por Productos cuando un Admin confirma que el producto llegó al almacén: deja el evento
    llegada_almacen (con lo declarado, lo recibido y las observaciones, en texto) al final de la cadena.
    Hasta ahora esa llegada no dejaba ningún rastro: el producto pasaba a "disponible" sin cantidad
    recibida y sin evento en la cadena que escanea el comprador.

    Idempotente: un producto tiene un solo evento de llegada, así que un reintento (por ejemplo, tras un
    fallo de Productos justo después de esta llamada) no duplica el bloque. Falla cerrado si el secreto
    no está configurado."""
    if not secreto_de_servicio_valido(x_servicio_secreto, PRODUCTOS_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    existente = db.query(models.Bloque).filter(
        models.Bloque.producto_id == producto_id,
        models.Bloque.evento == "llegada_almacen",
    ).first()
    if existente:
        return {"creado": False, "indice": existente.indice}

    bloque = crear_bloque(db, str(producto_id), "llegada_almacen", datos.datos)
    return {"creado": True, "indice": bloque.indice}

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "certificacion"}

# POST /certificacion/{producto_id}/eventos se eliminó: dejaba a cualquier productor o verificador (rol ya retirado)
# agregar bloques a la cadena de CUALQUIER producto (no comprobaba que existiera ni que fuera suyo),
# con texto libre, y dos escrituras simultáneas bifurcaban la cadena. Ningún flujo lo usaba: los
# eventos de la cadena solo entran por los endpoints internos con secreto de servicio
# (/cosecha-aprobada desde Productores y /eventos-internos desde Transporte).

@app.get("/certificacion/{producto_id}/historial")
def historial(producto_id: str, db: Session = Depends(get_db)):
    bloques = db.query(models.Bloque).filter(
        models.Bloque.producto_id == producto_id
    ).order_by(models.Bloque.indice).all()

    verificador_ids = {b.verificador_id for b in bloques if b.verificador_id}
    nombres_por_id = {}
    credenciales_por_id = {}
    if verificador_ids:
        verificadores = db.query(models.Verificador).filter(
            models.Verificador.id.in_(verificador_ids)
        ).all()
        nombres_por_id = {str(v.id): v.nombre for v in verificadores}
        credenciales_por_id = {str(v.id): v.credencial for v in verificadores}

    return [
        {
            "id": b.id,
            "producto_id": b.producto_id,
            "indice": b.indice,
            "evento": b.evento,
            "datos": b.datos,
            "fecha": b.fecha,
            "hash_anterior": b.hash_anterior,
            "hash_actual": b.hash_actual,
            "verificador_id": b.verificador_id,
            "verificador_nombre": nombres_por_id.get(str(b.verificador_id)) if b.verificador_id else None,
            "verificador_credencial": credenciales_por_id.get(str(b.verificador_id)) if b.verificador_id else None,
            "foto_url": b.foto_url,
        }
        for b in bloques
    ]

@app.get("/certificacion/{producto_id}/verificar")
def verificar(producto_id: str, db: Session = Depends(get_db)):
    return verificar_cadena(db, producto_id)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5500")

@app.get("/certificacion/{producto_id}/qr")
def generar_qr(producto_id: str):
    url_verificacion = f"{FRONTEND_URL}/verificar.html?producto_id={producto_id}"

    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url_verificacion)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(buffer, media_type="image/png")

