from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
import os
import json
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
from auth import verificar_token, requiere_rol
from culqi_client import crear_cargo
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from rabbitmq_publisher import publicar_evento
from logica_liquidaciones import crear_liquidaciones, pagos_sin_liquidaciones
from reintento_liquidaciones import lanzar_reintento_liquidaciones_en_hilo

logger = logging.getLogger("pagos.main")

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
PAGOS_A_PRODUCTOS_SECRETO = os.getenv("PAGOS_A_PRODUCTOS_SECRETO")  # para llamar a reponer_stock en Productos
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_pago (DELETE de limpieza QA)

Base.metadata.create_all(bind=engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def compensar_stock(items_json: str):
    if not items_json:
        print("[Pagos] No hay items guardados para este pago, no se puede compensar stock")
        return

    items = json.loads(items_json)
    with httpx.Client() as client:
        for item in items:
            try:
                client.post(
                    f"{PRODUCTOS_URL}/productos/{item['producto_id']}/stock/reponer",
                    json={"cantidad": item["cantidad"]},
                    headers={"X-Servicio-Secreto": PAGOS_A_PRODUCTOS_SECRETO},
                    timeout=5,
                )
                print(f"[Pagos] Stock repuesto: {item['cantidad']} unidades de {item['producto_id']}")
            except httpx.RequestError as e:
                print(f"[Pagos] ERROR al compensar stock de {item['producto_id']}: {e}")

class ProcesarPago(BaseModel):
    pedido_id: str
    token_culqi: str
    email: str

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "pagos"}

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

@app.get("/pagos/{pedido_id}")
def obtener_pago(pedido_id: str, db: Session = Depends(get_db)):
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    return pago

@app.delete("/pagos/{pago_id}")
def eliminar_pago(pago_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    pago = db.query(models.Pago).filter(models.Pago.id == pago_id).first()
    if not pago:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    db.delete(pago)
    db.commit()
    return {"mensaje": "Pago eliminado"}

@app.post("/pagos/procesar")
def procesar_pago(datos: ProcesarPago, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == datos.pedido_id).first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    if pago.estado == "aprobado":
        raise HTTPException(status_code=409, detail="Este pedido ya fue pagado")

    resultado = crear_cargo(
        token=datos.token_culqi,
        monto_soles=float(pago.monto),
        email=datos.email,
        descripcion=f"Pedido {pago.pedido_id}",
    )

    if resultado["status_code"] == 201:
        pago.estado = "aprobado"
        pago.culqi_charge_id = resultado["data"].get("id")
        db.commit()
        publicar_evento({
            "evento": "pago_confirmado",
            "pedido_id": str(pago.pedido_id),
        })

        # Separado del commit de arriba a propósito — ver el docstring de crear_liquidaciones: el
        # cargo ya se hizo y el pago ya quedó "aprobado", así que un fallo acá no debe deshacer
        # ni ocultar eso. Se degrada a "faltan liquidaciones, revisar a mano" en vez de fingir que
        # el pago no se confirmó.
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

        return {"estado": "aprobado", "detalle": resultado["data"]}
    else:
        pago.estado = "rechazado"
        db.commit()
        compensar_stock(pago.items)
        publicar_evento({
            "evento": "pago_rechazado",
            "pedido_id": str(pago.pedido_id),
    })
    raise HTTPException(
        status_code=402,
        detail=resultado["data"].get("user_message", "El pago fue rechazado"),
    )