from prometheus_fastapi_instrumentator import Instrumentator
import os
import math
import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
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

class AlmacenActualizar(BaseModel):
    latitud: float
    longitud: float

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

    # El costo de envío se calcula acá (no depende de los items, así que no hace falta esperar a
    # validarlos) contra la ubicación ACTUAL del almacén — nunca hardcodeada, así un cambio del
    # Admin en /configuracion/almacen afecta a los pedidos nuevos sin tocar código.
    almacen = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_ALMACEN
    ).first()
    if not almacen:
        # No debería pasar nunca en la práctica (sembrar_configuracion_almacen corre al arrancar
        # cada réplica), pero sin esto un pedido se crearía con un costo de envío inventado.
        raise HTTPException(status_code=503, detail="No se pudo calcular el costo de envío: falta la configuración del almacén")

    distancia_envio_km = calcular_distancia_km(
        almacen.valor_latitud, almacen.valor_longitud,
        datos.destino_latitud, datos.destino_longitud,
    )
    costo_envio = calcular_costo_envio(distancia_envio_km)

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
            costo_envio=costo_envio,
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
        "costo_envio": float(nuevo_pedido.costo_envio),
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