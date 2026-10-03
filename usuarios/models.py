import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    # Nullable: una cuenta creada por primera vez vía Google (POST /usuarios/oauth/google) no
    # tiene contraseña propia — ver verificar_password en seguridad.py, ajustada para no
    # explotar contra un hash None en vez de simplemente rechazar el login normal.
    password_hash = Column(String, nullable=True)
    rol = Column(String, default="comprador")  # comprador, productor, repartidor, admin ("verificador" en cuentas de antes)
    fecha_registro = Column(DateTime, default=datetime.utcnow)
    # "sub" del id_token de Google ya verificado — único por cuenta de Google, nunca compartido.
    # Null hasta que la cuenta se vincule (login/registro con Google, ver
    # POST /usuarios/oauth/google): tanto una cuenta 100% Google desde el principio como una
    # cuenta con contraseña que se vincula después terminan con este campo poblado.
    google_id = Column(String, unique=True, nullable=True)
    # Solo para compradores (observación de la presentación del 2026-10-01: distinguir a quien compra para su casa de un
    # negocio que compra sacos para revender). "persona" o "negocio"; null en los demás roles y en los compradores de
    # antes (se tratan como "persona"). El RUC y el nombre comercial son opcionales y solo se guardan para un negocio.
    tipo_comprador = Column(String, nullable=True)
    ruc = Column(String(11), nullable=True)
    nombre_comercial = Column(String, nullable=True)
    # Lo que dijo SUNAT del RUC al verificarlo (ver sunat.py): un negocio solo se acepta con su RUC activo y habido.
    razon_social = Column(String, nullable=True)
    ruc_verificado_en = Column(DateTime, nullable=True)


# La tabla "invitaciones" (códigos de un solo uso para registrarse con un rol restringido) se quitó con el rol
# Verificador, su único uso: el modelo ya no existe, pero en una base ya creada la tabla se deja como estaba (es
# historial y nada la lee). Ver ROLES_DE_REGISTRO_ABIERTO en main.py.


class IntentoLogin(Base):
    """Fallos seguidos de login por correo (exista o no la cuenta) y, si se agotó el tope, hasta cuándo está
    bloqueado — ver acceso.py. Tabla nueva: la crea Base.metadata.create_all() al arrancar."""
    __tablename__ = "intentos_login"

    email = Column(String, primary_key=True)  # normalizado: sin espacios y en minúsculas
    fallos = Column(Integer, nullable=False, default=0)
    ultimo_fallo = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    bloqueado_hasta = Column(DateTime, nullable=True)
