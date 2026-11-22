from comun.logging import configurar_logging

configurar_logging()  # lo primero: lo que se registre al importar lo demás ya sale con hora, nivel y nombre del logger

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi.encoders import jsonable_encoder
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator
import os
import sys
import re
import json
import math
import uuid
from datetime import datetime, timedelta
import threading
import time
import httpx
import logging

from comun.database import Base, engine, SessionLocal, en_su_sesion_sin_lanzar, get_db
import models
from comun.auth import verificar_token, requiere_rol, security
from comun.auth import secreto_de_servicio_valido
from fastapi.security import HTTPAuthorizationCredentials
import culqi_client
from culqi_client import crear_cargo, crear_devolucion, devuelto_del_cargo, obtener_cargo, obtener_devolucion
from rabbitmq_consumer import crear_pago_si_falta, lanzar_consumidor_en_hilo, PLAZO_PARA_PAGAR_MINUTOS
import plazos
from eventos import EXCHANGE as EXCHANGE_DE_EVENTOS, registrar_evento
from comun import bandeja_salida
from logica_liquidaciones import crear_liquidaciones, crear_liquidacion_repartidor, crear_liquidacion_recojo, pagos_sin_liquidaciones
from logica_liquidaciones import COMISION_PLATAFORMA_PORCENTAJE
import logica_cobros
from reintento_liquidaciones import lanzar_reintento_liquidaciones_en_hilo
from comun.migraciones import agregar_columnas_faltantes
from migraciones import asegurar_indice_unico_de_pagos, asegurar_indices_de_cargos
from comun.imagenes import leer_imagen_validada, LIMITE_CUERPO_BYTES, MENSAJE_MUY_GRANDE
from minio_client import subir_constancia, leer_constancia
from fastapi.responses import JSONResponse

logger = logging.getLogger("pagos.main")

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# "Mis ganancias": quién es el agricultor (Productores) o el repartidor (Transporte) con sesión.
PRODUCTORES_URL = os.getenv("PRODUCTORES_URL", "http://localhost:8001")
TRANSPORTE_URL = os.getenv("TRANSPORTE_URL", "http://localhost:8004")
PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
PAGOS_A_PRODUCTOS_SECRETO = os.getenv("PAGOS_A_PRODUCTOS_SECRETO") or sys.exit("Falta la variable de entorno PAGOS_A_PRODUCTOS_SECRETO: el servicio no arranca sin ella.")  # para llamar a reponer_stock en Productos
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO") or sys.exit("Falta la variable de entorno QA_LIMPIEZA_SECRETO: el servicio no arranca sin ella.")  # eliminar_pago (DELETE de limpieza QA)
# Pagos -> Pedidos: GET /pedidos/interno/{id}/para-cobrar, para crear el pago al cobrar si pedido_creado no llegó.
PAGOS_A_PEDIDOS_SECRETO = os.getenv("PAGOS_A_PEDIDOS_SECRETO") or sys.exit("Falta la variable de entorno PAGOS_A_PEDIDOS_SECRETO: el servicio no arranca sin ella.")
TRANSPORTE_A_PAGOS_SECRETO = os.getenv("TRANSPORTE_A_PAGOS_SECRETO") or sys.exit("Falta la variable de entorno TRANSPORTE_A_PAGOS_SECRETO: el servicio no arranca sin ella.")  # para /pagos/interno/liquidar-repartidor

# Cuenta a la que el comprador transfiere. En el clúster local y en docker-compose van los datos de una cuenta de
# EJEMPLO (lo dice el propio nombre del banco); en un despliegue real, los de la cuenta del negocio. Si falta alguno,
# la transferencia no se ofrece (GET /pagos/condiciones la devuelve en null y POST /pagos/transferencia responde 503).
TRANSFERENCIA_DATOS = {
    "banco": os.getenv("TRANSFERENCIA_BANCO"),
    "titular": os.getenv("TRANSFERENCIA_TITULAR"),
    "cuenta": os.getenv("TRANSFERENCIA_CUENTA"),
    "cci": os.getenv("TRANSFERENCIA_CCI"),
}


def _datos_transferencia():
    return dict(TRANSFERENCIA_DATOS) if all(TRANSFERENCIA_DATOS.values()) else None


Base.metadata.create_all(bind=engine)
# create_all() no agrega columnas a una tabla que ya existe (p. ej. la constancia de la transferencia en `pagos`).
agregar_columnas_faltantes(engine, Base)
asegurar_indice_unico_de_pagos(engine)
asegurar_indices_de_cargos(engine)  # un cargo de Culqi nunca se asigna a dos pagos
bandeja_salida.asegurar_esquema(engine, EXCHANGE_DE_EVENTOS)  # los índices de la bandeja de salida

app = FastAPI()

Instrumentator().instrument(app).expose(app)


@app.middleware("http")
async def limitar_el_tamano_de_las_constancias(request, call_next):
    """Rechaza por su Content-Length una constancia demasiado grande ANTES de leer el cuerpo (mismo criterio que
    productos/main.py). Va antes del middleware de CORS (queda por dentro) para que el 413 lleve las cabeceras CORS."""
    if request.method == "POST" and request.url.path.endswith("/pagos/transferencia"):
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

@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()
    lanzar_reintento_liquidaciones_en_hilo()
    threading.Thread(target=_bucle_vencimientos, daemon=True).start()
    threading.Thread(target=_bucle_conciliacion, daemon=True, name="conciliacion-culqi").start()
    bandeja_salida.lanzar_relevo_en_hilo()  # el único que publica: lo que dejó registrar_evento, en orden

def _cantidades_base_por_producto(items_json) -> dict:
    """Cuánto stock (en la unidad base de cada producto) reserva un pedido: la suma de sus líneas por producto.
    Los pedidos nuevos traen cantidad_base (lo que Pedidos descontó, ya convertido: 3 sacos de 10 kg = 30); los
    anteriores solo traen cantidad, que coincide con la base cuando se compró en la unidad base."""
    if not items_json:
        return {}
    totales = {}
    for item in json.loads(items_json):
        cantidad = item.get("cantidad_base", item.get("cantidad"))
        totales[item["producto_id"]] = totales.get(item["producto_id"], 0) + float(cantidad)
    return totales


# Respuestas de Productos que dicen que la devolución NO se aplicó y se puede reintentar sin sumar dos veces.
HTTP_REINTENTABLES = {502, 503, 504}


def _reponer_en_productos(client, producto_id, cantidad: int) -> tuple[str, str | None]:
    """Le pide a Productos sumar `cantidad` al stock. Devuelve (estado, error):
    - "devuelta": Productos la sumó.
    - "pendiente": seguro NO llegó (no se pudo conectar, o 502/503/504): se puede reintentar.
    - "dudosa": pudo haber llegado (se cortó esperando la respuesta, u otra respuesta): no se reintenta, porque
      /stock/reponer suma cada vez que se lo llama."""
    try:
        resp = client.post(
            f"{PRODUCTOS_URL}/productos/{producto_id}/stock/reponer",
            json={"cantidad": cantidad},
            headers={"X-Servicio-Secreto": PAGOS_A_PRODUCTOS_SECRETO},
            timeout=5,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        return "pendiente", f"{type(e).__name__}: {e}"[:300]
    except httpx.RequestError as e:
        return "dudosa", f"{type(e).__name__}: {e}"[:300]
    if resp.status_code == 200:
        return "devuelta", None
    return ("pendiente" if resp.status_code in HTTP_REINTENTABLES else "dudosa"), f"HTTP {resp.status_code}"


def compensar_stock(items_json: str, pedido_id=None) -> dict:
    """Devuelve a Productos el stock reservado por un pedido, en la MISMA unidad y con el mismo redondeo con que
    se descontó (suma por producto, redondeada hacia arriba). Antes se devolvía la cantidad en la unidad que
    eligió el comprador: 3 sacos de 10 kg descontaban 30 kg y devolvían 3.

    Lo que no entra a la primera queda anotado en devoluciones_stock (ver models.DevolucionStock) y el ciclo de
    vencimientos lo reintenta: antes solo quedaba un print y el stock se perdía (pedido de maíz del 2026-10-17).
    Devuelve {producto_id: "devuelta" | "pendiente" | "dudosa"}."""
    cantidades = _cantidades_base_por_producto(items_json)
    if not cantidades:
        print("[Pagos] No hay items guardados para este pago, no se puede compensar stock")
        return {}

    resultado, por_anotar = {}, []
    with httpx.Client() as client:
        for producto_id, cantidad in cantidades.items():
            reponer = math.ceil(cantidad)
            estado, error = _reponer_en_productos(client, producto_id, reponer)
            resultado[producto_id] = estado
            if estado == "devuelta":
                print(f"[Pagos] Stock repuesto: {reponer} de {producto_id}")
                continue
            logger.error("No se pudo devolver %s de %s a Productos (%s): queda %s%s", reponer, producto_id, error, estado,
                         ", se reintenta" if estado == "pendiente" else "; revisar a mano")
            por_anotar.append(models.DevolucionStock(pedido_id=pedido_id, producto_id=str(producto_id), cantidad=reponer,
                                                     estado=estado, intentos=1, ultimo_error=error))
    if por_anotar:
        db = SessionLocal()
        try:
            db.add_all(por_anotar)
            db.commit()
        except Exception:
            db.rollback()
            logger.error("No se pudieron anotar %d devoluciones de stock: %s", len(por_anotar),
                         [(d.producto_id, d.cantidad) for d in por_anotar], exc_info=True)
        finally:
            db.close()
    return resultado


def reintentar_devoluciones_pendientes(db: Session) -> int:
    """Reintenta las devoluciones de stock "pendiente" (las que seguro no llegaron). Una por una y con la fila bloqueada
    (SKIP LOCKED: con varias réplicas, cada devolución la intenta una sola). Devuelve cuántas entraron."""
    # Mientras su cobro está "procesando", el stock de ese pedido no se repone: el cobro puede haber salido.
    en_proceso = db.query(models.Pago.pedido_id).filter(models.Pago.estado == "procesando")
    ids = [d.id for d in db.query(models.DevolucionStock.id).filter(models.DevolucionStock.estado == "pendiente",
                                                                    ~models.DevolucionStock.pedido_id.in_(en_proceso)).all()]
    devueltas = 0
    with httpx.Client() as client:
        for devolucion_id in ids:
            fila = (db.query(models.DevolucionStock)
                    .filter(models.DevolucionStock.id == devolucion_id, models.DevolucionStock.estado == "pendiente")
                    .with_for_update(skip_locked=True).first())
            if fila is None:
                db.rollback()
                continue
            estado, error = _reponer_en_productos(client, fila.producto_id, fila.cantidad)
            fila.estado, fila.ultimo_error, fila.intentos = estado, error, (fila.intentos or 0) + 1
            if estado == "devuelta":
                fila.fecha_devuelta = datetime.utcnow()
                devueltas += 1
                logger.info("Devolución de stock reintentada: %s de %s (pedido %s)", fila.cantidad, fila.producto_id, fila.pedido_id)
            elif estado == "dudosa":
                logger.error("La devolución de %s de %s (pedido %s) quedó dudosa (%s): revisar a mano",
                             fila.cantidad, fila.producto_id, fila.pedido_id, error)
            db.commit()
    return devueltas


def reservar_stock(items_json: str):
    """Vuelve a descontar el stock de un pedido cuyo pago se rechazó (al rechazarse se devolvió) justo antes de
    reintentar el cobro: sin esto, un reintento que sale bien cobra un pedido cuyo stock ya se devolvió y quizá se
    vendió a otro. Todo o nada, en una sola llamada a Productos (que exige que los productos sigan disponibles)."""
    cantidades = _cantidades_base_por_producto(items_json)
    if not cantidades:
        raise HTTPException(
            status_code=409,
            detail="Este pedido no guarda el detalle de sus productos, así que no se puede reintentar el pago. Crea un nuevo pedido.",
        )
    try:
        with httpx.Client() as client:
            resp = client.post(
                f"{PRODUCTOS_URL}/productos/interno/descontar-stock-lote",
                json={"lineas": [{"producto_id": producto_id, "cantidad": cantidad} for producto_id, cantidad in cantidades.items()]},
                headers={"X-Servicio-Secreto": PAGOS_A_PRODUCTOS_SECRETO},
                timeout=5,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Servicio de Productos no disponible")
    if resp.status_code == 200:
        return
    try:
        detalle = resp.json().get("detail")
    except ValueError:
        detalle = None
    if resp.status_code in (404, 409) and isinstance(detalle, str):
        raise HTTPException(status_code=409, detail=f"No se puede reintentar el pago: {detalle.rstrip('.')}. Crea un nuevo pedido.")
    raise HTTPException(status_code=503, detail="No se pudo reservar el stock de los productos")

# Yape permite hasta S/ 500 por operación (y por día entre S/ 500 y S/ 2,000, según el banco del cliente). Un pago del
# checkout es una sola operación: un total mayor no se puede pagar con Yape (observación de la presentación del
# 2026-10-01: "¿qué pasa si no pueden pagar por Yape?" → tarjeta o transferencia bancaria).
YAPE_MAXIMO_POR_OPERACION = Decimal("500.00")


class ProcesarPago(BaseModel):
    pedido_id: uuid.UUID
    token_culqi: str
    email: str
    metodo: Literal["tarjeta", "yape"] = "tarjeta"  # el que eligió en el checkout (Culqi abre solo ese)

class LiquidarRepartidor(BaseModel):
    pedido_id: str
    repartidor_id: str
    costo_envio: float


class LiquidarRecojo(BaseModel):
    recojo_id: uuid.UUID
    # None (H8): lo llevó un transporte que contrató el admin; al conductor se le paga por fuera de la app.
    repartidor_id: uuid.UUID | None = None
    productor_id: uuid.UUID
    producto_id: uuid.UUID | None = None
    costo: float = Field(gt=0, le=100000)

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "pagos"}

# Público y antes de /pagos/{pedido_id} (si no, "condiciones" se tomaría por un id). Lo que el frontend muestra al
# comprador y al repartidor sobre el dinero, desde la misma constante con que se calculan las liquidaciones: así la
# pantalla no puede decir un porcentaje distinto del que se cobra. Observación de la presentación del 2026-10-01:
# "¿quién le paga al repartidor?" — el envío que paga el comprador es para el repartidor, menos esta comisión.
@app.get("/pagos/condiciones")
def condiciones_de_pago():
    return {
        "comision_plataforma_porcentaje": float(COMISION_PLATAFORMA_PORCENTAJE * 100),
        "yape_maximo_por_operacion": float(YAPE_MAXIMO_POR_OPERACION),
        "transferencia": _datos_transferencia(),  # null si no hay una cuenta configurada
        # Cuánto se guarda un pedido sin pagar (la hora exacta es la de su pago: vence_en) y cuánto si se paga por transferencia.
        "plazo_para_pagar_minutos": PLAZO_PARA_PAGAR_MINUTOS,
        "plazo_transferencia_minutos": plazos.PLAZO_TRANSFERENCIA_MINUTOS,
    }


@app.get("/pagos/sin-liquidaciones")
def listar_pagos_sin_liquidaciones(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    """Pagos "aprobado" que todavía no tienen ninguna Liquidacion — normalmente el job de fondo
    (reintento_liquidaciones.py) los resuelve solo cada INTERVALO_SEGUNDOS, pero el Admin puede
    querer verlo en cualquier momento (ej. si Productos lleva caído más que ese intervalo). Sin
    endpoint de reintento manual: el hilo ya lo cubre, no hace falta uno aparte.

    Registrada ANTES de /pagos/{pedido_id} a propósito: FastAPI resuelve rutas en el orden en que
    se registran, así que si "sin-liquidaciones" quedara después, un GET acá lo capturaría esa
    ruta con pedido_id="sin-liquidaciones" en vez de esta (mismo criterio que /productos/mios
    antes de /productos/{producto_id} en Productos)."""
    return pagos_sin_liquidaciones(db)

def _exigir_que_el_pedido_es_del_usuario(pedido_id, usuario: dict, token: str, accion: str = "ver este pago"):
    """Solo el comprador dueño del pedido (y el admin, que ni llega acá) puede ver su pago. Se le pregunta
    a Pedidos con el mismo token del usuario: si responde 200 se compara el usuario_id del pedido con el del
    token (un repartidor o un productor también reciben 200 ahí, pero no son el comprador)."""
    try:
        with httpx.Client() as client:
            resp = client.get(
                f"{PEDIDOS_URL}/pedidos/{pedido_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Servicio de Pedidos no disponible")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    if resp.status_code != 200 or str(resp.json().get("usuario_id")) != str(usuario.get("sub")):
        raise HTTPException(status_code=403, detail=f"No tienes permiso para {accion}")


def _exigir_dueno_para_pagar(pedido_id, usuario: dict, token: str):
    """Solo el comprador dueño del pedido lo paga (ni siquiera el admin). Antes cualquier usuario con sesión que
    conociera el id podía cobrar con su tarjeta o, con una tarjeta mala, dejar rechazado el pedido de otro."""
    _exigir_que_el_pedido_es_del_usuario(pedido_id, usuario, token, accion="pagar este pedido")


# Antes era público: bastaba conocer el id de un pedido para leer el monto y el cargo de Culqi de su pago.
@app.get("/pagos/{pedido_id}")
def obtener_pago(
    pedido_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: dict = Depends(verificar_token),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    if usuario.get("rol") != "admin":
        _exigir_que_el_pedido_es_del_usuario(pedido_id, usuario, credenciales.credentials)
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    datos = jsonable_encoder(pago)
    parte = parte_a_cobrar(pago)
    # Lo que el comprador tiene que pagar ahora (nada si ya está todo pagado, devuelto o cancelado).
    pagado_o_devuelto = pago.estado in ("aprobado", "reembolsado", "cancelado")
    datos["parte_a_pagar"] = None if pagado_o_devuelto else parte
    datos["monto_a_pagar"] = None if pagado_o_devuelto else float(monto_de(pago, parte))
    return datos

@app.delete("/pagos/{pago_id}")
def eliminar_pago(pago_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    pago = db.query(models.Pago).filter(models.Pago.id == pago_id).first()
    if not pago:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    db.delete(pago)
    db.commit()
    return {"mensaje": "Pago eliminado"}

@app.post("/pagos/interno/liquidar-repartidor")
def liquidar_repartidor(
        datos: LiquidarRepartidor,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None),
):
    """Llamado por Transporte cuando un Envio pasa a "entregado" (y por su propio job de
    reintento si la primera llamada falló) — mismo espíritu que crear_liquidaciones, pero para el
    repartidor: acá el monto ya viene resuelto (costo_envio), no hace falta ninguna llamada
    cruzada para calcularlo. Ver el docstring de crear_liquidacion_repartidor sobre por qué esto
    responde 200 incluso ante un duplicado (idempotente vía UniqueConstraint, no un 409)."""
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_PAGOS_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    crear_liquidacion_repartidor(db, datos.pedido_id, datos.repartidor_id, datos.costo_envio)
    return {"mensaje": "Liquidación de repartidor registrada"}


@app.post("/pagos/interno/liquidar-recojo")
def liquidar_recojo(datos: LiquidarRecojo, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    """Transporte, cuando el admin ya pesó en el almacén una cosecha que recogió un repartidor en la chacra: se le
    liquida el recojo al repartidor y se le anota al productor para descontarlo de sus ventas (idempotente)."""
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_PAGOS_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")
    crear_liquidacion_recojo(db, datos.recojo_id, datos.repartidor_id, datos.productor_id, datos.producto_id, datos.costo)
    return {"mensaje": "Recojo liquidado"}

def parte_a_cobrar(pago) -> str:
    """"total" (un solo cobro, como siempre), "adelanto" (pedido en camión sin nada pagado) o "saldo" (en camión, con el
    adelanto ya pagado). Mientras una transferencia está en revisión, la parte es la que se mandó a revisar."""
    if pago.estado == "en_revision" and pago.parte_en_revision:
        return pago.parte_en_revision
    if pago.monto_adelanto is None:
        return "total"
    return "saldo" if pago.estado == "adelanto_pagado" else "adelanto"


def _dinero(valor) -> str:
    """Un monto en un evento: texto con 2 decimales (Notificaciones lo pone en el aviso al comprador)."""
    return f"{Decimal(str(valor)):.2f}"


def monto_de(pago, parte: str) -> Decimal:
    total = Decimal(str(pago.monto))
    if parte == "adelanto":
        return Decimal(str(pago.monto_adelanto))
    if parte == "saldo":
        return total - Decimal(str(pago.monto_adelanto))
    return total


# ---- Pedidos que nadie paga: al vencer el plazo el stock vuelve al catálogo (último paso de la saga del pedido) ----
INTERVALO_VENCIMIENTOS_SEGUNDOS = 60
# Sin minutos fijos: el plazo de cada pago depende de cómo se pagaba (ver plazos.py).
MOTIVO_VENCIDO = "Venció el plazo para pagar y los productos volvieron al catálogo"


def vencer_pagos_sin_pagar(db: Session, ahora=None) -> int:
    """Los pagos "pendiente" cuyo plazo venció pasan a "rechazado" (como un pago rechazado: el comprador puede volver a
    pagar si el stock sigue ahí, ver reservar_stock), se devuelve su stock y se avisa (pago_rechazado: Pedidos lo cancela y
    Notificaciones le escribe al comprador). SKIP LOCKED: un cobro en curso tiene la fila bloqueada (procesar_pago y la
    transferencia la toman con FOR UPDATE), así que nunca se vence un pedido mientras se está pagando. Una transferencia
    en revisión no está "pendiente" y no vence."""
    ahora = ahora or datetime.utcnow()
    vencidos = (
        db.query(models.Pago)
        .filter(models.Pago.estado == "pendiente", models.Pago.vence_en.isnot(None), models.Pago.vence_en < ahora)
        .with_for_update(skip_locked=True)
        .all()
    )
    for pago in vencidos:
        pago.estado = "rechazado"
        pago.motivo_rechazo = MOTIVO_VENCIDO
        pago.vencido_en = ahora
        registrar_evento(db, "pago_rechazado", {"pedido_id": str(pago.pedido_id), "motivo": MOTIVO_VENCIDO, "vencido": True})
    db.commit()
    sin_devolver = 0
    for pago in vencidos:
        resultado = compensar_stock(pago.items, pedido_id=pago.pedido_id) or {}
        sin_devolver += sum(1 for estado in resultado.values() if estado != "devuelta")
    if vencidos and sin_devolver:
        logger.warning("Vencieron %d pedido(s) sin pagar: %d devolución(es) de stock quedaron anotadas para reintentar",
                       len(vencidos), sin_devolver)
    elif vencidos:
        logger.info("Vencieron %d pedido(s) sin pagar: su stock volvió al catálogo", len(vencidos))
    return len(vencidos)


def una_pasada_de_vencimientos() -> None:
    """Un ciclo del hilo, cada parte en su sesión: primero lo que no se pudo devolver antes (p. ej. Productos aún
    arrancaba), después los nuevos vencidos. Nunca lanza, aunque falle el rollback o el cierre: el hilo no debe morir, y
    un fallo de la primera parte no impide la segunda."""
    en_su_sesion_sin_lanzar(reintentar_devoluciones_pendientes, "El reintento de devoluciones de stock", logger)
    en_su_sesion_sin_lanzar(vencer_pagos_sin_pagar, "El ciclo de vencimiento de pagos", logger)


def _bucle_vencimientos():
    while True:
        try:
            una_pasada_de_vencimientos()
        except Exception:  # una_pasada_de_vencimientos no lanza; esto es por si algo cambia
            logger.exception("El ciclo de vencimientos falló")
        time.sleep(INTERVALO_VENCIMIENTOS_SEGUNDOS)


def _aplicar_parte_pagada(db: Session, pago, parte: str, metodo: str, cargo_id=None) -> None:
    """Lo que cambia en el pago cuando se cobra una parte (por Culqi o una transferencia que aprobó un admin), con su
    aviso en la bandeja de salida, dentro de la transacción de `db` (no confirma):
    - total: aprobado y pago_confirmado (Pedidos lo confirma y Transporte crea el envío);
    - adelanto: el pedido se confirma y sube al camión (pago_confirmado con adelanto=True); las liquidaciones esperan al
      saldo, porque el pedido todavía no está pagado entero;
    - saldo: aprobado y saldo_pagado (el chofer ya puede descargar)."""
    ahora = datetime.utcnow()
    pago.parte_en_revision = None
    if parte == "adelanto":
        pago.estado = "adelanto_pagado"
        pago.metodo = metodo
        pago.fecha_adelanto = ahora
        pago.culqi_charge_id = cargo_id or pago.culqi_charge_id
        registrar_evento(db, "pago_confirmado", {"pedido_id": str(pago.pedido_id), "adelanto": True,
                                                 "monto": _dinero(monto_de(pago, "adelanto")),
                                                 "saldo": _dinero(monto_de(pago, "saldo"))})
    elif parte == "saldo":
        pago.estado = "aprobado"
        pago.metodo_saldo = metodo
        pago.culqi_charge_id_saldo = cargo_id
        pago.fecha_saldo = ahora
        registrar_evento(db, "saldo_pagado", {"pedido_id": str(pago.pedido_id), "monto": _dinero(monto_de(pago, "saldo"))})
    else:
        pago.estado = "aprobado"
        pago.metodo = metodo
        pago.culqi_charge_id = cargo_id or pago.culqi_charge_id
        registrar_evento(db, "pago_confirmado", {"pedido_id": str(pago.pedido_id), "monto": _dinero(pago.monto)})


def _registrar_parte_pagada(db: Session, pago, parte: str, metodo: str, cargo_id=None) -> None:
    """Una parte cobrada sin Culqi de por medio (la transferencia que aprobó un admin): se guarda con su aviso y, si con
    eso el pedido quedó pagado entero, se crean sus liquidaciones."""
    _aplicar_parte_pagada(db, pago, parte, metodo, cargo_id)
    db.commit()
    if parte != "adelanto":
        _crear_liquidaciones_sin_romper(db, pago)


def _crear_liquidaciones_sin_romper(db: Session, pago) -> None:
    try:
        crear_liquidaciones(db, pago)
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "No se pudieron crear las liquidaciones del pedido %s (el pago ya quedó aprobado — revisar manualmente)",
            pago.pedido_id,
            exc_info=True,
        )


# ---- Cobro en dos fases: nada queda abierto mientras se llama a Culqi ----
# 1) Un commit corto: el pago pasa a "procesando" con un UPDATE condicional (solo desde el estado desde el que se cobra
#    esa parte; si no afecta filas, 409) y queda el intento con su referencia propia (models.IntentoPago). Reemplaza al
#    bloqueo de la fila durante la llamada: de dos intentos a la vez, el segundo recibe 409 y no llega a Culqi.
# 2) Culqi, sin transacción ni bloqueo abiertos, con la referencia en la metadata y en la descripción.
# 3) Un commit con el resultado, el estado del pago y su aviso en la bandeja de salida.
# Si el paso 3 no llega (falla el commit, muere el proceso, Culqi no contesta), el pago queda "procesando" y nunca vuelve
# solo a "pendiente": conciliar_intentos_en_curso averigua en Culqi qué pasó con esa referencia y lo resuelve.
DESDE_DONDE_SE_COBRA = {"total": ("pendiente", "rechazado"), "adelanto": ("pendiente", "rechazado"),
                        "saldo": ("adelanto_pagado",)}
DESCRIPCION_DE_LA_PARTE = {"total": "", "adelanto": " (adelanto)", "saldo": " (saldo)"}
MENSAJE_VERIFICANDO = ("No pudimos confirmar el pago con la pasarela y lo estamos verificando: no vuelvas a pagar. "
                       "Revisa Mis pedidos en unos minutos.")


MENSAJE_CANCELADO_POR_EL_COMPRADOR = "Cancelaste este pedido: ya no se puede pagar. Arma uno nuevo desde el catálogo."


def _exigir_que_se_puede_cobrar(pago) -> None:
    if pago.estado == "aprobado":
        raise HTTPException(status_code=409, detail="Este pedido ya fue pagado")
    if pago.estado == "reembolsado":
        raise HTTPException(status_code=409, detail="Este pedido se canceló y se te devolvió el dinero: ya no se puede pagar.")
    if pago.estado == "cancelado":
        raise HTTPException(status_code=409, detail=MENSAJE_CANCELADO_POR_EL_COMPRADOR)
    if pago.estado == "en_revision":
        raise HTTPException(status_code=409, detail="Ya enviaste la constancia de una transferencia para este pedido: está en revisión.")
    if pago.estado == "procesando":
        raise HTTPException(status_code=409, detail="Ya hay un pago en proceso para este pedido: espera unos minutos y revisa "
                                                    "Mis pedidos antes de volver a intentarlo.")
    if pago.estado == "reembolso_procesando":
        raise HTTPException(status_code=409, detail="Este pedido se está cancelando y devolviendo: ya no se puede pagar.")


def _pago_o_crearlo(db: Session, pedido_id) -> "models.Pago":
    """El pago del pedido. Si todavía no existe (pedido_creado no llegó: RabbitMQ caído o atrasado), se crea con lo que
    dice Pedidos, consultado con la credencial de Pagos: el monto lo calcula Pedidos, nunca el cliente. Es el mismo
    crear_pago_si_falta del consumidor, así que crearlo dos veces no puede (el índice único de pagos.pedido_id)."""
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
    if pago:
        return pago
    db.rollback()  # ninguna transacción abierta mientras se espera a Pedidos
    try:
        resp = httpx.get(f"{PEDIDOS_URL}/pedidos/interno/{pedido_id}/para-cobrar",
                         headers={"X-Servicio-Secreto": PAGOS_A_PEDIDOS_SECRETO}, timeout=5)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Servicio de Pedidos no disponible")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    if resp.status_code == 409:
        raise HTTPException(status_code=409, detail="Este pedido ya no se puede pagar.")
    if resp.status_code != 200:
        raise HTTPException(status_code=503, detail="Servicio de Pedidos no disponible")
    crear_pago_si_falta(resp.json())
    db.rollback()  # lo que hubiera leído esta sesión, fuera: el pago lo creó otra
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
    if not pago:
        raise HTTPException(status_code=503, detail="No se pudo preparar el pago del pedido. Intenta de nuevo en unos momentos.")
    logger.info("Pedido %s: su pago se creó al cobrar (pedido_creado todavía no había llegado)", pedido_id)
    return pago


def _empezar_cobro(db: Session, pago, parte: str, monto: Decimal, metodo: str) -> dict | None:
    """Fase 1: devuelve lo necesario para seguir sin tocar la base, o None si el pago ya no estaba en un estado desde el
    que se cobra esa parte (otro intento lo tomó, o cambió)."""
    referencia, ahora = uuid.uuid4(), datetime.utcnow()
    pago_id, pedido_id, items = pago.id, pago.pedido_id, pago.items
    cambiados = (db.query(models.Pago)
                 .filter(models.Pago.id == pago_id, models.Pago.estado.in_(DESDE_DONDE_SE_COBRA[parte]))
                 .update({models.Pago.estado_antes_de_procesar: models.Pago.estado, models.Pago.estado: "procesando",
                          models.Pago.parte_en_proceso: parte, models.Pago.procesando_desde: ahora},
                         synchronize_session=False))
    if not cambiados:
        db.rollback()
        return None
    anterior = db.query(models.Pago.estado_antes_de_procesar).filter(models.Pago.id == pago_id).scalar()
    db.add(models.IntentoPago(id=referencia, pago_id=pago_id, pedido_id=pedido_id, tipo="cobro", parte=parte, monto=monto,
                              metodo=metodo, estado="en_curso", estado_anterior=anterior, fecha_inicio=ahora))
    try:
        db.commit()
    except IntegrityError:  # otro intento en curso: el UPDATE condicional ya lo impide, el índice lo sostiene
        db.rollback()
        return None
    return {"referencia": str(referencia), "pago_id": pago_id, "pedido_id": pedido_id, "parte": parte, "monto": monto,
            "metodo": metodo, "estado_anterior": anterior, "items": items}


def _anotar_resolucion(intento, resolucion: dict | None) -> None:
    """La resolución a mano de un admin (panel "Cobros por revisar en Culqi"): quién, por qué y cuándo."""
    if resolucion:
        intento.resuelto_por = resolucion["por"]
        intento.motivo_resolucion = resolucion["motivo"]
        intento.fecha_resolucion = datetime.utcnow()


def _cerrar_cobro(db: Session, referencia, aprobado: bool, cargo_id=None, mensaje=None, resolucion: dict | None = None):
    """Fase 3, y también la de la conciliación y la del admin: un solo commit con el resultado del intento, el estado del
    pago y su aviso. Solo si el intento sigue en curso: si otro ya lo resolvió no se repite nada (ni el aviso). Devuelve
    (pago, parte, items) o None. Si el cargo ya está asignado a otro pago, el commit lanza IntegrityError."""
    intento = (db.query(models.IntentoPago)
               .filter(models.IntentoPago.id == referencia, models.IntentoPago.estado == "en_curso")
               .with_for_update().first())
    if intento is None:
        db.rollback()
        return None
    pago = db.query(models.Pago).filter(models.Pago.id == intento.pago_id).with_for_update().first()
    if pago.estado != "procesando":
        logger.error("Pedido %s: el intento %s seguía en curso con el pago en '%s'; se cierra igual", pago.pedido_id,
                     referencia, pago.estado)
    intento.estado = "aprobado" if aprobado else "rechazado"
    intento.cargo_id = cargo_id or intento.cargo_id
    intento.mensaje = mensaje
    intento.fecha_fin = datetime.utcnow()
    _anotar_resolucion(intento, resolucion)
    parte = intento.parte
    if aprobado:
        _aplicar_parte_pagada(db, pago, parte, intento.metodo, cargo_id)
    elif parte == "saldo":
        pago.estado = intento.estado_anterior or "adelanto_pagado"  # ya va en el camión: solo vuelve a esperar el saldo
    else:
        pago.estado = "rechazado"
        registrar_evento(db, "pago_rechazado", {"pedido_id": str(pago.pedido_id), "motivo": mensaje})
    pago.parte_en_proceso = pago.estado_antes_de_procesar = pago.procesando_desde = None
    items = pago.items
    db.commit()
    return pago, parte, items


def _despues_del_cobro(db: Session, cerrado, aprobado: bool) -> None:
    """Lo que sigue a un cobro ya guardado: las liquidaciones de un pedido pagado entero, o devolver el stock de un
    cobro rechazado (un saldo rechazado no toca el stock: el pedido ya va en el camión)."""
    if cerrado is None:
        return
    pago, parte, items = cerrado
    if aprobado and parte != "adelanto":
        _crear_liquidaciones_sin_romper(db, pago)
    elif not aprobado and parte != "saldo":
        compensar_stock(items, pedido_id=pago.pedido_id)


def _anular_intento(db: Session, referencia, mensaje: str) -> None:
    """Un intento que no llegó a Culqi (no se pudo volver a reservar el stock): el pago vuelve a como estaba, sin aviso."""
    intento = (db.query(models.IntentoPago)
               .filter(models.IntentoPago.id == referencia, models.IntentoPago.estado == "en_curso")
               .with_for_update().first())
    if intento is None:
        db.rollback()
        return
    pago = db.query(models.Pago).filter(models.Pago.id == intento.pago_id).with_for_update().first()
    intento.estado, intento.mensaje, intento.fecha_fin = "rechazado", mensaje, datetime.utcnow()
    pago.estado = intento.estado_anterior
    pago.parte_en_proceso = pago.estado_antes_de_procesar = pago.procesando_desde = None
    db.commit()


@app.post("/pagos/procesar")
def procesar_pago(
    datos: ProcesarPago,
    db: Session = Depends(get_db),
    usuario: dict = Depends(verificar_token),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    _exigir_dueno_para_pagar(datos.pedido_id, usuario, credenciales.credentials)
    pago = _pago_o_crearlo(db, datos.pedido_id)
    _exigir_que_se_puede_cobrar(pago)
    parte = parte_a_cobrar(pago)
    monto = monto_de(pago, parte)
    # Antes de reservar stock o llamar a Culqi: un Yape por encima del límite lo rechazaría la pasarela igual.
    if datos.metodo == "yape" and monto > YAPE_MAXIMO_POR_OPERACION:
        raise HTTPException(
            status_code=422,
            detail=f"Yape permite pagar hasta S/ {YAPE_MAXIMO_POR_OPERACION:.2f} por operación y este pago es de "
                   f"S/ {monto:.2f}. Paga con tarjeta.",
        )

    intento = _empezar_cobro(db, pago, parte, monto, datos.metodo)
    if intento is None:
        db.rollback()
        _exigir_que_se_puede_cobrar(db.query(models.Pago).filter(models.Pago.pedido_id == datos.pedido_id).first())
        raise HTTPException(status_code=409, detail="El pago de este pedido cambió mientras lo procesábamos: revisa Mis pedidos.")
    # Desde aquí no hay transacción ni bloqueo abiertos: lo de la base ya está confirmado (pago "procesando" + intento).
    referencia = intento["referencia"]

    # Un pago "rechazado" ya devolvió su stock (compensar_stock). Reintentarlo lo vuelve a reservar ANTES de cobrar: si ya
    # no alcanza, 409 y no se cobra nada. Si el reintento vuelve a fallar, se libera solo lo reservado para este intento.
    if intento["estado_anterior"] == "rechazado":
        try:
            reservar_stock(intento["items"])
        except HTTPException:
            _anular_intento(db, referencia, "No se pudo volver a reservar el stock: no se cobró")
            raise

    try:
        resultado = crear_cargo(
            token=datos.token_culqi,
            monto_soles=float(monto),
            email=datos.email,
            descripcion=f"Pedido {intento['pedido_id']}{DESCRIPCION_DE_LA_PARTE[parte]} ref {referencia[:8]}",
            metadata={"referencia": referencia, "pedido_id": intento["pedido_id"], "parte": parte},
        )
    except (httpx.RequestError, ValueError):
        # Puede que Culqi haya cobrado y se perdiera la respuesta: queda "procesando" y la conciliación lo resuelve.
        logger.error("Pedido %s: Culqi no contestó el cobro %s; queda procesando para la conciliación",
                     intento["pedido_id"], referencia, exc_info=True)
        raise HTTPException(status_code=502, detail=MENSAJE_VERIFICANDO)

    estado_http, cuerpo = resultado["status_code"], resultado["data"]
    if estado_http != 201 and not 400 <= estado_http < 500:
        logger.error("Pedido %s: Culqi respondió %s al cobro %s; queda procesando para la conciliación",
                     intento["pedido_id"], estado_http, referencia)
        raise HTTPException(status_code=502, detail=MENSAJE_VERIFICANDO)
    aprobado = estado_http == 201
    try:
        cerrado = _cerrar_cobro(db, referencia, aprobado, cargo_id=cuerpo.get("id") if aprobado else None,
                                mensaje=None if aprobado else cuerpo.get("user_message"))
    except Exception:
        # El cobro salió (o se rechazó) pero no se pudo guardar: queda "procesando" y la conciliación lo resuelve.
        db.rollback()
        logger.error("Pedido %s: no se pudo guardar el resultado del cobro %s; queda procesando", intento["pedido_id"],
                     referencia, exc_info=True)
        raise HTTPException(status_code=502, detail=MENSAJE_VERIFICANDO)
    _despues_del_cobro(db, cerrado, aprobado)
    if aprobado:
        return {"estado": "adelanto_pagado" if parte == "adelanto" else "aprobado", "parte": parte, "detalle": cuerpo}
    raise HTTPException(status_code=402, detail=cuerpo.get("user_message") or
                        ("El pago del saldo fue rechazado" if parte == "saldo" else "El pago fue rechazado"))


# ---- El plazo para pagar: elegir transferencia o abrir Culqi lo alarga (reglas en plazos.py) ----
class PlazoPedido(BaseModel):
    motivo: Literal["transferencia", "culqi"]


@app.post("/pagos/{pedido_id}/plazo")
def extender_plazo(
    pedido_id: uuid.UUID,
    datos: PlazoPedido,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("comprador")),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    """El checkout lo pide al elegir transferencia y justo antes de abrir Culqi. Solo alarga el plazo de un pago
    "pendiente" (uno en proceso o en revisión no vence; uno vencido se vuelve a reservar al cobrarlo) y nunca lo acorta.
    Cada extensión queda anotada (extensiones_plazo). Devuelve el plazo que vale ahora, para mostrarlo."""
    _exigir_dueno_para_pagar(pedido_id, usuario, credenciales.credentials)
    pago_id = _pago_o_crearlo(db, pedido_id).id
    db.rollback()
    pago = db.query(models.Pago).filter(models.Pago.id == pago_id).with_for_update().populate_existing().one()
    if pago.usuario_id is None:
        pago.usuario_id = usuario.get("sub")  # un pago de antes: desde ahora cuenta para el tope por comprador
    antes = pago.vence_en
    if pago.estado == "pendiente" and antes is not None:
        ahora = datetime.utcnow()
        creacion = pago.fecha_creacion or ahora
        nuevo = (plazos.vence_con_transferencia(creacion, antes) if datos.motivo == "transferencia"
                 else plazos.vence_al_abrir_culqi(creacion, antes, ahora))
        if nuevo > antes:
            pago.vence_en = nuevo
            db.add(models.ExtensionPlazo(pago_id=pago.id, pedido_id=pago.pedido_id, motivo=datos.motivo,
                                         vence_antes=antes, vence_despues=nuevo, fecha=ahora))
            logger.info("Pedido %s: plazo para pagar alargado (%s) de %s a %s", pedido_id, datos.motivo, antes, nuevo)
    respuesta = {"pedido_id": str(pedido_id), "estado": pago.estado, "vence_en": jsonable_encoder(pago.vence_en)}
    db.commit()
    return respuesta


# ---- El comprador cancela su pedido sin pagar ("Cancelar pedido" en Mis pedidos, "Reemplazar" en el checkout) ----
# Pedidos admite un solo pedido sin pagar por comprador: cancelarlo devuelve su stock en el acto (lo mismo que al vencer,
# sin esperar el plazo) y lo cancela también en Pedidos, para que el comprador pueda armar otro enseguida. Nunca mientras
# se cobra: con un cobro en curso (o en la conciliación) o una transferencia en revisión, 409 y no se toca nada.
MENSAJE_PAGO_SE_ESTA_VERIFICANDO = ("Tu pago anterior se está verificando: espera a que se confirme (lo verás en Mis "
                                    "pedidos) antes de cancelar o reemplazar este pedido.")
ESTADOS_QUE_SE_VERIFICAN = ("procesando", "en_revision")


def _exigir_dueno_para_cancelar(pedido_id, usuario: dict, token: str):
    _exigir_que_el_pedido_es_del_usuario(pedido_id, usuario, token, accion="cancelar este pedido")


def _cancelar_en_pedidos(pedido_id) -> bool:
    """Que Pedidos lo cancele en el acto. Si no responde, lo hará el evento pedido_cancelado_sin_pagar (ya confirmado
    en la bandeja de salida): devuelve si quedó hecho ahora."""
    try:
        resp = httpx.post(f"{PEDIDOS_URL}/pedidos/interno/{pedido_id}/cancelar-sin-pagar",
                          headers={"X-Servicio-Secreto": PAGOS_A_PEDIDOS_SECRETO}, timeout=5)
    except httpx.RequestError as error:
        logger.warning("Pedido %s: Pedidos no respondió al cancelarlo (%s); lo hará el evento pedido_cancelado_sin_pagar",
                       pedido_id, type(error).__name__)
        return False
    if resp.status_code != 200:
        logger.warning("Pedido %s: Pedidos respondió %s al cancelarlo; lo hará el evento pedido_cancelado_sin_pagar",
                       pedido_id, resp.status_code)
        return False
    return True


@app.post("/pagos/{pedido_id}/cancelar")
def cancelar_pedido_sin_pagar(
    pedido_id: uuid.UUID,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("comprador")),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    """Solo el comprador dueño. La fila del pago se toma con FOR UPDATE: un cobro que empieza a la vez o una constancia
    que se está subiendo se turnan con esto (el que llega después ve el estado nuevo). Idempotente."""
    _exigir_dueno_para_cancelar(pedido_id, usuario, credenciales.credentials)
    try:
        # Si pedido_creado todavía no llegó, el pago se crea (con sus productos, para poder devolver su stock).
        pago_id = _pago_o_crearlo(db, pedido_id).id
    except HTTPException as error:
        if error.status_code == 409:
            raise HTTPException(status_code=409, detail="Este pedido ya no está pendiente: revisa Mis pedidos.")
        raise
    db.rollback()  # lo leído hasta aquí, fuera: el estado que vale es el de la fila ya bloqueada
    pago = db.query(models.Pago).filter(models.Pago.id == pago_id).with_for_update().populate_existing().one()
    if pago.estado in ESTADOS_QUE_SE_VERIFICAN:
        db.rollback()
        raise HTTPException(status_code=409, detail=MENSAJE_PAGO_SE_ESTA_VERIFICANDO)
    if pago.estado in ("aprobado", "adelanto_pagado"):
        db.rollback()
        raise HTTPException(status_code=409, detail="Este pedido ya está pagado: si necesitas cancelarlo, escríbenos y lo "
                                                    "cancelamos con la devolución de tu dinero.")
    if pago.estado in ("reembolsado", "reembolso_procesando"):
        db.rollback()
        raise HTTPException(status_code=409, detail="Este pedido ya se canceló con la devolución de tu dinero.")
    devolver_stock = pago.estado == "pendiente"  # uno rechazado (o vencido) ya devolvió su stock
    if pago.estado != "cancelado":
        pago.estado = "cancelado"
        pago.cancelado_en = datetime.utcnow()
        registrar_evento(db, "pedido_cancelado_sin_pagar", {"pedido_id": str(pago.pedido_id)})
    items = pago.items
    db.commit()
    stock = "ya_devuelto"
    if devolver_stock:
        resultado = compensar_stock(items, pedido_id=pedido_id) or {}
        stock = "devuelto" if all(estado == "devuelta" for estado in resultado.values()) else "por_devolver"
    en_pedidos = _cancelar_en_pedidos(pedido_id)
    logger.info("Pedido %s: el comprador lo canceló antes de pagar (stock %s)", pedido_id, stock)
    return {"pedido_id": str(pedido_id), "estado": "cancelado", "stock": stock, "pedido_cancelado": en_pedidos}


# ---- Conciliación de lo que quedó en proceso: averigua en Culqi qué pasó con cada referencia ----
INTERVALO_CONCILIACION_SEGUNDOS = 60
ESPERA_ANTES_DE_CONCILIAR = timedelta(minutes=2)
MARGEN_DE_BUSQUEDA = timedelta(minutes=5)  # la ventana alrededor de la hora del intento (reloj de Culqi, demoras)
PASADAS_SIN_RASTRO = 3  # tantas búsquedas completas sin encontrar el cargo: revisión manual (nunca "no hubo cobro")
PASADAS_MAXIMAS = 10  # sin poder averiguarlo: revisión manual, con aviso en verificar_despliegue y en el panel


def _anotar_pasada(db: Session, referencia, mensaje: str, revision: bool = False) -> int:
    """Una búsqueda que no resolvió el intento. Con revision=True, o al llegar a PASADAS_MAXIMAS, pasa a revisión manual:
    la conciliación ya no lo toca y lo resuelve un admin desde el panel. Devuelve las pasadas."""
    intento = (db.query(models.IntentoPago)
               .filter(models.IntentoPago.id == referencia, models.IntentoPago.estado == "en_curso")
               .with_for_update().first())
    if intento is None:
        db.rollback()
        return 0
    intento.conciliaciones = (intento.conciliaciones or 0) + 1
    intento.mensaje = mensaje
    pasadas = intento.conciliaciones
    if (revision or pasadas >= PASADAS_MAXIMAS) and not intento.revision_manual:
        intento.revision_manual = True
        logger.error("Pedido %s: el %s con referencia %s queda para REVISIÓN MANUAL tras %d pasadas (%s): resolverlo desde "
                     "el panel del admin", intento.pedido_id, intento.tipo, referencia, pasadas, mensaje)
    db.commit()
    return pasadas


def averiguar_en_culqi(caso: dict) -> dict:
    """Qué pasó en Culqi con un intento ({"encontrado", "completo"}, ver culqi_client): el cobro se busca por su
    referencia (la de la metadata) entre los cargos de la ventana de su hora; la devolución, en el cargo que se devolvía,
    comparando lo acumulado (amount_refunded contra lo devuelto antes + esta), porque Culqi no guarda la metadata de las
    devoluciones."""
    desde, hasta = caso["inicio"] - MARGEN_DE_BUSQUEDA, caso["inicio"] + MARGEN_DE_BUSQUEDA
    if caso["tipo"] == "cobro":
        return culqi_client.buscar_cargo(caso["referencia"], desde, hasta)
    return culqi_client.buscar_devolucion(caso["cargo_id"], caso["monto"], desde, hasta, caso["devuelto_antes"])


def conciliar_intentos_en_curso(db: Session, ahora=None, buscar=None) -> int:
    """Los intentos (cobros y devoluciones) en curso hace más de ESPERA_ANTES_DE_CONCILIAR: se averigua en Culqi qué
    pasó con cada uno (averiguar_en_culqi) y se resuelven, cada uno en su commit (con su aviso, una sola vez). Nunca con
    una transacción abierta mientras se consulta a Culqi. Devuelve cuántos resolvió.

    Un cobro solo se resuelve solo cuando aparece su cargo: si no aparece, nunca se concluye "no hubo cobro" (el
    comprador podría haber pagado); pasa a revisión manual y lo decide un admin desde el panel. Una devolución sí se
    da por no hecha cuando el cargo dice que no se devolvió nada más (amount_refunded es del propio cargo, no de una
    búsqueda): se reintenta con tope y Culqi nunca devuelve más que el cargo."""
    ahora = ahora or datetime.utcnow()
    buscar = buscar or averiguar_en_culqi
    pendientes = [{"referencia": str(fila.id), "tipo": fila.tipo, "inicio": fila.fecha_inicio, "pasadas": fila.conciliaciones,
                   "cargo_id": fila.cargo_id, "monto": fila.monto, "devuelto_antes": fila.devuelto_antes or 0} for fila in (
        db.query(models.IntentoPago)
        .filter(models.IntentoPago.estado == "en_curso", models.IntentoPago.revision_manual.is_(False),
                models.IntentoPago.fecha_inicio < ahora - ESPERA_ANTES_DE_CONCILIAR)
        .order_by(models.IntentoPago.fecha_inicio).all())]
    db.rollback()
    resueltos = 0
    for caso in pendientes:
        referencia, tipo, pasadas = caso["referencia"], caso["tipo"], caso["pasadas"]
        try:
            hallado = buscar(caso)
        except Exception as error:  # noqa: BLE001 - Culqi no contestó: se reintenta en la pasada siguiente
            logger.warning("La búsqueda en Culqi del %s %s falló; se reintenta en la pasada siguiente", tipo, referencia,
                           exc_info=True)
            _anotar_pasada(db, referencia, f"Culqi no respondió a la búsqueda: {type(error).__name__}")
            continue
        objeto = hallado["encontrado"]
        try:
            if tipo == "cobro" and objeto is not None:
                aprobado = culqi_client.cargo_exitoso(objeto)
                cerrado = _cerrar_cobro(db, referencia, aprobado, cargo_id=objeto.get("id"),
                                        mensaje="Resuelto por la conciliación: " + ("Culqi lo cobró" if aprobado else
                                                                                   "Culqi no lo cobró"))
                _despues_del_cobro(db, cerrado, aprobado)
                resueltos += cerrado is not None
            elif tipo == "devolucion" and objeto is not None:
                if _cerrar_devolucion(db, referencia, True, devolucion_id=objeto.get("id"),
                                      mensaje="Resuelta por la conciliación: Culqi la hizo"):
                    resueltos += 1
                    _terminar_devolucion_si_corresponde(db, _pago_del_intento(db, referencia))
            elif tipo == "devolucion" and hallado["completo"] and pasadas + 1 >= PASADAS_SIN_RASTRO:
                if _cerrar_devolucion(db, referencia, False,
                                      mensaje="Resuelta por la conciliación: el cargo no tiene esta devolución (no se hizo)"):
                    resueltos += 1
            elif tipo == "cobro" and hallado["completo"] and pasadas + 1 >= PASADAS_SIN_RASTRO:
                _anotar_pasada(db, referencia, "No aparece en Culqi: lo decide un admin desde el panel (no se concluye "
                                               "solo que no hubo cobro)", revision=True)
            else:
                _anotar_pasada(db, referencia, "Todavía no aparece en Culqi" if hallado["completo"] else
                               "Lo que respondió Culqi no alcanza para saberlo")
        except IntegrityError:  # el cargo ya está asignado a otro pago (índice único): nunca se asigna a dos
            db.rollback()
            _anotar_pasada(db, referencia, f"El cargo {objeto.get('id') if objeto else '?'} ya está asignado a otro pago",
                           revision=True)
        except Exception:  # noqa: BLE001 - un caso que falla no frena a los demás: se reintenta en la pasada siguiente
            db.rollback()
            logger.exception("Conciliación: no se pudo resolver el %s %s; se reintenta en la pasada siguiente", tipo,
                             referencia)
    return resueltos


def una_pasada_de_conciliacion() -> None:
    """Un ciclo del hilo, cada parte en su sesión. Nunca lanza: el hilo no debe morir."""
    en_su_sesion_sin_lanzar(conciliar_intentos_en_curso, "La conciliación de cobros y devoluciones", logger)
    en_su_sesion_sin_lanzar(reintentar_devoluciones_a_medias, "El reintento de devoluciones a medias", logger)


def _bucle_conciliacion():
    while True:
        try:
            una_pasada_de_conciliacion()
        except Exception:  # una_pasada_de_conciliacion no lanza; esto es por si algo cambia
            logger.exception("El ciclo de conciliación falló")
        time.sleep(INTERVALO_CONCILIACION_SEGUNDOS)


# ============ Transferencia bancaria (presentación del 2026-10-01: "¿y si no pueden pagar por Yape?") ============
# El comprador transfiere a la cuenta de Chakra Shop y sube la constancia con el número de operación: el pago queda
# "en_revision" (el stock ya está reservado desde que se creó el pedido, así que no vence). Un admin mira la constancia
# y la aprueba (lo mismo que un pago de Culqi aprobado) o la rechaza con un motivo (se devuelve el stock y el comprador
# puede volver a pagar). Nadie más que un admin ve la constancia: va a un bucket privado.

NUMERO_OPERACION = re.compile(r"^[A-Za-z0-9-]{4,30}$")
MOTIVO_RECHAZO_MAXIMO = 300


@app.post("/pagos/transferencia")
def enviar_constancia_de_transferencia(
    pedido_id: uuid.UUID = Form(...),
    numero_operacion: str = Form(...),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: dict = Depends(verificar_token),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    if not _datos_transferencia():
        raise HTTPException(status_code=503, detail="El pago por transferencia no está disponible por ahora. Paga con tarjeta.")
    numero = (numero_operacion or "").strip()
    if not NUMERO_OPERACION.match(numero):
        raise HTTPException(status_code=422, detail="El número de operación tiene de 4 a 30 letras o números: lo encuentras en tu constancia.")
    if archivo.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=422, detail="La constancia debe ser una foto o captura JPG, PNG o WEBP.")
    _exigir_dueno_para_pagar(pedido_id, usuario, credenciales.credentials)
    datos, tipo = leer_imagen_validada(archivo)

    # Bloqueada como en procesar_pago: una constancia y un pago con tarjeta simultáneos se turnan.
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).with_for_update().first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    if pago.estado == "aprobado":
        raise HTTPException(status_code=409, detail="Este pedido ya fue pagado")
    if pago.estado == "reembolsado":
        raise HTTPException(status_code=409, detail="Este pedido se canceló y se te devolvió el dinero: ya no se puede pagar.")
    if pago.estado == "cancelado":
        # Su stock ya volvió al catálogo: aceptar la constancia dejaría pagado un pedido sin stock reservado.
        raise HTTPException(status_code=409, detail=MENSAJE_CANCELADO_POR_EL_COMPRADOR)
    if pago.estado == "en_revision":
        raise HTTPException(status_code=409, detail="Ya enviaste una constancia para este pedido: está en revisión.")
    if pago.estado in ("procesando", "reembolso_procesando"):
        raise HTTPException(status_code=409, detail="Hay un pago en proceso para este pedido: espera unos minutos y revisa "
                                                    "Mis pedidos.")

    parte = parte_a_cobrar(pago)
    # Un pago rechazado ya devolvió su stock: se vuelve a reservar antes (mismo criterio que reintentar con tarjeta).
    es_reintento = pago.estado == "rechazado"
    if es_reintento:
        reservar_stock(pago.items)
    try:
        objeto = subir_constancia(datos, tipo)
    except Exception:
        if es_reintento:
            compensar_stock(pago.items, pedido_id=pago.pedido_id)
        logger.error("No se pudo guardar la constancia del pedido %s", pedido_id, exc_info=True)
        raise HTTPException(status_code=502, detail="No se pudo guardar la constancia. Intenta de nuevo en unos momentos.")

    if parte == "saldo":
        pago.metodo_saldo = "transferencia"
    else:
        pago.metodo = "transferencia"
    pago.estado = "en_revision"
    pago.parte_en_revision = parte
    pago.numero_operacion = numero
    pago.constancia_objeto = objeto
    pago.constancia_tipo = tipo
    pago.fecha_constancia = datetime.utcnow()
    pago.motivo_rechazo = None
    pago.revisado_por = None
    pago.fecha_revision = None
    db.commit()
    return {"estado": "en_revision", "pedido_id": str(pago.pedido_id), "numero_operacion": numero, "parte": parte}


def _transferencia_resumen(pago, parte=None) -> dict:
    parte = parte or parte_a_cobrar(pago)
    return {
        "pedido_id": str(pago.pedido_id),
        # Lo que se tiene que ver en el banco: el total, o solo el adelanto o el saldo de un pedido en camión.
        "monto": float(monto_de(pago, parte)),
        "parte": parte,
        "monto_total": float(pago.monto),
        "estado": pago.estado,
        "numero_operacion": pago.numero_operacion,
        "fecha_constancia": pago.fecha_constancia,
        "motivo_rechazo": pago.motivo_rechazo,
    }


MINUTOS_PARA_AVISAR = 10


@app.get("/pagos/admin/por-revisar")
def pagos_por_revisar(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    """Los cobros y devoluciones que no se resolvieron solos: el pago lleva más de MINUTOS_PARA_AVISAR minutos
    "procesando" o "reembolso_procesando" (la conciliación todavía no pudo, o ya los dejó para revisión manual). Cada uno
    con la referencia de su intento: es la que va en la metadata del cargo, para buscarlo en el panel de Culqi."""
    limite = datetime.utcnow() - timedelta(minutes=MINUTOS_PARA_AVISAR)
    pagos = (db.query(models.Pago)
             .filter(models.Pago.estado.in_(("procesando", "reembolso_procesando")), models.Pago.procesando_desde < limite)
             .order_by(models.Pago.procesando_desde).all())
    salida = []
    for pago in pagos:
        intentos = (db.query(models.IntentoPago).filter(models.IntentoPago.pago_id == pago.id)
                    .order_by(models.IntentoPago.fecha_inicio.desc()).all())
        actual = next((i for i in intentos if i.estado == "en_curso"), intentos[0] if intentos else None)
        ultima_decision = max((i.fecha_resolucion for i in intentos if i.fecha_resolucion), default=None)
        salida.append({
            "pedido_id": str(pago.pedido_id),
            "tipo": "devolucion" if pago.estado == "reembolso_procesando" else "cobro",
            "parte": pago.parte_en_proceso or (actual.parte if actual else None),
            "monto": float(actual.monto) if actual else float(pago.monto),
            "desde": pago.procesando_desde.isoformat(),
            "referencia": str(actual.id) if actual else None,
            "cargo_id": actual.cargo_id if actual else None,
            "estado_del_intento": actual.estado if actual else None,
            "pasadas": actual.conciliaciones if actual else 0,
            "revision_manual": any(i.revision_manual for i in intentos
                                   if ultima_decision is None or i.fecha_inicio > ultima_decision),
            "puede_resolver": actual is not None and (actual.estado != "en_curso" or
                                                      actual.fecha_inicio < datetime.utcnow() - ESPERA_ANTES_DE_CONCILIAR),
            "mensaje": actual.mensaje if actual else None,
        })
    return salida


# ---- Resolver a mano lo que la conciliación no pudo (panel "Cobros por revisar en Culqi") ----
ID_DE_CULQI = re.compile(r"^[A-Za-z0-9_]{6,64}$")
ESTADOS_DE_DEVOLUCION_HECHA = ("completa",)


class ResolucionManual(BaseModel):
    resultado: Literal["cobrado", "no_cobrado", "devuelto", "no_devuelto"]
    cargo_id: str | None = Field(default=None, max_length=64)  # "cobrado": el id del cargo en Culqi
    devolucion_id: str | None = Field(default=None, max_length=64)  # "devuelto": el id de la devolución en Culqi
    motivo: str = Field(max_length=500)

    @field_validator("motivo")
    @classmethod
    def _motivo_obligatorio(cls, valor: str) -> str:
        limpio = " ".join(valor.split())
        if len(limpio) < 10:
            raise ValueError("Escribe el motivo (al menos 10 caracteres): queda registrado con tu nombre.")
        return limpio


def _culqi_no_lo_tiene(resultado: dict) -> bool:
    """Si Culqi dice que ese id no existe. Para un id desconocido NO responde 404 sino 401 authentication_error con «Ruta
    inválida» (comprobado en el sandbox el 2026-11-19); un 400 también es un id que no reconoce. Otro 401/403 es un
    problema de nuestra llave, no del id."""
    datos = resultado["data"] if isinstance(resultado["data"], dict) else {}
    ruta_invalida = "ruta inv" in str(datos.get("merchant_message") or datos.get("user_message") or "").lower()
    return resultado["status_code"] in (400, 404) or (resultado["status_code"] == 401 and ruta_invalida)


def _cargo_validado(cargo_id: str, referencia: str, pedido_id: str, monto) -> dict:
    """El cargo que el admin dice que cobró este intento, validado contra Culqi: existe, se cobró, por el monto del
    intento, en soles, y su metadata no es de otro intento ni de otro pedido. Si no cuadra, 422 con el porqué."""
    try:
        resultado = obtener_cargo(cargo_id)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=503, detail="No se pudo consultar Culqi para validar el cargo: intenta de nuevo en un momento.")
    if _culqi_no_lo_tiene(resultado):
        raise HTTPException(status_code=422, detail="Culqi no tiene un cargo con ese id.")
    if resultado["status_code"] != 200:
        raise HTTPException(status_code=503, detail="Culqi no respondió bien al validar el cargo: intenta de nuevo en un momento.")
    cargo = resultado["data"]
    metadata = cargo.get("metadata") or {}
    if not culqi_client.cargo_exitoso(cargo):
        raise HTTPException(status_code=422, detail="Según Culqi, ese cargo no se cobró.")
    if cargo.get("amount") != culqi_client.centimos(monto):
        raise HTTPException(status_code=422, detail=f"Ese cargo es por S/ {(cargo.get('amount') or 0) / 100:.2f} y este "
                                                    f"cobro era por S/ {Decimal(str(monto)):.2f}.")
    if cargo.get("currency_code") not in (None, "PEN"):
        raise HTTPException(status_code=422, detail="Ese cargo no es en soles.")
    if metadata.get("referencia") and str(metadata["referencia"]) != referencia:
        raise HTTPException(status_code=422, detail="Ese cargo es de otro intento de cobro: su referencia no es esta.")
    if metadata.get("pedido_id") and str(metadata["pedido_id"]) != pedido_id:
        raise HTTPException(status_code=422, detail="Ese cargo es de otro pedido.")
    return cargo


def _exigir_que_culqi_no_tenga_el_cargo(referencia: str, inicio: datetime) -> None:
    """Antes de dar un cobro por no hecho: si Culqi tiene un cargo con esta referencia, 409 (es "cobrado"). Si la búsqueda
    no se puede hacer, decide el admin (ya lo buscó en el panel de Culqi)."""
    try:
        hallado = culqi_client.buscar_cargo(referencia, inicio - MARGEN_DE_BUSQUEDA, inicio + MARGEN_DE_BUSQUEDA)
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("No se pudo buscar en Culqi el cobro %s antes de darlo por no hecho (%s): decide el admin",
                       referencia, error)
        return
    if hallado["encontrado"] is not None:
        raise HTTPException(status_code=409, detail=f"Culqi tiene un cargo con esta referencia "
                                                    f"({hallado['encontrado'].get('id')}): resuélvelo como cobrado.")


def _resolver_cobro(db: Session, intento, datos: ResolucionManual, resolucion: dict) -> dict:
    if datos.resultado not in ("cobrado", "no_cobrado"):
        raise HTTPException(status_code=422, detail="Un cobro se resuelve como cobrado o no cobrado.")
    if intento.estado != "en_curso":
        raise HTTPException(status_code=409, detail=f"Este cobro ya se resolvió ({intento.estado}).")
    if intento.fecha_inicio > datetime.utcnow() - ESPERA_ANTES_DE_CONCILIAR:
        raise HTTPException(status_code=409, detail="Este cobro todavía se está procesando: espera unos minutos.")
    referencia, pedido_id, monto, inicio = str(intento.id), str(intento.pedido_id), intento.monto, intento.fecha_inicio
    db.rollback()  # sin transacción abierta mientras se consulta a Culqi
    if datos.resultado == "cobrado":
        cargo_id = (datos.cargo_id or "").strip()
        if not ID_DE_CULQI.match(cargo_id):
            raise HTTPException(status_code=422, detail="Escribe el id del cargo tal como aparece en el panel de Culqi.")
        _cargo_validado(cargo_id, referencia, pedido_id, monto)
        try:
            cerrado = _cerrar_cobro(db, referencia, True, cargo_id=cargo_id, mensaje="Resuelto a mano: cobrado",
                                    resolucion=resolucion)
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Ese cargo ya está asignado a otro pago: un cargo nunca se asigna a dos.")
    else:
        _exigir_que_culqi_no_tenga_el_cargo(referencia, inicio)
        cerrado = _cerrar_cobro(db, referencia, False, mensaje="Resuelto a mano: no cobrado", resolucion=resolucion)
    if cerrado is None:
        raise HTTPException(status_code=409, detail="Este cobro se resolvió mientras lo revisabas: recarga la lista.")
    _despues_del_cobro(db, cerrado, datos.resultado == "cobrado")
    logger.info("Pedido %s: el admin %s resolvió a mano el cobro %s como %s", pedido_id, resolucion["por"], referencia,
                datos.resultado)
    return {"resultado": datos.resultado, "pedido_id": pedido_id, "estado_del_pago": cerrado[0].estado}


def _devolucion_validada(devolucion_id: str, cargo: str, monto) -> None:
    """La devolución que el admin dice que se hizo, validada contra Culqi: existe, es de ese cargo, por el monto que había
    que devolver y está completa. Si no cuadra, 422 con el porqué."""
    try:
        resultado = obtener_devolucion(devolucion_id)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=503, detail="No se pudo consultar Culqi para validar la devolución: intenta de nuevo en un momento.")
    if _culqi_no_lo_tiene(resultado):
        raise HTTPException(status_code=422, detail="Culqi no tiene una devolución con ese id.")
    if resultado["status_code"] != 200:
        raise HTTPException(status_code=503, detail="Culqi no respondió bien al validar la devolución: intenta de nuevo en un momento.")
    devolucion = resultado["data"]
    if devolucion.get("charge_id") != cargo:
        raise HTTPException(status_code=422, detail="Esa devolución es de otro cargo.")
    if devolucion.get("amount") != culqi_client.centimos(monto):
        raise HTTPException(status_code=422, detail=f"Esa devolución es por S/ {(devolucion.get('amount') or 0) / 100:.2f} "
                                                    f"y había que devolver S/ {Decimal(str(monto)):.2f}.")
    estado = str(devolucion.get("status") or "").lower()
    if estado and estado not in ESTADOS_DE_DEVOLUCION_HECHA:
        raise HTTPException(status_code=422, detail=f"Según Culqi esa devolución está «{estado}», no completa.")


def _exigir_que_el_cargo_no_figure_devuelto(cargo: str, monto, devuelto_antes: int) -> None:
    """Antes de dar una devolución por no hecha: si el cargo ya figura devuelto en Culqi (lo acumulado), 409. Si no se
    puede consultar, decide el admin."""
    try:
        devuelto = devuelto_del_cargo(cargo)
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("No se pudo consultar en Culqi el cargo %s antes de dar su devolución por no hecha (%s): decide "
                       "el admin", cargo, error)
        return
    if devuelto >= devuelto_antes + culqi_client.centimos(monto):
        raise HTTPException(status_code=409, detail="Según Culqi ese cargo ya se devolvió: resuélvelo como devuelto, con el "
                                                    "id de la devolución.")


def _resolver_devolucion(db: Session, intento, datos: ResolucionManual, resolucion: dict) -> dict:
    if datos.resultado not in ("devuelto", "no_devuelto"):
        raise HTTPException(status_code=422, detail="Una devolución se resuelve como devuelta o no devuelta.")
    pago = db.query(models.Pago).filter(models.Pago.id == intento.pago_id).first()
    if pago is None or pago.estado != "reembolso_procesando":
        raise HTTPException(status_code=409, detail="Esta devolución ya no está pendiente.")
    cargo, monto, parte, pago_id, pedido_uuid = intento.cargo_id, intento.monto, intento.parte, pago.id, pago.pedido_id
    pedido_id = str(pedido_uuid)
    del_cargo = (db.query(models.IntentoPago)
                 .filter(models.IntentoPago.tipo == "devolucion", models.IntentoPago.cargo_id == cargo)
                 .order_by(models.IntentoPago.fecha_inicio.desc()).all())
    if any(i.estado == "aprobado" for i in del_cargo):
        raise HTTPException(status_code=409, detail="Ese cargo ya figura devuelto.")
    en_curso = next((i for i in del_cargo if i.estado == "en_curso"), None)
    if en_curso is not None and en_curso.fecha_inicio > datetime.utcnow() - ESPERA_ANTES_DE_CONCILIAR:
        raise HTTPException(status_code=409, detail="Esta devolución todavía se está procesando: espera unos minutos.")
    referencia_en_curso = str(en_curso.id) if en_curso else None
    ultimo = str(del_cargo[0].id)
    devuelto_antes = (en_curso or del_cargo[0]).devuelto_antes or 0
    db.rollback()  # sin transacción abierta mientras se consulta a Culqi
    if datos.resultado == "devuelto":
        devolucion_id = (datos.devolucion_id or "").strip()
        if not ID_DE_CULQI.match(devolucion_id):
            raise HTTPException(status_code=422, detail="Escribe el id de la devolución tal como aparece en el panel de Culqi.")
        _devolucion_validada(devolucion_id, cargo, monto)
        if referencia_en_curso is None:  # la última se rechazó: la devolución hecha queda como un intento más, ya cerrado
            referencia_en_curso = str(uuid.uuid4())
            db.add(models.IntentoPago(id=referencia_en_curso, pago_id=pago_id, pedido_id=pedido_uuid,
                                      tipo="devolucion", parte=parte, monto=monto, estado="en_curso",
                                      estado_anterior="reembolso_procesando", cargo_id=cargo, fecha_inicio=datetime.utcnow()))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(status_code=409, detail="Otra devolución de ese cargo empezó mientras lo revisabas: recarga la lista.")
        if not _cerrar_devolucion(db, referencia_en_curso, True, devolucion_id=devolucion_id,
                                  mensaje="Resuelta a mano: devuelta", resolucion=resolucion):
            raise HTTPException(status_code=409, detail="Esta devolución se resolvió mientras lo revisabas: recarga la lista.")
        _terminar_devolucion_si_corresponde(db, pago_id)
    else:
        _exigir_que_el_cargo_no_figure_devuelto(cargo, monto, devuelto_antes)
        if referencia_en_curso is not None:
            if not _cerrar_devolucion(db, referencia_en_curso, False, mensaje="Resuelta a mano: no devuelta (se reintenta)",
                                      resolucion=resolucion):
                raise HTTPException(status_code=409, detail="Esta devolución se resolvió mientras lo revisabas: recarga la lista.")
        else:  # la decisión queda en el último intento: el tope de reintentos vuelve a contar desde aquí
            fila = db.query(models.IntentoPago).filter(models.IntentoPago.id == ultimo).with_for_update().first()
            _anotar_resolucion(fila, resolucion)
            db.commit()
    logger.info("Pedido %s: el admin %s resolvió a mano la devolución del cargo %s como %s", pedido_id, resolucion["por"],
                cargo, datos.resultado)
    estado = db.query(models.Pago.estado).filter(models.Pago.id == pago_id).scalar()
    return {"resultado": datos.resultado, "pedido_id": pedido_id, "estado_del_pago": estado}


@app.post("/pagos/admin/por-revisar/{referencia}/resolver")
def resolver_caso_por_revisar(referencia: uuid.UUID, datos: ResolucionManual, db: Session = Depends(get_db),
                              usuario: dict = Depends(requiere_rol("admin"))):
    """Un admin resuelve a mano un cobro o una devolución que la conciliación no resolvió sola. El motivo es obligatorio y
    queda en el intento con quién y cuándo (resuelto_por, motivo_resolucion, fecha_resolucion). Nada se hace con una
    transacción abierta mientras se consulta a Culqi.
    - Cobro "cobrado": con el id del cargo, validado contra Culqi (_cargo_validado). El pago queda aprobado con su aviso,
      una sola vez; un cargo ya asignado a otro pago da 409.
    - Cobro "no_cobrado": el pago queda rechazado (con su aviso), se libera el stock y el comprador puede volver a pagar.
      Si Culqi tiene un cargo con esta referencia, 409.
    - Devolución "devuelto": con el id de la devolución, validado contra Culqi (_devolucion_validada).
    - Devolución "no_devuelto": se vuelve a intentar sola (el tope vuelve a contar). Si el cargo ya figura devuelto, 409."""
    intento = db.query(models.IntentoPago).filter(models.IntentoPago.id == referencia).first()
    if intento is None:
        raise HTTPException(status_code=404, detail="No hay un cobro ni una devolución con esa referencia.")
    try:
        admin = uuid.UUID(str(usuario.get("sub")))
    except ValueError:
        raise HTTPException(status_code=403, detail="No se pudo saber qué admin resuelve.")
    resolucion = {"por": admin, "motivo": datos.motivo}
    if intento.tipo == "cobro":
        return _resolver_cobro(db, intento, datos, resolucion)
    return _resolver_devolucion(db, intento, datos, resolucion)


@app.get("/pagos/admin/transferencias")
def listar_transferencias_en_revision(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    """Las que esperan la revisión de un admin, la más antigua primero."""
    pagos = db.query(models.Pago).filter(models.Pago.estado == "en_revision").order_by(models.Pago.fecha_constancia.asc()).all()
    return [_transferencia_resumen(p) for p in pagos]


def _pago_en_revision(db: Session, pedido_id) -> "models.Pago":
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).with_for_update().first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    if pago.estado != "en_revision":
        raise HTTPException(status_code=409, detail="Esta transferencia ya no está en revisión (otro administrador la revisó).")
    return pago


@app.get("/pagos/admin/transferencias/{pedido_id}/constancia")
def ver_constancia(pedido_id: uuid.UUID, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
    if not pago or not pago.constancia_objeto:
        raise HTTPException(status_code=404, detail="Este pedido no tiene una constancia de transferencia")
    try:
        datos = leer_constancia(pago.constancia_objeto)
    except Exception:
        logger.error("No se pudo leer la constancia del pedido %s", pedido_id, exc_info=True)
        raise HTTPException(status_code=502, detail="No se pudo leer la constancia. Intenta de nuevo en unos momentos.")
    return Response(content=datos, media_type=pago.constancia_tipo or "image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/pagos/admin/transferencias/{pedido_id}/aprobar")
def aprobar_transferencia(pedido_id: uuid.UUID, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    pago = _pago_en_revision(db, pedido_id)
    parte = parte_a_cobrar(pago)
    pago.revisado_por = usuario.get("sub")
    pago.fecha_revision = datetime.utcnow()
    _registrar_parte_pagada(db, pago, parte, "transferencia")
    return _transferencia_resumen(pago, parte)


class RechazarTransferencia(BaseModel):
    motivo: str

    @field_validator("motivo")
    @classmethod
    def _motivo_valido(cls, valor: str) -> str:
        limpio = (valor or "").strip()
        if len(limpio) < 3:
            raise ValueError("Escribe el motivo del rechazo: el comprador lo va a leer")
        if len(limpio) > MOTIVO_RECHAZO_MAXIMO or "\x00" in limpio:
            raise ValueError(f"El motivo no puede superar {MOTIVO_RECHAZO_MAXIMO} caracteres")
        return limpio


@app.post("/pagos/admin/transferencias/{pedido_id}/rechazar")
def rechazar_transferencia(
    pedido_id: uuid.UUID,
    datos: RechazarTransferencia,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("admin")),
):
    """Mismo efecto que un pago rechazado por Culqi (se devuelve el stock, el pedido queda cancelado hasta que vuelva a
    pagar), más el motivo, que el comprador ve en Mis Pedidos."""
    pago = _pago_en_revision(db, pedido_id)
    parte = parte_a_cobrar(pago)
    pago.motivo_rechazo = datos.motivo
    pago.revisado_por = usuario.get("sub")
    pago.fecha_revision = datetime.utcnow()
    pago.parte_en_revision = None
    if parte == "saldo":
        # El pedido ya va en el camión con su adelanto: solo vuelve a esperar el saldo (sin tocar stock ni cancelar).
        # saldo_rechazado y no pago_rechazado: este último cancela el pedido en Pedidos.
        pago.estado = "adelanto_pagado"
        registrar_evento(db, "saldo_rechazado", {"pedido_id": str(pago.pedido_id), "motivo": datos.motivo})
        db.commit()
        return _transferencia_resumen(pago, parte)
    pago.estado = "rechazado"
    registrar_evento(db, "pago_rechazado", {"pedido_id": str(pago.pedido_id), "motivo": datos.motivo})
    db.commit()
    compensar_stock(pago.items, pedido_id=pago.pedido_id)
    return _transferencia_resumen(pago, parte)




# ============ Devolver lo pagado de un pedido que se cancela (lo pide Pedidos, con el token del admin) ============
class Devolucion(BaseModel):
    motivo: str = Field(min_length=3, max_length=300)


def _cobros_a_devolver(pago) -> tuple[list, Decimal]:
    """[(id del cargo en Culqi, monto, parte)] y lo que hay que devolver a mano (lo pagado por transferencia). Un pedido
    en camión tiene dos cobros: el adelanto y, si ya llegó, el saldo."""
    cobros, manual = [], Decimal("0")
    pagado = pago.estado_antes_de_procesar if pago.estado == "reembolso_procesando" else pago.estado

    def agregar(metodo, cargo, monto, parte):
        nonlocal manual
        if metodo == "transferencia" or not cargo:
            manual += monto
        else:
            cobros.append((cargo, monto, parte))

    if pago.monto_adelanto is None:
        agregar(pago.metodo, pago.culqi_charge_id, monto_de(pago, "total"), "total")
    else:
        agregar(pago.metodo, pago.culqi_charge_id, monto_de(pago, "adelanto"), "adelanto")
        if pagado == "aprobado":  # el saldo también se pagó
            agregar(pago.metodo_saldo, pago.culqi_charge_id_saldo, monto_de(pago, "saldo"), "saldo")
    return cobros, manual


def _devolver_descuentos_cobrados(db: Session, liquidacion) -> None:
    """Si la liquidación que se anula ya le había cobrado al productor parte de un recojo, esa deuda vuelve a quedar
    pendiente (las más recientes primero)."""
    resto = Decimal(str(liquidacion.descuento_recojo or 0))
    if resto <= 0:
        return
    deudas = (db.query(models.DescuentoProductor)
              .filter(models.DescuentoProductor.productor_id == liquidacion.beneficiario_id, models.DescuentoProductor.aplicado > 0)
              .order_by(models.DescuentoProductor.fecha_creacion.desc()).with_for_update().all())
    for deuda in deudas:
        quitar = min(resto, Decimal(str(deuda.aplicado)))
        deuda.aplicado = Decimal(str(deuda.aplicado)) - quitar
        resto -= quitar
        if resto <= 0:
            break


def _resumen_devolucion(pago) -> dict:
    return {
        "estado": pago.estado,
        "monto_reembolsado": float(pago.monto_reembolsado or 0),
        "reembolso_manual": float(pago.reembolso_manual) if pago.reembolso_manual else None,
        "reembolso_ids": json.loads(pago.reembolso_ids) if pago.reembolso_ids else [],
    }


# ---- Devolución en dos fases, como el cobro: el pago pasa a "reembolso_procesando" y cada cargo se devuelve con su
# intento (referencia propia en la metadata) confirmado ANTES de llamar a Culqi; el resultado va en su propio commit. Si
# queda a medias, la conciliación la busca en Culqi por la referencia. Un cargo nunca se devuelve dos veces: mientras su
# devolución está en curso o ya salió, el índice único no deja empezar otra. Los reintentos tienen tope; después,
# revisión manual.
MAX_INTENTOS_DEVOLUCION = 3
ESPERA_ANTES_DE_REINTENTAR_DEVOLUCION = timedelta(minutes=2)


def _pago_del_intento(db: Session, referencia):
    pago_id = db.query(models.IntentoPago.pago_id).filter(models.IntentoPago.id == referencia).scalar()
    db.rollback()
    return pago_id


def _cerrar_devolucion(db: Session, referencia, aprobado: bool, devolucion_id=None, mensaje=None,
                       resolucion: dict | None = None) -> bool:
    """El resultado de devolver un cargo, en un commit, solo si su intento seguía en curso. Una devolución hecha queda
    también en pago.reembolso_ids (lo que se muestra)."""
    intento = (db.query(models.IntentoPago)
               .filter(models.IntentoPago.id == referencia, models.IntentoPago.estado == "en_curso")
               .with_for_update().first())
    if intento is None:
        db.rollback()
        return False
    intento.estado = "aprobado" if aprobado else "rechazado"
    intento.devolucion_id, intento.mensaje, intento.fecha_fin = devolucion_id, mensaje, datetime.utcnow()
    _anotar_resolucion(intento, resolucion)
    if aprobado:
        pago = db.query(models.Pago).filter(models.Pago.id == intento.pago_id).with_for_update().first()
        hechos = json.loads(pago.reembolso_ids) if pago.reembolso_ids else []
        hechos.append({"cargo": intento.cargo_id, "devolucion": devolucion_id, "monto": float(intento.monto)})
        pago.reembolso_ids = json.dumps(hechos)
    db.commit()
    return True


def _devolver_cargo(db: Session, pago_id, pedido_id, cargo: str, monto: Decimal, parte: str) -> str:
    """Devuelve un cargo en dos fases. "aprobado" (devuelto ahora o antes), "en_curso" (hay una en curso, o Culqi no
    contestó: lo resuelve la conciliación), "rechazado" (Culqi la rechazó; se reintenta con tope), "pendiente" (no se
    pudo saber cuánto llevaba devuelto el cargo: no se empezó y se reintenta) o "revision" (al tope de rechazos, o
    marcada para revisión manual: la decide un admin desde el panel; después de su decisión el tope vuelve a contar).

    Hoy cada cargo se devuelve entero (el monto de su parte) y una sola vez: el índice uq_intentos_pago_devolucion no
    deja empezar otra mientras una está en curso o ya salió. Puede haber varios intentos rechazados, nunca dos hechos."""
    filas = db.query(models.IntentoPago.estado, models.IntentoPago.revision_manual, models.IntentoPago.fecha_inicio,
                     models.IntentoPago.fecha_resolucion).filter(
        models.IntentoPago.tipo == "devolucion", models.IntentoPago.cargo_id == cargo).all()
    db.rollback()
    estados = [fila.estado for fila in filas]
    if "aprobado" in estados:
        return "aprobado"
    if "en_curso" in estados:
        return "en_curso"
    ultima_decision = max((fila.fecha_resolucion for fila in filas if fila.fecha_resolucion), default=None)
    desde_la_decision = [fila for fila in filas if ultima_decision is None or fila.fecha_inicio > ultima_decision]
    if (any(fila.revision_manual for fila in desde_la_decision)
            or sum(fila.estado == "rechazado" for fila in desde_la_decision) >= MAX_INTENTOS_DEVOLUCION):
        return "revision"
    # Fase 0, sin transacción abierta: cuánto llevaba devuelto el cargo según Culqi. La conciliación compara lo
    # acumulado (esto + la devolución), así que sin ese dato no se empieza.
    try:
        devuelto_antes = devuelto_del_cargo(cargo)
    except (httpx.HTTPError, ValueError) as error:
        logger.error("Pedido %s: no se pudo saber cuánto llevaba devuelto el cargo %s (%s); la devolución se reintenta",
                     pedido_id, cargo, error)
        return "pendiente"
    referencia = uuid.uuid4()
    db.add(models.IntentoPago(id=referencia, pago_id=pago_id, pedido_id=pedido_id, tipo="devolucion", parte=parte,
                              monto=monto, estado="en_curso", estado_anterior="reembolso_procesando", cargo_id=cargo,
                              devuelto_antes=devuelto_antes, fecha_inicio=datetime.utcnow()))
    try:
        db.commit()
    except IntegrityError:  # otra devolución del mismo cargo empezó a la vez (otra réplica, otro pedido de cancelación)
        db.rollback()
        return "en_curso"
    try:
        resultado = crear_devolucion(cargo, float(monto), metadata={"referencia": str(referencia), "pedido_id": str(pedido_id)})
    except (httpx.RequestError, ValueError):
        logger.error("Pedido %s: Culqi no contestó la devolución %s del cargo %s; la resuelve la conciliación", pedido_id,
                     referencia, cargo, exc_info=True)
        return "en_curso"
    estado_http, cuerpo = resultado["status_code"], resultado["data"]
    if estado_http not in (200, 201) and not 400 <= estado_http < 500:
        return "en_curso"  # una respuesta rara de Culqi: no se sabe si devolvió; lo resuelve la conciliación
    aprobado = estado_http in (200, 201)
    mensaje = None if aprobado else (cuerpo.get("user_message") or cuerpo.get("merchant_message") or "Culqi rechazó la devolución")
    try:
        _cerrar_devolucion(db, referencia, aprobado, devolucion_id=cuerpo.get("id") if aprobado else None, mensaje=mensaje)
    except Exception:
        # Culqi respondió pero no se pudo guardar: queda en curso y la conciliación la busca por la referencia.
        db.rollback()
        logger.error("Pedido %s: no se pudo guardar el resultado de la devolución %s; queda en curso", pedido_id,
                     referencia, exc_info=True)
        return "en_curso"
    if not aprobado:
        logger.error("Pedido %s: Culqi rechazó la devolución del cargo %s (%s)", pedido_id, cargo, mensaje)
    return "aprobado" if aprobado else "rechazado"


def _terminar_devolucion_si_corresponde(db: Session, pago_id) -> bool:
    """Si ya se devolvieron todos los cargos, el pago queda "reembolsado" con su aviso (pago_reembolsado), en un commit y
    una sola vez; después se devuelve el stock. Devuelve si terminó ahora."""
    pago = (db.query(models.Pago).filter(models.Pago.id == pago_id, models.Pago.estado == "reembolso_procesando")
            .with_for_update().first())
    if pago is None:
        db.rollback()
        return False
    cobros, manual = _cobros_a_devolver(pago)
    devueltos = {fila.cargo_id: fila.monto for fila in db.query(models.IntentoPago).filter(
        models.IntentoPago.pago_id == pago_id, models.IntentoPago.tipo == "devolucion",
        models.IntentoPago.estado == "aprobado").all()}
    if any(cargo not in devueltos for cargo, _, _ in cobros):
        db.rollback()
        return False
    pago.estado = "reembolsado"
    pago.monto_reembolsado = sum((Decimal(str(devueltos[cargo])) for cargo, _, _ in cobros), Decimal("0"))
    pago.reembolso_manual = manual or None
    pago.fecha_reembolso = datetime.utcnow()
    pago.parte_en_proceso = pago.estado_antes_de_procesar = pago.procesando_desde = None
    for liquidacion in db.query(models.Liquidacion).filter(models.Liquidacion.pedido_id == pago.pedido_id,
                                                           models.Liquidacion.estado == "pendiente").all():
        _devolver_descuentos_cobrados(db, liquidacion)
        liquidacion.estado = "anulada"
    registrar_evento(db, "pago_reembolsado", {"pedido_id": str(pago.pedido_id), "motivo": pago.motivo_reembolso,
                                              "monto": _dinero(pago.monto_reembolsado),
                                              "manual": _dinero(manual) if manual else None})
    items, pedido_id = pago.items, pago.pedido_id
    db.commit()
    compensar_stock(items, pedido_id=pedido_id)
    return True


def _devolver_lo_que_falta(db: Session, pago_id) -> dict:
    """Intenta devolver cada cargo que falta y, si con eso están todos, termina la devolución. Devuelve
    {cargo: resultado} de los que no quedaron devueltos."""
    pago = db.query(models.Pago).filter(models.Pago.id == pago_id).first()
    cobros, _ = _cobros_a_devolver(pago)
    pedido_id = pago.pedido_id
    db.rollback()
    faltan = {}
    for cargo, monto, parte in cobros:
        resultado = _devolver_cargo(db, pago_id, pedido_id, cargo, monto, parte)
        if resultado != "aprobado":
            faltan[cargo] = resultado
    if not faltan:
        _terminar_devolucion_si_corresponde(db, pago_id)
    return faltan


def reintentar_devoluciones_a_medias(db: Session, ahora=None) -> int:
    """Las devoluciones que quedaron a medias (un cargo rechazado por Culqi, o que la conciliación resolvió como no
    hecho) se reintentan, con tope por cargo; al tope, revisión manual. No toca las que tienen un intento en curso (las
    resuelve la conciliación) ni las que empezaron hace muy poco (las está haciendo su petición). Devuelve cuántas
    terminó."""
    ahora = ahora or datetime.utcnow()
    en_curso = db.query(models.IntentoPago.pago_id).filter(models.IntentoPago.tipo == "devolucion",
                                                           models.IntentoPago.estado == "en_curso")
    ids = [fila.id for fila in db.query(models.Pago.id).filter(
        models.Pago.estado == "reembolso_procesando", ~models.Pago.id.in_(en_curso),
        models.Pago.procesando_desde < ahora - ESPERA_ANTES_DE_REINTENTAR_DEVOLUCION).all()]
    db.rollback()
    terminadas = 0
    for pago_id in ids:
        faltan = _devolver_lo_que_falta(db, pago_id)
        if not faltan:
            terminadas += 1
        for cargo, resultado in faltan.items():
            if resultado == "revision":
                _marcar_devolucion_para_revision(db, pago_id, cargo)
    return terminadas


def _marcar_devolucion_para_revision(db: Session, pago_id, cargo: str) -> None:
    ultimo = (db.query(models.IntentoPago)
              .filter(models.IntentoPago.pago_id == pago_id, models.IntentoPago.tipo == "devolucion",
                      models.IntentoPago.cargo_id == cargo)
              .order_by(models.IntentoPago.fecha_inicio.desc()).with_for_update().first())
    if ultimo is not None and not ultimo.revision_manual:
        ultimo.revision_manual = True
        logger.error("Pedido %s: la devolución del cargo %s falló %d veces. REVISIÓN MANUAL: referencia %s",
                     ultimo.pedido_id, cargo, MAX_INTENTOS_DEVOLUCION, ultimo.id)
    db.commit()


@app.post("/pagos/admin/{pedido_id}/devolver")
def devolver_pago(pedido_id: uuid.UUID, datos: Devolucion, db: Session = Depends(get_db),
                  usuario: dict = Depends(requiere_rol("admin"))):
    """Paso de la saga de cancelación de un pedido pagado (cancelar_pedido_pagado en Pedidos, DESPUÉS de cancelar el
    envío). Devuelve en Culqi cada cobro (tarjeta o Yape), en dos fases; lo pagado por transferencia queda anotado para
    devolverlo a mano. Al terminar anula las ganancias que todavía no se pagaron, avisa (pago_reembolsado) y devuelve el
    stock. Idempotente: repetirlo retoma lo que falte sin devolver dos veces. Responde 200 si quedó todo devuelto y 202
    si quedó a medias (se reintenta sola y, al tope, queda para revisión manual)."""
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    if pago.estado == "reembolsado":
        return _resumen_devolucion(pago)
    if pago.estado != "reembolso_procesando":
        if pago.estado not in ("aprobado", "adelanto_pagado"):
            raise HTTPException(status_code=409, detail="Este pedido no tiene un pago confirmado que devolver.")
        motivo = " ".join(datos.motivo.split())
        cambiados = (db.query(models.Pago)
                     .filter(models.Pago.id == pago.id, models.Pago.estado.in_(("aprobado", "adelanto_pagado")))
                     .update({models.Pago.estado_antes_de_procesar: models.Pago.estado,
                              models.Pago.estado: "reembolso_procesando", models.Pago.procesando_desde: datetime.utcnow(),
                              models.Pago.motivo_reembolso: motivo}, synchronize_session=False))
        db.commit()
        if not cambiados:
            pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
            if pago.estado == "reembolsado":
                return _resumen_devolucion(pago)
            if pago.estado != "reembolso_procesando":
                raise HTTPException(status_code=409, detail="Este pedido no tiene un pago confirmado que devolver.")
    pago_id = pago.id
    db.rollback()
    faltan = _devolver_lo_que_falta(db, pago_id)
    pago = db.query(models.Pago).filter(models.Pago.id == pago_id).first()
    if not faltan:
        return _resumen_devolucion(pago)
    return JSONResponse(status_code=202, content={
        **_resumen_devolucion(pago),
        "pendientes": [{"cargo": cargo, "estado": estado} for cargo, estado in faltan.items()],
        "detalle": "La devolución quedó a medias: se reintenta sola y, si no sale, queda para revisión manual.",
    })


# ============ Cobros semanales de agricultores y repartidores (ver logica_cobros.py) ============
BANCOS_PERU = ("BCP", "Interbank", "BBVA", "Scotiabank", "Banco de la Nación", "BanBif", "Banco Pichincha", "Mibanco",
               "Caja Arequipa", "Caja Huancayo", "Caja Cusco", "Caja Piura", "Caja Trujillo", "Otro")


def _beneficiario_autenticado(usuario: dict, token: str) -> dict:
    """El agricultor o repartidor con sesión, con el id con que se guardan sus ganancias: su id de productor
    (Productores) o de repartidor (Transporte)."""
    rol = usuario.get("rol")
    if rol not in ("productor", "repartidor"):
        raise HTTPException(status_code=403, detail="Solo los agricultores y los repartidores tienen ganancias.")
    url = f"{PRODUCTORES_URL}/productores/me" if rol == "productor" else f"{TRANSPORTE_URL}/repartidores/me"
    try:
        with httpx.Client() as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="No se pudo consultar tu perfil. Intenta de nuevo en un momento.")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Completa primero tu perfil para ver tus ganancias.")
    if resp.status_code != 200:
        raise HTTPException(status_code=503, detail="No se pudo consultar tu perfil. Intenta de nuevo en un momento.")
    perfil = resp.json()
    return {"tipo": rol, "id": uuid.UUID(str(perfil["id"])), "nombre": perfil.get("nombre"), "usuario_id": usuario.get("sub")}


def _cuenta_de(db: Session, tipo: str, beneficiario_id) -> "models.CuentaCobro | None":
    return db.query(models.CuentaCobro).filter(models.CuentaCobro.beneficiario_tipo == tipo,
                                               models.CuentaCobro.beneficiario_id == beneficiario_id).first()


def _cuenta_salida(cuenta) -> dict | None:
    if not cuenta:
        return None
    return {"yape_celular": cuenta.yape_celular, "yape_titular": cuenta.yape_titular, "banco": cuenta.banco,
            "cuenta_titular": cuenta.cuenta_titular, "cci": cuenta.cci, "fecha_actualizacion": cuenta.fecha_actualizacion}


def _pago_beneficiario_salida(pago) -> dict:
    return {"id": str(pago.id), "beneficiario_tipo": pago.beneficiario_tipo, "beneficiario_id": str(pago.beneficiario_id),
            "nombre": pago.nombre, "monto": float(pago.monto), "metodo": pago.metodo, "destino": pago.destino,
            "numero_operacion": pago.numero_operacion, "cantidad": int(pago.cantidad), "hasta": pago.hasta, "fecha": pago.fecha}


class CuentaCobroDatos(BaseModel):
    yape_celular: str | None = Field(default=None, max_length=20)
    yape_titular: str | None = Field(default=None, max_length=100)
    banco: str | None = Field(default=None, max_length=40)
    cuenta_titular: str | None = Field(default=None, max_length=100)
    cci: str | None = Field(default=None, max_length=40)


def _validar_cuenta_cobro(datos: CuentaCobroDatos) -> dict:
    """Al menos un medio completo: Yape (celular + nombre) o cuenta bancaria (banco + CCI + titular)."""
    texto = lambda valor: " ".join((valor or "").split())
    celular, cci = re.sub(r"\D", "", datos.yape_celular or ""), re.sub(r"\D", "", datos.cci or "")
    con_yape = bool(celular or texto(datos.yape_titular))
    con_banco = bool(cci or texto(datos.banco) or texto(datos.cuenta_titular))
    if not con_yape and not con_banco:
        raise HTTPException(status_code=422, detail="Registra al menos un medio para cobrar: Yape o una cuenta bancaria.")
    if con_yape:
        if not re.fullmatch(r"9\d{8}", celular):
            raise HTTPException(status_code=422, detail="El celular de Yape tiene 9 dígitos y empieza con 9.")
        if len(texto(datos.yape_titular)) < 3:
            raise HTTPException(status_code=422, detail="Escribe el nombre que aparece en tu Yape.")
    if con_banco:
        if texto(datos.banco) not in BANCOS_PERU:
            raise HTTPException(status_code=422, detail="Elige tu banco o caja de la lista.")
        if len(cci) != 20:
            raise HTTPException(status_code=422, detail="El CCI tiene 20 dígitos: lo ves en la app de tu banco o en tu estado de cuenta.")
        if len(texto(datos.cuenta_titular)) < 3:
            raise HTTPException(status_code=422, detail="Escribe el nombre del titular de la cuenta.")
    return {"yape_celular": celular if con_yape else None, "yape_titular": texto(datos.yape_titular) if con_yape else None,
            "banco": texto(datos.banco) if con_banco else None, "cuenta_titular": texto(datos.cuenta_titular) if con_banco else None,
            "cci": cci if con_banco else None}


@app.get("/ganancias/me")
def mis_ganancias(db: Session = Depends(get_db), usuario: dict = Depends(verificar_token),
                  credenciales: HTTPAuthorizationCredentials = Depends(security)):
    """Lo que gana el agricultor o el repartidor: cada venta, envío o recojo con su estado (esperando la entrega, por
    cobrar y en qué lunes se paga, pagado o anulado), los pagos que ya recibió y su cuenta de cobro."""
    yo = _beneficiario_autenticado(usuario, credenciales.credentials)
    liquidaciones = (db.query(models.Liquidacion)
                     .filter(models.Liquidacion.beneficiario_tipo == yo["tipo"], models.Liquidacion.beneficiario_id == yo["id"])
                     .order_by(models.Liquidacion.fecha_creacion.desc()).all())
    entregas = logica_cobros.entregas_de(db, {l.pedido_id for l in liquidaciones if l.beneficiario_tipo == "productor"})
    movimientos, por_dia = [], {}
    totales = {"por_cobrar": Decimal("0"), "esperando_entrega": Decimal("0"), "cobrado": Decimal("0")}
    for l in liquidaciones:
        estado = logica_cobros.estado_de_cobro(l, entregas)
        cumplido = logica_cobros.cumplido_en(l, entregas)
        se_paga_el = logica_cobros.dia_de_pago(cumplido) if estado == "por_cobrar" else None
        neto = Decimal(str(l.monto_neto))
        if estado == "por_cobrar":
            totales["por_cobrar"] += neto
            por_dia[se_paga_el] = por_dia.get(se_paga_el, Decimal("0")) + neto
        elif estado == "esperando_entrega":
            totales["esperando_entrega"] += neto
        elif estado == "pagado":
            totales["cobrado"] += neto
        movimientos.append({
            "id": str(l.id), "pedido_id": str(l.pedido_id), "concepto": logica_cobros.concepto_de(l), "estado": estado,
            "fecha": l.fecha_creacion, "cumplido_en": cumplido, "se_paga_el": se_paga_el, "pagado_el": l.fecha_pago,
            "monto_bruto": float(l.monto_bruto), "comision": float(l.comision_plataforma),
            "descuento_recojo": float(l.descuento_recojo) if l.descuento_recojo else None, "monto_neto": float(neto),
        })
    deuda_recojos = None
    if yo["tipo"] == "productor":
        deudas = db.query(models.DescuentoProductor).filter(models.DescuentoProductor.productor_id == yo["id"]).all()
        deuda_recojos = float(sum((Decimal(str(d.monto)) - Decimal(str(d.aplicado)) for d in deudas), Decimal("0")))
    pagos = (db.query(models.PagoBeneficiario)
             .filter(models.PagoBeneficiario.beneficiario_tipo == yo["tipo"], models.PagoBeneficiario.beneficiario_id == yo["id"])
             .order_by(models.PagoBeneficiario.fecha.desc()).limit(30).all())
    return {
        "beneficiario": {"tipo": yo["tipo"], "id": str(yo["id"]), "nombre": yo["nombre"]},
        "cuenta": _cuenta_salida(_cuenta_de(db, yo["tipo"], yo["id"])),
        "resumen": {**{k: float(v) for k, v in totales.items()}, "deuda_recojos": deuda_recojos},
        "proximos_pagos": [{"fecha": dia, "monto": float(monto)} for dia, monto in sorted(por_dia.items())],
        "movimientos": movimientos[:150],
        "pagos": [_pago_beneficiario_salida(p) for p in pagos],
        "yape_maximo_por_operacion": float(YAPE_MAXIMO_POR_OPERACION),
        "bancos": list(BANCOS_PERU),
    }


@app.put("/ganancias/me/cuenta")
def guardar_mi_cuenta_cobro(datos: CuentaCobroDatos, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token),
                            credenciales: HTTPAuthorizationCredentials = Depends(security)):
    yo = _beneficiario_autenticado(usuario, credenciales.credentials)
    valores = _validar_cuenta_cobro(datos)
    cuenta = _cuenta_de(db, yo["tipo"], yo["id"])
    if not cuenta:
        cuenta = models.CuentaCobro(beneficiario_tipo=yo["tipo"], beneficiario_id=yo["id"], usuario_id=yo["usuario_id"])
        db.add(cuenta)
    cuenta.usuario_id = yo["usuario_id"]
    cuenta.nombre = yo["nombre"]
    for campo, valor in valores.items():
        setattr(cuenta, campo, valor)
    cuenta.fecha_actualizacion = datetime.utcnow()
    db.commit()
    db.refresh(cuenta)
    return _cuenta_salida(cuenta)


@app.get("/ganancias/admin/por-pagar")
def ganancias_por_pagar(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    """Lo que el admin tiene que pagar este lunes a cada agricultor y repartidor (lo cumplido antes del corte), lo que ya
    entra en el pago del lunes siguiente y lo que todavía espera la entrega, con la cuenta de cobro de cada uno."""
    corte = logica_cobros.corte_de_la_semana()
    liquidaciones, entregas = logica_cobros.pendientes(db)
    grupos = {}
    for l in liquidaciones:
        g = grupos.setdefault((l.beneficiario_tipo, l.beneficiario_id), {
            "a_pagar": Decimal("0"), "cantidad_a_pagar": 0, "esta_semana": Decimal("0"), "esperando_entrega": Decimal("0")})
        cumplido, neto = logica_cobros.cumplido_en(l, entregas), Decimal(str(l.monto_neto))
        if cumplido is None:
            g["esperando_entrega"] += neto
        elif cumplido < corte:
            g["a_pagar"] += neto
            g["cantidad_a_pagar"] += 1
        else:
            g["esta_semana"] += neto
    cuentas = {(c.beneficiario_tipo, c.beneficiario_id): c for c in db.query(models.CuentaCobro).all()}
    filas = []
    for (tipo, beneficiario_id), g in grupos.items():
        if g["a_pagar"] + g["esta_semana"] + g["esperando_entrega"] <= 0:
            continue
        cuenta = cuentas.get((tipo, beneficiario_id))
        filas.append({"beneficiario_tipo": tipo, "beneficiario_id": str(beneficiario_id), "nombre": cuenta.nombre if cuenta else None,
                      "cuenta": _cuenta_salida(cuenta), "cantidad_a_pagar": g["cantidad_a_pagar"],
                      **{k: float(g[k]) for k in ("a_pagar", "esta_semana", "esperando_entrega")}})
    filas.sort(key=lambda f: (-f["a_pagar"], -f["esta_semana"]))
    return {"corte": corte, "dia_de_pago": (corte - logica_cobros.HORAS_PERU).date(),
            "yape_maximo_por_operacion": float(YAPE_MAXIMO_POR_OPERACION),
            "total_a_pagar": float(sum((Decimal(str(f["a_pagar"])) for f in filas), Decimal("0"))), "beneficiarios": filas}


class PagoABeneficiario(BaseModel):
    metodo: Literal["yape", "transferencia"]
    numero_operacion: str = Field(min_length=4, max_length=200)
    monto: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    incluir_semana_actual: bool = False


@app.post("/ganancias/admin/{tipo}/{beneficiario_id}/pagar")
def registrar_pago_a_beneficiario(tipo: Literal["productor", "repartidor"], beneficiario_id: uuid.UUID, datos: PagoABeneficiario,
                                  db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    """El admin ya le pagó (por Yape o transferencia, fuera del sistema) y lo registra con el número de operación: todo lo
    cumplido antes del corte (o hasta ahora, si adelanta lo de esta semana) queda pagado y la persona recibe un aviso.
    `monto` es lo que el admin vio en la lista: si cambió entre tanto (se entregó otro pedido), no se registra nada."""
    cuenta = _cuenta_de(db, tipo, beneficiario_id)
    if not cuenta:
        raise HTTPException(status_code=409, detail="Todavía no registró cómo cobrar: pídele que lo haga en \"Mis ganancias\".")
    if datos.metodo == "yape" and not cuenta.yape_celular:
        raise HTTPException(status_code=409, detail="No registró un Yape: págale por transferencia a su cuenta.")
    if datos.metodo == "transferencia" and not cuenta.cci:
        raise HTTPException(status_code=409, detail="No registró una cuenta bancaria: págale por Yape.")
    liquidaciones, entregas = logica_cobros.pendientes(db, tipo, beneficiario_id, bloquear=True)
    ahora = datetime.utcnow()
    hasta = ahora if datos.incluir_semana_actual else logica_cobros.corte_de_la_semana(ahora)
    cubiertas = logica_cobros.cumplidas_antes(liquidaciones, entregas, hasta)
    total = logica_cobros.neto(cubiertas)
    if not cubiertas or total <= 0:
        raise HTTPException(status_code=409, detail="No tiene nada por cobrar en este pago.")
    if total != datos.monto:
        raise HTTPException(status_code=409, detail=f"Lo que se le debe cambió (ahora S/ {total:.2f}): actualiza la lista antes de registrar el pago.")
    operaciones = re.findall(r"[A-Za-z0-9-]{4,}", datos.numero_operacion)
    if not operaciones:
        raise HTTPException(status_code=422, detail="Escribe el número de operación que te dio Yape o tu banco.")
    necesarias = math.ceil(total / YAPE_MAXIMO_POR_OPERACION)
    if datos.metodo == "yape" and len(operaciones) < necesarias:
        raise HTTPException(status_code=422, detail=(
            f"Por Yape se manda hasta S/ {YAPE_MAXIMO_POR_OPERACION:.2f} por operación: para S/ {total:.2f} anota los "
            f"{necesarias} números de operación separados por comas, o págale por transferencia."))
    destino = (f"Yape {cuenta.yape_celular} ({cuenta.yape_titular})" if datos.metodo == "yape"
               else f"{cuenta.banco} · CCI {cuenta.cci} ({cuenta.cuenta_titular})")
    pago = models.PagoBeneficiario(beneficiario_tipo=tipo, beneficiario_id=beneficiario_id, nombre=cuenta.nombre, monto=total,
                                   metodo=datos.metodo, destino=destino, numero_operacion=", ".join(operaciones),
                                   cantidad=len(cubiertas), hasta=hasta, pagado_por=usuario.get("sub"), fecha=ahora)
    db.add(pago)
    db.flush()
    for l in cubiertas:
        l.estado, l.fecha_pago, l.pago_beneficiario_id = "pagado", ahora, pago.id
    registrar_evento(db, "pago_a_beneficiario", {"usuario_id": str(cuenta.usuario_id), "beneficiario_tipo": tipo,
                                                 "monto": _dinero(total), "metodo": datos.metodo,
                                                 "numero_operacion": pago.numero_operacion, "cantidad": len(cubiertas)})
    db.commit()
    db.refresh(pago)
    return _pago_beneficiario_salida(pago)


@app.get("/ganancias/admin/pagos-hechos")
def pagos_hechos_a_beneficiarios(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    pagos = db.query(models.PagoBeneficiario).order_by(models.PagoBeneficiario.fecha.desc()).limit(50).all()
    return [_pago_beneficiario_salida(p) for p in pagos]
