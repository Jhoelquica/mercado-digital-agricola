import os

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from auth import verificar_token

from database import Base, engine, SessionLocal
import models
from rabbitmq_consumer import lanzar_consumidor_en_hilo

Base.metadata.create_all(bind=engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py). Usada SOLO
# por el DELETE de limpieza QA de este servicio — ningún otro servicio lo llama.
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "notificaciones"}

@app.delete("/notificaciones/{notificacion_id}")
def eliminar_notificacion(notificacion_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    notificacion = db.query(models.Notificacion).filter(models.Notificacion.id == notificacion_id).first()
    if not notificacion:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")

    db.delete(notificacion)
    db.commit()
    return {"mensaje": "Notificación eliminada"}

@app.get("/notificaciones/{pedido_id}")
def historial_por_pedido(pedido_id: str, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    return db.query(models.Notificacion).filter(models.Notificacion.pedido_id == pedido_id).all()


@app.get("/notificaciones")
def listar_todas(db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    return db.query(models.Notificacion).filter(
        models.Notificacion.usuario_id == usuario.get("sub")
    ).order_by(models.Notificacion.fecha_envio.desc()).all()