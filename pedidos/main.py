from prometheus_fastapi_instrumentator import Instrumentator
import json
import logging
import os
import re
import math
import threading
import time
from typing import Literal
import uuid
from datetime import datetime
import httpx
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field, field_validator
from rabbitmq_publisher import publicar_evento
from auth import verificar_token
from auth import secreto_de_servicio_valido
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from reintento_eventos import lanzar_reintento_eventos_en_hilo
from auth import verificar_token, requiere_rol, security

from database import Base, engine, SessionLocal
from migraciones import agregar_columnas_faltantes
import models
import reniec
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger("pedidos")

Base.metadata.create_all(bind=engine)
agregar_columnas_faltantes(engine, Base)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

# evento de aranque
@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()
    lanzar_reintento_eventos_en_hilo()

CLAVE_ALMACEN = "almacen_ubicacion"
# Aproximación del Mercado Nery García Zárate, en Ayacucho — valor inicial hasta que el Admin lo
# cambie desde PATCH /configuracion/almacen. Sembrado una sola vez al arrancar (ver
# sembrar_configuracion_almacen); un cambio posterior por el Admin nunca se pisa con esto.
ALMACEN_LATITUD_DEFAULT = -13.1553
ALMACEN_LONGITUD_DEFAULT = -74.2287

@app.on_event("startup")
def sembrar_configuracion_almacen():
    db = SessionLocal()
    try:
        # Idempotente por existencia de la clave, no por sus valores — así un cambio hecho por el
        # Admin en un arranque anterior nunca se pisa en el siguiente restart.
        ya_existe = db.query(models.ConfiguracionSistema).filter(
            models.ConfiguracionSistema.clave == CLAVE_ALMACEN
        ).first()
        if ya_existe:
            return

        nueva = models.ConfiguracionSistema(
            clave=CLAVE_ALMACEN,
            valor_latitud=ALMACEN_LATITUD_DEFAULT,
            valor_longitud=ALMACEN_LONGITUD_DEFAULT,
        )
        db.add(nueva)
        try:
            db.commit()
        except IntegrityError:
            # Mismo caso de carrera que sembrar_admin_inicial en usuarios/main.py: con varias
            # réplicas arrancando a la vez sobre una BD recién creada, más de una puede pasar el
            # chequeo "no existe" antes de que cualquiera haga commit — el UNIQUE en clave deja
            # pasar solo a la primera, las demás ceden la siembra sin romper el arranque del pod.
            db.rollback()
    finally:
        db.close()

CLAVE_UMBRAL_PEDIDO_GRANDE = "umbral_pedido_grande_kg"
# Aproximación del peso de un saco chico — valor inicial hasta que el Admin lo cambie desde
# PATCH /configuracion/umbral-pedido-grande. Mismo criterio de siembra que el almacén: una sola
# vez al arrancar, un cambio posterior del Admin nunca se pisa.
UMBRAL_PEDIDO_GRANDE_KG_DEFAULT = 25.0

@app.on_event("startup")
def sembrar_configuracion_umbral_pedido_grande():
    db = SessionLocal()
    try:
        ya_existe = db.query(models.ConfiguracionSistema).filter(
            models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
        ).first()
        if ya_existe:
            return

        nueva = models.ConfiguracionSistema(
            clave=CLAVE_UMBRAL_PEDIDO_GRANDE,
            valor_numerico=UMBRAL_PEDIDO_GRANDE_KG_DEFAULT,
        )
        db.add(nueva)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    finally:
        db.close()

CLAVE_AGENCIA_DESPACHO = "agencia_despacho_ubicacion"
# Dónde deja el repartidor los pedidos que van por agencia de carga: las agencias (Shalom, Marvisur...) están junto al
# Terminal Terrestre Libertadores de América, en Huamanga. Coordenadas APROXIMADAS: el admin las corrige desde
# PATCH /configuracion/agencia-despacho. Misma siembra que el almacén: una sola vez, nunca pisa un cambio del admin.
AGENCIA_DESPACHO_LATITUD_DEFAULT = -13.1460
AGENCIA_DESPACHO_LONGITUD_DEFAULT = -74.2190

@app.on_event("startup")
def sembrar_agencia_de_despacho():
    db = SessionLocal()
    try:
        if db.query(models.ConfiguracionSistema).filter(models.ConfiguracionSistema.clave == CLAVE_AGENCIA_DESPACHO).first():
            return
        db.add(models.ConfiguracionSistema(clave=CLAVE_AGENCIA_DESPACHO, valor_latitud=AGENCIA_DESPACHO_LATITUD_DEFAULT,
                                           valor_longitud=AGENCIA_DESPACHO_LONGITUD_DEFAULT))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    finally:
        db.close()

# Lima, para empezar (valores DE EJEMPLO que el admin edita: el flete por kg de la agencia hay que confirmarlo con la
# agencia, y el precio por saco del camión sale del informe de rentabilidad). Solo si todavía no hay ningún destino.
DESTINO_INICIAL = {
    "ciudad": "Lima", "departamento": "Lima", "latitud": -12.0464, "longitud": -77.0428, "radio_km": 40,
    "agencia_disponible": True, "agencia_nombre": "Shalom", "agencia_flete_kg": 0.50, "agencia_dias": 2,
    "camion_disponible": True, "camion_precio_saco": 38.00, "camion_minimo_sacos": 50, "camion_capacidad_sacos": 100,
    "camion_porcentaje_salida": 80, "camion_dias": 3,
    "nota": "Valores de ejemplo: confirma el flete con la agencia y el precio del camión con el transportista.",
}

@app.on_event("startup")
def sembrar_destinos_lejanos():
    db = SessionLocal()
    try:
        if db.query(models.DestinoLejano).first():
            return
        db.add(models.DestinoLejano(**DESTINO_INICIAL))
        db.commit()
    except IntegrityError:
        db.rollback()
    finally:
        db.close()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
TRANSPORTE_URL = os.getenv("TRANSPORTE_URL", "http://localhost:8004")
PAGOS_URL = os.getenv("PAGOS_URL", "http://localhost:8007")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
PEDIDOS_A_PRODUCTOS_SECRETO = os.getenv("PEDIDOS_A_PRODUCTOS_SECRETO")  # para llamar a descontar_stock en Productos
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_pedido (DELETE de limpieza QA)
TRANSPORTE_A_PEDIDOS_SECRETO = os.getenv("TRANSPORTE_A_PEDIDOS_SECRETO")  # obtener_pedido_interno (lo llama Transporte)

# Rango aproximado del departamento de Ayacucho — el pedido solo se acepta si el destino de
# entrega cae dentro de esta caja (no es el polígono real del departamento, es un rectángulo que
# lo contiene; suficiente para la validación que se pidió, sin traer una librería de geometría).
AYACUCHO_LAT_MIN = -15.20
AYACUCHO_LAT_MAX = -12.85
AYACUCHO_LNG_MIN = -75.10
AYACUCHO_LNG_MAX = -73.00

MONTO_MINIMO_PEDIDO = 30.00  # soles, sobre el subtotal (precio × cantidad de cada item, sin envío)

# Copiada tal cual de transporte/main.py (calcular_distancia_km) — cada microservicio de este
# proyecto es autosuficiente, no hay una librería compartida entre servicios (confirmado: la
# misma fórmula también vive, con otra variante numérica equivalente, en
# certificacion/logica_cadena.py para el geofencing de cosechas).
def calcular_distancia_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    R = 6371  # radio de la Tierra en km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

# Tramos de envío por distancia entre el almacén (ConfiguracionSistema) y el destino del pedido.
# Cada tupla es (límite_km, costo) — el primer límite que la distancia no supera define el costo;
# si supera todos, se cobra COSTO_ENVIO_MAS_DE_25KM.
COSTO_ENVIO_TRAMOS = [
    (10, 5.00),
    (25, 8.00),
]
COSTO_ENVIO_MAS_DE_25KM = 12.00

def calcular_costo_envio(distancia_km: float) -> float:
    for limite_km, costo in COSTO_ENVIO_TRAMOS:
        if distancia_km <= limite_km:
            return costo
    return COSTO_ENVIO_MAS_DE_25KM

# Compras por mayor (observación de la presentación del 2026-10-01): un pedido que pasa el umbral de peso (el mismo que
# pide vehículo grande, configurable por el admin) es "por mayor", y su envío suma este recargo por cada kg de más sobre
# la tarifa por distancia: el repartidor pone una camioneta y carga más (recibe el 90% del envío, como siempre). Ej.:
# 200 kg con umbral de 25 kg = 175 kg de más = S/ 17,50 sobre la tarifa por distancia.
RECARGO_ENVIO_POR_KG_EXTRA = 0.10

def calcular_recargo_por_peso(peso_total_kg, umbral_kg: float) -> float:
    """0 si el pedido no pasa el umbral o si su peso no se conoce (mismo criterio que requiere_vehiculo_grande)."""
    if peso_total_kg is None or peso_total_kg <= umbral_kg:
        return 0.0
    return round((peso_total_kg - umbral_kg) * RECARGO_ENVIO_POR_KG_EXTRA, 2)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Validación fail-fast: el destino de entrega debe caer dentro de la región de Ayacucho. La usan
# crear_pedido y la cotización del envío, para que ambas rechacen exactamente lo mismo.
def dentro_de_ayacucho(latitud: float, longitud: float) -> bool:
    return AYACUCHO_LAT_MIN <= latitud <= AYACUCHO_LAT_MAX and AYACUCHO_LNG_MIN <= longitud <= AYACUCHO_LNG_MAX

def validar_destino_en_region(latitud: float, longitud: float):
    if not (AYACUCHO_LAT_MIN <= latitud <= AYACUCHO_LAT_MAX) or \
       not (AYACUCHO_LNG_MIN <= longitud <= AYACUCHO_LNG_MAX):
        raise HTTPException(
            status_code=400,
            detail="El destino del pedido debe estar dentro de la región de Ayacucho.",
        )

# Distancia y costo de envío desde la ubicación ACTUAL del almacén (nunca hardcodeada: un cambio del
# Admin en /configuracion/almacen afecta a los pedidos nuevos sin tocar código). Es la única
# fuente del costo: la usan crear_pedido (que lo guarda) y la cotización (que lo muestra antes de
# pagar), así que lo que ve el comprador es siempre lo que se le cobra.
def calcular_envio_a_destino(db: Session, latitud: float, longitud: float):
    almacen = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_ALMACEN
    ).first()
    if not almacen:
        # No debería pasar nunca en la práctica (sembrar_configuracion_almacen corre al arrancar
        # cada réplica), pero sin esto se devolvería/guardaría un costo de envío inventado.
        raise HTTPException(status_code=503, detail="No se pudo calcular el costo de envío: falta la configuración del almacén")

    distancia_km = calcular_distancia_km(almacen.valor_latitud, almacen.valor_longitud, latitud, longitud)
    return distancia_km, calcular_costo_envio(distancia_km)

def _peso_total(items_validados):
    """Peso total del pedido: null si CUALQUIER línea tiene peso desconocido — no se aproxima ni se ignora la línea
    faltante, porque no podemos afirmar un total si falta un dato."""
    pesos = [peso_kg for _, _, _, _, peso_kg in items_validados]
    return sum(pesos) if all(p is not None for p in pesos) else None

def _umbral_pedido_grande_kg(db: Session) -> float:
    """Mismo criterio que el almacén: el umbral se lee de ConfiguracionSistema (nunca hardcodeado), así un cambio del
    Admin en /configuracion/umbral-pedido-grande afecta a los pedidos nuevos sin tocar código."""
    umbral_config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
    ).first()
    if not umbral_config:
        # No debería pasar nunca en la práctica (sembrar_configuracion_umbral_pedido_grande corre al arrancar cada
        # réplica), pero sin esto un pedido se crearía con un criterio de vehículo grande inventado.
        raise HTTPException(status_code=503, detail="No se pudo determinar el umbral de pedido grande: falta la configuración")
    return umbral_config.valor_numerico

# ---- Compradores lejanos (decisiones del usuario, 2026-10-03) ----
KG_POR_SACO = 50
PORCENTAJE_ADELANTO_CAMION = 70  # se paga al pedir; el resto al llegar, antes de descargar
MODALIDADES_LEJANAS = ("agencia", "camion")

def destino_lejano_para(db: Session, latitud: float, longitud: float):
    """None si el punto está en Ayacucho (reparto local); si no, el destino lejano ACTIVO más cercano que lo cubre (dentro
    de su radio). Fuera de todo, 400 con la lista de ciudades a las que sí se llega."""
    if dentro_de_ayacucho(latitud, longitud):
        return None
    activos = db.query(models.DestinoLejano).filter(models.DestinoLejano.activo.is_(True)).all()
    cerca = [(calcular_distancia_km(d.latitud, d.longitud, latitud, longitud), d) for d in activos]
    cerca = [(km, d) for km, d in cerca if km <= d.radio_km]
    if not cerca:
        ciudades = ", ".join(sorted(d.ciudad for d in activos))
        raise HTTPException(
            status_code=400,
            detail=f"Por ahora entregamos en la región de Ayacucho{' y en ' + ciudades if ciudades else ''}. "
                   "Tu punto de entrega no está cerca de ninguna de esas ciudades.",
        )
    return min(cerca, key=lambda par: par[0])[1]

def _agencia_de_despacho(db: Session):
    config = db.query(models.ConfiguracionSistema).filter(models.ConfiguracionSistema.clave == CLAVE_AGENCIA_DESPACHO).first()
    if not config:
        raise HTTPException(status_code=503, detail="Falta la configuración de la agencia de despacho")
    return config

def opcion_agencia(db: Session, destino, peso_total_kg, umbral_kg) -> dict | None:
    """Por agencia: se cobra el tramo local almacén -> agencia en Huamanga (con el recargo por peso, como cualquier envío
    local); el flete de la agencia lo paga el comprador al recoger y aquí solo se estima."""
    if not destino.agencia_disponible:
        return None
    almacen = db.query(models.ConfiguracionSistema).filter(models.ConfiguracionSistema.clave == CLAVE_ALMACEN).first()
    agencia = _agencia_de_despacho(db)
    tramo_km = calcular_distancia_km(almacen.valor_latitud, almacen.valor_longitud, agencia.valor_latitud, agencia.valor_longitud)
    recargo = calcular_recargo_por_peso(peso_total_kg, umbral_kg)
    flete_kg = float(destino.agencia_flete_kg) if destino.agencia_flete_kg is not None else None
    return {
        "agencia": destino.agencia_nombre,
        "dias": destino.agencia_dias,
        "costo_envio": round(calcular_costo_envio(tramo_km) + recargo, 2),
        "recargo_envio_peso": recargo,
        "flete_kg": flete_kg,
        "flete_destino_estimado": round(peso_total_kg * flete_kg, 2) if peso_total_kg is not None and flete_kg is not None else None,
        "motivo_no_disponible": None,
    }

def opcion_camion(destino, subtotal: float, peso_total_kg) -> dict | None:
    """Camión compartido: precio por saco (de 50 kg), mínimo por comprador, y sale cuando junta el porcentaje de su
    capacidad. Se paga un adelanto al pedir y el resto al llegar."""
    if not destino.camion_disponible or destino.camion_precio_saco is None:
        return None
    precio = float(destino.camion_precio_saco)
    sacos = math.ceil(peso_total_kg / KG_POR_SACO) if peso_total_kg else None
    motivo = None
    if sacos is None:
        motivo = "Para el camión necesitamos saber el peso: hay productos de este carrito sin peso conocido."
    elif sacos < destino.camion_minimo_sacos:
        motivo = (f"El camión lleva desde {destino.camion_minimo_sacos} sacos de {KG_POR_SACO} kg por comprador "
                  f"({destino.camion_minimo_sacos * KG_POR_SACO} kg) y este pedido es de {sacos}.")
    elif sacos > destino.camion_capacidad_sacos:
        motivo = (f"Este pedido ({sacos} sacos) no entra en un solo camión (hasta {destino.camion_capacidad_sacos} sacos): "
                  "divídelo en dos pedidos o escríbenos.")
    costo = round(sacos * precio, 2) if sacos else None
    total = round(subtotal + costo, 2) if costo is not None else None
    adelanto = round(total * PORCENTAJE_ADELANTO_CAMION / 100, 2) if total is not None else None
    return {
        "sacos": sacos,
        "precio_saco": precio,
        "minimo_sacos": destino.camion_minimo_sacos,
        "capacidad_sacos": destino.camion_capacidad_sacos,
        "porcentaje_salida": destino.camion_porcentaje_salida,
        "sacos_para_salir": math.ceil(destino.camion_capacidad_sacos * destino.camion_porcentaje_salida / 100),
        "dias": destino.camion_dias,
        "costo_envio": costo,
        "porcentaje_adelanto": PORCENTAJE_ADELANTO_CAMION,
        "adelanto": adelanto,
        "saldo": round(total - adelanto, 2) if total is not None else None,
        "motivo_no_disponible": motivo,
    }

def destino_publico(destino) -> dict:
    return {
        "id": destino.id, "ciudad": destino.ciudad, "departamento": destino.departamento,
        "latitud": destino.latitud, "longitud": destino.longitud, "radio_km": destino.radio_km, "nota": destino.nota,
        "agencia": {"nombre": destino.agencia_nombre, "flete_kg": float(destino.agencia_flete_kg) if destino.agencia_flete_kg is not None else None,
                    "dias": destino.agencia_dias} if destino.agencia_disponible else None,
        "camion": {"precio_saco": float(destino.camion_precio_saco), "minimo_sacos": destino.camion_minimo_sacos,
                   "capacidad_sacos": destino.camion_capacidad_sacos, "porcentaje_salida": destino.camion_porcentaje_salida,
                   "sacos_para_salir": math.ceil(destino.camion_capacidad_sacos * destino.camion_porcentaje_salida / 100),
                   "dias": destino.camion_dias}
                  if destino.camion_disponible and destino.camion_precio_saco is not None else None,
    }

# ---- DNI de quien recibe, consultado en RENIEC (reniec.py) ----
CONSULTAS_DNI_POR_HORA = 10  # por comprador: el endpoint no debe servir para averiguar nombres de cualquier DNI
_consultas_dni = {}
_candado_dni = threading.Lock()

def _puede_consultar_dni(usuario_id: str) -> bool:
    ahora = time.monotonic()
    with _candado_dni:
        recientes = [t for t in _consultas_dni.get(usuario_id, []) if ahora - t < 3600]
        permitido = len(recientes) < CONSULTAS_DNI_POR_HORA
        _consultas_dni[usuario_id] = recientes + ([ahora] if permitido else [])
        return permitido

def nombre_en_reniec(dni: str) -> str:
    """El nombre oficial del DNI, o la HTTPException con el motivo (422 si RENIEC no lo tiene, 503 si no responde)."""
    if not (len(dni or "") == 8 and dni.isdigit()):
        raise HTTPException(status_code=422, detail="El DNI de quien recibe son 8 dígitos.")
    try:
        datos = reniec.consultar_dni(dni)
    except reniec.ReniecNoDisponible as error:
        logger.warning("No se pudo consultar el DNI en RENIEC (%s)", error)
        raise HTTPException(status_code=503, detail="No pudimos consultar RENIEC en este momento. Intenta en unos minutos.")
    if datos is None:
        raise HTTPException(status_code=422, detail=f"RENIEC no tiene registrado el DNI {dni}. Revisa que esté bien escrito.")
    return datos["nombre"]

def pedido_para(usuario: dict, pedido) -> dict:
    """El pedido como lo ve quien lo pide: el comprador ve el nombre de quien recibe ENMASCARADO (si no, crear pedidos
    serviría para averiguar el nombre de cualquier DNI); el admin y el repartidor del envío lo ven completo."""
    datos = jsonable_encoder(pedido)
    if datos.get("receptor_nombre") and usuario.get("rol") not in ("admin", "repartidor"):
        datos["receptor_nombre"] = reniec.nombre_para_confirmar(datos["receptor_nombre"])
    return datos

# Topes de un pedido. Sin límite inferior, una línea con cantidad negativa restaba dinero al total (el
# cargo se calcula sumando cantidad x precio) y "descontaba" stock al revés; con una segunda línea
# positiva para pasar el mínimo de compra, el pedido salía casi gratis y el stock del producto subía.
CANTIDAD_MAXIMA_POR_LINEA = 100_000
LINEAS_MAXIMAS_POR_PEDIDO = 50
NOMBRE_COMPRADOR_MAXIMO = 100


class ItemPedido(BaseModel):
    # UUID y no str: el valor se pega en la URL que se le pide a Productos, y un texto libre podía
    # colar rutas ("../") en esa petición.
    producto_id: uuid.UUID
    cantidad: int = Field(ge=1, le=CANTIDAD_MAXIMA_POR_LINEA)
    # None = unidad base del producto (comportamiento de siempre). Un valor (ej. "saco") busca esa
    # unidad dentro de producto["unidades_alternativas"] al validar (ver crear_pedido) — nunca se
    # acepta un precio del cliente, solo el nombre de la unidad; el precio siempre sale de
    # Productos.
    unidad: str | None = Field(default=None, max_length=30)

class PedidoCrear(BaseModel):
    comprador_nombre: str

    @field_validator("comprador_nombre")
    @classmethod
    def _nombre_valido(cls, valor: str) -> str:
        limpio = (valor or "").strip()
        if not limpio:
            raise ValueError("Indica el nombre de quien recibe el pedido")
        if "\x00" in limpio:
            raise ValueError("El nombre contiene caracteres no válidos")
        if len(limpio) > NOMBRE_COMPRADOR_MAXIMO:
            raise ValueError(f"El nombre no puede superar {NOMBRE_COMPRADOR_MAXIMO} caracteres")
        return limpio

    # Obligatorio: es el único canal para que el repartidor coordine la entrega con el
    # comprador (ver el bloque de contacto en Gestionar Envíos). Celular peruano: 9 dígitos
    # que empiezan con 9 — se normaliza (sin espacios) antes de guardarse.
    comprador_telefono: str

    @field_validator("comprador_telefono")
    @classmethod
    def _telefono_valido(cls, valor: str) -> str:
        limpio = re.sub(r"\s+", "", valor or "")
        if not re.fullmatch(r"9\d{8}", limpio):
            raise ValueError("El teléfono de contacto debe tener 9 dígitos y empezar con 9 (ej. 987654321)")
        return limpio

    destino_latitud: float
    destino_longitud: float
    items: list[ItemPedido] = Field(min_length=1, max_length=LINEAS_MAXIMAS_POR_PEDIDO)
    # Fuera de Ayacucho hay que elegir cómo llega (agencia o camión) y dar el DNI de quien recibe.
    modalidad_envio: Literal["local", "agencia", "camion"] = "local"
    receptor_dni: str | None = Field(default=None, max_length=12)

class DestinoLejanoDatos(BaseModel):
    ciudad: str = Field(min_length=2, max_length=80)
    departamento: str | None = Field(default=None, max_length=80)
    latitud: float = Field(ge=-18.5, le=0.2)    # dentro del Perú (aprox.)
    longitud: float = Field(ge=-81.5, le=-68.5)
    radio_km: float = Field(default=40, gt=0, le=200)
    activo: bool = True
    agencia_disponible: bool = True
    agencia_nombre: str | None = Field(default=None, max_length=80)
    agencia_flete_kg: float | None = Field(default=None, ge=0, le=100)
    agencia_dias: int | None = Field(default=None, ge=0, le=60)
    camion_disponible: bool = True
    camion_precio_saco: float | None = Field(default=None, ge=0, le=100000)
    camion_minimo_sacos: int = Field(default=50, ge=1, le=10000)
    camion_capacidad_sacos: int = Field(default=100, ge=1, le=10000)
    camion_porcentaje_salida: int = Field(default=80, ge=1, le=100)
    camion_dias: int | None = Field(default=None, ge=0, le=60)
    nota: str | None = Field(default=None, max_length=300)

    @field_validator("ciudad", "departamento", "agencia_nombre", "nota")
    @classmethod
    def _texto(cls, valor):
        if valor is None:
            return None
        valor = " ".join(valor.split())
        if "\x00" in valor:
            raise ValueError("contiene caracteres no válidos")
        return valor or None

class AgenciaDespachoActualizar(BaseModel):
    latitud: float
    longitud: float

class CotizacionCrear(BaseModel):
    destino_latitud: float
    destino_longitud: float
    items: list[ItemPedido] = Field(min_length=1, max_length=LINEAS_MAXIMAS_POR_PEDIDO)

class AlmacenActualizar(BaseModel):
    latitud: float
    longitud: float

class UmbralActualizar(BaseModel):
    umbral_kg: float

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "pedidos"}

# Público a propósito (sin Depends de auth): el comprador necesita poder ver de dónde sale su
# pedido antes de pagar, sin que eso exija tener sesión iniciada.
@app.get("/configuracion/almacen")
def obtener_ubicacion_almacen(db: Session = Depends(get_db)):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_ALMACEN
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del almacén")
    return {
        "latitud": config.valor_latitud,
        "longitud": config.valor_longitud,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

@app.patch("/configuracion/almacen")
def actualizar_ubicacion_almacen(
    datos: AlmacenActualizar,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("admin")),
):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_ALMACEN
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del almacén")

    config.valor_latitud = datos.latitud
    config.valor_longitud = datos.longitud
    db.commit()
    db.refresh(config)
    return {
        "latitud": config.valor_latitud,
        "longitud": config.valor_longitud,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

# Público a propósito, mismo criterio que /configuracion/almacen: el comprador (o cualquier
# integración) puede querer saber desde cuánto peso un pedido va a requerir vehículo grande, sin
# necesitar sesión iniciada.
@app.get("/configuracion/umbral-pedido-grande")
def obtener_umbral_pedido_grande(db: Session = Depends(get_db)):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del umbral de pedido grande")
    return {
        "umbral_kg": config.valor_numerico,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

@app.patch("/configuracion/umbral-pedido-grande")
def actualizar_umbral_pedido_grande(
    datos: UmbralActualizar,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("admin")),
):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del umbral de pedido grande")

    config.valor_numerico = datos.umbral_kg
    db.commit()
    db.refresh(config)
    return {
        "umbral_kg": config.valor_numerico,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

def _validar_items(client, items):
    """Valida cada línea contra Productos (existe, se puede comprar, unidad, stock) y devuelve
    [(item, producto, precio_unitario, cantidad_base, peso_kg)]. La usan crear_pedido y la cotización: el envío
    que se muestra antes de pagar sale de los mismos datos que el que se cobra."""
    items_validados = []
    for item in items:
        try:
            resp = client.get(f"{PRODUCTOS_URL}/productos/{item.producto_id}", timeout=5)
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Productos no disponible")

        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Producto {item.producto_id} no existe")

        producto = resp.json()

        # Un borrador (sin precio) o un producto que aún viaja al almacén no se compra. Esta es la
        # revisión rápida para dar un mensaje claro antes de tocar la base; la definitiva es atómica,
        # dentro de Productos, al descontar el stock (más abajo).
        if producto.get("estado", "disponible") != "disponible" or producto.get("precio") is None:
            raise HTTPException(status_code=409, detail=f"{producto.get('nombre', 'El producto')} aún no está disponible para comprar")

        if item.unidad is None:
            # Comportamiento de siempre, sin cambios: precio y cantidad ya están en la unidad
            # base del producto.
            precio_unitario = producto["precio"]
            cantidad_base = item.cantidad
        else:
            # Busca la unidad alternativa DENTRO de la respuesta fresca de Productos — nunca
            # se confía en un precio que mande el cliente (acá ni siquiera existe ese campo
            # en ItemPedido), solo en el nombre de la unidad.
            unidad_alt = next(
                (u for u in producto.get("unidades_alternativas", []) if u["unidad"] == item.unidad),
                None,
            )
            if unidad_alt is None:
                raise HTTPException(status_code=400, detail="Este producto no está disponible en esa unidad.")
            precio_unitario = unidad_alt["precio"]
            # Conversión a unidad base SOLO para validar stock y para lo que se le manda a
            # descontar_stock (que sigue recibiendo todo en unidad base, sin cambios de su
            # lado) — precio_unitario y la cantidad guardada en PedidoItem quedan en la
            # unidad que eligió el comprador.
            cantidad_base = item.cantidad * unidad_alt["factor_a_base"]

        if producto["stock"] < cantidad_base:
            raise HTTPException(status_code=409, detail=f"Stock insuficiente para {producto['nombre']}")

        # Peso real de esta línea (cantidad ya en unidad base): directo si la unidad base del
        # producto es "kg" (1 unidad base = 1 kg, no hace falta factor). Si no es "kg", se
        # convierte con producto["equivalencia_kg"] cuando el producto lo tiene (llega desde
        # RegistroProduccion.equivalencia_kg vía aprobar_cosecha — ver Producto.equivalencia_kg
        # en productos/models.py); si el producto no lo tiene (ej. uno creado a mano, sin ese
        # dato), el peso sigue siendo indeterminable.
        if producto.get("unidad_medida") == "kg":
            peso_kg = cantidad_base
        elif producto.get("equivalencia_kg") is not None:
            peso_kg = cantidad_base * producto["equivalencia_kg"]
        else:
            peso_kg = None

        items_validados.append((item, producto, precio_unitario, cantidad_base, peso_kg))
    return items_validados


@app.post("/pedidos")
def crear_pedido(datos: PedidoCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("comprador"))):
    # Validación 1 (fail-fast, antes de tocar la base de datos): el destino es la región de Ayacucho (reparto local) o
    # una de las ciudades lejanas que el admin habilitó; fuera de eso, 400.
    destino = destino_lejano_para(db, datos.destino_latitud, datos.destino_longitud)
    receptor_nombre = None
    if destino is None:
        if datos.modalidad_envio != "local":
            raise HTTPException(status_code=422, detail="Dentro de Ayacucho el pedido va con un repartidor local.")
        # El costo de envío se calcula acá (no depende de los items) — ver calcular_envio_a_destino.
        _, costo_envio = calcular_envio_a_destino(db, datos.destino_latitud, datos.destino_longitud)
    else:
        if datos.modalidad_envio not in MODALIDADES_LEJANAS:
            raise HTTPException(status_code=422, detail=f"Para {destino.ciudad} elige cómo llega tu pedido: por agencia de carga o en camión.")
        receptor_nombre = nombre_en_reniec((datos.receptor_dni or "").strip())

    with httpx.Client() as client:
        items_validados = _validar_items(client, datos.items)

        # Validación 2 (fail-fast, todavía antes de tocar la base de datos): el subtotal (precio ×
        # cantidad de cada item, sin contar envío) debe alcanzar el mínimo de compra. precio_unitario
        # y cantidad ya están en la MISMA unidad (la que eligió el comprador), así que la cuenta es
        # correcta sin importar si es la unidad base o una alternativa.
        subtotal = sum(precio_unitario * item.cantidad for item, producto, precio_unitario, cantidad_base, peso_kg in items_validados)
        if subtotal < MONTO_MINIMO_PEDIDO:
            raise HTTPException(
                status_code=400,
                detail="El monto mínimo de compra es S/30.00. Agrega más productos para continuar.",
            )

        peso_total_kg = _peso_total(items_validados)
        umbral_kg = _umbral_pedido_grande_kg(db)
        requiere_vehiculo_grande = peso_total_kg is not None and peso_total_kg > umbral_kg
        recargo_envio_peso = calcular_recargo_por_peso(peso_total_kg, umbral_kg)
        lejano = {}
        if destino is not None:
            if datos.modalidad_envio == "agencia":
                opcion = opcion_agencia(db, destino, peso_total_kg, umbral_kg)
                if opcion is None:
                    raise HTTPException(status_code=422, detail=f"A {destino.ciudad} no se envía por agencia: elige el camión.")
                costo_envio, recargo_envio_peso = opcion["costo_envio"], opcion["recargo_envio_peso"]
                lejano = {"flete_destino_estimado": opcion["flete_destino_estimado"]}
            else:
                opcion = opcion_camion(destino, subtotal, peso_total_kg)
                if opcion is None:
                    raise HTTPException(status_code=422, detail=f"A {destino.ciudad} no va el camión: elige la agencia.")
                if opcion["motivo_no_disponible"]:
                    raise HTTPException(status_code=422, detail=opcion["motivo_no_disponible"])
                costo_envio, recargo_envio_peso = opcion["costo_envio"], 0.0
                lejano = {"sacos": opcion["sacos"], "monto_adelanto": opcion["adelanto"],
                          "porcentaje_adelanto": PORCENTAJE_ADELANTO_CAMION}
            costo_envio -= recargo_envio_peso  # abajo se vuelve a sumar (igual que en el reparto local)
            lejano.update(modalidad_envio=datos.modalidad_envio, destino_lejano_id=destino.id, destino_ciudad=destino.ciudad,
                          receptor_dni=datos.receptor_dni.strip(), receptor_nombre=receptor_nombre)

        # Todo validado: ahora sí descontamos stock y creamos el pedido
        nuevo_pedido = models.Pedido(
            usuario_id=usuario.get("sub"),
            comprador_nombre=datos.comprador_nombre,
            comprador_telefono=datos.comprador_telefono,
            destino_latitud=datos.destino_latitud,
            destino_longitud=datos.destino_longitud,
            costo_envio=round(costo_envio + recargo_envio_peso, 2),
            peso_total_kg=peso_total_kg,
            requiere_vehiculo_grande=requiere_vehiculo_grande,
            recargo_envio_peso=recargo_envio_peso,
            **lejano,
        )
        db.add(nuevo_pedido)
        db.flush()  # genera el id del pedido sin cerrar la transacción todavía

        # Todas las líneas se descuentan juntas, en una sola llamada: si una no alcanza (otro comprador se
        # llevó el stock justo antes, dos líneas del mismo producto que juntas superan el stock, un
        # producto que ya no está disponible) Productos responde 409 y no descuenta nada. Antes se llamaba
        # línea por línea sin mirar la respuesta y el pedido se creaba igual.
        try:
            resp_stock = client.post(
                f"{PRODUCTOS_URL}/productos/interno/descontar-stock-lote",
                json={"lineas": [
                    {"producto_id": str(item.producto_id), "cantidad": cantidad_base}
                    for item, _, _, cantidad_base, _ in items_validados
                ]},
                headers={"X-Servicio-Secreto": PEDIDOS_A_PRODUCTOS_SECRETO or ""},
                timeout=5,
            )
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Productos no disponible")
        if resp_stock.status_code != 200:
            try:
                detalle = resp_stock.json().get("detail")
            except ValueError:
                detalle = None
            if resp_stock.status_code in (404, 409) and isinstance(detalle, str):
                raise HTTPException(status_code=resp_stock.status_code, detail=detalle)
            raise HTTPException(status_code=503, detail="No se pudo reservar el stock de los productos")

        filas_con_base = []  # (fila del pedido, cantidad en unidad base) para el evento de más abajo
        for item, producto, precio_unitario, cantidad_base, peso_kg in items_validados:
            nuevo_item = models.PedidoItem(
                pedido_id=nuevo_pedido.id,
                producto_id=item.producto_id,
                cantidad=item.cantidad,
                unidad=item.unidad,
                precio_unitario=precio_unitario,
                peso_kg=peso_kg,
            )
            db.add(nuevo_item)
            filas_con_base.append((nuevo_item, cantidad_base))

        # El evento va a la bandeja de salida en la MISMA transacción que el pedido (ver models.EventoSaliente).
        saliente = models.EventoSaliente(tipo="pedido_creado", cuerpo=json.dumps(_evento_pedido_creado(nuevo_pedido, usuario, filas_con_base)))
        db.add(saliente)
        try:
            db.commit()
        except Exception:
            # El stock ya salió de Productos pero el pedido no quedó guardado: se devuelve (compensación de la saga).
            db.rollback()
            logger.error("No se pudo guardar el pedido después de descontar el stock: se devuelve", exc_info=True)
            _devolver_stock(client, [(item.producto_id, cantidad_base) for item, _, _, cantidad_base, _ in items_validados])
            raise HTTPException(status_code=503, detail="No se pudo registrar tu pedido y no se te cobró nada. Intenta de nuevo en unos momentos.")

    _publicar_saliente(db, saliente)
    # Después de publicar: marcar el evento enviado hace commit y vence los atributos del pedido ya cargados.
    db.refresh(nuevo_pedido)
    nuevo_pedido.items  # fuerza la carga de la relación: sin esto la respuesta sale sin "items"
    return pedido_para(usuario, nuevo_pedido)


def _evento_pedido_creado(nuevo_pedido, usuario: dict, filas_con_base) -> dict:
    return {
        "evento": "pedido_creado",
        "pedido_id": str(nuevo_pedido.id),
        "usuario_id": str(usuario.get("sub")),
        "comprador_nombre": nuevo_pedido.comprador_nombre,
        "costo_envio": float(nuevo_pedido.costo_envio),
        # Pagos cobra el adelanto en vez del total cuando el pedido va en camión (el resto al llegar).
        "modalidad_envio": nuevo_pedido.modalidad_envio or "local",
        "monto_adelanto": float(nuevo_pedido.monto_adelanto) if nuevo_pedido.monto_adelanto is not None else None,
        # cantidad_base es lo que se descontó del stock (ya convertido a la unidad base del producto: 3 sacos de
        # 10 kg = 30) mientras que cantidad es lo que compró el comprador. Pagos lo necesita para devolver o
        # volver a reservar exactamente ese stock si el pago se rechaza o se reintenta.
        "items": [
            {
                "producto_id": str(fila.producto_id),
                "cantidad": fila.cantidad,
                "unidad": fila.unidad,
                "cantidad_base": cantidad_base,
                "precio_unitario": float(fila.precio_unitario),
            }
            for fila, cantidad_base in filas_con_base
        ],
    }


def _devolver_stock(client, lineas) -> None:
    """Compensación de la saga del pedido: devuelve a Productos lo que se descontó para un pedido que no se pudo guardar.
    Si tampoco se puede devolver, queda en el log para corregirlo a mano (con las cantidades exactas)."""
    try:
        resp = client.post(
            f"{PRODUCTOS_URL}/productos/interno/reponer-stock-lote",
            json={"lineas": [{"producto_id": str(producto_id), "cantidad": cantidad} for producto_id, cantidad in lineas]},
            headers={"X-Servicio-Secreto": PEDIDOS_A_PRODUCTOS_SECRETO or ""},
            timeout=5,
        )
    except httpx.RequestError:
        logger.error("Productos no respondió al devolver el stock de un pedido que no se guardó. REVISAR A MANO: %s", lineas, exc_info=True)
        return
    if resp.status_code != 200:
        logger.error("Productos no devolvió el stock (HTTP %s). REVISAR A MANO: %s", resp.status_code, lineas)


def _publicar_saliente(db: Session, saliente) -> None:
    """Publica un evento de la bandeja de salida y lo marca enviado. Si RabbitMQ no responde, el pedido ya está creado
    igual: el evento sigue en la bandeja y reintento_eventos.py lo reenvía."""
    try:
        publicar_evento(json.loads(saliente.cuerpo))
    except Exception:
        logger.warning("RabbitMQ no respondió al publicar %s: queda en la bandeja de salida", saliente.tipo, exc_info=True)
        return
    saliente.fecha_envio = datetime.utcnow()
    saliente.intentos = (saliente.intentos or 0) + 1
    db.commit()

# Público a propósito (mismo criterio que /configuracion/almacen): el comprador ve cuánto le va a
# costar el envío ANTES de pagar, sin crear todavía el pedido. DEBE declararse antes de
# /pedidos/{pedido_id}: si no, esa ruta con parámetro se comería "costo-envio" como si fuera un id.
@app.get("/pedidos/costo-envio")
def cotizar_costo_envio(latitud: float, longitud: float, db: Session = Depends(get_db)):
    validar_destino_en_region(latitud, longitud)
    distancia_km, costo_envio = calcular_envio_a_destino(db, latitud, longitud)
    return {"costo_envio": costo_envio, "distancia_km": distancia_km}


# Recojo de una cosecha en la chacra (tramo chacra -> almacén; decisión del usuario 2026-10-03): la misma tarifa que el
# envío a un comprador (por distancia al almacén + recargo por los kg sobre el umbral), así el agricultor paga lo mismo
# que pagaría un comprador por mover ese peso esa distancia. Lo pide Transporte al cotizar y al crear el recojo; es
# público como /pedidos/costo-envio porque es solo una calculadora.
@app.get("/pedidos/costo-recojo")
def cotizar_costo_recojo(latitud: float, longitud: float, peso_kg: float = Query(gt=0, le=100000), db: Session = Depends(get_db)):
    if not dentro_de_ayacucho(latitud, longitud):
        raise HTTPException(status_code=422, detail="El recojo es solo dentro de la región de Ayacucho: revisa la ubicación de tu chacra.")
    distancia_km, tarifa = calcular_envio_a_destino(db, latitud, longitud)
    recargo = calcular_recargo_por_peso(peso_kg, _umbral_pedido_grande_kg(db))
    return {"distancia_km": distancia_km, "peso_kg": peso_kg, "tarifa_distancia": tarifa, "recargo_peso": recargo,
            "total": round(tarifa + recargo, 2)}


# El envío EXACTO de este carrito a este destino, antes de pagar: misma validación de productos y mismas fórmulas que
# crear_pedido (no crea nada ni reserva stock), así que lo que ve el comprador es lo que se le cobra. Dice además si la
# compra es por mayor (pasa el umbral de peso) y cuánto suma el recargo por los kg de más.
@app.post("/pedidos/cotizar")
def cotizar_pedido(datos: CotizacionCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("comprador"))):
    destino = destino_lejano_para(db, datos.destino_latitud, datos.destino_longitud)
    with httpx.Client() as client:
        items_validados = _validar_items(client, datos.items)
    peso_total_kg = _peso_total(items_validados)
    umbral_kg = _umbral_pedido_grande_kg(db)
    subtotal = round(sum(precio * item.cantidad for item, _, precio, _, _ in items_validados), 2)
    if destino is not None:
        # Fuera de Ayacucho: las dos formas de llegar, con todo lo que el comprador necesita saber para elegir.
        return {
            "tipo": "lejano",
            "destino": destino_publico(destino),
            "subtotal": subtotal,
            "peso_total_kg": peso_total_kg,
            "kg_por_saco": KG_POR_SACO,
            "opciones": {
                "agencia": opcion_agencia(db, destino, peso_total_kg, umbral_kg),
                "camion": opcion_camion(destino, subtotal, peso_total_kg),
            },
        }
    distancia_km, costo_base = calcular_envio_a_destino(db, datos.destino_latitud, datos.destino_longitud)
    recargo = calcular_recargo_por_peso(peso_total_kg, umbral_kg)
    return {
        "tipo": "local",
        "distancia_km": distancia_km,
        "subtotal": round(sum(precio * item.cantidad for item, _, precio, _, _ in items_validados), 2),
        "peso_total_kg": peso_total_kg,
        "umbral_kg": umbral_kg,
        "por_mayor": peso_total_kg is not None and peso_total_kg > umbral_kg,
        "kg_extra": round(max(0.0, peso_total_kg - umbral_kg), 3) if peso_total_kg is not None else 0.0,
        "recargo_por_kg": RECARGO_ENVIO_POR_KG_EXTRA,
        "costo_envio_base": costo_base,
        "recargo_envio_peso": recargo,
        "costo_envio": round(costo_base + recargo, 2),
    }


# Lo que el comprador necesita saber ANTES de armar su pedido: el monto mínimo de compra (el carrito no deja seguir hasta
# llegar, en vez de que se entere al pulsar "Confirmar y pagar") y desde cuántos kg la compra es por mayor y cuánto suma
# el envío por cada kg de más. Pública, y declarada antes de /pedidos/{pedido_id} por la misma razón que
# /pedidos/costo-envio. Las reglas reales se siguen aplicando al crear el pedido.
@app.get("/pedidos/condiciones")
def condiciones_de_compra(db: Session = Depends(get_db)):
    return {
        "monto_minimo": float(MONTO_MINIMO_PEDIDO),
        "umbral_por_mayor_kg": _umbral_pedido_grande_kg(db),
        "recargo_envio_por_kg": RECARGO_ENVIO_POR_KG_EXTRA,
        "kg_por_saco": KG_POR_SACO,
        "porcentaje_adelanto_camion": PORCENTAJE_ADELANTO_CAMION,
    }


# ---- Ciudades lejanas: la lista que ve el comprador y que edita el admin ----
@app.get("/destinos-lejanos")
def listar_destinos_lejanos(db: Session = Depends(get_db)):
    activos = db.query(models.DestinoLejano).filter(models.DestinoLejano.activo.is_(True)).order_by(models.DestinoLejano.ciudad).all()
    return [destino_publico(d) for d in activos]


@app.get("/destinos-lejanos/admin")
def listar_destinos_lejanos_admin(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    return db.query(models.DestinoLejano).order_by(models.DestinoLejano.ciudad).all()


def _validar_destino(datos: DestinoLejanoDatos):
    if dentro_de_ayacucho(datos.latitud, datos.longitud):
        raise HTTPException(status_code=422, detail="Esa ciudad está en Ayacucho: ahí ya se reparte con repartidores locales.")
    if datos.camion_capacidad_sacos < datos.camion_minimo_sacos:
        raise HTTPException(status_code=422, detail="La capacidad del camión no puede ser menor que el mínimo por comprador.")
    if datos.camion_disponible and datos.camion_precio_saco is None:
        raise HTTPException(status_code=422, detail="Pon el precio por saco del camión, o desactiva el camión para esta ciudad.")


@app.post("/destinos-lejanos")
def crear_destino_lejano(datos: DestinoLejanoDatos, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    _validar_destino(datos)
    destino = models.DestinoLejano(**datos.model_dump())
    db.add(destino)
    db.commit()
    db.refresh(destino)
    return destino


@app.put("/destinos-lejanos/{destino_id}")
def actualizar_destino_lejano(destino_id: uuid.UUID, datos: DestinoLejanoDatos, db: Session = Depends(get_db),
                              usuario: dict = Depends(requiere_rol("admin"))):
    destino = db.query(models.DestinoLejano).filter(models.DestinoLejano.id == destino_id).first()
    if not destino:
        raise HTTPException(status_code=404, detail="Destino no encontrado")
    _validar_destino(datos)
    for campo, valor in datos.model_dump().items():
        setattr(destino, campo, valor)
    db.commit()
    db.refresh(destino)
    return destino


@app.get("/configuracion/agencia-despacho")
def obtener_agencia_despacho(db: Session = Depends(get_db)):
    config = _agencia_de_despacho(db)
    return {"latitud": config.valor_latitud, "longitud": config.valor_longitud, "fecha_actualizacion": config.fecha_actualizacion}


@app.patch("/configuracion/agencia-despacho")
def actualizar_agencia_despacho(datos: AgenciaDespachoActualizar, db: Session = Depends(get_db),
                                usuario: dict = Depends(requiere_rol("admin"))):
    if not dentro_de_ayacucho(datos.latitud, datos.longitud):
        raise HTTPException(status_code=422, detail="La agencia de despacho tiene que estar en Ayacucho (donde el repartidor deja los pedidos).")
    config = _agencia_de_despacho(db)
    config.valor_latitud, config.valor_longitud = datos.latitud, datos.longitud
    db.commit()
    return {"latitud": config.valor_latitud, "longitud": config.valor_longitud}


# El comprador confirma que el DNI de quien recibe es el correcto: se le muestra el nombre ENMASCARADO (reniec.py).
@app.get("/pedidos/dni/{dni}")
def consultar_dni_de_quien_recibe(dni: str, usuario: dict = Depends(requiere_rol("comprador"))):
    if not _puede_consultar_dni(str(usuario.get("sub"))):
        raise HTTPException(status_code=429, detail="Hiciste muchas consultas de DNI seguidas. Intenta en una hora.")
    nombre = nombre_en_reniec(dni.strip())
    return {"dni": dni.strip(), "nombre_para_confirmar": reniec.nombre_para_confirmar(nombre)}


def _pedido_o_404(db: Session, pedido_id: uuid.UUID):
    pedido = db.query(models.Pedido).filter(models.Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    pedido.items  # fuerza la carga de la relación para que se incluya en la respuesta
    return pedido


# Ruta INTERNA para Transporte (proponer envíos, liquidar al repartidor, certificar la entrega y comprobar
# de quién es un pedido antes de mostrar un envío): esos procesos no tienen a ningún usuario detrás, así que
# se protege con el secreto de servicio TRANSPORTE_A_PEDIDOS_SECRETO. Va antes de /pedidos/{pedido_id}.
@app.get("/pedidos/interno/{pedido_id}")
def obtener_pedido_interno(
    pedido_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_servicio_secreto: str = Header(None),
):
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_PEDIDOS_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")
    return _pedido_o_404(db, pedido_id)


# Lo que un repartidor necesita para decidir si acepta un envío que se le PROPONE: cuánto hay que recorrer, cuánto
# pesa y cuánto se paga. Sin nombre, teléfono ni coordenadas del comprador: el pedido completo solo se ve una vez
# aceptado el envío (ver más abajo). Ruta interna, con el secreto de Transporte, que la pide para armar la lista de
# propuestas. distancia_km es la del almacén al destino (la misma con la que se calculó la tarifa).
@app.get("/pedidos/interno/{pedido_id}/resumen-entrega")
def resumen_de_entrega_interno(
    pedido_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_servicio_secreto: str = Header(None),
):
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_PEDIDOS_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")
    pedido = _pedido_o_404(db, pedido_id)
    distancia_km, _ = calcular_envio_a_destino(db, pedido.destino_latitud, pedido.destino_longitud)
    return {
        "distancia_km": distancia_km,
        "peso_total_kg": float(pedido.peso_total_kg) if pedido.peso_total_kg is not None else None,
        "costo_envio": float(pedido.costo_envio),
        "requiere_vehiculo_grande": bool(pedido.requiere_vehiculo_grande),
        # Fuera de Ayacucho: por agencia (el repartidor lo deja en la agencia) o en camión (viaje compartido).
        "modalidad_envio": pedido.modalidad_envio or "local",
        "destino_ciudad": pedido.destino_ciudad,
        "sacos": pedido.sacos,
    }


# El repartidor solo ve el pedido (nombre, teléfono y dirección del comprador) cuando ya aceptó el envío: una
# propuesta que todavía no aceptó, o que rechazó, no cuenta.
# "en_destino": el camión de un viaje compartido ya llegó a la ciudad y el transportista entrega pedido por pedido.
ESTADOS_DE_ENVIO_CON_ACCESO_AL_PEDIDO = ("asignado", "en_camino", "en_destino", "entregado")


def _repartidor_tiene_el_envio(pedido_id: uuid.UUID, token: str) -> bool:
    """¿El repartidor que pide el pedido tiene asignado su envío? Se le pregunta a Transporte con el token del
    propio repartidor: GET /envios/{pedido_id} solo responde 200 al repartidor asignado (además del comprador
    dueño y el admin), y ese 200 trae el estado del envío."""
    try:
        with httpx.Client() as client:
            resp = client.get(
                f"{TRANSPORTE_URL}/envios/{pedido_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Servicio de Transporte no disponible")
    return resp.status_code == 200 and resp.json().get("estado") in ESTADOS_DE_ENVIO_CON_ACCESO_AL_PEDIDO


# Quién puede ver un pedido: el comprador que lo hizo, el admin y el repartidor que tiene asignado su envío.
# Antes era público (bastaba conocer el id para leer el nombre, el teléfono y las coordenadas exactas del
# comprador) y, tras exigir sesión, lo leía cualquier repartidor o productor con solo conocer el id. Un
# productor no lo necesita para nada (ve sus ventas en otro lado), así que no entra. pedido_id va tipado
# como UUID: un id mal formado da 422 en vez de un error de la base.
# Para el admin: los pedidos pagados que todavía no se entregaron (ahí ve los que se quedaron sin repartidor y puede
# cancelarlos devolviendo el dinero). Antes de /pedidos/{pedido_id}, que se comería "admin" como id.
@app.get("/pedidos/admin/sin-entregar")
def pedidos_sin_entregar(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    pedidos = db.query(models.Pedido).filter(models.Pedido.estado == "confirmado").order_by(models.Pedido.fecha_creacion).all()
    for pedido in pedidos:
        pedido.items  # carga la relación para la respuesta
    return [pedido_para(usuario, p) for p in pedidos]


class CancelacionPedido(BaseModel):
    motivo: str = Field(min_length=3, max_length=300)


def _detalle_de(resp) -> str:
    try:
        detalle = resp.json().get("detail")
    except ValueError:
        detalle = None
    return detalle if isinstance(detalle, str) else f"HTTP {resp.status_code}"


@app.post("/pedidos/{pedido_id}/cancelar")
def cancelar_pedido_pagado(pedido_id: uuid.UUID, datos: CancelacionPedido, db: Session = Depends(get_db),
                           usuario: dict = Depends(requiere_rol("admin")),
                           credenciales: HTTPAuthorizationCredentials = Depends(security)):
    """Saga para cancelar un pedido ya pagado (la pide el admin; cada paso va con su token):
    1) Transporte dice si el envío todavía se puede cancelar (no salió). Si no, no se toca nada.
    2) Pagos devuelve el dinero (Culqi; lo pagado por transferencia queda para devolver a mano), anula las ganancias que
       no se pagaron y devuelve el stock. Si falla, no se toca nada.
    3) Transporte cancela el envío y libera al repartidor.
    4) El pedido queda cancelado con su motivo (el aviso al comprador lo publica Pagos al devolver).
    Si el paso 3 fallara (el envío salió justo entre medio) el dinero ya está devuelto: queda en el log para resolverlo a mano."""
    pedido = db.query(models.Pedido).filter(models.Pedido.id == pedido_id).with_for_update().first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    if pedido.estado == "cancelado" and pedido.motivo_cancelacion:
        return pedido_para(usuario, pedido)  # ya se canceló antes: repetir no hace nada
    if pedido.estado != "confirmado":
        raise HTTPException(status_code=409, detail="Solo se cancela así un pedido pagado que todavía no se entregó.")
    motivo = " ".join(datos.motivo.split())
    cabecera = {"Authorization": f"Bearer {credenciales.credentials}"}
    try:
        with httpx.Client() as client:
            revision = client.post(f"{TRANSPORTE_URL}/envios/pedido/{pedido_id}/cancelar", params={"solo_revisar": "true"},
                                   headers=cabecera, timeout=10)
            if revision.status_code == 409:
                raise HTTPException(status_code=409, detail=_detalle_de(revision))
            if revision.status_code != 200:
                raise HTTPException(status_code=503, detail="Transporte no respondió: no se canceló nada.")
            devolucion = client.post(f"{PAGOS_URL}/pagos/admin/{pedido_id}/devolver", json={"motivo": motivo},
                                     headers=cabecera, timeout=40)
            if devolucion.status_code != 200:
                raise HTTPException(status_code=devolucion.status_code if devolucion.status_code in (409, 502) else 503,
                                    detail=_detalle_de(devolucion))
            envio = client.post(f"{TRANSPORTE_URL}/envios/pedido/{pedido_id}/cancelar", headers=cabecera, timeout=10)
            if envio.status_code != 200:
                logger.error("Pedido %s: el dinero ya se devolvió pero Transporte no canceló el envío (%s). REVISAR A MANO.",
                             pedido_id, _detalle_de(envio))
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Un servicio no respondió: no se canceló nada.")
    pedido.estado = "cancelado"
    pedido.motivo_cancelacion = motivo
    pedido.fecha_cancelacion = datetime.utcnow()
    db.commit()
    db.refresh(pedido)
    pedido.items
    respuesta = pedido_para(usuario, pedido)
    respuesta["devolucion"] = devolucion.json()
    return respuesta


@app.get("/pedidos/{pedido_id}")
def obtener_pedido(
    pedido_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: dict = Depends(verificar_token),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    pedido = _pedido_o_404(db, pedido_id)
    rol = usuario.get("rol")
    es_el_comprador = str(pedido.usuario_id) == str(usuario.get("sub"))
    if es_el_comprador or rol == "admin":
        return pedido_para(usuario, pedido)
    if rol == "repartidor" and _repartidor_tiene_el_envio(pedido_id, credenciales.credentials):
        return pedido_para(usuario, pedido)
    raise HTTPException(status_code=403, detail="No tienes permiso para ver este pedido")

@app.delete("/pedidos/{pedido_id}")
def eliminar_pedido(pedido_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    pedido = db.query(models.Pedido).filter(models.Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    db.query(models.PedidoItem).filter(models.PedidoItem.pedido_id == pedido_id).delete()
    db.delete(pedido)
    db.commit()
    return {"mensaje": "Pedido eliminado"}