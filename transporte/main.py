import os

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from auth import verificar_token, requiere_rol

from database import Base, engine, SessionLocal
import models
from logica_repartidores import proponer_envio_a_repartidor
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from rabbitmq_publisher import publicar_evento
import math

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
#FUNCIONES AUXILIARES
@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def calcular_distancia_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    R = 6371  # radio de la Tierra en km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

#clases esquemas de entrada

class EstadoEnvio(BaseModel):
    estado: str

class RepartidorCrear(BaseModel):
    nombre: str
    dni: str

class UbicacionActualizar(BaseModel):
    latitud: str
    longitud: str

#endpoint

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "transporte"}

@app.get("/envios")
def listar_envios(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    return db.query(models.Envio).all()

#ENDPOINT PARA VER PROPUESTAS PENDIENTES
@app.get("/envios/propuestas")
def mis_propuestas(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    return db.query(models.Envio).filter(
        models.Envio.repartidor_id == repartidor.id,
        models.Envio.estado == "propuesto"
    ).all()

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")

@app.get("/envios/{envio_id}/ruta")
def obtener_ruta(envio_id: str, db: Session = Depends(get_db)):
    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")

    with httpx.Client() as client:
        try:
            resp_pedido = client.get(f"{PEDIDOS_URL}/pedidos/{envio.pedido_id}", timeout=5)
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Pedidos no disponible")

    if resp_pedido.status_code != 200:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    pedido = resp_pedido.json()

    if not pedido.get("destino_latitud") or not pedido.get("destino_longitud"):
        return {"disponible": False, "mensaje": "Este pedido no tiene coordenadas de destino registradas"}

    if not envio.repartidor_latitud or not envio.repartidor_longitud:
        return {"disponible": False, "mensaje": "El repartidor aún no ha compartido su ubicación"}

    distancia = calcular_distancia_km(
        envio.repartidor_latitud, envio.repartidor_longitud,
        pedido["destino_latitud"], pedido["destino_longitud"],
    )

    return {
        "disponible": True,
        "distancia_km": distancia,
        "tiempo_estimado_min": round(distancia / 25 * 60),  # asumiendo ~25 km/h promedio urbano/rural
        "origen": {"latitud": envio.repartidor_latitud, "longitud": envio.repartidor_longitud},
        "destino": {"latitud": pedido["destino_latitud"], "longitud": pedido["destino_longitud"]},
    }
@app.post("/envios/{envio_id}/aceptar")
def aceptar_propuesta(envio_id: str, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    if str(envio.repartidor_id) != str(repartidor.id):
        raise HTTPException(status_code=403, detail="Esta propuesta no es tuya")
    if envio.estado != "propuesto":
        raise HTTPException(status_code=409, detail="Esta propuesta ya no está disponible")

    envio.estado = "asignado"
    db.commit()
    db.refresh(envio)

    publicar_evento({
        "evento": "envio_actualizado",
        "pedido_id": str(envio.pedido_id),
        "estado": envio.estado,
    })
    return envio


@app.post("/envios/{envio_id}/rechazar")
def rechazar_propuesta(envio_id: str, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    if str(envio.repartidor_id) != str(repartidor.id):
        raise HTTPException(status_code=403, detail="Esta propuesta no es tuya")
    if envio.estado != "propuesto":
        raise HTTPException(status_code=409, detail="Esta propuesta ya no está disponible")

    repartidor.estado_disponibilidad = "disponible"
    envio.repartidor_id = None
    envio.estado = "pendiente_asignacion"
    db.commit()

    proponer_envio_a_repartidor(envio.id, db, excluir_id=repartidor.id)

    return {"mensaje": "Propuesta rechazada, se ofreció a otro repartidor"}

@app.get("/envios/{pedido_id}")
def obtener_envio(pedido_id: str, db: Session = Depends(get_db)):
    envio = db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado para ese pedido")
    return envio

@app.patch("/envios/{envio_id}/estado")
def actualizar_estado(envio_id: str, datos: EstadoEnvio, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    envio.estado = datos.estado
    db.commit()
    db.refresh(envio)

    publicar_evento({
        "evento": "envio_actualizado",
        "pedido_id": str(envio.pedido_id),
        "estado": envio.estado,
    })
    return envio

@app.post("/repartidores")
def crear_repartidor(datos: RepartidorCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    if not datos.dni.isdigit() or len(datos.dni) != 8:
        raise HTTPException(status_code=422, detail="El DNI debe tener exactamente 8 dígitos numéricos")

    usuario_id = usuario.get("sub")
    existente = db.query(models.Repartidor).filter(models.Repartidor.usuario_id == usuario_id).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya tienes un perfil de repartidor registrado")

    nuevo = models.Repartidor(
        usuario_id=usuario_id,
        nombre=datos.nombre,
        dni=datos.dni,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


@app.get("/repartidores/me")
def mi_perfil_repartidor(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor. Créalo primero.")
    return repartidor

@app.patch("/envios/{envio_id}/ubicacion")
def actualizar_ubicacion(envio_id: str, datos: UbicacionActualizar, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    if str(envio.repartidor_id) != str(repartidor.id):
        raise HTTPException(status_code=403, detail="No puedes actualizar la ubicación de un envío que no es tuyo")
    if envio.estado not in ["asignado", "en_camino"]:
        raise HTTPException(status_code=409, detail="Este envío no está en un estado que permita actualizar ubicación")

    envio.repartidor_latitud = datos.latitud
    envio.repartidor_longitud = datos.longitud
    db.commit()
    db.refresh(envio)
    return envio
