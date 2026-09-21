import hmac
import os
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

security = HTTPBearer()

# Para rutas públicas que muestran algo distinto según quién mira (un borrador solo lo ve su productor): trae
# al usuario si el token es válido y, si no hay token o está vencido, sigue como visitante en vez de dar 401
# (mirar un producto público no exige sesión).
security_opcional = HTTPBearer(auto_error=False)


def usuario_opcional(credentials: HTTPAuthorizationCredentials | None = Depends(security_opcional)):
    if credentials is None:
        return None
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None

def verificar_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
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
