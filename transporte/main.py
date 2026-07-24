from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from auth import verificar_token

from database import Base, engine, SessionLocal
import models
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from rabbitmq_publisher import publicar_evento

Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class EstadoEnvio(BaseModel):
    estado: str

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "transporte"}

@app.get("/envios/{pedido_id}")
def obtener_envio(pedido_id: str, db: Session = Depends(get_db)):
    envio = db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado para ese pedido")
    return envio

@app.patch("/envios/{envio_id}/estado")
def actualizar_estado(envio_id: str, datos: EstadoEnvio, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    envio.estado = datos.estado
    db.commit()
    db.refresh(envio)

    publicar_evento({
        "evento": "envio_actualizado",
        "pedido_id": str(envio.pedido_id),
        "estado": envio.estado,
    })
    return envio