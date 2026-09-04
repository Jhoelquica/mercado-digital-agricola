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
    destino_latitud: str | None = None
    destino_longitud: str | None = None
    items: list[ItemPedido]

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "pedidos"}

@app.post("/pedidos")
def crear_pedido(datos: PedidoCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("comprador"))):
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