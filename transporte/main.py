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

#clases esquemas de entrada

class EstadoEnvio(BaseModel):
    estado: str

class RepartidorCrear(BaseModel):
    nombre: str
    dni: str

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
