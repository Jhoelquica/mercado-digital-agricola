# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py
"""Autenticación de las peticiones: el JWT que firma Usuarios (RS256), los roles y el secreto entre servicios.

Firma y verificación:
- Solo Usuarios tiene la clave privada (Secret jwt-firma, montado solo en sus pods; ver usuarios/seguridad.py). Todos los
  servicios, Usuarios incluido, verifican con las claves PÚBLICAS de la carpeta JWT_CLAVES_PUBLICAS (la ConfigMap
  jwt-claves-publicas, o .claves/jwt/publicas en compose). Cada archivo .pem es una clave, y su kid es la huella de la
  propia clave (kid_de), así que el nombre del archivo da igual. Varias claves a la vez permiten rotar sin cortar
  sesiones (README, "Claves de firma de los tokens").
- Validación estricta: algorithms=[ALGORITMO] ("RS256") y ningún otro. HS256 no está en ninguna lista, así que un token
  "HS256" firmado con la clave pública como secreto (en PEM o en DER) se rechaza. El kid del encabezado tiene que ser de
  una clave publicada, y se exigen iss, aud, exp, iat, sub, rol y ver.
- Las claves se cargan como objetos de cryptography (RSAPublicKey de BITS_MINIMOS o más), nunca como bytes sueltos.
- La versión de credencial ("ver") no se compara aquí: la compara el ForwardAuth del gateway en cada petición
  (usuarios/sesiones.py), que corre en todas las rutas de /api.
"""
import hashlib
import hmac
import logging
import os
import sys
import threading
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger("comun.auth")

ALGORITMO = "RS256"
EMISOR = "chakrashop-usuarios"
AUDIENCIA = "chakrashop-api"
BITS_MINIMOS = 2048
CLAIMS_OBLIGATORIOS = ["exp", "iat", "iss", "aud", "sub", "rol", "ver"]
# Un kid que no se conoce (una rotación en curso) hace releer la carpeta, como mucho una vez cada tanto.
RECARGA_SEGUNDOS = 30
MENSAJE_TOKEN_INVALIDO = "Token inválido o expirado"

CARPETA_CLAVES = os.getenv("JWT_CLAVES_PUBLICAS") or sys.exit(
    "Falta la variable de entorno JWT_CLAVES_PUBLICAS (la carpeta con las claves públicas que verifican los tokens): "
    "el servicio no arranca sin ella.")


def kid_de(clave_publica) -> str:
    """El kid de una clave: los primeros 16 caracteres del SHA-256 de su forma DER (SubjectPublicKeyInfo)."""
    der = clave_publica.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def cargar_claves_publicas(carpeta) -> dict:
    """{kid: RSAPublicKey} de los .pem de la carpeta. ValueError si alguno no es una clave pública RSA de BITS_MINIMOS
    o más: mejor no arrancar que aceptar una clave débil o de otro tipo."""
    claves = {}
    for archivo in sorted(Path(carpeta).glob("*.pem")):
        try:
            clave = serialization.load_pem_public_key(archivo.read_bytes())
        except ValueError:
            raise ValueError(f"{archivo.name} no es una clave pública en PEM")
        if not isinstance(clave, RSAPublicKey) or clave.key_size < BITS_MINIMOS:
            raise ValueError(f"{archivo.name} no es una clave pública RSA de {BITS_MINIMOS} bits o más")
        claves[kid_de(clave)] = clave
    return claves


try:
    _claves = cargar_claves_publicas(CARPETA_CLAVES)
except (OSError, ValueError) as error:
    sys.exit(f"No se pudieron leer las claves públicas del JWT en {CARPETA_CLAVES}: {error}")
if not _claves:
    sys.exit(f"No hay claves públicas del JWT (*.pem) en {CARPETA_CLAVES}: el servicio no arranca sin ellas.")
_ultima_recarga = time.monotonic()
_candado = threading.Lock()


def claves_publicas() -> dict:
    """{kid: RSAPublicKey} de las claves con que se verifica ahora."""
    return dict(_claves)


def _clave_para(kid):
    global _claves, _ultima_recarga
    if not isinstance(kid, str):
        return None
    clave = _claves.get(kid)
    if clave is not None:
        return clave
    with _candado:
        if time.monotonic() - _ultima_recarga >= RECARGA_SEGUNDOS:
            _ultima_recarga = time.monotonic()
            try:
                _claves = cargar_claves_publicas(CARPETA_CLAVES) or _claves
            except (OSError, ValueError):
                logger.exception("No se pudieron releer las claves públicas del JWT; siguen las de antes")
        return _claves.get(kid)


def validar(token: str) -> dict:
    """El payload de un token válido (firma RS256 de una clave publicada, iss, aud, vigente y con todos sus claims).
    Cualquier otra cosa, 401. Un token de un rol retirado tampoco vale."""
    try:
        clave = _clave_para(jwt.get_unverified_header(token).get("kid"))
        if clave is None:
            raise jwt.InvalidKeyError("kid desconocido")
        payload = jwt.decode(token, key=clave, algorithms=[ALGORITMO], issuer=EMISOR, audience=AUDIENCIA,
                             options={"require": CLAIMS_OBLIGATORIOS})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail=MENSAJE_TOKEN_INVALIDO)
    if payload.get("rol") in ROLES_RETIRADOS:
        raise HTTPException(status_code=401, detail=MENSAJE_CUENTA_DESACTIVADA)
    return payload


security = HTTPBearer()

# Roles retirados (histórico: rol verificador retirado el 2026-10-01). Sus cuentas quedaron desactivadas sin borrarse:
# no inician sesión (usuarios/main.py) y un token que todavía lleve uno de estos roles no vale en ningún servicio,
# aunque no haya vencido. Si se retira otro rol, se agrega aquí y se corre scripts/sincronizar_comun.py: vale para los ocho.
ROLES_RETIRADOS = frozenset({"verificador"})
MENSAJE_CUENTA_DESACTIVADA = "Esta cuenta está desactivada: su rol ya no existe en ChakraShop."

# Para rutas públicas que muestran algo distinto según quién mira (un borrador solo lo ve su productor): trae
# al usuario si el token es válido y, si no hay token o está vencido, sigue como visitante en vez de dar 401
# (mirar un producto público no exige sesión). Por el gateway, un token que no vale ya recibe 401 antes (auth-sesion).
security_opcional = HTTPBearer(auto_error=False)


def usuario_opcional(credentials: HTTPAuthorizationCredentials | None = Depends(security_opcional)):
    if credentials is None:
        return None
    try:
        return validar(credentials.credentials)
    except HTTPException:
        return None  # una cuenta desactivada (rol retirado) mira lo público como cualquier visitante, sin sesión


def verificar_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return validar(credentials.credentials)


def requiere_rol(*roles_permitidos):
    def verificar(usuario: dict = Depends(verificar_token)):
        if usuario.get("rol") not in roles_permitidos:
            raise HTTPException(
                status_code=403,
                detail=f"Esta acción requiere rol: {', '.join(roles_permitidos)}"
            )
        return usuario
    return verificar


def secreto_de_servicio_valido(recibido, esperado) -> bool:
    """Comprueba el secreto de servicio a servicio (cabecera X-Servicio-Secreto) en tiempo constante.

    FALLA CERRADO: si el secreto no está configurado (None o vacío) no pasa nadie. Antes cada
    endpoint hacía `if cabecera != SECRETO`: con la variable de entorno sin definir (SECRETO = None)
    una petición sin cabecera (None) resultaba igual y pasaba, así que bastaba un despliegue sin la
    variable para dejar abiertos los endpoints internos y los de limpieza."""
    if not esperado or not recibido:
        return False
    return hmac.compare_digest(str(recibido).encode("utf-8"), str(esperado).encode("utf-8"))
