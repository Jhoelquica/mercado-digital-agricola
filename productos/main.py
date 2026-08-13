from prometheus_fastapi_instrumentator import Instrumentator
import os
import httpx
from sqlalchemy.orm import Session
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from auth import verificar_token, requiere_rol, security
from fastapi.security import HTTPAuthorizationCredentials
from database import Base, engine, SessionLocal
import models
from sqlalchemy import func
from fastapi import FastAPI, Depends, HTTPException, Header

from fastapi import UploadFile, File
from minio_client import subir_imagen
import pybreaker


Base.metadata.create_all(bind=engine)
breaker_productores = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=30)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

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

class ReponerStock(BaseModel):
    cantidad: int

@breaker_productores
def llamar_productores_me(token: str):
    with httpx.Client() as client:
        resp = client.get(
            f"{PRODUCTORES_URL}/productores/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "productos"}

USUARIOS_URL = os.getenv("USUARIOS_URL", "http://localhost:8006")

@app.get("/productos/{producto_id}/resenas")
def listar_resenas(producto_id: str, db: Session = Depends(get_db)):
    return db.query(models.Resena).filter(
        models.Resena.producto_id == producto_id
    ).order_by(models.Resena.fecha_creacion.desc()).all()

SERVICIO_SECRETO = os.getenv("SERVICIO_SECRETO", "clave-interna-servicios")

@app.post("/productos/{producto_id}/stock/reponer")
def reponer_stock(producto_id: str, datos: ReponerStock, x_servicio_secreto: str = Header(None), db: Session = Depends(get_db)):
    if x_servicio_secreto != SERVICIO_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    producto.stock += datos.cantidad
    db.commit()
    db.refresh(producto)
    return producto

@app.post("/productos")
def crear_producto(
        datos: ProductoCrear,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    try:
        resp = llamar_productores_me(credenciales.credentials)
    except pybreaker.CircuitBreakerError:
        raise HTTPException(status_code=503, detail="Servicio de Productores no disponible temporalmente, intenta en unos segundos")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Debes crear tu perfil de productor antes de publicar productos")
        raise HTTPException(status_code=502, detail="No se pudo verificar tu perfil de productor")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Servicio de Productores no disponible")

    productor = resp.json()
    nuevo = models.Producto(
        productor_id=productor["id"],
        productor_nombre=productor["nombre"],
        nombre=datos.nombre,
        categoria=datos.categoria,
        precio=datos.precio,
        stock=datos.stock,
        unidad_medida=datos.unidad_medida,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo

@app.post("/productos/{producto_id}/imagenes/subir")
def subir_imagen_producto(
        producto_id: str,
        archivo: UploadFile = File(...),
        orden: int = 0,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    if archivo.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=422, detail="Solo se permiten imágenes JPEG, PNG o WEBP")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    with httpx.Client() as client:
        try:
            resp = client.get(
                f"{PRODUCTORES_URL}/productores/me",
                headers={"Authorization": f"Bearer {credenciales.credentials}"},
                timeout=5,
            )
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Productores no disponible")

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Debes tener un perfil de productor para subir imágenes")

    productor = resp.json()
    if str(producto.productor_id) != str(productor["id"]):
        raise HTTPException(status_code=403, detail="No puedes modificar un producto que no te pertenece")

    total_imagenes = db.query(models.ProductoImagen).filter(
        models.ProductoImagen.producto_id == producto_id
    ).count()
    if total_imagenes >= 5:
        raise HTTPException(status_code=409, detail="Este producto ya tiene el máximo de 5 imágenes")

    try:
        url_publica = subir_imagen(archivo.file, archivo.content_type)
    except Exception:
        raise HTTPException(status_code=502, detail="No se pudo subir la imagen al almacenamiento")

    nueva_imagen = models.ProductoImagen(
        producto_id=producto_id,
        url=url_publica,
        orden=orden,
    )
    db.add(nueva_imagen)
    db.commit()
    db.refresh(nueva_imagen)
    return nueva_imagen

@app.delete("/productos/imagenes/{imagen_id}")
def eliminar_imagen(
        imagen_id: str,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    imagen = db.query(models.ProductoImagen).filter(models.ProductoImagen.id == imagen_id).first()
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    producto = db.query(models.Producto).filter(models.Producto.id == imagen.producto_id).first()

    with httpx.Client() as client:
        try:
            resp = client.get(
                f"{PRODUCTORES_URL}/productores/me",
                headers={"Authorization": f"Bearer {credenciales.credentials}"},
                timeout=5,
            )
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Productores no disponible")

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Debes tener un perfil de productor")

    productor = resp.json()
    if str(producto.productor_id) != str(productor["id"]):
        raise HTTPException(status_code=403, detail="No puedes eliminar imágenes de un producto que no te pertenece")

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