from prometheus_fastapi_instrumentator import Instrumentator
from fastapi.encoders import jsonable_encoder
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_validator
import os
import re
import json
import math
import uuid
from datetime import datetime
import threading
import time
import httpx
import logging

# Sin esto, el proceso nunca queda con un handler configurado y Python cae al "handler de
# último recurso": solo WARNING y superior salen por stderr, cualquier logger.info() de acá o de
# cualquier módulo que importemos (ej. reintento_liquidaciones, cuyo hilo corre en este mismo
# proceso) queda mudo sin ningún aviso ni error — simplemente no aparece en `kubectl logs`. Va
# antes de crear cualquier logger o importar cualquier módulo del proyecto a propósito: el nivel
# efectivo de un logger se resuelve recién cuando se llama .info()/.error(), así que en principio
# el orden no sería crítico, pero dejarlo primero evita tener que razonar sobre eso cada vez.
logging.basicConfig(level=logging.INFO)

from database import Base, engine, SessionLocal
import models
from auth import verificar_token, requiere_rol, security
from auth import secreto_de_servicio_valido
from fastapi.security import HTTPAuthorizationCredentials
from culqi_client import crear_cargo
from rabbitmq_consumer import lanzar_consumidor_en_hilo, PLAZO_PARA_PAGAR_MINUTOS
from rabbitmq_publisher import publicar_evento
from logica_liquidaciones import crear_liquidaciones, crear_liquidacion_repartidor, pagos_sin_liquidaciones
from logica_liquidaciones import COMISION_PLATAFORMA_PORCENTAJE
from reintento_liquidaciones import lanzar_reintento_liquidaciones_en_hilo
from migraciones import agregar_columnas_faltantes
from imagenes import leer_imagen_validada, LIMITE_CUERPO_BYTES, MENSAJE_MUY_GRANDE
from minio_client import subir_constancia, leer_constancia
from fastapi.responses import JSONResponse

logger = logging.getLogger("pagos.main")

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
PAGOS_A_PRODUCTOS_SECRETO = os.getenv("PAGOS_A_PRODUCTOS_SECRETO")  # para llamar a reponer_stock en Productos
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_pago (DELETE de limpieza QA)
TRANSPORTE_A_PAGOS_SECRETO = os.getenv("TRANSPORTE_A_PAGOS_SECRETO")  # para /pagos/interno/liquidar-repartidor

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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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


def compensar_stock(items_json: str):
    """Devuelve a Productos el stock reservado por un pedido, en la MISMA unidad y con el mismo redondeo con que
    se descontó (suma por producto, redondeada hacia arriba). Antes se devolvía la cantidad en la unidad que
    eligió el comprador: 3 sacos de 10 kg descontaban 30 kg y devolvían 3."""
    cantidades = _cantidades_base_por_producto(items_json)
    if not cantidades:
        print("[Pagos] No hay items guardados para este pago, no se puede compensar stock")
        return

    with httpx.Client() as client:
        for producto_id, cantidad in cantidades.items():
            reponer = math.ceil(cantidad)
            try:
                resp = client.post(
                    f"{PRODUCTOS_URL}/productos/{producto_id}/stock/reponer",
                    json={"cantidad": reponer},
                    headers={"X-Servicio-Secreto": PAGOS_A_PRODUCTOS_SECRETO or ""},
                    timeout=5,
                )
                if resp.status_code == 200:
                    print(f"[Pagos] Stock repuesto: {reponer} de {producto_id}")
                else:
                    logger.error("Productos rechazó reponer %s de %s (HTTP %s)", reponer, producto_id, resp.status_code)
            except httpx.RequestError as e:
                print(f"[Pagos] ERROR al compensar stock de {producto_id}: {e}")


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
                headers={"X-Servicio-Secreto": PAGOS_A_PRODUCTOS_SECRETO or ""},
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
    # Lo que el comprador tiene que pagar ahora (nada si ya está todo pagado).
    datos["parte_a_pagar"] = None if pago.estado == "aprobado" else parte
    datos["monto_a_pagar"] = None if pago.estado == "aprobado" else float(monto_de(pago, parte))
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
MOTIVO_VENCIDO = f"Pasaron {PLAZO_PARA_PAGAR_MINUTOS} minutos sin pagar y los productos volvieron al catálogo"


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
    db.commit()
    for pago in vencidos:
        compensar_stock(pago.items)
        try:
            publicar_evento({"evento": "pago_rechazado", "pedido_id": str(pago.pedido_id), "motivo": MOTIVO_VENCIDO, "vencido": True})
        except Exception:
            logger.error("No se pudo avisar que venció el pago del pedido %s", pago.pedido_id, exc_info=True)
    if vencidos:
        logger.info("Vencieron %d pedido(s) sin pagar: su stock volvió al catálogo", len(vencidos))
    return len(vencidos)


def _bucle_vencimientos():
    while True:
        db = SessionLocal()
        try:
            vencer_pagos_sin_pagar(db)
        except Exception:
            db.rollback()
            logger.error("El ciclo de vencimiento de pagos falló, reintenta en %ds", INTERVALO_VENCIMIENTOS_SEGUNDOS, exc_info=True)
        finally:
            db.close()
        time.sleep(INTERVALO_VENCIMIENTOS_SEGUNDOS)


def _registrar_parte_pagada(db: Session, pago, parte: str, metodo: str, cargo_id=None) -> None:
    """Un cobro aprobado (Culqi o transferencia que aprobó un admin), según la parte:
    - total: como siempre -> aprobado, pago_confirmado y liquidaciones.
    - adelanto: el pedido se confirma y sube al camión (pago_confirmado con adelanto=True); las liquidaciones esperan
      al saldo, porque el pedido todavía no está pagado entero.
    - saldo: aprobado, evento saldo_pagado (el chofer ya puede descargar) y recién ahí las liquidaciones."""
    ahora = datetime.utcnow()
    if parte == "adelanto":
        pago.estado = "adelanto_pagado"
        pago.fecha_adelanto = ahora
        pago.culqi_charge_id = cargo_id or pago.culqi_charge_id
        pago.parte_en_revision = None
        db.commit()
        publicar_evento({"evento": "pago_confirmado", "pedido_id": str(pago.pedido_id), "adelanto": True,
                         "monto": _dinero(monto_de(pago, "adelanto")), "saldo": _dinero(monto_de(pago, "saldo"))})
        return
    if parte == "saldo":
        pago.estado = "aprobado"
        pago.metodo_saldo = metodo
        pago.culqi_charge_id_saldo = cargo_id
        pago.fecha_saldo = ahora
        pago.parte_en_revision = None
        db.commit()
        publicar_evento({"evento": "saldo_pagado", "pedido_id": str(pago.pedido_id), "monto": _dinero(monto_de(pago, "saldo"))})
        _crear_liquidaciones_sin_romper(db, pago)
        return
    pago.estado = "aprobado"
    pago.culqi_charge_id = cargo_id or pago.culqi_charge_id
    pago.parte_en_revision = None
    _confirmar_pago_aprobado(db, pago)


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


def _confirmar_pago_aprobado(db: Session, pago) -> None:
    """Lo que sigue a un pago aprobado, venga de Culqi o de una transferencia que aprobó un admin: se guarda, se
    avisa (pago_confirmado: Pedidos lo marca confirmado y Transporte crea el envío) y se crean las liquidaciones."""
    db.commit()
    publicar_evento({
        "evento": "pago_confirmado",
        "pedido_id": str(pago.pedido_id),
        "monto": _dinero(pago.monto),
    })

    # Separado del commit de arriba a propósito — ver el docstring de crear_liquidaciones: el
    # pago ya quedó "aprobado", así que un fallo acá no debe deshacer ni ocultar eso. Se degrada
    # a "faltan liquidaciones, revisar a mano" en vez de fingir que el pago no se confirmó.
    _crear_liquidaciones_sin_romper(db, pago)


@app.post("/pagos/procesar")
def procesar_pago(
    datos: ProcesarPago,
    db: Session = Depends(get_db),
    usuario: dict = Depends(verificar_token),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    _exigir_dueno_para_pagar(datos.pedido_id, usuario, credenciales.credentials)

    # La fila del pago se bloquea durante TODO el intento, llamada a Culqi incluida: dos intentos simultáneos
    # (doble clic, dos pestañas) se turnan y el segundo ve el resultado del primero. Antes los dos veían
    # "pendiente" y los dos cobraban.
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == datos.pedido_id).with_for_update().first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    if pago.estado == "aprobado":
        raise HTTPException(status_code=409, detail="Este pedido ya fue pagado")
    if pago.estado == "en_revision":
        raise HTTPException(status_code=409, detail="Ya enviaste la constancia de una transferencia para este pedido: está en revisión.")
    parte = parte_a_cobrar(pago)
    monto = monto_de(pago, parte)
    # Antes de reservar stock o llamar a Culqi: un Yape por encima del límite lo rechazaría la pasarela igual.
    if datos.metodo == "yape" and monto > YAPE_MAXIMO_POR_OPERACION:
        raise HTTPException(
            status_code=422,
            detail=f"Yape permite pagar hasta S/ {YAPE_MAXIMO_POR_OPERACION:.2f} por operación y este pago es de "
                   f"S/ {monto:.2f}. Paga con tarjeta.",
        )
    if parte == "saldo":
        return _cobrar_saldo(db, pago, datos, monto)
    pago.metodo = datos.metodo

    # Un pago "rechazado" ya devolvió su stock (compensar_stock). Reintentarlo vuelve a reservarlo ANTES de
    # cobrar: si ya no alcanza, 409 y no se cobra nada. Si el reintento vuelve a fallar, se libera solo lo
    # reservado para este intento y el pago sigue rechazado (el stock se devuelve una sola vez, no una por
    # intento fallido: con una tarjeta mala repetida el stock del producto subía sin límite).
    es_reintento = pago.estado == "rechazado"
    if es_reintento:
        reservar_stock(pago.items)

    try:
        resultado = crear_cargo(
            token=datos.token_culqi,
            monto_soles=float(monto),
            email=datos.email,
            descripcion=f"Pedido {pago.pedido_id}" + (" (adelanto)" if parte == "adelanto" else ""),
        )
    except (httpx.RequestError, ValueError):
        # La pasarela no respondió (o respondió algo ilegible): no se cobró nada, el pago queda como estaba.
        if es_reintento:
            compensar_stock(pago.items)
        logger.error("La pasarela de pagos no respondió al cobrar el pedido %s", pago.pedido_id, exc_info=True)
        raise HTTPException(status_code=502, detail="No se pudo comunicar con la pasarela de pagos. Inténtalo de nuevo en unos momentos.")

    if resultado["status_code"] == 201:
        _registrar_parte_pagada(db, pago, parte, datos.metodo, resultado["data"].get("id"))
        return {"estado": pago.estado, "parte": parte, "detalle": resultado["data"]}
    else:
        if not es_reintento:
            pago.estado = "rechazado"
            db.commit()
        compensar_stock(pago.items)
        publicar_evento({
            "evento": "pago_rechazado",
            "pedido_id": str(pago.pedido_id),
            "motivo": resultado["data"].get("user_message"),
        })
    raise HTTPException(
        status_code=402,
        detail=resultado["data"].get("user_message", "El pago fue rechazado"),
    )


def _cobrar_saldo(db: Session, pago, datos, monto: Decimal):
    """El 30% de un pedido en camión. Si falla NO se toca el stock ni se cancela nada: el pedido ya va en el camión y el
    comprador solo tiene que volver a intentar."""
    try:
        resultado = crear_cargo(token=datos.token_culqi, monto_soles=float(monto), email=datos.email,
                                descripcion=f"Pedido {pago.pedido_id} (saldo)")
    except (httpx.RequestError, ValueError):
        logger.error("La pasarela de pagos no respondió al cobrar el saldo del pedido %s", pago.pedido_id, exc_info=True)
        raise HTTPException(status_code=502, detail="No se pudo comunicar con la pasarela de pagos. Inténtalo de nuevo en unos momentos.")
    if resultado["status_code"] == 201:
        _registrar_parte_pagada(db, pago, "saldo", datos.metodo, resultado["data"].get("id"))
        return {"estado": pago.estado, "parte": "saldo", "detalle": resultado["data"]}
    db.rollback()
    raise HTTPException(status_code=402, detail=resultado["data"].get("user_message", "El pago del saldo fue rechazado"))


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
    if pago.estado == "en_revision":
        raise HTTPException(status_code=409, detail="Ya enviaste una constancia para este pedido: está en revisión.")

    parte = parte_a_cobrar(pago)
    # Un pago rechazado ya devolvió su stock: se vuelve a reservar antes (mismo criterio que reintentar con tarjeta).
    es_reintento = pago.estado == "rechazado"
    if es_reintento:
        reservar_stock(pago.items)
    try:
        objeto = subir_constancia(datos, tipo)
    except Exception:
        if es_reintento:
            compensar_stock(pago.items)
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
        db.commit()
        publicar_evento({"evento": "saldo_rechazado", "pedido_id": str(pago.pedido_id), "motivo": datos.motivo})
        return _transferencia_resumen(pago, parte)
    pago.estado = "rechazado"
    db.commit()
    compensar_stock(pago.items)
    publicar_evento({"evento": "pago_rechazado", "pedido_id": str(pago.pedido_id), "motivo": datos.motivo})
    return _transferencia_resumen(pago, parte)

