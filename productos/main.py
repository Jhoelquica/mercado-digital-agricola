from prometheus_fastapi_instrumentator import Instrumentator
import io
import logging
import math
import os
import unicodedata
import uuid
import httpx
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field, field_validator
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from auth import verificar_token, requiere_rol, security, security_opcional, usuario_opcional
from auth import secreto_de_servicio_valido
from fastapi.security import HTTPAuthorizationCredentials
from database import Base, engine, SessionLocal
from migraciones import agregar_columnas_faltantes
import models
from sqlalchemy import func
from fastapi import FastAPI, Depends, HTTPException, Header

from fastapi import UploadFile, File
from minio_client import subir_imagen, asegurar_bucket_publico
import pybreaker
import cache
from rabbitmq_publisher import publicar_evento
from imagenes import leer_imagen_validada, LIMITE_CUERPO_BYTES, MENSAJE_MUY_GRANDE


Base.metadata.create_all(bind=engine)
# create_all() no agrega columnas a una tabla que ya existe (p. ej. productos.descripcion).
agregar_columnas_faltantes(engine, Base)


def _asegurar_una_resena_por_comprador():
    """El índice único de Resena en una base creada antes de que existiera (create_all no lo agrega). Si hubiera
    reseñas repetidas de antes, no se crea y lo sigue cuidando la comprobación de crear_resena."""
    try:
        with engine.begin() as conexion:
            conexion.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_resena_producto_usuario ON resenas (producto_id, usuario_id)")
    except Exception as error:  # noqa: BLE001 - no impedir que el servicio arranque
        logging.getLogger(__name__).warning("No se creó el índice único de reseñas: %s", error)


_asegurar_una_resena_por_comprador()
breaker_productores = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=30)

app = FastAPI()

Instrumentator().instrument(app).expose(app)


@app.exception_handler(RequestValidationError)
async def _errores_de_validacion(request, exc):
    """Igual que el manejador por defecto de FastAPI, pero sin el valor recibido ("input"): si el
    cliente manda NaN o Infinity, ese valor no se puede serializar a JSON y la respuesta 422 salía
    como un error 500."""
    detalle = [{clave: valor for clave, valor in error.items() if clave != "input"} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detalle)})

@app.middleware("http")
async def limitar_el_tamano_de_las_subidas(request, call_next):
    """Rechaza por su Content-Length una subida demasiado grande ANTES de leer el cuerpo: sin esto el servidor
    recibía entero un archivo de cualquier tamaño y recién después decidía. Va antes del middleware de CORS a
    propósito (queda por dentro), para que el 413 también lleve las cabeceras CORS y el navegador muestre el
    mensaje en vez de un error de red."""
    if request.method == "POST" and request.url.path.endswith("/imagenes/subir"):
        longitud = request.headers.get("content-length", "")
        if longitud.isdigit() and int(longitud) > LIMITE_CUERPO_BYTES:
            return JSONResponse(status_code=413, content={"detail": MENSAJE_MUY_GRANDE})
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PRODUCTORES_URL = os.getenv("PRODUCTORES_URL", "http://localhost:8001")

# Lista fija de unidades válidas — hasta ahora Producto.unidad_medida no se validaba contra nada
# (string libre), pero UnidadAlternativa.unidad sí lo necesita para no terminar con valores
# inconsistentes ("Kg", "kilos", etc.) mezclados con los ya establecidos. Constante Python
# compartida en este servicio (la usa crear_unidad_alternativa) en vez de repetirla en cada
# endpoint. Mismo conjunto que ya usa el frontend en UNIDADES_MEDIDA/OPCIONES_UNIDAD_MEDIDA_HTML
# (app.js) — si se agrega una unidad acá, hay que agregarla ahí también.
UNIDADES_VALIDAS = ["kg", "unidad", "saco", "arroba", "litro"]

# Las 8 categorías del catálogo (mismo conjunto que CATEGORIAS_PRODUCTO en frontend/js/app.js — si
# cambia en un lado, hay que cambiarlo en el otro). Antes la categoría era texto libre: cualquier
# cosa que un productor escribiera terminaba en la página pública del producto.
CATEGORIAS_VALIDAS = ("fruta", "verdura", "tuberculo", "grano", "legumbre", "lacteo", "huevo", "hierba")

# Unidades de venta de cada categoría, para los productos creados antes de que Productores mandara la de su alimento
# (H1 de la validación del 2026-10-13: la papa no se vende por unidades). Es la unión de las familias de cada categoría
# en productores/plantillas_produccion.UNIDADES_VENTA_POR_FAMILIA: si cambia allá, revisar acá.
UNIDADES_POR_CATEGORIA = {
    "tuberculo": ("kg", "saco", "arroba"),
    "grano": ("kg", "unidad", "saco", "arroba"),
    "legumbre": ("kg", "saco", "arroba"),
    "verdura": ("kg", "unidad", "saco"),
    "fruta": ("kg", "unidad", "saco"),
    "hierba": ("kg", "unidad"),
    "huevo": ("unidad",),
    "lacteo": ("kg", "unidad", "litro"),
}


def unidades_permitidas_de(producto) -> list[str] | None:
    """En qué unidades se puede vender el producto (la base siempre): la lista de su alimento o, en los de antes, la de
    su categoría. None = sin restricción (producto sin alimento ni categoría conocidos)."""
    if producto.unidades_permitidas:
        return producto.unidades_permitidas.split(",")
    unidades = UNIDADES_POR_CATEGORIA.get(producto.categoria)
    return list(unidades) if unidades else None


def alternativas_a_la_venta(producto, alternativas):
    """Las unidades alternativas que se ofrecen al comprador: las que el alimento permite. Las que no (p. ej. "unidad" en
    una papa de antes) no se publican; como Pedidos busca la unidad en esta misma respuesta, tampoco se pueden comprar."""
    permitidas = unidades_permitidas_de(producto)
    return [a for a in alternativas if permitidas is None or a["unidad"] in permitidas]


def normalizar_categoria(valor):
    """Devuelve la categoría canónica (minúsculas, sin tildes) o None si no es una de las 8."""
    if valor is None:
        return None
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", str(valor).strip().lower()) if not unicodedata.combining(c)
    )
    return sin_tildes if sin_tildes in CATEGORIAS_VALIDAS else None


# Topes de lo que se puede escribir en un precio/factor/texto. NaN e Infinity entran por JSON
# (Python los acepta) y NaN pasa cualquier comparación "<= 0" sin saltar: se rechazan acá. El límite
# inferior (> 0) lo comprueba cada endpoint con su propio mensaje.
PRECIO_MAXIMO = 100_000            # soles por unidad de venta
FACTOR_MAXIMO = 10_000             # unidades base por unidad alternativa (ej. un saco de 50 kg)
COMENTARIO_RESENA_MAXIMO = 1_000
DESCRIPCION_MAXIMA = 600
TERMINO_BUSQUEDA_MAXIMO = 100


def _numero_acotado(valor, maximo):
    if valor is None:
        return None
    if not math.isfinite(valor):
        raise ValueError("debe ser un número válido")
    if valor > maximo:
        raise ValueError(f"no puede superar {maximo}")
    return valor


def _texto_acotado(valor, maximo):
    if valor is None:
        return None
    valor = valor.strip()
    if "\x00" in valor:
        raise ValueError("contiene caracteres no válidos")
    if len(valor) > maximo:
        raise ValueError(f"no puede superar {maximo} caracteres")
    return valor

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ProductoCrearDesdeCosecha(BaseModel):
    productor_id: str
    productor_nombre: str
    nombre: str
    unidad_medida: str = "kg"
    stock: int
    registro_produccion_id: str
    # Mismo campo y mismo criterio de obligatoriedad que RegistroProduccion.equivalencia_kg en
    # Productores (obligatorio solo si unidad_medida != "kg", validado abajo) — Productores ya lo
    # valida al crear el registro, esto es una segunda barrera del lado de Productos.
    equivalencia_kg: float | None = None
    # La categoría que corresponde al alimento (huevos → huevo, queso → lacteo...), ver
    # productores/plantillas_produccion.categoria_de. Si no es una de las 8, el borrador nace sin categoría.
    categoria: str | None = None
    # Las unidades en que se vende el alimento (productores/plantillas_produccion.unidades_venta); None = sin restricción.
    unidades_permitidas: list[str] | None = None

    @field_validator("unidades_permitidas")
    @classmethod
    def _unidades_conocidas(cls, valor):
        if valor is None:
            return None
        limpias = list(dict.fromkeys(valor))
        if not limpias or any(u not in UNIDADES_VALIDAS for u in limpias):
            raise ValueError(f"Las unidades deben ser de: {', '.join(UNIDADES_VALIDAS)}")
        return limpias

class ProductoActualizar(BaseModel):
    categoria: str | None = None
    precio: float | None = None
    # None = no tocar; "" = borrarla.
    descripcion: str | None = None

    @field_validator("descripcion")
    @classmethod
    def _descripcion_acotada(cls, valor):
        return _texto_acotado(valor, DESCRIPCION_MAXIMA)

    @field_validator("categoria")
    @classmethod
    def _categoria_valida(cls, valor):
        if valor is None:
            return None
        canonica = normalizar_categoria(valor)
        if canonica is None:
            raise ValueError("Categoría no válida. Elige una de: " + ", ".join(CATEGORIAS_VALIDAS))
        return canonica

    @field_validator("precio")
    @classmethod
    def _precio_acotado(cls, valor):
        return _numero_acotado(valor, PRECIO_MAXIMO)

class ImagenCrear(BaseModel):
    url: str
    orden: int = 0

class ResenaCrear(BaseModel):
    calificacion: int
    comentario: str | None = None

    @field_validator("comentario")
    @classmethod
    def _comentario_acotado(cls, valor):
        return _texto_acotado(valor, COMENTARIO_RESENA_MAXIMO)

# Tope de una línea de compra o reposición. Sin límite inferior, una cantidad negativa "descontaba" stock
# al revés: el stock subía en vez de bajar.
CANTIDAD_MAXIMA_LINEA = 1_000_000


class DescontarStock(BaseModel):
    cantidad: int = Field(ge=1, le=CANTIDAD_MAXIMA_LINEA)

class ReponerStock(BaseModel):
    cantidad: int = Field(ge=1, le=CANTIDAD_MAXIMA_LINEA)


OBSERVACIONES_LLEGADA_MAXIMO = 300


class LlegadaAlmacen(BaseModel):
    """Lo que el Admin comprobó al recibir el producto en el almacén."""
    # Unidades enteras, igual que el stock. El tope contra lo declarado por el productor lo comprueba el
    # endpoint (depende del producto).
    cantidad_recibida: int = Field(ge=1, le=CANTIDAD_MAXIMA_LINEA)
    observaciones: str | None = None

    @field_validator("observaciones")
    @classmethod
    def _observaciones_acotadas(cls, valor):
        return _texto_acotado(valor, OBSERVACIONES_LLEGADA_MAXIMO) or None


class LineaDescuento(BaseModel):
    producto_id: uuid.UUID
    # En la unidad base del producto; puede ser fraccionaria (1 arroba = 11,5 kg) y se redondea hacia
    # arriba al descontar, porque el stock es un entero.
    cantidad: float = Field(gt=0, le=CANTIDAD_MAXIMA_LINEA, allow_inf_nan=False)


class DescontarLote(BaseModel):
    lineas: list[LineaDescuento] = Field(min_length=1, max_length=100)

class OrdenImagen(BaseModel):
    orden: int


class ReservaCrear(BaseModel):
    """H7: cuánto le interesa al comprador (opcional). Sin unidad, es la unidad base del producto."""
    cantidad: float | None = Field(default=None, gt=0, le=CANTIDAD_MAXIMA_LINEA)
    unidad: str | None = Field(default=None, max_length=20)

class BusquedaRegistrar(BaseModel):
    termino: str

    @field_validator("termino")
    @classmethod
    def _termino_acotado(cls, valor):
        return _texto_acotado(valor, TERMINO_BUSQUEDA_MAXIMO)

class UnidadAlternativaCrear(BaseModel):
    unidad: str
    precio: float
    factor_a_base: float

    @field_validator("precio")
    @classmethod
    def _precio_acotado(cls, valor):
        return _numero_acotado(valor, PRECIO_MAXIMO)

    @field_validator("factor_a_base")
    @classmethod
    def _factor_acotado(cls, valor):
        return _numero_acotado(valor, FACTOR_MAXIMO)

class UnidadAlternativaActualizar(BaseModel):
    precio: float | None = None
    factor_a_base: float | None = None

    @field_validator("precio")
    @classmethod
    def _precio_acotado(cls, valor):
        return _numero_acotado(valor, PRECIO_MAXIMO)

    @field_validator("factor_a_base")
    @classmethod
    def _factor_acotado(cls, valor):
        return _numero_acotado(valor, FACTOR_MAXIMO)

# Circuit Breaker de Productos hacia Productores (RNF01). Todas las rutas que preguntan "¿quién es el productor de
# este token?" pasan por acá. Antes el breaker envolvía una función que nadie llamaba y cada ruta iba directo a
# Productores: con Productores caído, cada petición esperaba su propio intento. Ahora, tras 3 fallos seguidos, el
# circuito se abre 30 s y las rutas responden 503 al instante, sin intentar conectar.
# Solo cuentan como fallo los errores de red: un 404 ("todavía no tienes perfil") es una respuesta válida.
@breaker_productores
def _pedir_productor_me(token: str):
    with httpx.Client() as client:
        return client.get(
            f"{PRODUCTORES_URL}/productores/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )


def productor_del_token(token: str):
    """GET /productores/me con el token del usuario, a través del breaker. Productores caído (o el circuito abierto)
    es 503; cualquier otra respuesta (200, 404 sin perfil) la decide quien llama."""
    try:
        return _pedir_productor_me(token)
    except (pybreaker.CircuitBreakerError, httpx.RequestError):
        raise HTTPException(status_code=503, detail="Servicio de Productores no disponible")

@app.on_event("startup")
def preparar_almacen_de_imagenes():
    """Crea el bucket de imágenes (y su lectura pública) si no existe: en un MinIO nuevo no hay nada. Best-effort: MinIO
    puede tardar en estar listo, y subir_imagen lo vuelve a intentar en la primera subida."""
    try:
        asegurar_bucket_publico()
    except Exception:
        logging.getLogger(__name__).warning("No se pudo preparar el bucket de imágenes al arrancar", exc_info=True)


@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "productos"}

USUARIOS_URL = os.getenv("USUARIOS_URL", "http://localhost:8006")
PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")

def _nombre_publico(nombre: str | None) -> str:
    """Cómo se ve quién escribió una reseña: nombre e inicial del apellido ("Rosa Q."). La lista es pública y antes
    mostraba el nombre completo del comprador a cualquiera."""
    partes = (nombre or "").split()
    if not partes:
        return "Comprador"
    return partes[0] if len(partes) == 1 else f"{partes[0]} {partes[1][0].upper()}."


@app.get("/productos/{producto_id}/resenas")
def listar_resenas(producto_id: str, db: Session = Depends(get_db)):
    resenas = db.query(models.Resena).filter(
        models.Resena.producto_id == producto_id
    ).order_by(models.Resena.fecha_creacion.desc()).all()
    return [{**jsonable_encoder(r), "usuario_nombre": _nombre_publico(r.usuario_nombre)} for r in resenas]

# Antes cualquiera con sesión (también el agricultor dueño del producto) dejaba reseñas sin haber comprado, y cuantas
# quisiera: el promedio que se ve en el catálogo se podía inflar o hundir a mano. Ahora solo califica un comprador que
# RECIBIÓ el producto (Pedidos lo dice con el token del propio comprador) y una sola vez.
def _productos_que_recibio(client: httpx.Client, token: str) -> set[str]:
    try:
        resp = client.get(f"{PEDIDOS_URL}/pedidos/mios/productos-recibidos",
                          headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except httpx.RequestError:
        resp = None
    if resp is None or resp.status_code != 200:
        raise HTTPException(status_code=503, detail="No pudimos comprobar tu compra. Intenta de nuevo en un momento.")
    return set(resp.json().get("productos") or [])


@app.post("/productos/{producto_id}/resenas")
def crear_resena(
        producto_id: uuid.UUID,
        datos: ResenaCrear,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("comprador")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    if datos.calificacion < 1 or datos.calificacion > 5:
        raise HTTPException(status_code=422, detail="La calificación debe ser entre 1 y 5")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    usuario_id = usuario.get("sub")
    ya_califico = db.query(models.Resena).filter(models.Resena.producto_id == producto_id,
                                                 models.Resena.usuario_id == usuario_id).first()
    if ya_califico:
        raise HTTPException(status_code=409, detail="Ya calificaste este producto.")

    with httpx.Client() as client:
        if str(producto_id) not in _productos_que_recibio(client, credenciales.credentials):
            raise HTTPException(status_code=403, detail="Solo quien compró y recibió este producto puede calificarlo.")
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
    try:
        db.commit()
    except IntegrityError:  # doble clic: la otra petición ya la guardó (índice uq_resena_producto_usuario)
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya calificaste este producto.")
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
CERTIFICACION_URL = os.getenv("CERTIFICACION_URL", "http://localhost:8008")
PRODUCTOS_A_CERTIFICACION_SECRETO = os.getenv("PRODUCTOS_A_CERTIFICACION_SECRETO")  # registrar la llegada al almacén en la cadena (Productos llama a Certificación)
PRODUCTORES_A_PRODUCTOS_SECRETO = os.getenv("PRODUCTORES_A_PRODUCTOS_SECRETO")  # crear_producto_desde_cosecha (Productores llama al aprobar una cosecha — sub-entrega 3, todavía no conectado)

@app.post("/productos/{producto_id}/stock/reponer")
def reponer_stock(producto_id: str, datos: ReponerStock, x_servicio_secreto: str = Header(None), db: Session = Depends(get_db)):
    if not secreto_de_servicio_valido(x_servicio_secreto, PAGOS_A_PRODUCTOS_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    producto.stock += datos.cantidad
    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return producto

# POST /productos se eliminó: dejaba a cualquier productor publicar un producto "disponible" con
# nombre, precio, stock y unidad libres, sin cosecha, sin verificación y sin certificación. Un
# producto solo nace cuando el agricultor completa su cosecha con la foto tomada en la chacra
# (crear_producto_desde_cosecha, más abajo; antes la aprobaba un Verificador, rol retirado el 2026-10-01),
# pasa por borrador -> en_transito -> disponible y su cadena de certificación se registra en ese mismo flujo.

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

    resp = productor_del_token(credenciales.credentials)

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

    # Tamaño y contenido reales (ver imagenes.py): el content-type de arriba solo lo declara el cliente.
    datos_imagen, tipo_imagen = leer_imagen_validada(archivo)
    try:
        url_publica = subir_imagen(io.BytesIO(datos_imagen), tipo_imagen)
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

    resp = productor_del_token(credenciales.credentials)

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
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
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

    # Join simple, una sola query para todo el catálogo — igual que imagenes/stats_resenas de
    # arriba: el frontend necesita ver las unidades alternativas de cada producto para poder
    # ofrecer "comprar por saco" además de "comprar por kg" sin una llamada aparte por producto.
    unidades_alt = db.query(models.UnidadAlternativa).filter(
        models.UnidadAlternativa.producto_id.in_([p.id for p in productos])
    ).order_by(models.UnidadAlternativa.unidad).all() if productos else []
    unidades_por_producto = {}
    for u in unidades_alt:
        unidades_por_producto.setdefault(str(u.producto_id), []).append({
            "id": u.id,
            "unidad": u.unidad,
            "precio": u.precio,
            "factor_a_base": u.factor_a_base,
        })

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
            "equivalencia_kg": p.equivalencia_kg,
            "imagen_url": p.imagen_url,
            "fecha_publicacion": p.fecha_publicacion,
            "imagen_principal": primera_imagen_por_producto.get(str(p.id)),
            "calificacion_promedio": stats_por_producto.get(str(p.id), (None, 0))[0],
            "total_resenas": stats_por_producto.get(str(p.id), (None, 0))[1],
            "estado": p.estado,
            "unidades_alternativas": alternativas_a_la_venta(p, unidades_por_producto.get(str(p.id), [])),
            "unidades_permitidas": unidades_permitidas_de(p),
        }
        for p in productos
    ]
    cache.guardar(cache.CLAVE_CATALOGO, resultado)
    return resultado

# Lo que el comprador reservó (para que la página del producto diga "te avisaremos" en vez de ofrecer reservar otra
# vez). Va antes de /productos/{producto_id}, que se comería "mis-reservas" como id.
@app.get("/productos/mis-reservas")
def mis_reservas(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("comprador"))):
    filas = db.query(models.Reserva).filter(models.Reserva.comprador_id == usuario.get("sub")).all()
    return {"productos": sorted(str(r.producto_id) for r in filas),
            # H7: cuánto pidió en cada una (para decirle "reservaste 2 sacos")
            "reservas": [{"producto_id": str(r.producto_id), "cantidad": float(r.cantidad) if r.cantidad is not None else None,
                          "unidad": r.unidad} for r in filas]}


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
    resp = productor_del_token(credenciales.credentials)

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
            "descripcion": p.descripcion,
            "unidades_permitidas": unidades_permitidas_de(p),
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

def _puede_ver_el_borrador(producto, usuario, credenciales) -> bool:
    """Un borrador (todavía sin publicar: sin precio confirmado, sin imagen, sin revisión del productor) lo ven
    solo su productor y el admin. Antes GET /productos/{id} lo devolvía a cualquiera que conociera el id."""
    if usuario is None:
        return False
    if usuario.get("rol") == "admin":
        return True
    if usuario.get("rol") != "productor" or credenciales is None:
        return False
    resp = productor_del_token(credenciales.credentials)
    return resp.status_code == 200 and str(producto.productor_id) == str(resp.json().get("id"))


@app.get("/productos/{producto_id}")
def obtener_producto(
        producto_id: uuid.UUID,
        db: Session = Depends(get_db),
        usuario: dict | None = Depends(usuario_opcional),
        credenciales: HTTPAuthorizationCredentials | None = Depends(security_opcional),
):
    producto_id = str(producto_id)
    clave = cache.clave_detalle(producto_id)
    cacheado = cache.obtener(clave)
    # Un borrador nunca se sirve desde la caché (ni se guarda en ella): esa respuesta la comparten todos los
    # visitantes. Una entrada sin "estado" es de antes de que el detalle lo trajera: se ignora por si era un borrador.
    if cacheado is not None and cacheado.get("estado") not in (None, "borrador"):
        return cacheado

    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    if producto.estado == "borrador" and not _puede_ver_el_borrador(producto, usuario, credenciales):
        # 404 y no 403: a quien no es el dueño no se le confirma ni que existe
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

    unidades_alt = db.query(models.UnidadAlternativa).filter(
        models.UnidadAlternativa.producto_id == producto_id
    ).order_by(models.UnidadAlternativa.unidad).all()

    resultado = {
        "id": producto.id,
        "productor_id": producto.productor_id,
        "productor_nombre": producto.productor_nombre,
        "nombre": producto.nombre,
        "descripcion": producto.descripcion,
        "categoria": producto.categoria,
        "precio": producto.precio,
        "stock": producto.stock,
        "unidad_medida": producto.unidad_medida,
        "equivalencia_kg": producto.equivalencia_kg,
        "imagen_url": producto.imagen_url,
        "fecha_publicacion": producto.fecha_publicacion,
        "estado": producto.estado,
        "imagenes": [{"id": img.id, "url": img.url, "orden": img.orden} for img in imagenes],
        "calificacion_promedio": promedio,
        "total_resenas": total_resenas,
        "unidades_alternativas": alternativas_a_la_venta(producto, [
            {"id": u.id, "unidad": u.unidad, "precio": u.precio, "factor_a_base": u.factor_a_base}
            for u in unidades_alt
        ]),
        "unidades_permitidas": unidades_permitidas_de(producto),
    }
    if producto.estado != "borrador":
        cache.guardar(clave, resultado)
    return resultado

# Descuenta el stock de TODAS las líneas de un pedido en una sola transacción: o se descuentan todas o no
# se descuenta ninguna. Antes Pedidos llamaba a PATCH /productos/{id}/stock una vez por línea y no miraba la
# respuesta: si una fallaba (stock que otro se llevó justo antes, cantidad fraccionaria rechazada por el
# tipo entero) el pedido se creaba igual y se vendía stock que no existía. Además es aquí donde se decide
# de verdad qué se puede comprar: solo los productos "disponible" (ya en el almacén), no los borradores ni
# los que siguen en tránsito. Las filas se bloquean en orden de id, así dos pedidos simultáneos no se
# traban entre sí, y las líneas repetidas de un mismo producto se suman antes de comparar con el stock.
@app.post("/productos/interno/descontar-stock-lote")
def descontar_stock_lote(datos: DescontarLote, x_servicio_secreto: str = Header(None), db: Session = Depends(get_db)):
    # Lo llaman dos servicios, cada uno con su propia clave: Pedidos al crear un pedido y Pagos al reintentar
    # el pago de uno rechazado (cuyo stock ya se había devuelto). Cada comparación falla cerrado por separado.
    if not (
        secreto_de_servicio_valido(x_servicio_secreto, PEDIDOS_A_PRODUCTOS_SECRETO)
        or secreto_de_servicio_valido(x_servicio_secreto, PAGOS_A_PRODUCTOS_SECRETO)
    ):
        raise HTTPException(status_code=403, detail="No autorizado")

    pedido_por_producto = {}
    for linea in datos.lineas:
        pedido_por_producto[linea.producto_id] = pedido_por_producto.get(linea.producto_id, 0) + linea.cantidad

    ids = sorted(pedido_por_producto, key=str)
    productos = db.query(models.Producto).filter(
        models.Producto.id.in_(ids)
    ).order_by(models.Producto.id).with_for_update().all()
    por_id = {p.id: p for p in productos}

    for producto_id in ids:
        producto = por_id.get(producto_id)
        if producto is None:
            raise HTTPException(status_code=404, detail=f"Producto {producto_id} no existe")
        if producto.estado != "disponible":
            raise HTTPException(status_code=409, detail=f"{producto.nombre} aún no está disponible para comprar")
        if producto.stock < math.ceil(pedido_por_producto[producto_id]):
            raise HTTPException(status_code=409, detail=f"Stock insuficiente para {producto.nombre}")

    for producto_id in ids:
        por_id[producto_id].stock -= math.ceil(pedido_por_producto[producto_id])
    db.commit()
    cache.invalidar(cache.CLAVE_CATALOGO, *[cache.clave_detalle(str(pid)) for pid in ids])
    return {"descontados": len(ids)}


# La compensación del descuento de arriba (saga del pedido): Pedidos devuelve lo que descontó si al final no pudo guardar
# el pedido. Mismas claves y mismo redondeo hacia arriba que al descontar, así vuelve exactamente lo que salió. Un
# producto que ya no existe se informa y no frena a los demás.
@app.post("/productos/interno/reponer-stock-lote")
def reponer_stock_lote(datos: DescontarLote, x_servicio_secreto: str = Header(None), db: Session = Depends(get_db)):
    if not (
        secreto_de_servicio_valido(x_servicio_secreto, PEDIDOS_A_PRODUCTOS_SECRETO)
        or secreto_de_servicio_valido(x_servicio_secreto, PAGOS_A_PRODUCTOS_SECRETO)
    ):
        raise HTTPException(status_code=403, detail="No autorizado")

    por_producto = {}
    for linea in datos.lineas:
        por_producto[linea.producto_id] = por_producto.get(linea.producto_id, 0) + linea.cantidad
    ids = sorted(por_producto, key=str)
    productos = db.query(models.Producto).filter(
        models.Producto.id.in_(ids)
    ).order_by(models.Producto.id).with_for_update().all()
    por_id = {p.id: p for p in productos}
    for producto_id, producto in por_id.items():
        producto.stock += math.ceil(por_producto[producto_id])
    db.commit()
    cache.invalidar(cache.CLAVE_CATALOGO, *[cache.clave_detalle(str(pid)) for pid in por_id])
    return {"repuestos": len(por_id), "no_encontrados": [str(pid) for pid in ids if pid not in por_id]}

@app.patch("/productos/{producto_id}/stock")
def descontar_stock(producto_id: str, datos: DescontarStock, x_servicio_secreto: str = Header(None), db: Session = Depends(get_db)):
    if not secreto_de_servicio_valido(x_servicio_secreto, PEDIDOS_A_PRODUCTOS_SECRETO):
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
    """Edita categoria/precio/descripcion de un producto propio — de a uno o juntos, lo que venga en
    el body (None = no tocar ese campo). Solo mientras está en "borrador": editar un producto ya
    publicado es una conversación aparte (¿republicar? ¿efecto inmediato en el catálogo?), no se
    resuelve acá."""
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    resp = productor_del_token(credenciales.credentials)

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
    if datos.descripcion is not None:
        producto.descripcion = datos.descripcion or None

    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return producto

@app.get("/productos/{producto_id}/unidades")
def listar_unidades_alternativas(
        producto_id: uuid.UUID,
        db: Session = Depends(get_db),
        usuario: dict | None = Depends(usuario_opcional),
        credenciales: HTTPAuthorizationCredentials | None = Depends(security_opcional),
):
    # Público: el comprador necesita verlas en el catálogo/detalle (aunque en la
    # práctica el frontend las trae embebidas en GET /productos y GET /productos/{id} — este
    # endpoint queda para consultarlas sueltas si hace falta). Mismo criterio que listar_resenas:
    # sin 404 si el producto no existe, simplemente no hay filas que devolver. Las de un borrador solo las
    # ve su productor (o el admin): para el resto, como si no hubiera ninguna.
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if producto is not None and producto.estado == "borrador" and not _puede_ver_el_borrador(producto, usuario, credenciales):
        return []
    return db.query(models.UnidadAlternativa).filter(
        models.UnidadAlternativa.producto_id == producto_id
    ).order_by(models.UnidadAlternativa.unidad).all()

@app.post("/productos/{producto_id}/unidades")
def crear_unidad_alternativa(
        producto_id: str,
        datos: UnidadAlternativaCrear,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    """Unidad adicional en la que se puede comprar este producto, con su propio precio — no
    reemplaza precio/unidad_medida del producto (la unidad "base"), la complementa. Sin gate de
    estado a propósito, a diferencia de PATCH /productos/{id}: el productor puede ajustar sus
    unidades alternativas en cualquier momento, publicado o no."""
    producto = db.query(models.Producto).filter(models.Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    resp = productor_del_token(credenciales.credentials)

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Debes tener un perfil de productor")

    productor = resp.json()
    if str(producto.productor_id) != str(productor["id"]):
        raise HTTPException(status_code=403, detail="No puedes modificar un producto que no te pertenece")

    if datos.unidad not in UNIDADES_VALIDAS:
        raise HTTPException(status_code=422, detail=f"Unidad no válida. Debe ser una de: {', '.join(UNIDADES_VALIDAS)}")
    permitidas = unidades_permitidas_de(producto)
    if permitidas is not None and datos.unidad not in permitidas:
        raise HTTPException(status_code=422, detail=f"{producto.nombre} se vende por {', '.join(permitidas)}: no por {datos.unidad}.")
    if datos.unidad == producto.unidad_medida:
        raise HTTPException(status_code=400, detail="Esa unidad ya es la unidad base del producto, no tiene sentido agregarla como alternativa")
    if datos.precio <= 0:
        raise HTTPException(status_code=422, detail="El precio debe ser mayor a 0")
    if datos.factor_a_base <= 0:
        raise HTTPException(status_code=422, detail="El factor de conversión debe ser mayor a 0")

    ya_existe = db.query(models.UnidadAlternativa).filter(
        models.UnidadAlternativa.producto_id == producto_id,
        models.UnidadAlternativa.unidad == datos.unidad,
    ).first()
    if ya_existe:
        raise HTTPException(status_code=400, detail="Ya existe una unidad alternativa con esa unidad para este producto")

    nueva = models.UnidadAlternativa(
        producto_id=producto_id,
        unidad=datos.unidad,
        precio=datos.precio,
        factor_a_base=datos.factor_a_base,
    )
    db.add(nueva)
    try:
        db.commit()
    except IntegrityError:
        # Carrera entre el SELECT de arriba y este insert — el UniqueConstraint del modelo es
        # quien de verdad evita el duplicado, acá solo se traduce a un 400 legible en vez de 500.
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya existe una unidad alternativa con esa unidad para este producto")
    db.refresh(nueva)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))
    return nueva

@app.patch("/productos/unidades/{unidad_id}")
def actualizar_unidad_alternativa(
        unidad_id: str,
        datos: UnidadAlternativaActualizar,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    unidad_alt = db.query(models.UnidadAlternativa).filter(models.UnidadAlternativa.id == unidad_id).first()
    if not unidad_alt:
        raise HTTPException(status_code=404, detail="Unidad alternativa no encontrada")

    producto = db.query(models.Producto).filter(models.Producto.id == unidad_alt.producto_id).first()

    resp = productor_del_token(credenciales.credentials)

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Debes tener un perfil de productor")

    productor = resp.json()
    if str(producto.productor_id) != str(productor["id"]):
        raise HTTPException(status_code=403, detail="No puedes modificar una unidad alternativa de un producto que no te pertenece")

    if datos.precio is not None:
        if datos.precio <= 0:
            raise HTTPException(status_code=422, detail="El precio debe ser mayor a 0")
        unidad_alt.precio = datos.precio
    if datos.factor_a_base is not None:
        if datos.factor_a_base <= 0:
            raise HTTPException(status_code=422, detail="El factor de conversión debe ser mayor a 0")
        unidad_alt.factor_a_base = datos.factor_a_base

    db.commit()
    db.refresh(unidad_alt)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(str(producto.id)))
    return unidad_alt

@app.delete("/productos/unidades/{unidad_id}")
def eliminar_unidad_alternativa(
        unidad_id: str,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("productor")),
        credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    unidad_alt = db.query(models.UnidadAlternativa).filter(models.UnidadAlternativa.id == unidad_id).first()
    if not unidad_alt:
        raise HTTPException(status_code=404, detail="Unidad alternativa no encontrada")

    producto = db.query(models.Producto).filter(models.Producto.id == unidad_alt.producto_id).first()

    resp = productor_del_token(credenciales.credentials)

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Debes tener un perfil de productor")

    productor = resp.json()
    if str(producto.productor_id) != str(productor["id"]):
        raise HTTPException(status_code=403, detail="No puedes eliminar una unidad alternativa de un producto que no te pertenece")

    producto_id_afectado = str(producto.id)
    db.delete(unidad_alt)
    db.commit()
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id_afectado))
    return {"mensaje": "Unidad alternativa eliminada"}

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

    resp = productor_del_token(credenciales.credentials)

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
    if normalizar_categoria(producto.categoria) is None:
        raise HTTPException(status_code=400, detail="Elige una categoría válida antes de publicar.")
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

def _texto_de_llegada(producto, declarada: int, recibida: int, observaciones: str | None) -> str:
    """El texto que queda en la cadena de certificación: lo que se declaró, lo que llegó y, si hay
    diferencia u observaciones, también eso. La fecha la pone el bloque."""
    unidad = producto.unidad_medida or "kg"
    if recibida == declarada:
        texto = f"Llegaron al almacén {recibida} {unidad} de {producto.nombre}: la cantidad completa declarada por el productor."
    else:
        texto = (
            f"Llegaron al almacén {recibida} {unidad} de {producto.nombre}: "
            f"{declarada - recibida} {unidad} menos de las {declarada} declaradas por el productor."
        )
    if observaciones:
        texto += f" Observaciones: {observaciones}"
    return texto


def _registrar_llegada_en_la_cadena(producto_id, texto: str) -> None:
    """Deja el evento llegada_almacen en Certificación ANTES de cambiar el producto: si no se puede
    registrar, el producto sigue "en_transito" y el Admin reintenta (el evento es idempotente en
    Certificación). Así ningún producto pasa a "disponible" sin su registro de llegada."""
    error = HTTPException(
        status_code=503,
        detail="No se pudo registrar la llegada en la cadena de certificación. El producto sigue en tránsito: intenta de nuevo en un momento.",
    )
    try:
        resp = httpx.post(
            f"{CERTIFICACION_URL}/certificacion/{producto_id}/llegada-almacen",
            json={"datos": texto},
            headers={"X-Servicio-Secreto": PRODUCTOS_A_CERTIFICACION_SECRETO or ""},
            timeout=5,
        )
    except httpx.RequestError:
        raise error
    if resp.status_code != 200:
        raise error


@app.patch("/productos/{producto_id}/confirmar-llegada-almacen")
def confirmar_llegada_almacen(
        producto_id: uuid.UUID,
        datos: LlegadaAlmacen,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("admin")),
):
    """El Admin confirma que un producto "en_transito" llegó físicamente al almacén central y cuánto llegó:
    recién ahí pasa a "disponible" (comprable de verdad, no solo reservable) y su stock pasa a ser lo
    recibido. Antes no se registraba ninguna cantidad (el stock seguía siendo lo que el productor declaró)
    ni quedaba rastro en la cadena.

    - No puede recibirse más de lo declarado: esa cantidad es la que certificó la cosecha aprobada.
    - Si llegó menos, el stock es lo recibido y la diferencia queda escrita en la cadena.
    - La fila se bloquea mientras se decide: dos confirmaciones simultáneas se turnan y la segunda ve el
      producto ya "disponible" (400)."""
    producto_id = str(producto_id)
    producto = db.query(models.Producto).filter(
        models.Producto.id == producto_id
    ).with_for_update().first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if producto.estado != "en_transito":
        raise HTTPException(status_code=400, detail="Este producto no está en tránsito hacia el almacén")

    declarada = producto.stock
    if datos.cantidad_recibida > declarada:
        raise HTTPException(
            status_code=422,
            detail=f"No puede llegar más de lo declarado por el productor ({declarada} {producto.unidad_medida or 'kg'}): esa es la cantidad que certificó la cosecha.",
        )

    _registrar_llegada_en_la_cadena(
        producto_id, _texto_de_llegada(producto, declarada, datos.cantidad_recibida, datos.observaciones)
    )

    producto.stock = datos.cantidad_recibida
    producto.estado = "disponible"
    db.commit()
    db.refresh(producto)
    cache.invalidar(cache.CLAVE_CATALOGO, cache.clave_detalle(producto_id))

    # La cosecha ya está pesada en el almacén: Transporte cierra su recojo en la chacra (si lo hubo) y recién ahí se le
    # liquida al repartidor. Si RabbitMQ no responde la llegada ya quedó confirmada igual: el admin ve el recojo
    # "en el almacén" y Transporte lo cierra con el siguiente aviso.
    try:
        publicar_evento({
            "evento": "llegada_almacen",
            "producto_id": str(producto.id),
            "productor_id": str(producto.productor_id),
            "cantidad_recibida": datos.cantidad_recibida,
        })
    except Exception:
        logging.getLogger(__name__).error("No se pudo avisar la llegada al almacén del producto %s", producto.id, exc_info=True)

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

def _cantidad_de_la_reserva(producto, datos: ReservaCrear | None):
    """H7: la cantidad y la unidad que pidió el comprador, validadas: la unidad tiene que ser una en que se vende el
    producto (la base o una alternativa a la venta) y la cantidad no puede pasar de lo que viene en camino."""
    if datos is None or datos.cantidad is None:
        return None, None
    unidad = datos.unidad or producto.unidad_medida
    if unidad == producto.unidad_medida:
        factor = 1.0
    else:
        alternativas = alternativas_a_la_venta(producto, [
            {"unidad": u.unidad, "factor_a_base": u.factor_a_base} for u in producto.unidades_alternativas])
        alternativa = next((a for a in alternativas if a["unidad"] == unidad), None)
        if not alternativa:
            raise HTTPException(status_code=422, detail=f"{producto.nombre} no se vende por {unidad}.")
        factor = float(alternativa["factor_a_base"])
    if producto.stock is not None and datos.cantidad * factor > float(producto.stock):
        raise HTTPException(status_code=422,
                            detail=f"Solo vienen {producto.stock} {producto.unidad_medida}: reserva una cantidad menor.")
    return datos.cantidad, unidad


@app.post("/productos/{producto_id}/reservar")
def reservar_producto(
        producto_id: str,
        datos: ReservaCrear | None = None,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("comprador")),
):
    """Reserva de interés (sin monto, sin pago) sobre un producto todavía "en_transito" — no
    tiene sentido reservar algo que ya está "disponible", ahí se compra directo. Puede decir cuánto
    le interesa (H7): no aparta stock, solo le avisa al agricultor y al Admin cuánto se pide."""
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

    cantidad, unidad = _cantidad_de_la_reserva(producto, datos)
    nueva_reserva = models.Reserva(producto_id=producto_id, comprador_id=comprador_id, cantidad=cantidad, unidad=unidad)
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

@app.delete("/productos/{producto_id}/reservar")
def cancelar_reserva(
        producto_id: uuid.UUID,
        db: Session = Depends(get_db),
        usuario: dict = Depends(requiere_rol("comprador")),
):
    """"Ya no me avises": el comprador quita SU reserva (nunca la de otro)."""
    borradas = db.query(models.Reserva).filter(models.Reserva.producto_id == producto_id,
                                               models.Reserva.comprador_id == usuario.get("sub")).delete()
    db.commit()
    if not borradas:
        raise HTTPException(status_code=404, detail="No tenías una reserva de este producto")
    return {"mensaje": "Listo: ya no te avisaremos de este producto"}


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
        resp = productor_del_token(credenciales.credentials)

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
    """Llamado por Productores cuando el agricultor completa su cosecha (completar_cosecha). Nace en "borrador", sin
    precio y con la categoría que corresponde al alimento (si se conoce): el productor completa precio + imágenes y publica
    a mano (PATCH /productos/{id}/publicar) antes de que sea visible en el catálogo.

    productor_nombre viaja en el body en vez de resolverse acá: quien llama (Productores) ya
    tiene ese dato en su propia base, así que no hace falta una llamada cruzada solo para
    obtenerlo (mismo criterio que productor_id, que tampoco se resuelve desde un token).

    Idempotente por construcción: UniqueConstraint("registro_produccion_id") en el modelo
    (models.py) es quien de verdad evita duplicados si Productores reintenta esta llamada tras
    un fallo de red — acá solo se traduce esa violación en un 409 legible, no se re-implementa
    la unicidad a mano con un SELECT previo (que además sería vulnerable a una carrera entre
    el chequeo y el insert)."""
    if not secreto_de_servicio_valido(x_servicio_secreto, PRODUCTORES_A_PRODUCTOS_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    if datos.unidad_medida != "kg" and not datos.equivalencia_kg:
        raise HTTPException(
            status_code=422,
            detail="Cuando la unidad de medida no es 'kg', debes indicar la equivalencia a kg (ej. 1 litro = X kg)",
        )

    nuevo = models.Producto(
        productor_id=datos.productor_id,
        productor_nombre=datos.productor_nombre,
        nombre=datos.nombre,
        categoria=normalizar_categoria(datos.categoria),
        precio=None,
        stock=datos.stock,
        unidad_medida=datos.unidad_medida,
        unidades_permitidas=",".join(datos.unidades_permitidas) if datos.unidades_permitidas else None,
        estado="borrador",
        registro_produccion_id=datos.registro_produccion_id,
        equivalencia_kg=datos.equivalencia_kg,
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

    resp = productor_del_token(credenciales.credentials)

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