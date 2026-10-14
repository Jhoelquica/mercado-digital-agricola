import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database import Base

class Notificacion(Base):
    __tablename__ = "notificaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=True)
    # Una notificación cuelga de un pedido O de un producto, nunca de los dos a la vez — depende
    # del tipo (pedido_creado/envio_actualizado siguen usando pedido_id; producto_disponible, que
    # nace de una Reserva y no de un Pedido, usa producto_id con pedido_id en None). No hay
    # CHECK constraint que lo fuerce a nivel de base de datos: cada evento que llega acá ya trae
    # exactamente uno de los dos (ver rabbitmq_consumer._procesar_mensaje), así que alcanza con
    # la garantía de la aplicación.
    pedido_id = Column(UUID(as_uuid=True), nullable=True)
    producto_id = Column(UUID(as_uuid=True), nullable=True)
    tipo = Column(String, nullable=False)       # pedido_creado, envio_actualizado, producto_disponible, pago_confirmado, saldo_pagado, pago_rechazado, saldo_rechazado, recojo_actualizado
    # Estado del envío en un envio_actualizado (p. ej. "en_destino"): el frontend lo muestra como etiqueta junto al texto.
    # Nullable: la migración lo agrega a la tabla ya creada; los avisos de antes lo traían dentro del mensaje.
    estado = Column(String, nullable=True)
    mensaje = Column(String, nullable=False)
    canal = Column(String, default="email")     # simulado, no se envía de verdad
    fecha_envio = Column(DateTime, default=datetime.utcnow)

class SuscripcionPush(Base):
    """H6 (validación con usuarios del 2026-10-13): un navegador (celular o computadora) donde el usuario activó "Avisos
    en este celular". endpoint/p256dh/auth son los que da el navegador al suscribirse (Web Push); el endpoint es único:
    si otra cuenta lo activa en el mismo navegador, pasa a ser de esa cuenta."""
    __tablename__ = "suscripciones_push"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    endpoint = Column(String, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)


class PreferenciaCorreo(Base):
    """H6: el usuario pidió que, si un aviso no le llega al celular, le llegue por correo. Si existe la fila, está activo.
    El correo se copia de Usuarios al activarlo (el token no lo trae)."""
    __tablename__ = "preferencias_correo"

    usuario_id = Column(UUID(as_uuid=True), primary_key=True)
    correo = Column(String, nullable=False)
    fecha_actualizacion = Column(DateTime, default=datetime.utcnow)
