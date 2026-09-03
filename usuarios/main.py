import os

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, EmailStr
from auth import verificar_token

from database import Base, engine, SessionLocal
import models
from seguridad import hashear_password, verificar_password, crear_token

Base.metadata.create_all(bind=engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class UsuarioRegistro(BaseModel):
    nombre: str
    email: EmailStr
    password: str
    rol: str = "comprador"

class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str

SERVICIO_SECRETO = os.getenv("SERVICIO_SECRETO", "clave-interna-servicios")

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "usuarios"}

@app.post("/usuarios/registro")
def registrar(datos: UsuarioRegistro, db: Session = Depends(get_db)):
    nuevo = models.Usuario(
        nombre=datos.nombre,
        email=datos.email,
        password_hash=hashear_password(datos.password),
        rol=datos.rol,
    )
    db.add(nuevo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ese email ya está registrado")
    db.refresh(nuevo)
    return {"id": nuevo.id, "nombre": nuevo.nombre, "email": nuevo.email, "rol": nuevo.rol}

@app.post("/usuarios/login")
def login(datos: UsuarioLogin, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.email == datos.email).first()
    if not usuario or not verificar_password(datos.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    token = crear_token(str(usuario.id), usuario.rol)
    return {"access_token": token, "token_type": "bearer"}

# Usado por el Middleware ForwardAuth del API Gateway (Traefik) para validar el JWT
# ANTES de que la petición llegue a cualquier microservicio (ver k8s/traefik-middlewares.yaml).
# IMPORTANTE: esto es una capa adicional de rechazo temprano, no un reemplazo de la validación
# que cada microservicio sigue haciendo con su propio Depends(verificar_token)/requiere_rol().
# Debe registrarse ANTES de "/usuarios/{usuario_id}" para que FastAPI no confunda
# "validar-token" con un usuario_id.
@app.get("/usuarios/validar-token")
def validar_token(response: Response, usuario: dict = Depends(verificar_token)):
    # verificar_token ya lanza 401 si el token es inválido/expirado, así que llegar aquí implica válido.
    # Se exponen sub/rol como headers de respuesta: Traefik puede reenviarlos al microservicio de
    # destino (authResponseHeaders) como conveniencia, pero ningún microservicio debe confiar en ellos
    # como fuente de autenticación — siguen validando el JWT por su cuenta.
    response.headers["X-Usuario-Id"] = str(usuario.get("sub", ""))
    response.headers["X-Usuario-Rol"] = str(usuario.get("rol", ""))
    return {"valido": True}

@app.get("/usuarios/{usuario_id}")
def obtener_usuario(usuario_id: str, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"id": usuario.id, "nombre": usuario.nombre, "email": usuario.email, "rol": usuario.rol}

@app.delete("/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != SERVICIO_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    db.delete(usuario)
    db.commit()
    return {"mensaje": "Usuario eliminado"}