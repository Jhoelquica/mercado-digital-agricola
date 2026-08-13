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
from logica_cadena import crear_bloque, verificar_cadena
from auth import requiere_rol

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

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "certificacion"}

@app.post("/certificacion/{producto_id}/eventos")
def registrar_evento(producto_id: str, datos: EventoCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("productor"))):
    if datos.evento not in ["cosecha_registrada", "certificado_productor", "verificado_punto_venta"]:
        raise HTTPException(status_code=422, detail="Tipo de evento no válido")

    bloque = crear_bloque(db, producto_id, datos.evento, datos.datos)
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