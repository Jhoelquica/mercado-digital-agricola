import logging
import os
import uuid
from datetime import datetime

from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from google.auth import exceptions as google_exceptions
from auth import verificar_token
from auth import secreto_de_servicio_valido

from database import Base, engine, SessionLocal
import models
from migraciones import agregar_columnas_faltantes, crear_indices_faltantes
import sunat
from seguridad import hashear_password, verificar_password, crear_token, HASH_DE_RELLENO
import acceso

# No es secreta (el Client ID de Google es público por diseño — viaja en el propio id_token como
# audience), pero igual se maneja como env var y no hardcodeada, mismo criterio que cualquier
# otro valor de configuración de este proyecto que varía por entorno.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

Base.metadata.create_all(bind=engine)
agregar_columnas_faltantes(engine, Base)
crear_indices_faltantes(engine, Base)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

# ============ Bootstrap del primer admin ============
# Patrón "seed superuser por env var al arrancar" — el mismo que ya usa este proyecto para
# Grafana (ver k8s/grafana.yaml: GF_SECURITY_ADMIN_USER/PASSWORD). Es NECESARIO acá porque el
# registro solo admite roles abiertos (ver ROLES_DE_REGISTRO_ABIERTO): un admin no puede
# registrarse, así que tiene que existir una forma de crear el primero.
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
        # admin (creado por este bootstrap en un arranque anterior, o a mano en la base), no se
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

# ---- Tipo de comprador: persona o negocio ----
NOMBRE_COMERCIAL_MAXIMO = 120
PESOS_DIGITO_RUC = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def ruc_valido(ruc: str) -> bool:
    """RUC peruano: 11 dígitos, empieza con 10, 15, 17 o 20 y el último es el dígito verificador (módulo 11 de SUNAT).
    Evita que un número mal tipeado quede guardado; no consulta a SUNAT si el RUC existe."""
    if len(ruc) != 11 or not ruc.isdigit() or ruc[:2] not in ("10", "15", "17", "20"):
        return False
    resto = 11 - sum(int(d) * p for d, p in zip(ruc[:10], PESOS_DIGITO_RUC)) % 11
    return int(ruc[10]) == {10: 0, 11: 1}.get(resto, resto)


class _DatosDeComprador(BaseModel):
    tipo_comprador: Literal["persona", "negocio"] | None = None
    ruc: str | None = None
    nombre_comercial: str | None = None

    @field_validator("ruc")
    @classmethod
    def _ruc(cls, valor):
        valor = (valor or "").strip().replace(" ", "")
        if not valor:
            return None
        if not ruc_valido(valor):
            raise ValueError("El RUC no es válido: son 11 dígitos y empieza con 10 o 20. Revisa que esté bien escrito.")
        return valor

    @field_validator("nombre_comercial")
    @classmethod
    def _nombre_comercial(cls, valor):
        valor = " ".join((valor or "").split())
        if not valor:
            return None
        if "\x00" in valor or len(valor) > NOMBRE_COMERCIAL_MAXIMO:
            raise ValueError(f"El nombre comercial no puede superar {NOMBRE_COMERCIAL_MAXIMO} caracteres")
        return valor


logger = logging.getLogger("usuarios")


MENSAJE_RUC_OCUPADO = "Ese RUC ya está registrado en otra cuenta de Chakra Shop. Si es tu negocio, entra con esa cuenta."


def _ruc_ocupado(db: Session, ruc: str, usuario_id=None) -> bool:
    """¿Otra cuenta (no la que se está cambiando) ya tiene este RUC?"""
    consulta = db.query(models.Usuario.id).filter(models.Usuario.ruc == ruc)
    if usuario_id is not None:
        consulta = consulta.filter(models.Usuario.id != usuario_id)
    return consulta.first() is not None


def _conflicto_al_guardar(error: IntegrityError) -> HTTPException:
    """El índice único del RUC (uq_usuarios_ruc) o el del correo: dos registros a la vez pueden pasar los chequeos
    previos, y la base deja entrar solo al primero."""
    if "uq_usuarios_ruc" in str(error.orig):
        return HTTPException(status_code=409, detail=MENSAJE_RUC_OCUPADO)
    return HTTPException(status_code=409, detail="Ese email ya está registrado")


def _verificar_negocio_en_sunat(datos: _DatosDeComprador, db: Session, usuario_id=None):
    """Un comprador "negocio" tiene que dar su RUC, que no puede estar en otra cuenta, y SUNAT tiene que tenerlo ACTIVO y
    HABIDO (ver sunat.py). Devuelve lo que dijo SUNAT (para guardar la razón social), o None si no compra como negocio.
    Se llama ANTES de crear o cambiar la cuenta: si el RUC no pasa, no se guarda nada."""
    if datos.tipo_comprador != "negocio":
        return None
    if not datos.ruc:
        raise HTTPException(status_code=422, detail="Para comprar como negocio pon el RUC de tu negocio: lo verificamos en SUNAT.")
    if _ruc_ocupado(db, datos.ruc, usuario_id):
        raise HTTPException(status_code=409, detail=MENSAJE_RUC_OCUPADO)
    try:
        en_sunat = sunat.consultar_ruc(datos.ruc)
    except sunat.SunatNoDisponible as error:
        logger.warning("No se pudo consultar el RUC en SUNAT (%s)", error)
        raise HTTPException(
            status_code=503,
            detail="No pudimos consultar SUNAT en este momento. Intenta en unos minutos, o regístrate para tu casa y cambia "
                   "a negocio desde tu perfil.",
        )
    if en_sunat is None:
        raise HTTPException(status_code=422, detail=f"SUNAT no tiene registrado el RUC {datos.ruc}. Revisa que esté bien escrito.")
    if en_sunat["estado"] != "ACTIVO":
        raise HTTPException(
            status_code=422,
            detail=f"El RUC {datos.ruc} figura en SUNAT como {en_sunat['estado'].lower()}: solo un negocio activo puede comprar como negocio.",
        )
    if en_sunat["condicion"] != "HABIDO":
        raise HTTPException(
            status_code=422,
            detail=f"El RUC {datos.ruc} figura en SUNAT como {en_sunat['condicion'].lower() or 'sin condición'} (su domicilio "
                   f"fiscal no está confirmado): solo un negocio habido puede comprar como negocio.",
        )
    return en_sunat


def _aplicar_datos_de_comprador(usuario, datos: _DatosDeComprador, en_sunat=None) -> None:
    """Solo un comprador tiene tipo (por defecto "persona"); el RUC, el nombre comercial y lo verificado en SUNAT solo se
    guardan para un negocio (si pasa a persona, se borran). `en_sunat` es lo que devolvió _verificar_negocio_en_sunat."""
    if usuario.rol != "comprador":
        usuario.tipo_comprador = usuario.ruc = usuario.nombre_comercial = None
        usuario.razon_social = usuario.ruc_verificado_en = None
        return
    usuario.tipo_comprador = datos.tipo_comprador or "persona"
    es_negocio = usuario.tipo_comprador == "negocio"
    usuario.ruc = datos.ruc if es_negocio else None
    usuario.nombre_comercial = datos.nombre_comercial if es_negocio else None
    usuario.razon_social = en_sunat["razon_social"] if es_negocio and en_sunat else None
    usuario.ruc_verificado_en = datetime.utcnow() if es_negocio and en_sunat else None


def _perfil(usuario) -> dict:
    perfil = {"id": usuario.id, "nombre": usuario.nombre, "email": usuario.email, "rol": usuario.rol}
    if usuario.rol == "comprador":
        perfil.update(tipo_comprador=usuario.tipo_comprador or "persona", ruc=usuario.ruc,
                      nombre_comercial=usuario.nombre_comercial, razon_social=usuario.razon_social,
                      ruc_verificado_en=usuario.ruc_verificado_en)
    return perfil


class UsuarioRegistro(_DatosDeComprador):
    nombre: str
    email: EmailStr
    password: str
    rol: str = "comprador"

    @model_validator(mode="after")
    def _password_valida(self):
        # mínimo 8 caracteres, máximo 72 bytes, no común ni igual al correo (ver acceso.py)
        acceso.validar_password_nueva(self.password, str(self.email))
        return self

class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)

class PerfilCompradorActualizar(_DatosDeComprador):
    tipo_comprador: Literal["persona", "negocio"]

class UsuarioOAuthGoogle(BaseModel):
    id_token: str
    rol: str | None = None  # solo aplica si el resultado es un registro nuevo (ver el endpoint)

# Los únicos roles con los que alguien puede crearse una cuenta. Deny-by-default: cualquier otro (admin, un rol
# inventado o "verificador", que se retiró en la presentación del 2026-10-01) se rechaza, así que agregar un rol
# sensible sin acordarse de esta lista NO abre un agujero. El admin lo crea el bootstrap de arriba. Antes había
# invitaciones por código para los roles restringidos; su único uso era el Verificador y se quitaron con él.
ROLES_DE_REGISTRO_ABIERTO = frozenset({"comprador", "productor", "repartidor"})

# Sin fallback hardcodeado a propósito: si el Deployment se olvida de wirear esta env var, el
# endpoint queda inutilizable (rechaza todo) en vez de aceptar en silencio un valor conocido.
# Usada SOLO por los DELETE de limpieza QA (ningún otro servicio llama a este endpoint) — ver
# inventario completo de X-Servicio-Secreto en el resumen de esta tarea.
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "usuarios"}

# Compartida entre /usuarios/registro y /usuarios/oauth/google (cuando ese resulta en un registro nuevo, no un
# login/vinculación): el mismo filtro de rol en los dos casos, sin duplicar la lógica.
def _validar_rol_de_registro(rol: str) -> None:
    if rol not in ROLES_DE_REGISTRO_ABIERTO:
        raise HTTPException(
            status_code=400,
            detail="Solo puedes registrarte como comprador, productor o repartidor.",
        )


@app.post("/usuarios/registro")
def registrar(datos: UsuarioRegistro, db: Session = Depends(get_db)):
    rol = datos.rol
    _validar_rol_de_registro(rol)
    en_sunat = _verificar_negocio_en_sunat(datos, db) if rol == "comprador" else None

    nuevo = models.Usuario(
        nombre=datos.nombre,
        email=datos.email,
        password_hash=hashear_password(datos.password),
        rol=rol,
    )
    _aplicar_datos_de_comprador(nuevo, datos, en_sunat)
    db.add(nuevo)
    try:
        db.flush()  # ejecuta el INSERT (asigna nuevo.id); acá salta el dup de email (o de RUC)
    except IntegrityError as error:
        db.rollback()
        raise _conflicto_al_guardar(error)

    db.commit()
    db.refresh(nuevo)
    return _perfil(nuevo)

@app.post("/usuarios/login")
def login(datos: UsuarioLogin, db: Session = Depends(get_db)):
    # Tras demasiados fallos seguidos el correo queda bloqueado un rato, incluso con la contraseña correcta
    # (y también si el correo no existe: ver acceso.py).
    acceso.comprobar_que_no_esta_bloqueado(db, datos.email)

    usuario = db.query(models.Usuario).filter(models.Usuario.email == datos.email).first()
    # Siempre se compara UNA contraseña con bcrypt, exista o no la cuenta (o si es solo de Google), para que el
    # tiempo de respuesta no delate qué correos están registrados.
    hash_real = usuario.password_hash if usuario else None
    coincide = verificar_password(datos.password, hash_real or HASH_DE_RELLENO) and hash_real is not None
    if not coincide:
        restantes = acceso.registrar_fallo(db, datos.email)  # responde 429 si este fallo agota el tope
        detalle = "Email o contraseña incorrectos"
        if restantes <= acceso.AVISAR_CUANDO_QUEDEN:
            detalle += f". Te quedan {restantes} intento{'s' if restantes != 1 else ''} antes de un bloqueo temporal."
        raise HTTPException(status_code=401, detail=detalle)

    acceso.limpiar_fallos(db, datos.email)
    token = crear_token(str(usuario.id), usuario.rol)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/usuarios/oauth/google")
def login_o_registro_google(datos: UsuarioOAuthGoogle, db: Session = Depends(get_db)):
    """Login/registro alternativo con Google, para los mismos roles que el registro normal.
    Tres casos, en este orden:
      1. Ya existe un Usuario con este google_id -> login directo con su rol de siempre.
      2. No hay match por google_id, pero SÍ por email (cuenta con contraseña creada antes) ->
         se vincula (se guarda google_id en esa fila) y login con su rol de siempre.
      3. No existe de ninguna forma -> registro nuevo, con el mismo filtro de rol que
         /usuarios/registro (ver _validar_rol_de_registro)."""
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

    # Desde acá el correo decide a quién se vincula esta cuenta de Google (o con qué correo se crea una nueva): solo
    # vale si Google lo tiene VERIFICADO. Sin esto, una cuenta de Google que declare el correo de otra persona sin
    # haberlo verificado entraría a la cuenta de esa persona —la del admin incluida— por la vinculación de abajo.
    if not email or payload.get("email_verified") not in (True, "true"):
        raise HTTPException(
            status_code=401,
            detail="Google no tiene verificado el correo de esta cuenta. Verifícalo en Google o usa correo y contraseña.",
        )

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

    _validar_rol_de_registro(datos.rol)

    nuevo = models.Usuario(
        nombre=nombre,
        email=email,
        password_hash=None,
        google_id=google_id,
        rol=datos.rol,
    )
    _aplicar_datos_de_comprador(nuevo, _DatosDeComprador())  # comprador "persona"; lo puede cambiar en su perfil
    db.add(nuevo)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ese email ya está registrado")

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
def obtener_usuario(usuario_id: uuid.UUID, db: Session = Depends(get_db), solicitante: dict = Depends(verificar_token)):
    # Solo el propio usuario o un admin: antes cualquier usuario con sesión podía leer el correo de
    # cualquier otro conociendo su id (y varios ids circulan en respuestas públicas, p. ej. las
    # reseñas). Quienes llaman a este endpoint (el frontend al iniciar sesión y Productos al
    # publicar una reseña) piden siempre el perfil del propio usuario del token.
    if str(usuario_id) != str(solicitante.get("sub")) and solicitante.get("rol") != "admin":
        raise HTTPException(status_code=403, detail="Solo puedes ver tu propio perfil")
    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return _perfil(usuario)

# El comprador cambia si compra como persona o como negocio (y su RUC / nombre comercial). Solo el propio comprador, por
# su token: no hay id en la ruta que se pueda cambiar por el de otro.
@app.patch("/usuarios/me/comprador")
def actualizar_perfil_comprador(datos: PerfilCompradorActualizar, db: Session = Depends(get_db),
                                solicitante: dict = Depends(verificar_token)):
    if solicitante.get("rol") != "comprador":
        raise HTTPException(status_code=403, detail="Solo una cuenta de comprador elige si compra como persona o negocio")
    usuario = db.query(models.Usuario).filter(models.Usuario.id == solicitante.get("sub")).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    _aplicar_datos_de_comprador(usuario, datos, _verificar_negocio_en_sunat(datos, db, usuario.id))
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise _conflicto_al_guardar(error)
    db.refresh(usuario)
    return _perfil(usuario)

@app.delete("/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    db.delete(usuario)
    db.commit()
    return {"mensaje": "Usuario eliminado"}

