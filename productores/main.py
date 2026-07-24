from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from auth import verificar_token

from database import Base, engine, SessionLocal
import models

Base.metadata.create_all(bind=engine)

app = FastAPI()

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

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "productores"}

@app.get("/productores/{productor_id}")
def obtener_productor(productor_id: str, db: Session = Depends(get_db)):
    productor = db.query(models.Productor).filter(models.Productor.id == productor_id).first()
    if not productor:
        raise HTTPException(status_code=404, detail="Productor no encontrado")
    return productor

@app.post("/productores")
def crear_productor(datos: ProductorCrear, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    nuevo = models.Productor(**datos.dict())
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@app.get("/productores")
def listar_productores(db: Session = Depends(get_db)):
    return db.query(models.Productor).all()