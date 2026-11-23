"""La versión de credencial de cada cuenta: cerrar todas sus sesiones a la vez, sin guardar una lista de tokens.

Cada token lleva en el claim "ver" la version_credencial que tenía la cuenta cuando se emitió. Subir la versión
(revocar_sesiones) deja sin valor todos los tokens anteriores de esa cuenta, en todos sus dispositivos. Es la única
función que la sube: la usan cambiar la contraseña y "Cerrar sesión en todos los dispositivos", y la usarán la
recuperación por correo, el cambio de rol y la desactivación de una cuenta cuando existan.

La comprobación (exigir_version_vigente) se hace en un solo punto: el ForwardAuth del gateway (validar-token y
validar-sesion en main.py), que corre en todas las rutas de /api. Los demás servicios solo validan la firma.

Caché en Redis: la versión de cada cuenta, CACHE_SEGUNDOS (30) como máximo. Con un acierto no se consulta la base.
- Al subir la versión se borra la clave y se escribe la nueva en la misma operación. La caché se llena solo si la
  clave no existe (SET NX): una petición que leyó la versión vieja de la base justo antes de subirla no puede volver a
  dejarla. Si Redis no responde en ese momento, un hilo reintenta borrar la clave hasta que responda (Redis guarda su
  contenido en un volumen: al reiniciarse podría devolver la versión vieja antes de que venza).
- Si Redis no responde (o tarda más de LIMITE_ESPERA_SEGUNDOS), se consulta la base: la comprobación nunca se salta.
  Tras 3 fallos seguidos, el circuito deja de intentar con Redis durante 30 s y se va directo a la base (el mismo
  patrón que productos/cache.py, con el mismo hallazgo: los timeouts de redis-py no acotan un DNS que no resuelve).
"""
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoExpirado

import pybreaker
import redis
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

import models
from comun.database import SessionLocal

logger = logging.getLogger("usuarios.sesiones")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
# REDIS_CACHE_PORT y no REDIS_PORT: Kubernetes inyecta REDIS_PORT=tcp://... en los pods (ver productos/cache.py).
REDIS_CACHE_PORT = int(os.getenv("REDIS_CACHE_PORT", "6379"))
CACHE_SEGUNDOS = 30
LIMITE_ESPERA_SEGUNDOS = 0.3
REINTENTO_SEGUNDOS = 0.5
MENSAJE_SESION_CERRADA = "Tu sesión se cerró (cambiaste la contraseña o cerraste la sesión en todos tus dispositivos): vuelve a iniciar sesión."

_cliente = redis.Redis(host=REDIS_HOST, port=REDIS_CACHE_PORT, db=0, decode_responses=True,
                       socket_connect_timeout=1, socket_timeout=1)
_ejecutor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="redis-sesiones")
_circuito = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=30)


def _clave(usuario_id) -> str:
    return f"usuarios:version_credencial:{usuario_id}"


def _llamar_con_limite(funcion, *args, **kwargs):
    futuro = _ejecutor.submit(funcion, *args, **kwargs)
    try:
        return futuro.result(timeout=LIMITE_ESPERA_SEGUNDOS)
    except FuturoExpirado:
        raise redis.exceptions.TimeoutError(f"Redis no respondió en {LIMITE_ESPERA_SEGUNDOS} s")


def _proteger(funcion, *args, **kwargs):
    """(ok, resultado) de una llamada a Redis con tope de tiempo y circuito. ok=False si Redis no respondió."""
    try:
        return True, _circuito.call(_llamar_con_limite, funcion, *args, **kwargs)
    except pybreaker.CircuitBreakerError:
        return False, None
    except redis.RedisError as error:
        logger.warning("Redis no disponible para las sesiones (%s): se consulta la base", error)
        return False, None


def _de_la_base(usuario_id) -> int | None:
    db = SessionLocal()
    try:
        return db.query(models.Usuario.version_credencial).filter(models.Usuario.id == usuario_id).scalar()
    finally:
        db.close()


def version_vigente(usuario_id) -> int | None:
    """La version_credencial de la cuenta: de la caché si está ahí, si no de la base (y queda en la caché). None si la
    cuenta no existe."""
    clave = _clave(usuario_id)
    ok, guardada = _proteger(_cliente.get, clave)
    if ok and guardada is not None:
        try:
            return int(guardada)
        except ValueError:
            pass
    version = _de_la_base(usuario_id)
    if version is not None and ok:  # con Redis caído no se insiste: la próxima petición vuelve a probar
        _proteger(_cliente.set, clave, str(version), ex=CACHE_SEGUNDOS, nx=True)
    return version


def exigir_version_vigente(payload: dict) -> None:
    """401 si el token es de una versión anterior de la credencial de su cuenta (o de una cuenta que ya no existe).
    Un token sin "ver" (de antes de esto) tampoco vale: su dueño vuelve a iniciar sesión."""
    ver = payload.get("ver")
    try:
        usuario_id = uuid.UUID(str(payload.get("sub")))
    except ValueError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    if not isinstance(ver, int) or isinstance(ver, bool):
        raise HTTPException(status_code=401, detail=MENSAJE_SESION_CERRADA)
    vigente = version_vigente(usuario_id)
    if vigente is None or ver != vigente:
        raise HTTPException(status_code=401, detail=MENSAJE_SESION_CERRADA)


def revocar_sesiones(db: Session, usuario) -> int:
    """Sube la version_credencial de la cuenta y confirma la transacción de `db`, con lo demás que haya cambiado quien
    llama (por ejemplo, la contraseña nueva): desde ese momento ningún token anterior de la cuenta vale, en ningún
    dispositivo. Devuelve la versión nueva, la del token que se emite ahora para la pestaña que lo pidió."""
    nueva = db.execute(update(models.Usuario).where(models.Usuario.id == usuario.id)
                       .values(version_credencial=models.Usuario.version_credencial + 1)
                       .returning(models.Usuario.version_credencial)).scalar_one()
    db.commit()
    clave = _clave(usuario.id)
    ok, _ = _proteger(lambda: _cliente.pipeline(transaction=True).delete(clave)
                      .set(clave, str(nueva), ex=CACHE_SEGUNDOS).execute())
    if not ok:
        logger.warning("Cuenta %s: Redis no respondió al subir la versión a %s; se reintenta borrar la clave vieja",
                       usuario.id, nueva)
        _borrar_cuando_responda(clave)
    logger.info("Cuenta %s: sesiones anteriores revocadas (versión de credencial %s)", usuario.id, nueva)
    return nueva


def _borrar_cuando_responda(clave) -> None:
    """Si Redis no respondió al subir la versión, la versión vieja puede seguir en la caché: Redis solo no se alcanzaba,
    o se reinició y la recuperó de su volumen antes de que venciera. Un hilo reintenta BORRAR la clave (sin pasar por el
    circuito, que estaría abierto) hasta lograrlo o hasta que ya habría vencido sola. Borrar nunca deja un valor viejo: la
    siguiente petición la vuelve a leer de la base."""
    def reintentar():
        fin = time.monotonic() + CACHE_SEGUNDOS + 1
        while time.monotonic() < fin:
            try:
                _llamar_con_limite(_cliente.delete, clave)
                logger.info("Clave %s borrada de Redis al reintentar", clave)
                return
            except redis.RedisError:
                time.sleep(REINTENTO_SEGUNDOS)
            except RuntimeError:
                return  # el proceso se está apagando (el ejecutor ya no acepta tareas)
    threading.Thread(target=reintentar, daemon=True, name="sesiones-borrar").start()
