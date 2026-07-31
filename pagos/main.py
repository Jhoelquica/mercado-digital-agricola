from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import Base, engine, SessionLocal
import models
from auth import verificar_token
from culqi_client import crear_cargo
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

@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ProcesarPago(BaseModel):
    pedido_id: str
    token_culqi: str
    email: str

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "pagos"}

@app.get("/pagos/{pedido_id}")
def obtener_pago(pedido_id: str, db: Session = Depends(get_db)):
    pago = db.query(models.Pago).filter(models.Pago.pedido_id == pedido_id).first()
    if not pago:
        raise HTTPException(status_code=404, detail="No hay registro de pago para ese pedido")
    return pago

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
        return {"estado": "aprobado", "detalle": resultado["data"]}
    else:
        pago.estado = "rechazado"
        db.commit()
        publicar_evento({
            "evento": "pago_rechazado",
            "pedido_id": str(pago.pedido_id),
        })
        raise HTTPException(
            status_code=402,
            detail=resultado["data"].get("user_message", "El pago fue rechazado"),
        )