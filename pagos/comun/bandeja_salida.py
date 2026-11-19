# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Bandeja de salida (patrón outbox): la ÚNICA forma de publicar un evento en RabbitMQ.

registrar_evento(sesion, tipo, datos) deja el evento en eventos_salientes, en la MISMA transacción que el cambio de
estado que lo origina: si el cambio se deshace, el evento también; si se confirma, el evento ya no se pierde, aunque
RabbitMQ esté caído (y la petición responde igual). Se guarda con su evento_id y el mensaje ya en JSON, así que
publicarlo después solo puede fallar por la conexión.

El relevo (un hilo de fondo en cada réplica) publica cada INTERVALO_DEL_RELEVO_SEGUNDOS:
- toma pg_try_advisory_xact_lock: una sola réplica publica a la vez, las demás no hacen nada en ese ciclo;
- publica los pendientes en el orden en que se crearon (la columna secuencia) y marca cada uno cuando RabbitMQ lo
  confirma;
- se DETIENE en el primer fallo: lo demás sale en un ciclo siguiente, en el mismo orden.
No usa SKIP LOCKED: con dos réplicas publicando a la vez, cada una tomaría una parte y el orden se rompería.

Entrega "al menos una vez": si el relevo se corta entre publicar y confirmar su transacción, esos eventos salen de nuevo
con el mismo evento_id, y los consumidores descartan el repetido. Los publicados se borran a los DIAS_RETENCION_EVENTOS
días.
"""
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from comun.database import Base, SessionLocal, en_su_sesion_sin_lanzar
from comun.rabbitmq import DIAS_RETENCION_EVENTOS, Publicador

logger = logging.getLogger("comun.bandeja_salida")

INTERVALO_DEL_RELEVO_SEGUNDOS = 1.5
LOTE = 100
INTERVALO_DE_LIMPIEZA_SEGUNDOS = 3600
# Mientras RabbitMQ no responde, el relevo lo reintenta en cada ciclo, pero avisa una vez por minuto, no en cada uno.
AVISO_CADA_SEGUNDOS = 60
# Si falla la base, el relevo espera cada vez más entre ciclos (hasta este tope), para no llenar el log.
ESPERA_MAXIMA_TRAS_FALLOS = 30
# El candado del relevo: una sola réplica publica a la vez. Cada servicio tiene su base, así que basta una clave fija.
CANDADO_DEL_RELEVO = "SELECT pg_try_advisory_xact_lock(hashtext('eventos_salientes'))"
CANDADO_DEL_ESQUEMA = "SELECT pg_advisory_xact_lock(hashtext('eventos_salientes_esquema'))"
TABLA = "eventos_salientes"


class EventoSaliente(Base):
    __tablename__ = TABLA

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # el evento_id del mensaje
    secuencia = Column(BigInteger, Identity(), nullable=False, unique=True)  # el orden de creación: así se publica
    exchange = Column(String, nullable=False)
    tipo = Column(String, nullable=False)
    cuerpo = Column(Text, nullable=False)  # el mensaje en JSON, tal cual se publica (con evento y evento_id)
    fecha_creacion = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    fecha_envio = Column(DateTime, nullable=True, index=True)  # NULL = pendiente; el índice, para la limpieza
    intentos = Column(Integer, nullable=False, default=0)  # cuántas veces se intentó publicar

    # Los pendientes, en orden: el relevo los busca así en cada ciclo, aunque la tabla tenga 30 días de publicados.
    __table_args__ = (Index("ix_eventos_salientes_pendientes", "secuencia", postgresql_where=text("fecha_envio IS NULL")),)

    def mensaje(self) -> dict:
        return json.loads(self.cuerpo)


def registrar_evento(sesion, tipo: str, datos: dict, exchange: str) -> uuid.UUID:
    """Deja el evento `tipo` en la bandeja de salida, dentro de la transacción de `sesion`: sale con su commit, y con su
    rollback no sale. Devuelve su evento_id. `datos` es el cuerpo del mensaje (fechas, decimales y UUID van como texto).

    Cada servicio lo usa con su exchange ya puesto (su eventos.py)."""
    if datos.get("evento", tipo) != tipo:
        raise ValueError(f"El evento {tipo!r} trae otro nombre en sus datos: {datos.get('evento')!r}")
    evento_id = uuid.uuid4()
    mensaje = {"evento": tipo, **{clave: valor for clave, valor in datos.items() if clave != "evento_id"},
               "evento_id": str(evento_id)}
    sesion.add(EventoSaliente(id=evento_id, exchange=exchange, tipo=tipo, cuerpo=json.dumps(mensaje, default=str),
                              fecha_creacion=datetime.utcnow(), intentos=0))
    return evento_id


# ---- El relevo ----

_fallando_desde = None  # desde cuándo RabbitMQ no acepta eventos (time.monotonic), o None
_ultimo_aviso = 0.0


def _avisar_fallo(error, publicados: int) -> None:
    global _fallando_desde, _ultimo_aviso
    ahora = time.monotonic()
    if _fallando_desde is None:
        _fallando_desde = ahora
    if ahora - _ultimo_aviso >= AVISO_CADA_SEGUNDOS:
        _ultimo_aviso = ahora
        logger.warning("RabbitMQ no aceptó un evento (%s: %s) y el relevo se detuvo ahí (%d publicado(s) antes en este "
                       "ciclo): lo pendiente sale en orden cuando vuelva. Lleva %d s así.", type(error).__name__, error,
                       publicados, int(ahora - _fallando_desde))


def _avisar_si_volvio(publicados: int) -> None:
    global _fallando_desde, _ultimo_aviso
    if _fallando_desde is not None:
        logger.info("RabbitMQ volvió a aceptar eventos tras %d s: el relevo publicó %d",
                    int(time.monotonic() - _fallando_desde), publicados)
        _fallando_desde, _ultimo_aviso = None, 0.0


def publicar_pendientes(sesion, publicador=Publicador):
    """Un ciclo del relevo, en la transacción de `sesion`, que sostiene el candado hasta el commit. Devuelve cuántos
    eventos publicó, o None si el turno lo tenía otra réplica. Lo que RabbitMQ confirmó queda marcado aunque un evento
    posterior falle."""
    if not sesion.execute(text(CANDADO_DEL_RELEVO)).scalar():
        sesion.rollback()
        return None
    pendientes = (sesion.query(EventoSaliente)
                  .filter(EventoSaliente.fecha_envio.is_(None))
                  .order_by(EventoSaliente.secuencia)
                  .limit(LOTE)
                  .all())
    if not pendientes:
        sesion.rollback()  # suelta el candado
        return 0
    publicados = 0
    try:
        with publicador() as canal:
            for evento in pendientes:
                evento.intentos = (evento.intentos or 0) + 1
                canal.publicar(evento.exchange, evento.cuerpo, str(evento.id))
                evento.fecha_envio = datetime.utcnow()
                publicados += 1
    except Exception as error:
        _avisar_fallo(error, publicados)
    else:
        _avisar_si_volvio(publicados)
    sesion.commit()
    return publicados


def borrar_publicados_viejos(sesion, dias: int = None, ahora: datetime = None) -> int:
    """Borra los eventos publicados hace más de `dias` días (DIAS_RETENCION_EVENTOS si no se dice). Los pendientes no
    se borran nunca. Devuelve cuántos borró."""
    limite = (ahora or datetime.utcnow()) - timedelta(days=dias or DIAS_RETENCION_EVENTOS)
    return (sesion.query(EventoSaliente).filter(EventoSaliente.fecha_envio < limite)
            .delete(synchronize_session=False))


def una_pasada(sesiones=None) -> bool:
    """Un ciclo del relevo en su propia sesión. Nunca lanza, aunque fallen la base, el rollback o el cierre."""
    return en_su_sesion_sin_lanzar(publicar_pendientes, "El relevo de la bandeja de salida", logger, sesiones)


def limpiar_una_vez(sesiones=None) -> bool:
    """Una pasada de la limpieza en su propia sesión. Nunca lanza."""

    def limpiar(sesion):
        borrados = borrar_publicados_viejos(sesion)
        sesion.commit()
        if borrados:
            logger.info("Bandeja de salida: %d evento(s) publicado(s) hace más de %d días borrado(s)", borrados,
                        DIAS_RETENCION_EVENTOS)

    return en_su_sesion_sin_lanzar(limpiar, "La limpieza de la bandeja de salida", logger, sesiones)


def _relevar_siempre():
    fallos_seguidos = 0
    proxima_limpieza = 0.0
    while True:
        try:
            fallos_seguidos = 0 if una_pasada() else fallos_seguidos + 1
            if time.monotonic() >= proxima_limpieza:
                limpiar_una_vez()
                proxima_limpieza = time.monotonic() + INTERVALO_DE_LIMPIEZA_SEGUNDOS
        except Exception:  # una_pasada y limpiar_una_vez no lanzan; esto es por si algo cambia
            fallos_seguidos += 1
            logger.exception("El relevo de la bandeja de salida falló")
        time.sleep(min(INTERVALO_DEL_RELEVO_SEGUNDOS * 2 ** min(fallos_seguidos, 5), ESPERA_MAXIMA_TRAS_FALLOS))


def lanzar_relevo_en_hilo() -> None:
    threading.Thread(target=_relevar_siempre, daemon=True, name="relevo-bandeja-salida").start()


# ---- El esquema ----

def asegurar_esquema(engine, exchange_de_las_filas_viejas: str, tabla: str = TABLA) -> None:
    """Completa una bandeja de antes de este módulo (la de Pedidos: sin secuencia ni exchange) y crea sus índices.
    Va después de create_all(), que crea entera una tabla nueva pero no toca una que ya existe. Idempotente, y segura
    con varias réplicas arrancando a la vez (todo en una transacción, con un candado). Las filas pendientes de antes se
    numeran en el orden en que se crearon, después de todas las demás. Si falla, lanza: sin estas columnas el servicio
    no puede registrar eventos."""
    with engine.begin() as conexion:
        conexion.execute(text(CANDADO_DEL_ESQUEMA))
        columnas = {fila[0] for fila in conexion.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :tabla"), {"tabla": tabla})}
        if not columnas:
            return
        if "exchange" not in columnas:
            conexion.execute(text(f'ALTER TABLE "{tabla}" ADD COLUMN exchange VARCHAR'))
            conexion.execute(text(f'UPDATE "{tabla}" SET exchange = :exchange'),
                             {"exchange": exchange_de_las_filas_viejas})
            conexion.execute(text(f'ALTER TABLE "{tabla}" ALTER COLUMN exchange SET NOT NULL'))
            logger.warning("Migración: %s.exchange agregada (las filas de antes, con %s)", tabla,
                           exchange_de_las_filas_viejas)
        if "secuencia" not in columnas:
            conexion.execute(text(f'ALTER TABLE "{tabla}" ADD COLUMN secuencia BIGINT GENERATED BY DEFAULT AS IDENTITY'))
            conexion.execute(text(
                f'UPDATE "{tabla}" e SET secuencia = n.nueva FROM ('
                f'  SELECT id, (SELECT max(secuencia) FROM "{tabla}") + row_number() OVER (ORDER BY fecha_creacion, id)'
                f'  AS nueva FROM "{tabla}" WHERE fecha_envio IS NULL) n WHERE e.id = n.id'))
            conexion.execute(text(
                f"SELECT setval(pg_get_serial_sequence('\"{tabla}\"', 'secuencia'), "
                f'(SELECT coalesce(max(secuencia), 0) + 1 FROM "{tabla}"), false)'))
            conexion.execute(text(f'ALTER TABLE "{tabla}" ADD CONSTRAINT "{tabla}_secuencia_key" UNIQUE (secuencia)'))
            logger.warning("Migración: %s.secuencia agregada (los pendientes de antes, en su orden de creación)", tabla)
        conexion.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{tabla}_pendientes" ON "{tabla}" (secuencia) '
                              "WHERE fecha_envio IS NULL"))
        conexion.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_{tabla}_fecha_envio" ON "{tabla}" (fecha_envio)'))
