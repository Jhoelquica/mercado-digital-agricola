from prometheus_fastapi_instrumentator import Instrumentator
import os

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
import qrcode
import io
from fastapi.responses import StreamingResponse

from database import Base, engine, SessionLocal
import models
from logica_cadena import crear_bloque, verificar_cadena, validar_geofencing, generar_certificado_pedido
from auth import requiere_rol, security
from fastapi.security import HTTPAuthorizationCredentials
from fastapi import Header
import json

Base.metadata.create_all(bind=engine)

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


class EventoCrear(BaseModel):
    evento: str
    datos: str | None = None
    latitud: str | None = None
    longitud: str | None = None

SERVICIO_SECRETO = os.getenv("SERVICIO_SECRETO", "clave-interna-servicios")

class EventoInterno(BaseModel):
    evento: str
    datos: str | None = None

class CertificadoPedidoCrear(BaseModel):
    productos_ids: list[str]

@app.post("/certificacion/pedido/{pedido_id}/certificado")
def crear_certificado_pedido(
        pedido_id: str,
        datos: CertificadoPedidoCrear,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    if x_servicio_secreto != SERVICIO_SECRETO:
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
    if x_servicio_secreto != SERVICIO_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    if datos.evento != "verificado_punto_venta":
        raise HTTPException(status_code=422, detail="Este endpoint solo acepta el evento verificado_punto_venta")

    bloque = crear_bloque(db, producto_id, datos.evento, datos.datos)
    return bloque

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "certificacion"}

@app.post("/certificacion/{producto_id}/eventos")
def registrar_evento(
        producto_id: str,
        datos: EventoCrear,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor", "verificador")),
        credentials: HTTPAuthorizationCredentials = Depends(security)
):
    if datos.evento not in ["cosecha_registrada", "certificado_productor"]:
        raise HTTPException(status_code=422, detail="Tipo de evento no válido")

    rol = usuario.get("rol")

    if datos.evento == "cosecha_registrada" and rol != "productor":
        raise HTTPException(status_code=403, detail="Solo un productor puede registrar la cosecha")

    if datos.evento == "certificado_productor" and rol != "verificador":
        raise HTTPException(status_code=403, detail="Solo un verificador puede certificar el producto")

    if datos.evento == "cosecha_registrada":
        if not datos.latitud or not datos.longitud:
            raise HTTPException(status_code=422, detail="Latitud y longitud son requeridas para registrar la cosecha")

        token = credentials.credentials
        validar_geofencing(token, datos.latitud, datos.longitud)

    verificador_id = None
    if datos.evento == "certificado_productor":
        verificador = db.query(models.Verificador).filter(
            models.Verificador.usuario_id == usuario.get("sub")
        ).first()
        if not verificador:
            raise HTTPException(status_code=404, detail="Aún no tienes un perfil de verificador. Créalo primero.")
        verificador_id = verificador.id

    bloque = crear_bloque(db, producto_id, datos.evento, datos.datos, verificador_id)
    return bloque

@app.get("/certificacion/{producto_id}/historial")
def historial(producto_id: str, db: Session = Depends(get_db)):
    return db.query(models.Bloque).filter(
        models.Bloque.producto_id == producto_id
    ).order_by(models.Bloque.indice).all()

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


class VerificadorCrear(BaseModel):
    nombre: str
    credencial: str

@app.post("/verificadores")
def crear_verificador(datos: VerificadorCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("verificador"))):
    if not datos.credencial.strip():
        raise HTTPException(status_code=422, detail="La credencial no puede estar vacía")

    usuario_id = usuario.get("sub")
    existente = db.query(models.Verificador).filter(models.Verificador.usuario_id == usuario_id).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya tienes un perfil de verificador registrado")

    nuevo = models.Verificador(
        usuario_id=usuario_id,
        nombre=datos.nombre,
        credencial=datos.credencial,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


@app.get("/verificadores/me")
def mi_perfil_verificador(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("verificador"))):
    verificador = db.query(models.Verificador).filter(
        models.Verificador.usuario_id == usuario.get("sub")
    ).first()
    if not verificador:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de verificador. Créalo primero.")
    return verificador

@app.delete("/verificadores/{verificador_id}")
def eliminar_verificador(verificador_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != SERVICIO_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    verificador = db.query(models.Verificador).filter(models.Verificador.id == verificador_id).first()
    if not verificador:
        raise HTTPException(status_code=404, detail="Verificador no encontrado")

    db.delete(verificador)
    db.commit()
    return {"mensaje": "Verificador eliminado"}