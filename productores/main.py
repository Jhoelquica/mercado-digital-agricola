import hashlib
import math
import os
import uuid
import logging
import httpx
from datetime import datetime, timedelta

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, model_validator
from auth import verificar_token, requiere_rol
from auth import secreto_de_servicio_valido
import pybreaker
from validaciones import (
    Cantidad, CodigoChacra, Comunidad, Contacto, Cultivo, Equivalencia, Fecha, Latitud, LatitudTexto,
    Longitud, LongitudTexto, Monto, NombreChacra, NombreProductor, Parcelas, Ubicacion,
    UnidadMedida, CICLO_MAXIMO_DIAS, COSECHA_FUTURA_TOLERANCIA_DIAS, FECHA_SIEMBRA_MINIMA,
    SIEMBRA_FUTURA_MAXIMA_DIAS,
)
from imagenes import leer_imagen_validada, LIMITE_CUERPO_BYTES, MENSAJE_MUY_GRANDE
from minio_client import subir_foto_cosecha, asegurar_bucket_publico
from migraciones import agregar_columnas_faltantes
import sugerencia_cosecha

from database import Base, engine, SessionLocal
import models

Base.metadata.create_all(bind=engine)
# create_all() no agrega columnas a una tabla que ya existe (p. ej. la evidencia GPS/foto de registros_produccion).
agregar_columnas_faltantes(engine, Base)

logger = logging.getLogger("productores.main")

app = FastAPI()

Instrumentator().instrument(app).expose(app)


@app.middleware("http")
async def limitar_el_tamano_de_las_subidas(request, call_next):
    """Rechaza por su Content-Length una foto demasiado grande ANTES de leer el cuerpo (mismo criterio que
    productos/main.py). Va antes del middleware de CORS (queda por dentro) para que el 413 lleve las cabeceras CORS."""
    if request.method == "POST" and request.url.path.endswith("/foto-cosecha"):
        longitud = request.headers.get("content-length", "")
        if longitud.isdigit() and int(longitud) > LIMITE_CUERPO_BYTES:
            return JSONResponse(status_code=413, content={"detail": MENSAJE_MUY_GRANDE})
    return await call_next(request)


@app.on_event("startup")
def preparar_almacen_de_fotos():
    """Crea el bucket de fotos de cosechas (y su lectura pública) si no existe. Best-effort: MinIO puede tardar en
    estar listo, y subir_foto_cosecha lo vuelve a intentar en la primera subida."""
    try:
        asegurar_bucket_publico()
    except Exception:
        logger.warning("No se pudo preparar el bucket de fotos de cosechas al arrancar", exc_info=True)


@app.exception_handler(RequestValidationError)
async def _errores_de_validacion(request, exc):
    """Igual que el manejador por defecto de FastAPI, pero sin el valor recibido ("input"): si el
    cliente manda NaN o Infinity, ese valor no se puede serializar a JSON y la respuesta 422 salía
    como un error 500."""
    detalle = [{clave: valor for clave, valor in error.items() if clave != "input"} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detalle)})

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
    # Los tipos de validaciones.py exigen lo mismo que el formulario (largos, coordenadas numéricas)
    # sin depender de que la petición venga del frontend.
    nombre: NombreProductor
    comunidad: Comunidad | None = None
    contacto: Contacto | None = None
    ubicacion: Ubicacion | None = None
    latitud: LatitudTexto | None = None
    longitud: LongitudTexto | None = None

class ProductorPublico(BaseModel):
    """Lo que ve cualquier visitante de un productor (rutas públicas GET /productores y
    /productores/{id}, que consume el detalle de producto para mostrar "cultivado por" y la zona de
    origen). No incluye el contacto (teléfono) ni el usuario_id, y las coordenadas van redondeadas:
    el dueño lo ve todo en /productores/me y el admin en /productores/admin/todos."""
    id: uuid.UUID
    nombre: str
    comunidad: str | None = None
    ubicacion: str | None = None
    fecha_registro: datetime | None = None
    latitud: str | None = None
    longitud: str | None = None


DECIMALES_COORDENADA_PUBLICA = 2  # 0,01° ≈ 1,1 km: sirve para mostrar la zona de origen, no la finca


def _aproximar_coordenada(valor):
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numero):
        return None
    return f"{numero:.{DECIMALES_COORDENADA_PUBLICA}f}"


def _a_publico(productor) -> ProductorPublico:
    return ProductorPublico(
        id=productor.id,
        nombre=productor.nombre,
        comunidad=productor.comunidad,
        ubicacion=productor.ubicacion,
        fecha_registro=productor.fecha_registro,
        latitud=_aproximar_coordenada(productor.latitud),
        longitud=_aproximar_coordenada(productor.longitud),
    )

# ============ Chacras ============

class ChacraCrear(BaseModel):
    codigo: CodigoChacra
    nombre: NombreChacra | None = None
    ubicacion_latitud: Latitud
    ubicacion_longitud: Longitud

class ChacraSalida(BaseModel):
    # Los demás endpoints devuelven el objeto ORM crudo; acá se usa un schema de salida
    # explícito (y como response_model) para dejar clara la forma pública de la entidad.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    productor_id: uuid.UUID
    codigo: str
    nombre: str | None = None
    ubicacion_latitud: float
    ubicacion_longitud: float
    fecha_registro: datetime

# ============ Gestión Económica (Módulo 1) ============

class _ConUbicacionGps(BaseModel):
    """Dónde está el agricultor al enviar el formulario (GPS del navegador). Opcional: si el navegador no la da (sin
    permiso, sin señal) se registra igual, con advertencia en la cadena. Si llega, tienen que llegar las dos."""
    latitud: Latitud | None = None
    longitud: Longitud | None = None

    @model_validator(mode="after")
    def _las_dos_o_ninguna(self):
        if (self.latitud is None) != (self.longitud is None):
            raise ValueError("La ubicación necesita latitud y longitud")
        return self


class RegistroProduccionCrear(_ConUbicacionGps):
    # Toda siembra se asocia a una chacra del productor (obligatorio). Se valida en el
    # endpoint que la chacra pertenezca a la cuenta autenticada, no solo que exista.
    chacra_id: uuid.UUID
    cultivo: Cultivo
    numero_parcelas: Parcelas
    ubicacion_cosecha: Ubicacion | None = None
    # Solo costos "de entrada" — la mano de obra se completa recién al cosechar
    # (ver comentario en models.RegistroProduccion).
    costo_semillas: Monto = 0
    costo_insumos: Monto = 0
    unidad_medida: UnidadMedida = "kg"
    equivalencia_kg: Equivalencia | None = None
    fecha_siembra: Fecha
    fecha_cosecha_estimada: Fecha

class RegistroProduccionEditar(BaseModel):
    """Lo que el productor puede corregir de una siembra todavía planificada. La chacra y dónde estaba al registrarla
    (siembra_latitud/longitud) no: son la evidencia en origen que después queda sellada en la cadena."""
    cultivo: Cultivo
    numero_parcelas: Parcelas
    costo_semillas: Monto = 0
    costo_insumos: Monto = 0
    unidad_medida: UnidadMedida = "kg"
    equivalencia_kg: Equivalencia | None = None
    fecha_siembra: Fecha
    fecha_cosecha_estimada: Fecha

class SugerirCosechaDatos(BaseModel):
    chacra_id: uuid.UUID
    cultivo: Cultivo
    fecha_siembra: Fecha


class CompletarCosechaDatos(_ConUbicacionGps):
    cantidad_cosechada: Cantidad
    fecha_cosecha_real: Fecha
    costo_mano_obra: Monto
    # Ya no se pide el "costo de envío final": se confundía con el envío que paga el comprador (observación de la
    # presentación del 2026-10-01). Si un cliente viejo lo sigue mandando, se ignora.

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py). Usada SOLO
# por los DELETE de limpieza QA de este servicio (eliminar_productor, eliminar_registro_
# produccion) — ningún otro servicio llama a estos endpoints.
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")
# Sin fallback hardcodeado a propósito (mismo criterio). La envía completar_cosecha al crear el
# producto en el catálogo — Productos valida este mismo valor en crear_producto_desde_cosecha.
PRODUCTORES_A_PRODUCTOS_SECRETO = os.getenv("PRODUCTORES_A_PRODUCTOS_SECRETO")
CERTIFICACION_URL = os.getenv("CERTIFICACION_URL", "http://localhost:8008")
# Sin fallback hardcodeado a propósito (mismo criterio). La envía completar_cosecha a Certificación
# para dejar registrada la cosecha (con su evidencia en origen) del producto recién creado.
PRODUCTORES_A_CERTIFICACION_SECRETO = os.getenv("PRODUCTORES_A_CERTIFICACION_SECRETO")

# Solo unidades chicas/granulares como unidad_medida de una cosecha — una unidad de empaque
# grande (saco, arroba) como base generaría, en cualquier unidad de venta alternativa que se
# agregue después en Productos (ver UnidadAlternativa.factor_a_base), conversiones con
# fracciones raras en vez de números enteros cómodos. Esas unidades de empaque siguen
# existiendo, pero recién como unidad alternativa del producto ya publicado, no acá. Mismo
# conjunto que UNIDADES_VALIDAS en productos/main.py menos "saco"/"arroba" — no se comparte
# literalmente entre servicios (no hay librería común entre microservicios en este proyecto);
# si se agrega una unidad chica nueva en un lado, hay que revisar si corresponde en el otro.
UNIDADES_COSECHA_VALIDAS = ["kg", "unidad", "litro"]

# Radio alrededor de la chacra dentro del cual se considera que el agricultor está en ella al registrar la siembra o la
# cosecha. Mismo valor que RADIO_GEOFENCING_KM en certificacion/logica_cadena.py. Fuera de él se permite igual (el GPS
# falla seguido en el campo) pero queda como advertencia en la cadena.
RADIO_CHACRA_KM = 1.5


def calcular_distancia_km(lat1, lon1, lat2, lon2):
    """Haversine, en km. Copiada de pedidos/main.py: cada microservicio es autosuficiente."""
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    radio_tierra = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return round(radio_tierra * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 3)


def _distancia_a_la_chacra(chacra, latitud, longitud):
    """None si no hay ubicación del agricultor (o no se sabe dónde está la chacra)."""
    if latitud is None or longitud is None or chacra is None:
        return None
    return calcular_distancia_km(chacra.ubicacion_latitud, chacra.ubicacion_longitud, latitud, longitud)

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


# Lista completa (con contacto y coordenadas exactas) solo para el panel de administración. Va con dos
# segmentos de ruta, así que no choca con GET /productores/{productor_id}.
@app.get("/productores/admin/todos")
def listar_productores_completo(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    return db.query(models.Productor).all()


# Públicas a propósito (el comprador ve quién cultiva un producto y de qué zona viene), pero solo con
# lo público: antes devolvían la fila entera, con el teléfono, el usuario_id y las coordenadas exactas
# de cada finca, a cualquiera y sin sesión. productor_id va tipado como UUID: un id mal formado da 422
# en vez de un error 500 de la base.
@app.get("/productores/{productor_id}", response_model=ProductorPublico)
def obtener_productor(productor_id: uuid.UUID, db: Session = Depends(get_db)):
    productor = db.query(models.Productor).filter(models.Productor.id == productor_id).first()
    if not productor:
        raise HTTPException(status_code=404, detail="Productor no encontrado")
    return _a_publico(productor)

@app.get("/productores", response_model=list[ProductorPublico])
def listar_productores(db: Session = Depends(get_db)):
    return [_a_publico(p) for p in db.query(models.Productor).all()]

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
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
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


def _obtener_productor_del_token(usuario: dict, db: Session) -> models.Productor:
    productor = db.query(models.Productor).filter(
        models.Productor.usuario_id == usuario.get("sub")
    ).first()
    if not productor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de productor. Créalo primero.")
    return productor


# ============ Chacras (CRUD) ============

def _obtener_chacra_propia(chacra_id: str, productor: models.Productor, db: Session) -> models.Chacra:
    """Devuelve la chacra si existe y es del productor. 404 si no existe, 403 si es de otro
    (mismo criterio que completar_cosecha con los registros de producción)."""
    chacra = db.query(models.Chacra).filter(models.Chacra.id == chacra_id).first()
    if not chacra:
        raise HTTPException(status_code=404, detail="Chacra no encontrada")
    if str(chacra.productor_id) != str(productor.id):
        raise HTTPException(status_code=403, detail="Esta chacra no te pertenece")
    return chacra


@app.post("/chacras", response_model=ChacraSalida)
def crear_chacra(
    datos: ChacraCrear,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)

    # Pre-chequeo para dar el mensaje amigable; el UniqueConstraint (productor_id, codigo)
    # en models.Chacra es el respaldo a nivel de base de datos. Mismo enfoque que crear_productor.
    duplicada = db.query(models.Chacra).filter(
        models.Chacra.productor_id == productor.id,
        models.Chacra.codigo == datos.codigo,
    ).first()
    if duplicada:
        raise HTTPException(
            status_code=400,
            detail=f"Ya tienes una chacra registrada con el código '{datos.codigo}'.",
        )

    nueva = models.Chacra(
        productor_id=productor.id,
        codigo=datos.codigo,
        nombre=datos.nombre,
        ubicacion_latitud=datos.ubicacion_latitud,
        ubicacion_longitud=datos.ubicacion_longitud,
    )
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return nueva


@app.get("/chacras/me", response_model=list[ChacraSalida])
def listar_mis_chacras(
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)
    return db.query(models.Chacra).filter(
        models.Chacra.productor_id == productor.id
    ).order_by(models.Chacra.fecha_registro.desc()).all()


@app.get("/chacras/{chacra_id}", response_model=ChacraSalida)
def obtener_chacra(
    chacra_id: str,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)
    return _obtener_chacra_propia(chacra_id, productor, db)


@app.put("/chacras/{chacra_id}", response_model=ChacraSalida)
def editar_chacra(
    chacra_id: str,
    datos: ChacraCrear,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)
    chacra = _obtener_chacra_propia(chacra_id, productor, db)

    # Si cambia el código, no debe chocar con otra chacra del mismo productor.
    if datos.codigo != chacra.codigo:
        choque = db.query(models.Chacra).filter(
            models.Chacra.productor_id == productor.id,
            models.Chacra.codigo == datos.codigo,
            models.Chacra.id != chacra.id,
        ).first()
        if choque:
            raise HTTPException(
                status_code=400,
                detail=f"Ya tienes una chacra registrada con el código '{datos.codigo}'.",
            )

    # Con cosechas ya registradas, la ubicación y el código de la chacra quedaron en la cadena de certificación (y la
    # distancia de cada cosecha se midió contra ese punto): moverla cambiaría la historia. El nombre sí se corrige.
    if (datos.ubicacion_latitud, datos.ubicacion_longitud, datos.codigo) != (chacra.ubicacion_latitud, chacra.ubicacion_longitud, chacra.codigo):
        cosechada = db.query(models.RegistroProduccion).filter(
            models.RegistroProduccion.chacra_id == chacra.id,
            models.RegistroProduccion.estado.notin_(("planificado", "anulado")),
        ).first()
        if cosechada:
            raise HTTPException(
                status_code=409,
                detail="Esta chacra ya tiene cosechas registradas: su ubicación y su código quedaron en la cadena de "
                       "certificación y no se cambian. Si la marcaste mal, registra una chacra nueva.",
            )

    chacra.codigo = datos.codigo
    chacra.nombre = datos.nombre
    chacra.ubicacion_latitud = datos.ubicacion_latitud
    chacra.ubicacion_longitud = datos.ubicacion_longitud
    db.commit()
    db.refresh(chacra)
    return chacra


# Mismo patrón que breaker_productores en productos/main.py (fail_max=3, reset_timeout=30):
# tras 3 fallos seguidos llamando a Productos, deja de intentar la conexión real durante 30s y
# falla instantáneo con CircuitBreakerError — así una caída de Productos no le cuesta hasta 5s
# de timeout a cada registro de cada GET /productores/produccion/me mientras dure la caída
# (medido: 3 registros con 3 cultivos distintos tardaban 10.2s en total antes de esto).
breaker_productos = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=30)

# Dedicado a la llamada de completar_cosecha (POST /productos/interno/crear-desde-cosecha) — NO
# comparte breaker con breaker_productos (arriba): son llamadas distintas hacia el mismo
# servicio, con causas y consecuencias de fallo propias. Que precio-referencia esté fallando no
# debería abrir el circuito de creación de productos, ni al revés.
breaker_crear_producto = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=30)


def _pedir_precio_referencia(cultivo: str):
    return httpx.get(f"{PRODUCTOS_URL}/productos/precio-referencia/{cultivo}", timeout=5)


def _pedir_crear_producto_desde_cosecha(payload: dict):
    return httpx.post(
        f"{PRODUCTOS_URL}/productos/interno/crear-desde-cosecha",
        json=payload,
        headers={"X-Servicio-Secreto": PRODUCTORES_A_PRODUCTOS_SECRETO},
        timeout=5,
    )


def _km(valor: float, formato: str) -> str:
    """Con coma decimal, como se lee en Perú: el texto lo ve el comprador en la página del QR."""
    return format(valor, formato).replace(".", ",")


def _texto_ubicacion(momento: str, distancia_km) -> str:
    if distancia_km is None:
        return f"Al {momento}: sin ubicación GPS (advertencia)."
    radio = _km(RADIO_CHACRA_KM, "g")
    if distancia_km > RADIO_CHACRA_KM:
        return f"Al {momento}: a {_km(distancia_km, '.2f')} km de la chacra, fuera del radio de {radio} km (advertencia)."
    return f"Al {momento}: a {_km(distancia_km, '.2f')} km de la chacra (dentro del radio de {radio} km)."


def _registrar_certificacion_cosecha(registro, productor, chacra, producto_id: str) -> bool:
    """Deja en la cadena de Certificación el primer evento del producto (cosecha_registrada) con la evidencia en
    origen: dónde estaba el agricultor al sembrar y al cosechar respecto de su chacra, y la huella SHA-256 de la foto
    de la cosecha (va en el texto del bloque, así que queda sellada por el hash). Ver POST
    /certificacion/{id}/cosecha-aprobada. Best-effort a propósito: para este punto el producto ya existe, y deshacer
    eso porque Certificación no respondió sería peor que dejar el registro pendiente (el endpoint es idempotente, así
    que se puede reintentar sin duplicar). Devuelve si se registró."""
    def _fecha(valor):
        return valor.strftime("%d/%m/%Y") if valor else "—"

    ubicacion = f"chacra {chacra.codigo}" + (f" ({chacra.nombre})" if chacra and chacra.nombre else "") if chacra else "chacra no especificada"
    partes = [
        f"{float(registro.cantidad_cosechada):g} {registro.unidad_medida} de {registro.cultivo} cosechados en "
        f"{ubicacion} por {productor.nombre} (sembrado el {_fecha(registro.fecha_siembra)}, "
        f"cosechado el {_fecha(registro.fecha_cosecha_real)}).",
        _texto_ubicacion("registrar la siembra", registro.siembra_distancia_km),
        _texto_ubicacion("completar la cosecha", registro.cosecha_distancia_km),
    ]
    if registro.foto_cosecha_sha256:
        partes.append(f"Foto de la cosecha (SHA-256 {registro.foto_cosecha_sha256}).")
    try:
        resp = httpx.post(
            f"{CERTIFICACION_URL}/certificacion/{producto_id}/cosecha-aprobada",
            json={"datos_cosecha": " ".join(partes), "foto_url": registro.foto_cosecha_url},
            headers={"X-Servicio-Secreto": PRODUCTORES_A_CERTIFICACION_SECRETO or ""},
            timeout=5,
        )
    except httpx.RequestError:
        logger.error("Certificación no respondió al registrar la cosecha del producto %s", producto_id, exc_info=True)
        return False
    if resp.status_code != 200:
        logger.error("Certificación rechazó registrar la cosecha del producto %s (HTTP %s)", producto_id, resp.status_code)
        return False
    return True


def _consultar_precio_referencia(cultivo: str):
    """Devuelve (precio_por_kg_o_None, motivo_de_fallo_o_None)."""
    try:
        resp = breaker_productos.call(_pedir_precio_referencia, cultivo)
    except (pybreaker.CircuitBreakerError, httpx.RequestError):
        # CircuitBreakerError = el circuito ya está abierto, ni se intentó conectar.
        # RequestError = sí se intentó (circuito cerrado/medio-abierto) y falló de verdad.
        # Mismo motivo para el usuario en ambos casos — la diferencia es interna (qué tan rápido
        # se rindió), no algo que le sirva ver a quien consulta su Gestión Económica.
        return None, "No se pudo consultar el precio de mercado (servicio de Productos no disponible)"

    if resp.status_code != 200:
        return None, "No se pudo consultar el precio de mercado"

    precio = resp.json().get("precio_promedio")
    if precio is None:
        return None, "Producto no encontrado en el catálogo (sin precio de referencia)"
    return float(precio), None


def _calcular_metricas(registro: models.RegistroProduccion, cache_precios: dict) -> dict:
    # Sin costo_envio: ese campo ya no se pide (ver CompletarCosechaDatos) y los registros viejos que lo tengan no
    # deben dar una ganancia distinta a la de uno nuevo.
    costo_total = (
        float(registro.costo_semillas or 0)
        + float(registro.costo_mano_obra or 0)
        + float(registro.costo_insumos or 0)
    )

    base = {
        "costo_total": round(costo_total, 2),
        "precio_referencia_kg": None,
        "ingreso_estimado": None,
        "ganancia_estimada": None,
        "margen_porcentaje": None,
        "motivo_sin_calculo": None,
    }

    # Aún no cosechado: no es un "fallo", simplemente no hay nada que calcular todavía.
    if registro.cantidad_cosechada is None:
        return base

    if registro.unidad_medida != "kg" and not registro.equivalencia_kg:
        base["motivo_sin_calculo"] = "Falta la equivalencia a kg para esta unidad de medida"
        return base

    clave = registro.cultivo.strip().lower()
    if clave not in cache_precios:
        cache_precios[clave] = _consultar_precio_referencia(registro.cultivo)
    precio_kg, motivo = cache_precios[clave]

    if precio_kg is None:
        base["motivo_sin_calculo"] = motivo
        return base

    cantidad_kg = (
        float(registro.cantidad_cosechada)
        if registro.unidad_medida == "kg"
        else float(registro.cantidad_cosechada) * float(registro.equivalencia_kg)
    )
    ingreso_estimado = cantidad_kg * precio_kg
    ganancia_estimada = ingreso_estimado - costo_total
    margen_porcentaje = (ganancia_estimada / ingreso_estimado * 100) if ingreso_estimado else None

    base.update({
        "precio_referencia_kg": round(precio_kg, 2),
        "ingreso_estimado": round(ingreso_estimado, 2),
        "ganancia_estimada": round(ganancia_estimada, 2),
        "margen_porcentaje": round(margen_porcentaje, 1) if margen_porcentaje is not None else None,
    })
    return base


def _motivo_ultimo_rechazo(registro: models.RegistroProduccion, db: Session) -> str | None:
    if registro.estado != "rechazado":
        return None
    ultimo = db.query(models.HistorialVerificacionCosecha).filter(
        models.HistorialVerificacionCosecha.registro_produccion_id == registro.id,
        models.HistorialVerificacionCosecha.accion == "rechazado",
    ).order_by(models.HistorialVerificacionCosecha.fecha.desc()).first()
    return ultimo.motivo if ultimo else None


def _serializar_registro(registro: models.RegistroProduccion, cache_precios: dict, db: Session) -> dict:
    return {
        "id": registro.id,
        "productor_id": registro.productor_id,
        "chacra_id": registro.chacra_id,
        "cultivo": registro.cultivo,
        "numero_parcelas": registro.numero_parcelas,
        "ubicacion_cosecha": registro.ubicacion_cosecha,
        "costo_semillas": registro.costo_semillas,
        "costo_mano_obra": registro.costo_mano_obra,
        "costo_insumos": registro.costo_insumos,
        "cantidad_cosechada": registro.cantidad_cosechada,
        "unidad_medida": registro.unidad_medida,
        "equivalencia_kg": registro.equivalencia_kg,
        "fecha_siembra": registro.fecha_siembra,
        "fecha_cosecha_estimada": registro.fecha_cosecha_estimada,
        "fecha_cosecha_real": registro.fecha_cosecha_real,
        "estado": registro.estado,
        "fecha_registro": registro.fecha_registro,
        "motivo_rechazo": _motivo_ultimo_rechazo(registro, db),
        # Evidencia en origen (ver models.RegistroProduccion): la distancia sirve para avisarle al agricultor si quedó
        # lejos de su chacra; las coordenadas no se devuelven.
        "siembra_distancia_km": registro.siembra_distancia_km,
        "cosecha_distancia_km": registro.cosecha_distancia_km,
        "radio_chacra_km": RADIO_CHACRA_KM,
        "foto_cosecha_url": registro.foto_cosecha_url,
        **_calcular_metricas(registro, cache_precios),
    }


def _validar_fecha_siembra(fecha_siembra: datetime):
    if fecha_siembra < FECHA_SIEMBRA_MINIMA or fecha_siembra > datetime.utcnow() + timedelta(days=SIEMBRA_FUTURA_MAXIMA_DIAS):
        raise HTTPException(
            status_code=422,
            detail="La fecha de siembra no es válida: debe ser posterior al año 2000 y no pasar de dos años a partir de hoy",
        )


def _validar_datos_de_siembra(datos) -> None:
    """Las reglas de una siembra, al crearla y al corregirla."""
    if datos.numero_parcelas <= 0:
        raise HTTPException(status_code=422, detail="El número de parcelas debe ser mayor a 0")
    if datos.fecha_cosecha_estimada < datos.fecha_siembra:
        raise HTTPException(status_code=422, detail="La fecha de cosecha estimada no puede ser anterior a la de siembra")
    _validar_fecha_siembra(datos.fecha_siembra)
    if datos.fecha_cosecha_estimada > datos.fecha_siembra + timedelta(days=CICLO_MAXIMO_DIAS):
        raise HTTPException(status_code=422, detail="La fecha de cosecha estimada no puede pasar de cinco años desde la siembra")
    if datos.unidad_medida not in UNIDADES_COSECHA_VALIDAS:
        raise HTTPException(
            status_code=422,
            detail="La unidad de la cosecha debe ser kg, unidad o litro. Las unidades de empaque "
                   "(saco, arroba) se agregan después, como unidad de venta alternativa del producto.",
        )
    if datos.unidad_medida != "kg" and not datos.equivalencia_kg:
        raise HTTPException(
            status_code=422,
            detail="Cuando la unidad de medida no es 'kg', debes indicar la equivalencia a kg (ej. 1 litro = X kg)",
        )


@app.post("/productores/produccion")
def crear_registro_produccion(
    datos: RegistroProduccionCrear,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)

    # La chacra debe pertenecer al productor autenticado — que exista no alcanza (podría ser
    # de otro productor). Mismo trato para "no existe" y "no es tuya": 403, sin filtrar cuál.
    chacra = db.query(models.Chacra).filter(models.Chacra.id == datos.chacra_id).first()
    if not chacra or str(chacra.productor_id) != str(productor.id):
        raise HTTPException(status_code=403, detail="La chacra especificada no pertenece a tu cuenta.")

    _validar_datos_de_siembra(datos)

    nuevo = models.RegistroProduccion(
        productor_id=productor.id,
        chacra_id=datos.chacra_id,
        cultivo=datos.cultivo,
        numero_parcelas=datos.numero_parcelas,
        ubicacion_cosecha=datos.ubicacion_cosecha,
        costo_semillas=datos.costo_semillas,
        costo_insumos=datos.costo_insumos,
        unidad_medida=datos.unidad_medida,
        equivalencia_kg=datos.equivalencia_kg,
        fecha_siembra=datos.fecha_siembra,
        fecha_cosecha_estimada=datos.fecha_cosecha_estimada,
        estado="planificado",
        siembra_latitud=datos.latitud,
        siembra_longitud=datos.longitud,
        siembra_distancia_km=_distancia_a_la_chacra(chacra, datos.latitud, datos.longitud),
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return _serializar_registro(nuevo, {}, db)


@app.get("/productores/produccion/me")
def listar_mi_produccion(
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)
    registros = db.query(models.RegistroProduccion).filter(
        models.RegistroProduccion.productor_id == productor.id,
        models.RegistroProduccion.estado != "anulado",
    ).order_by(models.RegistroProduccion.fecha_registro.desc()).all()

    # Un solo cache de precios de referencia por cultivo, compartido entre todos los registros
    # de esta respuesta — evita pedirle a Productos el mismo precio varias veces si hay más de
    # un registro con el mismo cultivo.
    cache_precios = {}
    return [_serializar_registro(r, cache_precios, db) for r in registros]


def _siembra_propia_planificada(registro_id, productor, db: Session) -> models.RegistroProduccion:
    registro = db.query(models.RegistroProduccion).filter(models.RegistroProduccion.id == registro_id).first()
    if not registro or registro.estado == "anulado":
        raise HTTPException(status_code=404, detail="Siembra no encontrada")
    if str(registro.productor_id) != str(productor.id):
        raise HTTPException(status_code=403, detail="Esta siembra no te pertenece")
    if registro.estado != "planificado":
        raise HTTPException(status_code=409, detail="Esta siembra ya se cosechó: lo que se registró quedó en la cadena de certificación y no se cambia.")
    return registro


@app.put("/productores/produccion/{registro_id}")
def corregir_siembra(
    registro_id: uuid.UUID,
    datos: RegistroProduccionEditar,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    """El productor corrige un dato mal puesto (cultivo, parcelas, fechas, costos) mientras la siembra no se cosecha."""
    productor = _obtener_productor_del_token(usuario, db)
    registro = _siembra_propia_planificada(registro_id, productor, db)
    _validar_datos_de_siembra(datos)
    for campo, valor in datos.model_dump().items():
        setattr(registro, campo, valor)
    db.commit()
    db.refresh(registro)
    return _serializar_registro(registro, {}, db)


@app.post("/productores/produccion/{registro_id}/anular")
def anular_siembra(
    registro_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    """Una siembra que no va (se registró por error, se perdió el cultivo): se anula, no se borra, para que quede su
    rastro. Deja de aparecer en la lista y ya no se puede cosechar."""
    productor = _obtener_productor_del_token(usuario, db)
    registro = _siembra_propia_planificada(registro_id, productor, db)
    registro.estado = "anulado"
    db.commit()
    return {"id": str(registro.id), "estado": "anulado"}


@app.post("/productores/produccion/sugerir-cosecha")
def sugerir_fecha_de_cosecha(
    datos: SugerirCosechaDatos,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    """Cuándo se podría cosechar lo que se está por registrar (paso "¿Cuándo crees que vas a cosechar?" del asistente
    de siembra). Es solo una sugerencia: el agricultor decide la fecha. Ver sugerencia_cosecha.py."""
    productor = _obtener_productor_del_token(usuario, db)
    chacra = _obtener_chacra_propia(str(datos.chacra_id), productor, db)
    _validar_fecha_siembra(datos.fecha_siembra)
    sugerencia = sugerencia_cosecha.sugerir(
        datos.cultivo, datos.fecha_siembra, chacra.ubicacion_latitud, chacra.ubicacion_longitud, str(productor.id),
    )
    if sugerencia is None:
        raise HTTPException(status_code=404, detail="No conocemos cuánto tarda ese cultivo: elige tú la fecha.")
    return sugerencia


# "planificado": la primera vez. "pendiente_verificacion" y "rechazado" son de antes de retirar al Verificador (ver
# models.RegistroProduccion): esas cosechas quedaron a medio camino y el agricultor las completa igual, con su foto.
ESTADOS_QUE_SE_PUEDEN_COMPLETAR = ("planificado", "pendiente_verificacion", "rechazado")


def _registro_propio_por_completar(registro_id, productor, db: Session) -> models.RegistroProduccion:
    registro = db.query(models.RegistroProduccion).filter(models.RegistroProduccion.id == registro_id).first()
    if not registro:
        raise HTTPException(status_code=404, detail="Registro de producción no encontrado")
    if str(registro.productor_id) != str(productor.id):
        raise HTTPException(status_code=403, detail="Este registro de producción no te pertenece")
    if registro.estado not in ESTADOS_QUE_SE_PUEDEN_COMPLETAR:
        raise HTTPException(status_code=409, detail="Esta cosecha ya fue completada")
    return registro


def _crear_producto_en_catalogo(registro, productor):
    """Crea en Productos el borrador de la cosecha y devuelve su id. None si ya existía: un intento anterior llegó a
    crearlo pero falló después, antes de confirmar acá (UniqueConstraint sobre registro_produccion_id en
    productos/models.py); ese 409 es el resultado esperado de un reintento, no una falla, y no trae el id. Cualquier otra
    respuesta es un 502: la cosecha no se da por completada sin un producto real detrás."""
    payload = {
        "productor_id": str(registro.productor_id),
        "productor_nombre": productor.nombre,
        "nombre": registro.cultivo,
        "unidad_medida": registro.unidad_medida,
        # RegistroProduccion.cantidad_cosechada es Numeric (admite decimales, ej. 45.5 kg);
        # Producto.stock es entero — se redondea en vez de truncar para no perder unidades
        # completas por un simple recorte de decimales (45.9 -> 46, no 45).
        "stock": round(float(registro.cantidad_cosechada)),
        "registro_produccion_id": str(registro.id),
        # Sin esto el peso de estos productos sería indeterminable en Pedidos (ver Producto.equivalencia_kg en
        # productos/models.py). float(...) porque es un Numeric (Decimal) y httpx no serializa Decimal a JSON.
        "equivalencia_kg": float(registro.equivalencia_kg) if registro.equivalencia_kg is not None else None,
    }
    error = HTTPException(
        status_code=502,
        detail="No se pudo crear el producto en el catálogo. Intenta completar la cosecha de nuevo en unos momentos.",
    )
    try:
        resp = breaker_crear_producto.call(_pedir_crear_producto_desde_cosecha, payload)
    except (pybreaker.CircuitBreakerError, httpx.RequestError):
        raise error
    if resp.status_code == 200:
        return resp.json().get("id")
    if resp.status_code == 409:
        return None
    raise error


@app.post("/productores/produccion/{registro_id}/foto-cosecha")
def subir_foto_de_cosecha(
    registro_id: uuid.UUID,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    """Primer paso de completar la cosecha: la foto, tomada en la chacra. Se sube aparte (y antes) para que el
    formulario de completar siga siendo JSON, y se puede repetir: vale la última. Su huella SHA-256 se calcula acá,
    sobre los bytes recibidos, y es la que queda sellada en la cadena de Certificación al completar."""
    if archivo.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=422, detail="Solo se permiten fotos JPEG, PNG o WEBP")
    productor = _obtener_productor_del_token(usuario, db)
    registro = _registro_propio_por_completar(registro_id, productor, db)

    # Tamaño y contenido reales (ver imagenes.py): el content-type de arriba solo lo declara el cliente.
    datos_foto, tipo_foto = leer_imagen_validada(archivo)
    try:
        url = subir_foto_cosecha(datos_foto, tipo_foto)
    except Exception:
        logger.error("No se pudo subir la foto de la cosecha %s", registro_id, exc_info=True)
        raise HTTPException(status_code=502, detail="No se pudo guardar la foto. Intenta de nuevo en unos momentos.")

    registro.foto_cosecha_url = url
    registro.foto_cosecha_sha256 = hashlib.sha256(datos_foto).hexdigest()
    registro.foto_cosecha_fecha = datetime.utcnow()
    db.commit()
    return {"foto_cosecha_url": url, "foto_cosecha_sha256": registro.foto_cosecha_sha256}


@app.patch("/productores/produccion/{registro_id}/completar-cosecha")
def completar_cosecha(
    registro_id: uuid.UUID,
    datos: CompletarCosechaDatos,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    """Cierra la cosecha y la publica como borrador en el catálogo, sin pasar por nadie: el rol Verificador se retiró
    (presentación del 2026-10-01). Lo que respalda a la cosecha es la evidencia en origen (la foto obligatoria y dónde
    estaba el agricultor, sellados en la cadena) y, después, la pesada del administrador al recibirla en el almacén."""
    productor = _obtener_productor_del_token(usuario, db)
    registro = _registro_propio_por_completar(registro_id, productor, db)

    if datos.cantidad_cosechada <= 0:
        raise HTTPException(status_code=422, detail="La cantidad cosechada debe ser mayor a 0")
    # Una cosecha no puede ser anterior a su siembra ni haber ocurrido en el futuro (margen de un día
    # por la diferencia de zona horaria con el navegador).
    if datos.fecha_cosecha_real < registro.fecha_siembra:
        raise HTTPException(status_code=422, detail="La fecha de cosecha real no puede ser anterior a la de siembra")
    if datos.fecha_cosecha_real > datetime.utcnow() + timedelta(days=COSECHA_FUTURA_TOLERANCIA_DIAS):
        raise HTTPException(status_code=422, detail="La fecha de cosecha real no puede estar en el futuro")
    if not registro.foto_cosecha_url:
        raise HTTPException(
            status_code=422, detail="Falta la foto de la cosecha: tómala desde tu chacra antes de confirmar.",
        )

    chacra = db.query(models.Chacra).filter(models.Chacra.id == registro.chacra_id).first()
    registro.cantidad_cosechada = datos.cantidad_cosechada
    registro.fecha_cosecha_real = datos.fecha_cosecha_real
    registro.costo_mano_obra = datos.costo_mano_obra
    registro.cosecha_latitud = datos.latitud
    registro.cosecha_longitud = datos.longitud
    registro.cosecha_distancia_km = _distancia_a_la_chacra(chacra, datos.latitud, datos.longitud)

    # El producto se crea ANTES de confirmar: si falla, nada de lo de arriba se guarda (la sesión se descarta) y el
    # agricultor puede reintentar; nunca queda una cosecha "cosechada" sin su producto.
    producto_id = _crear_producto_en_catalogo(registro, productor)
    registro.estado = "cosechado"
    db.commit()
    db.refresh(registro)

    resultado = _serializar_registro(registro, {}, db)
    resultado["producto_id"] = producto_id
    # Solo si conocemos el producto: en el 409 idempotente (reintento) Productos no devuelve su id.
    resultado["certificacion_registrada"] = bool(producto_id) and _registrar_certificacion_cosecha(
        registro, productor, chacra, producto_id
    )
    return resultado


@app.delete("/productores/produccion/{registro_id}")
def eliminar_registro_produccion(
    registro_id: str,
    db: Session = Depends(get_db),
    x_servicio_secreto: str = Header(None),
):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    registro = db.query(models.RegistroProduccion).filter(
        models.RegistroProduccion.id == registro_id
    ).first()
    if not registro:
        raise HTTPException(status_code=404, detail="Registro de producción no encontrado")

    db.delete(registro)
    db.commit()
    return {"mensaje": "Registro de producción eliminado"}