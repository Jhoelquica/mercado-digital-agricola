import os
from datetime import datetime

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, EmailStr
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from google.auth import exceptions as google_exceptions
from auth import verificar_token, requiere_rol

from database import Base, engine, SessionLocal
import models
from seguridad import hashear_password, verificar_password, crear_token

# No es secreta (el Client ID de Google es público por diseño — viaja en el propio id_token como
# audience), pero igual se maneja como env var y no hardcodeada, mismo criterio que cualquier
# otro valor de configuración de este proyecto que varía por entorno.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

Base.metadata.create_all(bind=engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

# ============ Bootstrap del primer admin ============
# Patrón "seed superuser por env var al arrancar" — el mismo que ya usa este proyecto para
# Grafana (ver k8s/grafana.yaml: GF_SECURITY_ADMIN_USER/PASSWORD). Es NECESARIO acá porque el
# sistema de invitaciones es circular: solo un admin puede generar invitaciones, así que tiene
# que existir una forma de crear el primero sin invitación.
#
# VÁLIDO PARA ESTE ENTORNO (sustentación/QA), NO para un despliegue de producción real: si este
# proyecto migra más allá de la sustentación, esto debería reemplazarse por un k8s Job de un solo
# uso (la opción "script standalone" que se evaluó junto con esta y se descartó por ahora) y esta
# función de auto-siembra en el startup debería eliminarse del código — un servicio que puede
# crear un superusuario automáticamente en cada arranque no debería quedar viviendo en prod
# indefinidamente, aunque esté gateado por env vars.
ADMIN_BOOTSTRAP_EMAIL = os.getenv("ADMIN_BOOTSTRAP_EMAIL")
ADMIN_BOOTSTRAP_PASSWORD = os.getenv("ADMIN_BOOTSTRAP_PASSWORD")

@app.on_event("startup")
def sembrar_admin_inicial():
    if not ADMIN_BOOTSTRAP_EMAIL or not ADMIN_BOOTSTRAP_PASSWORD:
        return  # sin ambas env vars, no se toca nada — no rompe entornos que no las seteen

    db = SessionLocal()
    try:
        # Idempotente por EXISTENCIA DE CUALQUIER ADMIN, no por el email puntual: si ya hay un
        # admin (creado por este bootstrap en un arranque anterior, o por invitación), no se
        # crea otro aunque las env vars sigan seteadas en cada restart.
        ya_existe_admin = db.query(models.Usuario).filter(models.Usuario.rol == "admin").first()
        if ya_existe_admin:
            return

        admin = models.Usuario(
            nombre="Administrador",
            email=ADMIN_BOOTSTRAP_EMAIL,
            password_hash=hashear_password(ADMIN_BOOTSTRAP_PASSWORD),
            rol="admin",
        )
        db.add(admin)
        try:
            db.commit()
        except IntegrityError:
            # usuarios corre con 3 réplicas (ver k8s/usuarios.yaml) — si arrancan a la vez con la
            # BD recién creada, más de una puede pasar el chequeo "no existe admin" antes de que
            # cualquiera haga commit. El UNIQUE en email deja pasar solo a la primera; las demás
            # caen acá y ceden el bootstrap sin romper el arranque del pod.
            db.rollback()
    finally:
        db.close()

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
    codigo_invitacion: str | None = None  # obligatorio solo para roles restringidos

class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str

class UsuarioOAuthGoogle(BaseModel):
    id_token: str
    rol: str | None = None  # solo aplica si el resultado es un registro nuevo (ver el endpoint)
    codigo_invitacion: str | None = None

class InvitacionCrear(BaseModel):
    rol_destino: str

# Roles de registro ABIERTO: cualquier persona puede auto-registrarse con estos.
# Deny-by-default: TODO lo que no esté acá (verificador, admin, y cualquier rol futuro)
# exige un codigo_invitacion válido cuyo rol_destino coincida — así, agregar un rol
# sensible sin acordarse de esta lista NO abre un agujero: por defecto queda restringido.
ROLES_DE_REGISTRO_ABIERTO = frozenset({"comprador", "productor", "repartidor"})

# Sin fallback hardcodeado a propósito: si el Deployment se olvida de wirear esta env var, el
# endpoint queda inutilizable (rechaza todo) en vez de aceptar en silencio un valor conocido.
# Usada SOLO por los DELETE de limpieza QA (ningún otro servicio llama a este endpoint) — ver
# inventario completo de X-Servicio-Secreto en el resumen de esta tarea.
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "usuarios"}

# Compartida entre /usuarios/registro y /usuarios/oauth/google (cuando ese resulta en un
# registro nuevo, no un login/vinculación) — mismo gating de rol restringido en los dos casos,
# sin duplicar la lógica. Roles en ROLES_DE_REGISTRO_ABIERTO no necesitan nada; cualquier otro
# exige un codigo_invitacion válido cuyo rol_destino coincida. Devuelve la Invitacion a marcar
# como usada (o None si el rol era abierto) — quien llama decide cuándo consumirla, recién
# después de crear el Usuario con éxito (ver ambos endpoints).
def _validar_rol_y_resolver_invitacion(db: Session, rol: str, codigo_invitacion: str | None) -> "models.Invitacion | None":
    if rol in ROLES_DE_REGISTRO_ABIERTO:
        return None
    codigo = (codigo_invitacion or "").strip()
    chequeo = validar_codigo_invitacion(db, codigo)
    if not chequeo["valido"]:
        motivo = chequeo["motivo"] if codigo else f"Registrarse como '{rol}' requiere un código de invitación"
        raise HTTPException(status_code=400, detail=motivo)
    if chequeo["rol_destino"] != rol:
        raise HTTPException(status_code=400, detail="Este código no habilita el rol solicitado.")
    return db.query(models.Invitacion).filter(models.Invitacion.codigo == codigo).first()


@app.post("/usuarios/registro")
def registrar(datos: UsuarioRegistro, db: Session = Depends(get_db)):
    rol = datos.rol
    invitacion = _validar_rol_y_resolver_invitacion(db, rol, datos.codigo_invitacion)

    nuevo = models.Usuario(
        nombre=datos.nombre,
        email=datos.email,
        password_hash=hashear_password(datos.password),
        rol=rol,
    )
    db.add(nuevo)
    try:
        db.flush()  # ejecuta el INSERT (asigna nuevo.id); acá salta el dup de email
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ese email ya está registrado")

    # Consumir la invitación en la MISMA transacción que la creación del usuario: si algo
    # falla antes del commit, el rollback deshace ambas (la invitación no queda "usada" en falso).
    if invitacion is not None:
        invitacion.usado = True
        invitacion.usado_por = nuevo.id

    db.commit()
    db.refresh(nuevo)
    return {"id": nuevo.id, "nombre": nuevo.nombre, "email": nuevo.email, "rol": nuevo.rol}

@app.post("/usuarios/login")
def login(datos: UsuarioLogin, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.email == datos.email).first()
    if not usuario or not verificar_password(datos.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    token = crear_token(str(usuario.id), usuario.rol)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/usuarios/oauth/google")
def login_o_registro_google(datos: UsuarioOAuthGoogle, db: Session = Depends(get_db)):
    """Login/registro alternativo con Google, para los mismos 4 roles que ya soporta el
    registro normal. Tres casos, en este orden:
      1. Ya existe un Usuario con este google_id -> login directo con su rol de siempre.
      2. No hay match por google_id, pero SÍ por email (cuenta con contraseña creada antes) ->
         se vincula (se guarda google_id en esa fila) y login con su rol de siempre.
      3. No existe de ninguna forma -> registro nuevo, con el mismo gating de rol restringido
         que /usuarios/registro (ver _validar_rol_y_resolver_invitacion)."""
    try:
        payload = google_id_token.verify_oauth2_token(
            datos.id_token, google_requests.Request(), GOOGLE_CLIENT_ID,
        )
    except (ValueError, google_exceptions.GoogleAuthError):
        raise HTTPException(status_code=401, detail="Token de Google inválido o expirado")

    google_id = payload["sub"]
    email = payload.get("email")
    nombre = payload.get("name") or email

    usuario = db.query(models.Usuario).filter(models.Usuario.google_id == google_id).first()
    if usuario:
        return {"access_token": crear_token(str(usuario.id), usuario.rol), "token_type": "bearer"}

    usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
    if usuario:
        usuario.google_id = google_id
        db.commit()
        return {"access_token": crear_token(str(usuario.id), usuario.rol), "token_type": "bearer"}

    # Registro nuevo: sin cuenta previa por ninguna vía. Si no viene un rol, es el intento
    # típico desde el tab de Login (sin saber todavía que hace falta elegir un rol) — se le
    # pide explícitamente en vez de asumir uno.
    if datos.rol is None:
        raise HTTPException(status_code=400, detail="Selecciona un rol antes de continuar con Google")

    invitacion = _validar_rol_y_resolver_invitacion(db, datos.rol, datos.codigo_invitacion)

    nuevo = models.Usuario(
        nombre=nombre,
        email=email,
        password_hash=None,
        google_id=google_id,
        rol=datos.rol,
    )
    db.add(nuevo)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ese email ya está registrado")

    if invitacion is not None:
        invitacion.usado = True
        invitacion.usado_por = nuevo.id

    db.commit()
    db.refresh(nuevo)
    return {"access_token": crear_token(str(nuevo.id), nuevo.rol), "token_type": "bearer"}

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