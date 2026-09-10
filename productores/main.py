import os
import uuid
import httpx
from datetime import datetime

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from auth import verificar_token, requiere_rol
import pybreaker

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

# ============ Chacras ============

class ChacraCrear(BaseModel):
    codigo: str
    nombre: str | None = None
    ubicacion_latitud: float
    ubicacion_longitud: float

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

class RegistroProduccionCrear(BaseModel):
    # Toda siembra se asocia a una chacra del productor (obligatorio). Se valida en el
    # endpoint que la chacra pertenezca a la cuenta autenticada, no solo que exista.
    chacra_id: uuid.UUID
    cultivo: str
    numero_parcelas: int
    ubicacion_cosecha: str | None = None
    # Solo costos "de entrada" — mano de obra y envío se completan recién al cosechar
    # (ver comentario en models.RegistroProduccion).
    costo_semillas: float = 0
    costo_insumos: float = 0
    unidad_medida: str = "kg"
    equivalencia_kg: float | None = None
    fecha_siembra: datetime
    fecha_cosecha_estimada: datetime

class CompletarCosechaDatos(BaseModel):
    cantidad_cosechada: float
    fecha_cosecha_real: datetime
    costo_mano_obra: float
    costo_envio: float

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py). Usada SOLO
# por los DELETE de limpieza QA de este servicio (eliminar_productor, eliminar_registro_
# produccion) — ningún otro servicio llama a estos endpoints.
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")

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
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
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


def _pedir_precio_referencia(cultivo: str):
    return httpx.get(f"{PRODUCTOS_URL}/productos/precio-referencia/{cultivo}", timeout=5)


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
    costo_total = (
        float(registro.costo_semillas or 0)
        + float(registro.costo_mano_obra or 0)
        + float(registro.costo_insumos or 0)
        + float(registro.costo_envio or 0)
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


def _serializar_registro(registro: models.RegistroProduccion, cache_precios: dict) -> dict:
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
        "costo_envio": registro.costo_envio,
        "cantidad_cosechada": registro.cantidad_cosechada,
        "unidad_medida": registro.unidad_medida,
        "equivalencia_kg": registro.equivalencia_kg,
        "fecha_siembra": registro.fecha_siembra,
        "fecha_cosecha_estimada": registro.fecha_cosecha_estimada,
        "fecha_cosecha_real": registro.fecha_cosecha_real,
        "estado": registro.estado,
        "fecha_registro": registro.fecha_registro,
        **_calcular_metricas(registro, cache_precios),
    }


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

    if datos.numero_parcelas <= 0:
        raise HTTPException(status_code=422, detail="El número de parcelas debe ser mayor a 0")
    if datos.fecha_cosecha_estimada < datos.fecha_siembra:
        raise HTTPException(status_code=422, detail="La fecha de cosecha estimada no puede ser anterior a la de siembra")
    if datos.unidad_medida != "kg" and not datos.equivalencia_kg:
        raise HTTPException(
            status_code=422,
            detail="Cuando la unidad de medida no es 'kg', debes indicar la equivalencia a kg (ej. 1 saco = X kg)",
        )

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
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return _serializar_registro(nuevo, {})


@app.get("/productores/produccion/me")
def listar_mi_produccion(
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)
    registros = db.query(models.RegistroProduccion).filter(
        models.RegistroProduccion.productor_id == productor.id
    ).order_by(models.RegistroProduccion.fecha_registro.desc()).all()

    # Un solo cache de precios de referencia por cultivo, compartido entre todos los registros
    # de esta respuesta — evita pedirle a Productos el mismo precio varias veces si hay más de
    # un registro con el mismo cultivo.
    cache_precios = {}
    return [_serializar_registro(r, cache_precios) for r in registros]


@app.patch("/productores/produccion/{registro_id}/completar-cosecha")
def completar_cosecha(
    registro_id: str,
    datos: CompletarCosechaDatos,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
):
    productor = _obtener_productor_del_token(usuario, db)

    registro = db.query(models.RegistroProduccion).filter(
        models.RegistroProduccion.id == registro_id
    ).first()
    if not registro:
        raise HTTPException(status_code=404, detail="Registro de producción no encontrado")
    if str(registro.productor_id) != str(productor.id):
        raise HTTPException(status_code=403, detail="Este registro de producción no te pertenece")
    if registro.estado == "cosechado":
        raise HTTPException(status_code=409, detail="Esta cosecha ya fue registrada como completa")

    if datos.cantidad_cosechada <= 0:
        raise HTTPException(status_code=422, detail="La cantidad cosechada debe ser mayor a 0")

    registro.cantidad_cosechada = datos.cantidad_cosechada
    registro.fecha_cosecha_real = datos.fecha_cosecha_real
    registro.costo_mano_obra = datos.costo_mano_obra
    registro.costo_envio = datos.costo_envio
    registro.estado = "cosechado"
    db.commit()
    db.refresh(registro)
    return _serializar_registro(registro, {})


@app.delete("/productores/produccion/{registro_id}")
def eliminar_registro_produccion(
    registro_id: str,
    db: Session = Depends(get_db),
    x_servicio_secreto: str = Header(None),
):
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    registro = db.query(models.RegistroProduccion).filter(
        models.RegistroProduccion.id == registro_id
    ).first()
    if not registro:
        raise HTTPException(status_code=404, detail="Registro de producción no encontrado")

    db.delete(registro)
    db.commit()
    return {"mensaje": "Registro de producción eliminado"}