import os
import httpx
from sqlalchemy.orm import Session
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException
from auth import verificar_token

from database import Base, engine, SessionLocal
import models

Base.metadata.create_all(bind=engine)

app = FastAPI()

PRODUCTORES_URL = os.getenv("PRODUCTORES_URL", "http://localhost:8001")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ProductoCrear(BaseModel):
    productor_id: str
    nombre: str
    categoria: str | None = None
    precio: float
    stock: int = 0

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "productos"}

@app.post("/productos")
def crear_producto(datos: ProductoCrear, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    # Llamada síncrona real a Productores para validar que existe
    with httpx.Client() as client:
        try:
            resp = client.get(f"{PRODUCTORES_URL}/productores/{datos.productor_id}", timeout=5)
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Productores no disponible")

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="El productor no existe")

    productor = resp.json()

    nuevo = models.Producto(
        productor_id=datos.productor_id,
        productor_nombre=productor["nombre"],  # aquí se guarda la réplica
        nombre=datos.nombre,
        categoria=datos.categoria,
        precio=datos.precio,
        stock=datos.stock,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@app.get("/productos")
def listar_productos(db: Session = Depends(get_db)):
    return db.query(models.Producto).all()

@app.get("/productos/{producto_id}")
def obtener_producto(producto_id: str, db: Session = Depends(get_db)):
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return producto

class DescontarStock(BaseModel):
    cantidad: int

@app.patch("/productos/{producto_id}/stock")
def descontar_stock(producto_id: str, datos: DescontarStock, db: Session = Depends(get_db)):
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    if producto.stock < datos.cantidad:
        raise HTTPException(status_code=409, detail="Stock insuficiente")
    producto.stock -= datos.cantidad
    db.commit()
    db.refresh(producto)
    return producto