import os
from datetime import datetime

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, EmailStr
from auth import verificar_token, requiere_rol

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

class InvitacionCrear(BaseModel):
    rol_destino: str

# Sin fallback hardcodeado a propósito: si el Deployment se olvida de wirear esta env var, el
# endpoint queda inutilizable (rechaza todo) en vez de aceptar en silencio un valor conocido.
# Usada SOLO por los DELETE de limpieza QA (ningún otro servicio llama a este endpoint) — ver
# inventario completo de X-Servicio-Secreto en el resumen de esta tarea.
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")

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
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    db.delete(usuario)
    db.commit()
    return {"mensaje": "Usuario eliminado"}


# ============ Admin: invitaciones por código de un solo uso ============
# requiere_rol("admin") ya funciona sin cambios: verifica `usuario.get("rol") not in roles_permitidos`
# contra el `rol` que viaja en el JWT (lo pone crear_token en el login). Solo faltaba que
# existiera un usuario real con rol "admin" y algún endpoint que lo exija — esto es eso.

def _serializar_invitacion(inv: models.Invitacion) -> dict:
    return {
        "id": inv.id,
        "codigo": inv.codigo,
        "rol_destino": inv.rol_destino,
        "usado": inv.usado,
        "usado_por": inv.usado_por,
        "creado_por": inv.creado_por,
        "fecha_creacion": inv.fecha_creacion,
        "fecha_expiracion": inv.fecha_expiracion,
    }


def validar_codigo_invitacion(db: Session, codigo: str) -> dict:
    """Chequeo reusable: ¿el código existe, no está usado y no expiró? Devuelve el rol_destino
    que habilita. Pensado para que /usuarios/registro lo llame directo (mismo proceso) en la
    sub-entrega B — todavía NO está conectado a registro."""
    inv = db.query(models.Invitacion).filter(models.Invitacion.codigo == codigo).first()
    if inv is None:
        return {"codigo": codigo, "valido": False, "rol_destino": None,
                "motivo": "El código de invitación no existe"}
    if inv.usado:
        return {"codigo": codigo, "valido": False, "rol_destino": inv.rol_destino,
                "motivo": "El código de invitación ya fue utilizado"}
    if inv.fecha_expiracion < datetime.utcnow():
        return {"codigo": codigo, "valido": False, "rol_destino": inv.rol_destino,
                "motivo": "El código de invitación expiró"}
    return {"codigo": codigo, "valido": True, "rol_destino": inv.rol_destino, "motivo": None}


@app.post("/admin/invitaciones")
def crear_invitacion(
    datos: InvitacionCrear,
    db: Session = Depends(get_db),
    admin: dict = Depends(requiere_rol("admin")),
):
    if not datos.rol_destino.strip():
        raise HTTPException(status_code=422, detail="rol_destino no puede estar vacío")

    invitacion = models.Invitacion(
        rol_destino=datos.rol_destino.strip(),
        creado_por=admin.get("sub"),
    )
    db.add(invitacion)
    db.commit()
    db.refresh(invitacion)
    return _serializar_invitacion(invitacion)


@app.get("/admin/invitaciones")
def listar_invitaciones(
    db: Session = Depends(get_db),
    admin: dict = Depends(requiere_rol("admin")),
):
    invitaciones = db.query(models.Invitacion).order_by(
        models.Invitacion.fecha_creacion.desc()
    ).all()
    return [_serializar_invitacion(i) for i in invitaciones]


# No público: protegido con requiere_rol("admin") igual que los demás. La reutilización real
# viene de la función validar_codigo_invitacion() de arriba, que registro consumirá en la
# sub-entrega B. Este endpoint deja el chequeo disponible para el panel admin y los tests.
@app.get("/admin/invitaciones/validar/{codigo}")
def validar_invitacion(
    codigo: str,
    db: Session = Depends(get_db),
    admin: dict = Depends(requiere_rol("admin")),
):
    return validar_codigo_invitacion(db, codigo)


@app.delete("/admin/invitaciones/{invitacion_id}")
def revocar_invitacion(
    invitacion_id: str,
    db: Session = Depends(get_db),
    admin: dict = Depends(requiere_rol("admin")),
):
    invitacion = db.query(models.Invitacion).filter(
        models.Invitacion.id == invitacion_id
    ).first()
    if not invitacion:
        raise HTTPException(status_code=404, detail="Invitación no encontrada")
    if invitacion.usado:
        raise HTTPException(status_code=400, detail="No se puede revocar una invitación ya utilizada")

    db.delete(invitacion)
    db.commit()
    return {"mensaje": "Invitación revocada"}