from prometheus_fastapi_instrumentator import Instrumentator
import os
import httpx
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
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
import cache
from rabbitmq_publisher import publicar_evento


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
    precio: float | None = None
    stock: int = 0
    unidad_medida: str = "kg"
    imagen_url: str | None = None

class ProductoCrearDesdeCosecha(BaseModel):
    productor_id: str
    productor_nombre: str
    nombre: str
    unidad_medida: str = "kg"
    stock: int
    registro_produccion_id: str

class ProductoActualizar(BaseModel):
    categoria: str | None = None
    precio: float | None = None

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

class OrdenImagen(BaseModel):
    orden: int

class BusquedaRegistrar(BaseModel):
    termino: str

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
    # calificacion_promedio/total_resenas se muestran tanto en el catálogo como en el detalle,
    # así que una reseña nueva deja ambos cachés desactualizados igual que un cambio de stock.
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return nueva_resena

# 3 claves dedicadas (sin fallback hardcodeado a propósito — ver el mismo comentario en
# usuarios/main.py) en vez del SERVICIO_SECRETO único de antes: cada endpoint interno valida
# contra la clave de SU llamador específico, no una compartida por los tres.
PAGOS_A_PRODUCTOS_SECRETO = os.getenv("PAGOS_A_PRODUCTOS_SECRETO")  # reponer_stock (Pagos llama tras un pago rechazado)
PEDIDOS_A_PRODUCTOS_SECRETO = os.getenv("PEDIDOS_A_PRODUCTOS_SECRETO")  # descontar_stock (Pedidos llama al crear un pedido)
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_producto (DELETE de limpieza QA)
PRODUCTORES_A_PRODUCTOS_SECRETO = os.getenv("PRODUCTORES_A_PRODUCTOS_SECRETO")  # crear_producto_desde_cosecha (Productores llama al aprobar una cosecha — sub-entrega 3, todavía no conectado)

@app.post("/productos/{producto_id}/stock/reponer")
def reponer_stock(producto_id: str, datos: ReponerStock, x_servicio_secreto: str = Header(None), db: Session = Depends(get_db)):
    if x_servicio_secreto != PAGOS_A_PRODUCTOS_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    producto.stock += datos.cantidad
    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
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
        # Disponible de inmediato (sin pasar por "en_transito"): este es el flujo manual de
        # siempre (el productor ya llena precio en el mismo formulario), a diferencia del alta
        # automática desde una cosecha aprobada (crear_producto_desde_cosecha), que sí nace en
        # "borrador" porque no tiene precio ni imágenes todavía. NOTA: el ciclo
        # borrador -> en_transito -> disponible se pensó para productos físicos que viajan desde
        # una chacra hasta el almacén central — este endpoint (sin uso desde el frontend, el
        # formulario de alta manual se quitó en una sub-entrega anterior) preserva el
        # comportamiento previo de visibilidad inmediata en vez de forzarlo también por el
        # tránsito. El default de la columna es "borrador" — se pisa acá a propósito.
        estado="disponible",
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    # Producto nuevo: no tiene todavía una clave de detalle propia en caché (id recién
    # generado), pero sí hay que sacarlo del catálogo cacheado para que aparezca de inmediato.
    cache.invalidar(cache.CLAVE_CATALOGO)
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
    # imagen_principal (catálogo) y el arreglo "imagenes" (detalle) cambian con esto.
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
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

    producto_id_afectado = str(producto.id)
    db.delete(imagen)
    db.commit()
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id_afectado))
    return {"mensaje": "Imagen eliminada"}

@app.delete("/productos/{producto_id}")
def eliminar_producto(producto_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    db.query(models.ProductoImagen).filter(models.ProductoImagen.producto_id == producto_id).delete()
    db.query(models.Resena).filter(models.Resena.producto_id == producto_id).delete()
    db.delete(producto)
    db.commit()
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return {"mensaje": "Producto eliminado"}

@app.get("/productos/precio-referencia/{nombre_producto}")
def precio_referencia(nombre_producto: str, db: Session = Depends(get_db)):
    """Precio de mercado de referencia para un nombre de cultivo/producto: promedio (AVG) del
    precio entre todos los productos del catálogo con ese nombre exacto (sin distinguir
    mayúsculas/minúsculas). Público — lo usa el Módulo de Gestión Económica de Productores para
    estimar ingresos, pero no requiere pertenecer a nadie en particular.

    LIMITACIÓN CONOCIDA: promedia el campo "precio" tal cual está en cada producto, sin agrupar
    por unidad_medida — si el mismo nombre de producto existe en el catálogo con precios en
    distintas unidades (ej. "Papa" vendida por kg en un caso y por saco en otro), el promedio
    mezcla unidades distintas y deja de ser un precio-por-kg real. Aceptado así para este primer
    módulo (así se especificó); si se vuelve un problema real, la forma correcta sería promediar
    solo dentro de una misma unidad_medida, o normalizar todo a precio-por-kg antes de promediar.
    """
    nombre_normalizado = nombre_producto.strip()
    promedio = db.query(func.avg(models.Producto.precio)).filter(
        func.lower(models.Producto.nombre) == nombre_normalizado.lower()
    ).scalar()

    if promedio is None:
        return {
            "nombre_producto": nombre_normalizado,
            "precio_promedio": None,
            "mensaje": "No hay productos activos con ese nombre en el catálogo",
        }

    return {
        "nombre_producto": nombre_normalizado,
        "precio_promedio": round(float(promedio), 2),
    }

@app.get("/productos")
def listar_productos(db: Session = Depends(get_db)):
    # Cache-aside: catálogo completo, TTL corto (ver cache.TTL_SEGUNDOS). Cualquier acción que
    # cambie lo que se ve acá (crear producto, stock, imágenes, reseñas) invalida esta clave de
    # inmediato — el TTL es solo la red de seguridad, no el mecanismo principal de frescura.
    cacheado = cache.obtener(cache.CLAVE_CATALOGO)
    if cacheado is not None:
        return cacheado

    # en_transito Y disponible: el comprador necesita ver ambos para poder reservar los que
    # todavía están en camino (POST /productos/{id}/reservar) o comprar directo los que ya
    # llegaron al almacén — el campo "estado" en la respuesta es lo que el frontend usa para
    # distinguir qué botón mostrar.
    productos = db.query(models.Producto).filter(
        models.Producto.estado.in_(["en_transito", "disponible"])
    ).all()

    imagenes = db.query(models.ProductoImagen).order_by(models.ProductoImagen.orden).all()
    primera_imagen_por_producto = {}
    for img in imagenes:
        pid = str(img.producto_id)
        if pid not in primera_imagen_por_producto:
            primera_imagen_por_producto[pid] = img.url

    stats_resenas = db.query(
        models.Resena.producto_id,
        func.avg(models.Resena.calificacion),
        func.count(models.Resena.id),
    ).group_by(models.Resena.producto_id).all()
    stats_por_producto = {
        str(pid): (round(float(promedio), 1), total)
        for pid, promedio, total in stats_resenas
    }

    resultado = [
        {
            "id": p.id,
            "productor_id": p.productor_id,
            "productor_nombre": p.productor_nombre,
            "nombre": p.nombre,
            "categoria": p.categoria,
            "precio": p.precio,
            "stock": p.stock,
            "unidad_medida": p.unidad_medida,
            "imagen_url": p.imagen_url,
            "fecha_publicacion": p.fecha_publicacion,
            "imagen_principal": primera_imagen_por_producto.get(str(p.id)),
            "calificacion_promedio": stats_por_producto.get(str(p.id), (None, 0))[0],
            "total_resenas": stats_por_producto.get(str(p.id), (None, 0))[1],
            "estado": p.estado,
        }
        for p in productos
    ]
    cache.guardar(cache.CLAVE_CATALOGO, resultado)
    return resultado

@app.get("/productos/mios")
def listar_mis_productos(
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    """Reemplaza el patrón del frontend de traer TODO el catálogo (GET /productos) y filtrar del
    lado del cliente por productor_id — acá el filtro ya viene hecho, y a diferencia del
    catálogo público, incluye los productos en "borrador" (para que el productor pueda verlos y
    completarlos). Sin caché a propósito: es data personal de bajo tráfico, no el catálogo
    compartido por todos los compradores."""
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
        raise HTTPException(status_code=404, detail="Debes crear tu perfil de productor antes de ver tus productos")

    productor_id = resp.json()["id"]

    productos = db.query(models.Producto).filter(
        models.Producto.productor_id == productor_id
    ).order_by(models.Producto.fecha_publicacion.desc()).all()

    imagenes = db.query(models.ProductoImagen).filter(
        models.ProductoImagen.producto_id.in_([p.id for p in productos])
    ).order_by(models.ProductoImagen.orden).all() if productos else []
    primera_imagen_por_producto = {}
    for img in imagenes:
        pid = str(img.producto_id)
        if pid not in primera_imagen_por_producto:
            primera_imagen_por_producto[pid] = img.url

    return [
        {
            "id": p.id,
            "productor_id": p.productor_id,
            "productor_nombre": p.productor_nombre,
            "nombre": p.nombre,
            "categoria": p.categoria,
            "precio": p.precio,
            "stock": p.stock,
            "unidad_medida": p.unidad_medida,
            "imagen_url": p.imagen_url,
            "fecha_publicacion": p.fecha_publicacion,
            "imagen_principal": primera_imagen_por_producto.get(str(p.id)),
            "estado": p.estado,
            "registro_produccion_id": p.registro_produccion_id,
        }
        for p in productos
    ]

@app.post("/productos/busquedas/registrar")
def registrar_busqueda(datos: BusquedaRegistrar, db: Session = Depends(get_db)):
    # Público, sin auth: cualquiera que busque cuenta, con o sin sesión. Ignora términos muy
    # cortos (ruido de una letra suelta) — el resto de la limpieza de ruido (debounce, no registrar
    # cada tecla) vive del lado del frontend, acá solo se guarda lo que ya llegó.
    termino = datos.termino.strip().lower()
    if len(termino) < 2:
        return {"registrado": False}
    db.add(models.BusquedaLog(termino=termino))
    db.commit()
    return {"registrado": True}

@app.get("/productos/busquedas/populares")
def busquedas_populares(db: Session = Depends(get_db)):
    desde = datetime.utcnow() - timedelta(days=7)
    resultados = (
        db.query(models.BusquedaLog.termino, func.count(models.BusquedaLog.id).label("total"))
        .filter(models.BusquedaLog.fecha >= desde)
        .group_by(models.BusquedaLog.termino)
        .order_by(func.count(models.BusquedaLog.id).desc())
        .limit(8)
        .all()
    )
    return [{"termino": termino, "total": total} for termino, total in resultados]

@app.get("/productos/{producto_id}")
def obtener_producto(producto_id: str, db: Session = Depends(get_db)):
    clave = cache.clave_detalle(producto_id)
    cacheado = cache.obtener(clave)
    if cacheado is not None:
        return cacheado

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

    resultado = {
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
    cache.guardar(clave, resultado)
    return resultado

@app.patch("/productos/{producto_id}/stock")
def descontar_stock(producto_id: str, datos: DescontarStock, x_servicio_secreto: str = Header(None), db: Session = Depends(get_db)):
    if x_servicio_secreto != PEDIDOS_A_PRODUCTOS_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    if producto.stock < datos.cantidad:
        raise HTTPException(status_code=409, detail="Stock insuficiente")
    producto.stock -= datos.cantidad
    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return producto

@app.patch("/productos/{producto_id}")
def actualizar_producto(
        producto_id: str,
        datos: ProductoActualizar,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    """Edita categoria/precio de un producto propio — de a uno o los dos juntos, lo que venga en
    el body (None = no tocar ese campo). Solo mientras está en "borrador": editar un producto ya
    publicado es una conversación aparte (¿republicar? ¿efecto inmediato en el catálogo?), no se
    resuelve acá."""
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
        raise HTTPException(status_code=404, detail="Debes tener un perfil de productor")

    productor = resp.json()
    if str(producto.productor_id) != str(productor["id"]):
        raise HTTPException(status_code=403, detail="No puedes modificar un producto que no te pertenece")

    if producto.estado != "borrador":
        raise HTTPException(status_code=400, detail="Solo se puede editar un producto mientras está en borrador")

    if datos.precio is not None:
        if datos.precio <= 0:
            raise HTTPException(status_code=422, detail="El precio debe ser mayor a 0")
        producto.precio = datos.precio
    if datos.categoria is not None:
        producto.categoria = datos.categoria

    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return producto

@app.patch("/productos/{producto_id}/publicar")
def publicar_producto(
        producto_id: str,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
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
        raise HTTPException(status_code=404, detail="Debes tener un perfil de productor")

    productor = resp.json()
    if str(producto.productor_id) != str(productor["id"]):
        raise HTTPException(status_code=403, detail="No puedes publicar un producto que no te pertenece")

    if producto.estado != "borrador":
        raise HTTPException(status_code=400, detail="Este producto ya está publicado")

    # Se valida en orden y se corta en el primer faltante — un solo detail específico por
    # respuesta, no una lista acumulada (mismo estilo que el resto de las validaciones del
    # proyecto, ej. crear_registro_produccion en Productores).
    if producto.precio is None or producto.precio <= 0:
        raise HTTPException(status_code=400, detail="Agrega un precio mayor a 0 antes de publicar.")
    if not producto.categoria or not producto.categoria.strip():
        raise HTTPException(status_code=400, detail="Elige una categoría antes de publicar.")
    tiene_imagen = db.query(models.ProductoImagen).filter(
        models.ProductoImagen.producto_id == producto_id
    ).first() is not None
    if not tiene_imagen:
        raise HTTPException(status_code=400, detail="Agrega al menos una imagen antes de publicar.")

    # No pasa directo a "disponible": todavía tiene que viajar físicamente hasta el almacén
    # central. Queda "en_transito" — visible en el catálogo y reservable — hasta que un Admin
    # confirme la llegada (PATCH /productos/{id}/confirmar-llegada-almacen).
    producto.estado = "en_transito"
    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return producto

@app.patch("/productos/{producto_id}/confirmar-llegada-almacen")
def confirmar_llegada_almacen(
        producto_id: str,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("admin")),
):
    """El Admin confirma que un producto "en_transito" llegó físicamente al almacén central —
    recién ahí pasa a "disponible" (comprable de verdad, no solo reservable)."""
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if producto.estado != "en_transito":
        raise HTTPException(status_code=400, detail="Este producto no está en tránsito hacia el almacén")

    producto.estado = "disponible"
    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))

    # Un evento por cada reserva (fanout, mismo patrón que pedidos/transporte con
    # eventos_pedidos/eventos_envios) — Notificaciones lo consume y crea el aviso; acá no hace
    # falta saber nada de Notificaciones, solo publicar.
    reservas = db.query(models.Reserva).filter(models.Reserva.producto_id == producto_id).all()
    for reserva in reservas:
        publicar_evento({
            "evento": "producto_disponible",
            "usuario_id": str(reserva.comprador_id),
            "producto_id": str(producto.id),
            "nombre_producto": producto.nombre,
        })

    return producto

@app.post("/productos/{producto_id}/reservar")
def reservar_producto(
        producto_id: str,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("comprador")),
):
    """Reserva de interés (sin monto, sin pago) sobre un producto todavía "en_transito" — no
    tiene sentido reservar algo que ya está "disponible", ahí se compra directo."""
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if producto.estado != "en_transito":
        raise HTTPException(
            status_code=400,
            detail="Este producto no está en tránsito — si ya está disponible, cómpralo directamente",
        )

    comprador_id = usuario.get("sub")
    ya_reservado = db.query(models.Reserva).filter(
        models.Reserva.producto_id == producto_id,
        models.Reserva.comprador_id == comprador_id,
    ).first()
    if ya_reservado:
        raise HTTPException(status_code=400, detail="Ya reservaste este producto")

    nueva_reserva = models.Reserva(producto_id=producto_id, comprador_id=comprador_id)
    db.add(nueva_reserva)
    try:
        db.commit()
    except IntegrityError:
        # Carrera entre el SELECT de arriba y este insert (ej. doble clic) — el
        # UniqueConstraint("producto_id", "comprador_id") del modelo es quien de verdad evita el
        # duplicado, acá solo se traduce a un 400 legible en vez de un 500.
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya reservaste este producto")
    db.refresh(nueva_reserva)
    return nueva_reserva

@app.get("/productos/{producto_id}/reservas")
def listar_reservas(
        producto_id: str,
        db: Session = Depends(get_db),
        usuario: dict = Depends(verificar_token),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    """Quién reservó este producto — la va a consumir el servicio de Notificaciones en la
    sub-entrega siguiente para avisarles cuando el Admin confirme la llegada al almacén (ver el
    TODO en confirmar_llegada_almacen), todavía no conectado acá. Acceso: admin, o el productor
    dueño del producto."""
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if usuario.get("rol") != "admin":
        with httpx.Client() as client:
            try:
                resp = client.get(
                    f"{PRODUCTORES_URL}/productores/me",
                    headers={"Authorization": f"Bearer {credenciales.credentials}"},
                    timeout=5,
                )
            except httpx.RequestError:
                raise HTTPException(status_code=503, detail="Servicio de Productores no disponible")

        if resp.status_code != 200 or str(producto.productor_id) != str(resp.json().get("id")):
            raise HTTPException(status_code=403, detail="No tienes permiso para ver las reservas de este producto")

    return db.query(models.Reserva).filter(
        models.Reserva.producto_id == producto_id
    ).order_by(models.Reserva.fecha_creacion.asc()).all()

@app.post("/productos/interno/crear-desde-cosecha")
def crear_producto_desde_cosecha(
        datos: ProductoCrearDesdeCosecha,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None),
):
    """Llamado por Productores al aprobar una cosecha (sub-entrega 3 — todavía NO conectado ahí,
    este endpoint solo existe y se prueba de forma aislada por ahora). Nace en "borrador", sin
    precio ni categoría: el productor todavía tiene que completar precio + imágenes y publicar
    a mano (PATCH /productos/{id}/publicar) antes de que sea visible en el catálogo.

    productor_nombre viaja en el body en vez de resolverse acá: quien llama (Productores) ya
    tiene ese dato en su propia base, así que no hace falta una llamada cruzada solo para
    obtenerlo (mismo criterio que productor_id, que tampoco se resuelve desde un token).

    Idempotente por construcción: UniqueConstraint("registro_produccion_id") en el modelo
    (models.py) es quien de verdad evita duplicados si Productores reintenta esta llamada tras
    un fallo de red — acá solo se traduce esa violación en un 409 legible, no se re-implementa
    la unicidad a mano con un SELECT previo (que además sería vulnerable a una carrera entre
    el chequeo y el insert)."""
    if x_servicio_secreto != PRODUCTORES_A_PRODUCTOS_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    nuevo = models.Producto(
        productor_id=datos.productor_id,
        productor_nombre=datos.productor_nombre,
        nombre=datos.nombre,
        categoria=None,
        precio=None,
        stock=datos.stock,
        unidad_medida=datos.unidad_medida,
        estado="borrador",
        registro_produccion_id=datos.registro_produccion_id,
    )
    db.add(nuevo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un producto para esta cosecha.")
    db.refresh(nuevo)
    # Sin invalidar CLAVE_CATALOGO: un producto en "borrador" no aparece en GET /productos de
    # todos modos (filtrado por estado == "publicado"), así que esta alta no le cambia el
    # resultado a nadie — invalidar un caché válido sin necesidad sería puro desperdicio.
    return nuevo

@app.patch("/productos/imagenes/{imagen_id}/orden")
def actualizar_orden_imagen(
        imagen_id: str,
        datos: OrdenImagen,
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
        raise HTTPException(status_code=403, detail="No puedes modificar imágenes de un producto que no te pertenece")

    imagen.orden = datos.orden
    db.commit()
    db.refresh(imagen)
    # imagen_principal en el catálogo depende del orden de las imágenes.
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(str(producto.id)))
    return imagen