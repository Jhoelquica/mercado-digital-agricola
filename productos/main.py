import os
import httpx
from sqlalchemy.orm import Session
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from auth import verificar_token
from auth import verificar_token, requiere_rol
from auth import verificar_token, requiere_rol, security
from fastapi.security import HTTPAuthorizationCredentials
from database import Base, engine, SessionLocal
import models
from sqlalchemy import func

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    unidad_medida: str = "kg"
    imagen_url: str | None = None

class ImagenCrear(BaseModel):
    url: str
    orden: int = 0

class ResenaCrear(BaseModel):
    calificacion: int
    comentario: str | None = None

class DescontarStock(BaseModel):
    cantidad: int


@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "productos"}

USUARIOS_URL = os.getenv("USUARIOS_URL", "http://localhost:8006")

@app.get("/productos/{producto_id}/resenas")
def listar_resenas(producto_id: str, db: Session = Depends(get_db)):
    return db.query(models.Resena).filter(
        models.Resena.producto_id == producto_id
    ).order_by(models.Resena.fecha_creacion.desc()).all()

@app.post("/productos/{producto_id}/resenas")
def crear_resena(
        producto_id: str,
        datos: ResenaCrear,
        db: Session = Depends(get_db),
        usuario: dict = Depends(verificar_token),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    if datos.calificacion < 1 or datos.calificacion > 5:
        raise HTTPException(status_code=422, detail="La calificación debe ser entre 1 y 5")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    usuario_id = usuario.get("sub")
    with httpx.Client() as client:
        try:
            resp = client.get(
                f"{USUARIOS_URL}/usuarios/{usuario_id}",
                headers={"Authorization": f"Bearer {credenciales.credentials}"},
                timeout=5,
            )
        except httpx.RequestError:
            usuario_nombre = "Usuario"
        else:
            usuario_nombre = resp.json().get("nombre", "Usuario") if resp.status_code == 200 else "Usuario"

    nueva_resena = models.Resena(
        producto_id=producto_id,
        usuario_id=usuario_id,
        usuario_nombre=usuario_nombre,
        calificacion=datos.calificacion,
        comentario=datos.comentario,
    )
    db.add(nueva_resena)
    db.commit()
    db.refresh(nueva_resena)
    return nueva_resena

@app.post("/productos")
def crear_producto(datos: ProductoCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("productor"))):
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
        unidad_medida=datos.unidad_medida,
        imagen_url=datos.imagen_url,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@app.post("/productos/{producto_id}/imagenes")
def agregar_imagen(producto_id: str, datos: ImagenCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("productor"))):
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    nueva_imagen = models.ProductoImagen(
        producto_id=producto_id,
        url=datos.url,
        orden=datos.orden,
    )
    db.add(nueva_imagen)
    db.commit()
    db.refresh(nueva_imagen)
    return nueva_imagen

@app.delete("/productos/imagenes/{imagen_id}")
def eliminar_imagen(imagen_id: str, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("productor"))):
    imagen = db.query(models.ProductoImagen).filter(models.ProductoImagen.id == imagen_id).first()
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    db.delete(imagen)
    db.commit()
    return {"mensaje": "Imagen eliminada"}

@app.get("/productos")
def listar_productos(db: Session = Depends(get_db)):
    return db.query(models.Producto).all()

@app.get("/productos/{producto_id}")
def obtener_producto(producto_id: str, db: Session = Depends(get_db)):
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    imagenes = db.query(models.ProductoImagen).filter(
        models.ProductoImagen.producto_id == producto_id
    ).order_by(models.ProductoImagen.orden).all()

    stats = db.query(
        func.avg(models.Resena.calificacion),
        func.count(models.Resena.id)
    ).filter(models.Resena.producto_id == producto_id).first()

    promedio, total_resenas = stats
    promedio = round(float(promedio), 1) if promedio else None

    return {
        "id": producto.id,
        "productor_id": producto.productor_id,
        "productor_nombre": producto.productor_nombre,
        "nombre": producto.nombre,
        "categoria": producto.categoria,
        "precio": producto.precio,
        "stock": producto.stock,
        "unidad_medida": producto.unidad_medida,
        "imagen_url": producto.imagen_url,
        "fecha_publicacion": producto.fecha_publicacion,
        "imagenes": [{"id": img.id, "url": img.url, "orden": img.orden} for img in imagenes],
        "calificacion_promedio": promedio,
        "total_resenas": total_resenas,
    }

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