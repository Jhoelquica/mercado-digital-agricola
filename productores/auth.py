import hmac
import os
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

security = HTTPBearer()

# Roles que ya no existen ("verificador" se retiró del todo). Sus cuentas quedaron desactivadas sin borrarse: no inician
# sesión (usuarios/main.py) y un token que todavía lleve uno de estos roles no vale en ningún servicio, aunque no haya
# vencido. Cada servicio tiene su copia de esta lista (no hay librería común entre microservicios): si se retira otro
# rol, se agrega en los ocho auth.py.
ROLES_RETIRADOS = frozenset({"verificador"})
MENSAJE_CUENTA_DESACTIVADA = "Esta cuenta está desactivada: su rol ya no existe en ChakraShop."

def verificar_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    if payload.get("rol") in ROLES_RETIRADOS:
        raise HTTPException(status_code=401, detail=MENSAJE_CUENTA_DESACTIVADA)
    return payload

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
