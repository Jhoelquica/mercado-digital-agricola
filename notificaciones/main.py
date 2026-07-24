from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from auth import verificar_token

from database import Base, engine, SessionLocal
import models
from rabbitmq_consumer import lanzar_consumidor_en_hilo

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

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "notificaciones"}

@app.get("/notificaciones/{pedido_id}")
def historial_por_pedido(pedido_id: str, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    return db.query(models.Notificacion).filter(models.Notificacion.pedido_id == pedido_id).all()


@app.get("/notificaciones")
def listar_todas(db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    return db.query(models.Notificacion).order_by(models.Notificacion.fecha_envio.desc()).all()