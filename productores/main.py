import os
import httpx

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from auth import verificar_token, requiere_rol

from database import Base, engine, SessionLocal
import models

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

class ProductorCrear(BaseModel):
    nombre: str
    comunidad: str | None = None
    contacto: str | None = None
    ubicacion: str | None = None
    latitud: str | None = None
    longitud: str | None = None

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
SERVICIO_SECRETO = os.getenv("SERVICIO_SECRETO", "clave-interna-servicios")

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "productores"}

@app.get("/productores/me")
def mi_perfil_productor(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("productor"))):
    productor = db.query(models.Productor).filter(
        models.Productor.usuario_id == usuario.get("sub")
    ).first()
    if not productor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de productor. Créalo primero.")
    return productor


@app.get("/productores/{productor_id}")
def obtener_productor(productor_id: str, db: Session = Depends(get_db)):
    productor = db.query(models.Productor).filter(models.Productor.id == productor_id).first()
    if not productor:
        raise HTTPException(status_code=404, detail="Productor no encontrado")
    return productor

@app.get("/productores")
def listar_productores(db: Session = Depends(get_db)):
    return db.query(models.Productor).all()

@app.post("/productores")
def crear_productor(datos: ProductorCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("productor"))):
    usuario_id = usuario.get("sub")

    existente = db.query(models.Productor).filter(models.Productor.usuario_id == usuario_id).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya tienes un perfil de productor registrado")

    nuevo = models.Productor(
        usuario_id=usuario_id,
        nombre=datos.nombre,
        comunidad=datos.comunidad,
        contacto=datos.contacto,
        ubicacion=datos.ubicacion,
        latitud=datos.latitud,
        longitud=datos.longitud,
    )
    
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@app.delete("/productores/{productor_id}")
def eliminar_productor(productor_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != SERVICIO_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    productor = db.query(models.Productor).filter(models.Productor.id == productor_id).first()
    if not productor:
        raise HTTPException(status_code=404, detail="Productor no encontrado")

    try:
        resp = httpx.get(f"{PRODUCTOS_URL}/productos", timeout=5)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="No se pudo verificar si el productor tiene productos (servicio de Productos no disponible)")

    if resp.status_code == 200:
        tiene_productos = any(
            str(p.get("productor_id")) == str(productor_id) for p in resp.json()
        )
        if tiene_productos:
            raise HTTPException(status_code=409, detail="Este productor aún tiene productos registrados, bórralos primero")

    db.delete(productor)
    db.commit()
    return {"mensaje": "Productor eliminado"}