from prometheus_fastapi_instrumentator import Instrumentator
import os
import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from rabbitmq_publisher import publicar_evento
from auth import verificar_token
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from auth import verificar_token, requiere_rol

from database import Base, engine, SessionLocal
import models

Base.metadata.create_all(bind=engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

# evento de aranque
@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
PEDIDOS_A_PRODUCTOS_SECRETO = os.getenv("PEDIDOS_A_PRODUCTOS_SECRETO")  # para llamar a descontar_stock en Productos
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_pedido (DELETE de limpieza QA)

# Rango aproximado del departamento de Ayacucho — el pedido solo se acepta si el destino de
# entrega cae dentro de esta caja (no es el polígono real del departamento, es un rectángulo que
# lo contiene; suficiente para la validación que se pidió, sin traer una librería de geometría).
AYACUCHO_LAT_MIN = -15.20
AYACUCHO_LAT_MAX = -12.85
AYACUCHO_LNG_MIN = -75.10
AYACUCHO_LNG_MAX = -73.00

MONTO_MINIMO_PEDIDO = 30.00  # soles, sobre el subtotal (precio × cantidad de cada item, sin envío)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ItemPedido(BaseModel):
    producto_id: str
    cantidad: int

class PedidoCrear(BaseModel):
    comprador_nombre: str
    comprador_telefono: str | None = None
    destino_latitud: float
    destino_longitud: float
    items: list[ItemPedido]

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "pedidos"}

@app.post("/pedidos")
def crear_pedido(datos: PedidoCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("comprador"))):
    # Validación 1 (fail-fast, antes de tocar la base de datos): el destino de entrega debe caer
    # dentro de la región de Ayacucho.
    if not (AYACUCHO_LAT_MIN <= datos.destino_latitud <= AYACUCHO_LAT_MAX) or \
       not (AYACUCHO_LNG_MIN <= datos.destino_longitud <= AYACUCHO_LNG_MAX):
        raise HTTPException(
            status_code=400,
            detail="El destino del pedido debe estar dentro de la región de Ayacucho.",
        )

    items_validados = []

    with httpx.Client() as client:
        for item in datos.items:
            try:
                resp = client.get(f"{PRODUCTOS_URL}/productos/{item.producto_id}", timeout=5)
            except httpx.RequestError:
                raise HTTPException(status_code=503, detail="Servicio de Productos no disponible")

            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail=f"Producto {item.producto_id} no existe")

            producto = resp.json()
            if producto["stock"] < item.cantidad:
                raise HTTPException(status_code=409, detail=f"Stock insuficiente para {producto['nombre']}")

            items_validados.append((item, producto))

        # Validación 2 (fail-fast, todavía antes de tocar la base de datos): el subtotal (precio ×
        # cantidad de cada item, sin contar envío) debe alcanzar el mínimo de compra.
        subtotal = sum(producto["precio"] * item.cantidad for item, producto in items_validados)
        if subtotal < MONTO_MINIMO_PEDIDO:
            raise HTTPException(
                status_code=400,
                detail="El monto mínimo de compra es S/30.00. Agrega más productos para continuar.",
            )

        # Todo validado: ahora sí descontamos stock y creamos el pedido
        nuevo_pedido = models.Pedido(
            usuario_id=usuario.get("sub"),
            comprador_nombre=datos.comprador_nombre,
            comprador_telefono=datos.comprador_telefono,
            destino_latitud=datos.destino_latitud,
            destino_longitud=datos.destino_longitud,
        )
        db.add(nuevo_pedido)
        db.flush()  # genera el id del pedido sin cerrar la transacción todavía

        for item, producto in items_validados:
            client.patch(
                f"{PRODUCTOS_URL}/productos/{item.producto_id}/stock",
                json={"cantidad": item.cantidad},
                headers={"X-Servicio-Secreto": PEDIDOS_A_PRODUCTOS_SECRETO},
                timeout=5,
            )
            nuevo_item = models.PedidoItem(
                pedido_id=nuevo_pedido.id,
                producto_id=item.producto_id,
                cantidad=item.cantidad,
                precio_unitario=producto["precio"],
            )
            db.add(nuevo_item)

    db.commit()
    db.refresh(nuevo_pedido)
    publicar_evento({
        "evento": "pedido_creado",
        "pedido_id": str(nuevo_pedido.id),
        "usuario_id": str(usuario.get("sub")),
        "comprador_nombre": nuevo_pedido.comprador_nombre,
        "items": [
            {
                "producto_id": str(i.producto_id),
                "cantidad": i.cantidad,
                "precio_unitario": float(i.precio_unitario),
            }
            for i in nuevo_pedido.items
        ],
    })
    return nuevo_pedido

@app.get("/pedidos/{pedido_id}")
def obtener_pedido(pedido_id: str, db: Session = Depends(get_db)):
    pedido = db.query(models.Pedido).filter(models.Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    pedido.items  # fuerza la carga de la relación para que se incluya en la respuesta
    return pedido

@app.delete("/pedidos/{pedido_id}")
def eliminar_pedido(pedido_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    pedido = db.query(models.Pedido).filter(models.Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    db.query(models.PedidoItem).filter(models.PedidoItem.pedido_id == pedido_id).delete()
    db.delete(pedido)
    db.commit()
    return {"mensaje": "Pedido eliminado"}