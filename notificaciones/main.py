import os
import uuid

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from auth import security, verificar_token
from auth import secreto_de_servicio_valido

import avisos_externos

from database import Base, engine, SessionLocal
import models
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from migraciones import agregar_columnas_faltantes

Base.metadata.create_all(bind=engine)
agregar_columnas_faltantes(engine, Base)

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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py). Usada SOLO
# por el DELETE de limpieza QA de este servicio — ningún otro servicio lo llama.
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "notificaciones"}

@app.delete("/notificaciones/{notificacion_id}")
def eliminar_notificacion(notificacion_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    notificacion = db.query(models.Notificacion).filter(models.Notificacion.id == notificacion_id).first()
    if not notificacion:
        raise HTTPException(status_code=404, detail="Notificación no encontrada")

    db.delete(notificacion)
    db.commit()
    return {"mensaje": "Notificación eliminada"}

# Solo los avisos del propio usuario sobre ese pedido (el admin ve todos): antes cualquier usuario con sesión
# leía las notificaciones de CUALQUIER pedido con solo conocer su id.
@app.get("/notificaciones/{pedido_id}")
def historial_por_pedido(pedido_id: uuid.UUID, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    consulta = db.query(models.Notificacion).filter(models.Notificacion.pedido_id == pedido_id)
    if usuario.get("rol") != "admin":
        consulta = consulta.filter(models.Notificacion.usuario_id == usuario.get("sub"))
    return consulta.all()


@app.get("/notificaciones")
def listar_todas(db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    return db.query(models.Notificacion).filter(
        models.Notificacion.usuario_id == usuario.get("sub")
    ).order_by(models.Notificacion.fecha_envio.desc()).all()


# ============ H6: avisos al celular (Web Push) y, de respaldo, por correo ============
# Ver avisos_externos.py. Todas estas rutas tienen dos segmentos después de /notificaciones, así que no chocan con
# GET/DELETE /notificaciones/{id}.

class ClavesPush(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class SuscripcionPushEntrada(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000)
    keys: ClavesPush


class EndpointPush(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000)


class PreferenciaCorreoEntrada(BaseModel):
    activo: bool


def _estado_de_avisos(db: Session, usuario_id) -> dict:
    preferencia = db.query(models.PreferenciaCorreo).filter(models.PreferenciaCorreo.usuario_id == usuario_id).first()
    return {
        "dispositivos": db.query(models.SuscripcionPush).filter(models.SuscripcionPush.usuario_id == usuario_id).count(),
        "correo": preferencia.correo if preferencia else None,
    }


@app.get("/notificaciones/avisos/configuracion")
def configuracion_de_avisos():
    """Qué canales están encendidos en este despliegue. La clave pública VAPID es pública por diseño: el navegador la
    necesita para suscribirse."""
    push = avisos_externos.push_disponible()
    return {"push": push, "clave_publica": avisos_externos.VAPID_CLAVE_PUBLICA if push else None,
            "correo": avisos_externos.correo_disponible()}


@app.get("/notificaciones/avisos/mios")
def mis_avisos(db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    return _estado_de_avisos(db, usuario.get("sub"))


@app.post("/notificaciones/avisos/push")
def activar_avisos_en_este_navegador(datos: SuscripcionPushEntrada, db: Session = Depends(get_db),
                                     usuario: dict = Depends(verificar_token)):
    if not avisos_externos.push_disponible():
        raise HTTPException(status_code=503, detail="Los avisos al celular todavía no están encendidos en ChakraShop.")
    if not avisos_externos.endpoint_valido(datos.endpoint):
        raise HTTPException(status_code=422, detail="Este navegador no da una dirección de avisos válida.")
    usuario_id = usuario.get("sub")
    existente = db.query(models.SuscripcionPush).filter(models.SuscripcionPush.endpoint == datos.endpoint).first()
    if existente:
        # El mismo navegador: puede ser otra cuenta que entró en él; desde ahora los avisos son de esta.
        existente.usuario_id, existente.p256dh, existente.auth = usuario_id, datos.keys.p256dh, datos.keys.auth
    else:
        db.add(models.SuscripcionPush(usuario_id=usuario_id, endpoint=datos.endpoint, p256dh=datos.keys.p256dh,
                                      auth=datos.keys.auth))
    db.flush()
    propias = (db.query(models.SuscripcionPush).filter(models.SuscripcionPush.usuario_id == usuario_id)
               .order_by(models.SuscripcionPush.fecha_creacion.desc()).all())
    for vieja in propias[avisos_externos.MAXIMO_DISPOSITIVOS:]:
        db.delete(vieja)
    db.commit()
    return _estado_de_avisos(db, usuario_id)


@app.post("/notificaciones/avisos/push/quitar")
def quitar_avisos_de_este_navegador(datos: EndpointPush, db: Session = Depends(get_db)):
    """Sin sesión a propósito: el frontend lo llama al cerrar sesión (el token ya se borró), para que el siguiente que
    use ese celular no reciba los avisos de la cuenta anterior. Solo el navegador conoce su endpoint."""
    borradas = db.query(models.SuscripcionPush).filter(models.SuscripcionPush.endpoint == datos.endpoint).delete()
    db.commit()
    return {"quitadas": borradas}


@app.put("/notificaciones/avisos/correo")
def cambiar_aviso_por_correo(datos: PreferenciaCorreoEntrada, db: Session = Depends(get_db),
                             usuario: dict = Depends(verificar_token),
                             credenciales: HTTPAuthorizationCredentials = Depends(security)):
    usuario_id = usuario.get("sub")
    preferencia = db.query(models.PreferenciaCorreo).filter(models.PreferenciaCorreo.usuario_id == usuario_id).first()
    if not datos.activo:
        if preferencia:
            db.delete(preferencia)
            db.commit()
        return _estado_de_avisos(db, usuario_id)
    if not avisos_externos.correo_disponible():
        raise HTTPException(status_code=503, detail="Los avisos por correo todavía no están encendidos en ChakraShop.")
    correo = avisos_externos.correo_del_usuario(usuario_id, credenciales.credentials)
    if not correo:
        raise HTTPException(status_code=503, detail="No pudimos leer el correo de tu cuenta. Intenta de nuevo en unos momentos.")
    if preferencia:
        preferencia.correo = correo
    else:
        db.add(models.PreferenciaCorreo(usuario_id=usuario_id, correo=correo))
    db.commit()
    return _estado_de_avisos(db, usuario_id)


@app.post("/notificaciones/avisos/prueba")
def probar_avisos(db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    """Un aviso de prueba a los navegadores activos del usuario, para que vea cómo le llegan."""
    usuario_id = usuario.get("sub")
    if not db.query(models.SuscripcionPush).filter(models.SuscripcionPush.usuario_id == usuario_id).first():
        raise HTTPException(status_code=409, detail="Primero activa los avisos en este celular.")
    llegaron = avisos_externos.enviar_push(db, usuario_id, "ChakraShop", "Así te llegarán los avisos de tus pedidos y reservas.")
    if not llegaron:
        raise HTTPException(status_code=502, detail="No pudimos mandar el aviso de prueba. Vuelve a activar los avisos en este celular.")
    return {"enviados": llegaron}
