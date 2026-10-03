import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Integer, Index, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"
    # Un RUC solo puede estar en una cuenta (pedido del usuario, 2026-10-03): así nadie más se registra como un negocio
    # que ya tiene cuenta. Varios NULL (personas y otros roles) sí se permiten. En una base ya creada lo agrega
    # migraciones.crear_indices_faltantes.
    __table_args__ = (Index("uq_usuarios_ruc", "ruc", unique=True),)

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



class ReclamoRuc(Base):
    """Alguien dice que el RUC que otra cuenta registró es de SU negocio (un RUC va en una sola cuenta, así que el primero
    que lo registra se quedaba con él). Sube la Ficha RUC y el DNI del dueño o representante legal, y el admin compara los
    nombres y decide: si lo aprueba, el RUC pasa a su cuenta y la otra queda "para mi casa". Ver los endpoints
    /usuarios/me/reclamos-ruc y /usuarios/admin/reclamos-ruc."""
    __tablename__ = "reclamos_ruc"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ruc = Column(String(11), nullable=False, index=True)
    razon_social = Column(String, nullable=True)  # lo que dijo SUNAT al reclamar
    reclamante_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False, index=True)
    titular_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)  # quien tenía el RUC
    # Los datos del dueño o representante legal, como figuran en su DNI (el admin los compara con los documentos).
    nombre_titular = Column(String, nullable=False)
    dni_numero = Column(String(8), nullable=False)
    mensaje = Column(String, nullable=True)
    # Documentos en el bucket PRIVADO usuarios-reclamos-ruc (ver minio_client.py)
    ficha_objeto = Column(String, nullable=False)
    ficha_tipo = Column(String, nullable=False)
    dni_objeto = Column(String, nullable=False)
    dni_tipo = Column(String, nullable=False)
    estado = Column(String, nullable=False, default="pendiente")  # pendiente | aprobado | rechazado
    motivo_rechazo = Column(String, nullable=True)
    revisado_por = Column(UUID(as_uuid=True), nullable=True)
    fecha_creacion = Column(DateTime, nullable=False, default=datetime.utcnow)
    fecha_revision = Column(DateTime, nullable=True)
