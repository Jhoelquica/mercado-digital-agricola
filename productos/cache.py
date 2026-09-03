"""Caché distribuida (Redis) para el catálogo de productos.

Cubre dos claves:
  - CLAVE_CATALOGO: el resultado completo de GET /productos.
  - clave_detalle(producto_id): el resultado de GET /productos/{id}, una por producto.

Regla de oro: Redis es una optimización, nunca un punto único de falla. Toda función de este
módulo atrapa los errores de Redis y se degrada a "no hay caché" (None en obtener(), no-op en
guardar()/invalidar()) — quien llama simplemente sigue a PostgreSQL como si el caché no
existiera. Nunca debe propagar una excepción hacia main.py.

HALLAZGO REAL (probado apagando el contenedor de Redis): los parámetros socket_connect_timeout/
socket_timeout de redis-py NO acotan el peor caso cuando el hostname deja de resolver — con
Redis caído, una sola llamada síncrona sin protección adicional tardó decenas de segundos en
fallar (la resolución DNS bloqueante no respeta esos timeouts, solo el connect() ya con el
socket abierto). Por eso cada llamada real a Redis va envuelta en _llamar_con_limite(), que la
manda a un hilo aparte y le pone un tope duro con concurrent.futures — si Redis no responde en
LIMITE_ESPERA_SEGUNDOS, se trata como fallo sin importar qué esté pasando por dentro. Encima de
eso, un CircuitBreaker (el mismo patrón que ya usa este servicio para llamar a Productores) evita
seguir intentando durante REINICIO_CIRCUITO_SEGUNDOS tras fallos consecutivos, para no acumular
hilos colgados esperando una respuesta que no va a llegar en cada petición mientras Redis sigue
caído.
"""
import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoExpirado

import redis
import pybreaker
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger("productos.cache")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
# OJO: NO uses "REDIS_PORT" para esta variable. Kubernetes inyecta automáticamente
# REDIS_PORT=tcp://<cluster-ip>:6379 (y REDIS_SERVICE_HOST, REDIS_SERVICE_PORT,
# REDIS_PORT_6379_TCP*) en cualquier Pod del mismo namespace, para CUALQUIER Service llamado
# "redis" — es el formato de descubrimiento estilo Docker-links que documenta Kubernetes en
# https://kubernetes.io/docs/concepts/services-networking/service/#environment-variables. Si
# esta variable se llama igual, ese valor (un string "tcp://...", no un puerto numérico) pisa
# lo que sea que pongas en el Deployment, y int() revienta con
# "invalid literal for int() with base 10: 'tcp://...'" — pasó en producción real, con las 3
# réplicas en CrashLoopBackOff. REDIS_HOST sí es seguro: Kubernetes no genera esa variable para
# un Service llamado "redis" (solo las listadas arriba), así que ese nombre no se toca.
REDIS_CACHE_PORT = int(os.getenv("REDIS_CACHE_PORT", "6379"))

# TTL corto a propósito: es la red de seguridad si por lo que sea una invalidación se pierde
# (ej. el proceso muere justo después del commit y antes de invalidar) — nunca deja el catálogo
# desactualizado más de este tiempo, incluso en el peor caso.
TTL_SEGUNDOS = 60

# Tope duro por llamada a Redis (ver hallazgo arriba). Un Redis sano en el mismo clúster responde
# en unos pocos milisegundos (medido: ~5-9ms) — 300ms deja margen de sobra para tráfico normal
# sin dejar que una llamada colgada retenga la petición HTTP del usuario.
LIMITE_ESPERA_SEGUNDOS = 0.3

CLAVE_CATALOGO = "productos:catalogo"


def clave_detalle(producto_id) -> str:
    return f"productos:detalle:{producto_id}"


# decode_responses=True: trabajamos con str en vez de bytes en todo el módulo.
_cliente = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_CACHE_PORT,
    db=0,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)

# Pool chico y dedicado: cada llamada a Redis corre acá para poder acotarla con un timeout duro
# (ver _llamar_con_limite). Si Redis está caído, los hilos que ya estaban en vuelo cuando el
# circuito se abre quedan bloqueados hasta que su propio intento falle por su cuenta (puede
# tardar bastante, según lo que esté colgando la conexión) — con solo 4 workers y el
# CircuitBreaker cortando intentos nuevos apenas se detectan fallos, el costo queda acotado en
# vez de ir acumulando un hilo colgado por cada petición mientras dure la caída.
_ejecutor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="redis-cache")

# Mismo patrón que breaker_productores en main.py: tras 3 fallos seguidos, deja de intentar
# durante 30s (falla instantáneo con CircuitBreakerError) en vez de seguir pagando el timeout
# completo en cada petición mientras Redis sigue caído.
_circuito = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=30)


def _llamar_con_limite(func, *args, **kwargs):
    futuro = _ejecutor.submit(func, *args, **kwargs)
    try:
        return futuro.result(timeout=LIMITE_ESPERA_SEGUNDOS)
    except FuturoExpirado:
        raise redis.exceptions.TimeoutError(
            f"la operación de caché no respondió en {LIMITE_ESPERA_SEGUNDOS}s"
        )


def _proteger(func, *args, **kwargs):
    """Ejecuta una llamada a Redis con tope de tiempo + circuit breaker. Devuelve
    (ok, resultado): ok=False para cualquier fallo (RedisError, timeout duro, o circuito
    abierto) — quien llama nunca necesita distinguir el motivo, solo que Redis no respondió."""
    try:
        return True, _circuito.call(_llamar_con_limite, func, *args, **kwargs)
    except pybreaker.CircuitBreakerError:
        return False, None
    except redis.RedisError as e:
        logger.warning(f"Redis no disponible ({func.__name__}): {e}")
        return False, None


def obtener(clave: str):
    """None si no hay hit, si el valor guardado está corrupto, o si Redis no responde."""
    ok, valor = _proteger(_cliente.get, clave)
    if not ok or valor is None:
        return None
    try:
        return json.loads(valor)
    except (TypeError, ValueError):
        return None


def guardar(clave: str, valor, ttl_segundos: int = TTL_SEGUNDOS) -> None:
    # jsonable_encoder primero: convierte UUID/Decimal/datetime exactamente igual que FastAPI
    # lo haría al serializar la respuesta, así un cache hit y un cache miss devuelven el mismo
    # JSON byte a byte (ej. precio como 4.0, no como el string "4.00").
    cuerpo = json.dumps(jsonable_encoder(valor))
    _proteger(_cliente.set, clave, cuerpo, ex=ttl_segundos)


def invalidar(*claves: str) -> None:
    """Borra una o más claves de inmediato (no espera el TTL). Se llama justo después de
    cualquier commit que cambie algo que GET /productos o GET /productos/{id} devuelven."""
    claves_validas = [c for c in claves if c]
    if not claves_validas:
        return
    _proteger(_cliente.delete, *claves_validas)
