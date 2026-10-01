from prometheus_fastapi_instrumentator import Instrumentator
import io
import os
import hmac
import uuid
import logging

# Sin esto, el proceso nunca queda con un handler configurado y Python cae al "handler de
# último recurso": solo WARNING y superior salen por stderr — cualquier logger.info()/error() de
# acá o de reintento_liquidacion_repartidor.py (cuyo hilo corre en este mismo proceso) queda mudo
# sin ningún aviso, simplemente no aparece en `kubectl logs`. Mismo problema que ya resolvimos en
# pagos/main.py — va antes de crear cualquier logger o importar cualquier módulo del proyecto.
logging.basicConfig(level=logging.INFO)

import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from auth import verificar_token, requiere_rol, security
from auth import secreto_de_servicio_valido
from pedidos_interno import cabecera_pedidos
from fastapi.responses import JSONResponse
from imagenes import leer_imagen_validada, LIMITE_CUERPO_BYTES, MENSAJE_MUY_GRANDE

from database import Base, engine, SessionLocal
import models
from migraciones import agregar_columnas_faltantes
from minio_client import subir_foto_entrega
from logica_repartidores import proponer_envio_a_repartidor, intentar_resolver_envio_huerfano
from logica_liquidacion_repartidor import notificar_liquidacion_repartidor
from logica_certificacion import notificar_entrega_a_certificacion
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from rabbitmq_publisher import publicar_evento
from reintento_huerfanos import lanzar_reintento_huerfanos_en_hilo
from reintento_liquidacion_repartidor import lanzar_reintento_liquidacion_repartidor_en_hilo
from reintento_certificacion import lanzar_reintento_certificacion_en_hilo
import math

logger = logging.getLogger("transporte.main")

Base.metadata.create_all(bind=engine)
# create_all() no agrega columnas nuevas a tablas que ya existen: sin esto, una base creada antes de
# una columna nueva en models.py falla en cada consulta (ver migraciones.py).
agregar_columnas_faltantes(engine, Base)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

@app.middleware("http")
async def limitar_el_tamano_de_las_subidas(request, call_next):
    """Rechaza por su Content-Length una subida demasiado grande ANTES de leer el cuerpo: sin esto el servidor
    recibía entero un archivo de cualquier tamaño y recién después decidía. Va antes del middleware de CORS a
    propósito (queda por dentro), para que el 413 también lleve las cabeceras CORS y el navegador muestre el
    mensaje en vez de un error de red."""
    if request.method == "POST" and request.url.path.endswith("/entregar"):
        longitud = request.headers.get("content-length", "")
        if longitud.isdigit() and int(longitud) > LIMITE_CUERPO_BYTES:
            return JSONResponse(status_code=413, content={"detail": MENSAJE_MUY_GRANDE})
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
#FUNCIONES AUXILIARES
@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()
    lanzar_reintento_huerfanos_en_hilo()
    lanzar_reintento_liquidacion_repartidor_en_hilo()
    lanzar_reintento_certificacion_en_hilo()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def calcular_distancia_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    R = 6371  # radio de la Tierra en km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

#clases esquemas de entrada

class EstadoEnvio(BaseModel):
    estado: str

# Lista fija — mismo criterio que UNIDADES_VALIDAS en productos/main.py: sin esto, tipo_vehiculo
# terminaría con valores inconsistentes ("Moto", "motocicleta", etc.) que el matching por
# capacidad de la sub-entrega siguiente no podría agrupar de forma confiable.
TIPOS_VEHICULO_VALIDOS = ["moto", "auto", "camioneta", "camion"]

class RepartidorCrear(BaseModel):
    nombre: str
    dni: str
    tipo_vehiculo: str
    capacidad_maxima_kg: float

class UbicacionActualizar(BaseModel):
    latitud: str
    longitud: str

#endpoint


# Un envío SIN el PIN: es lo que reciben el repartidor, el productor (entrega directa) y el admin en los
# listados y en cada respuesta que devuelve un envío. El PIN (pin_entrega) es el código que el comprador
# le dicta al repartidor en la puerta como prueba de que de verdad recibió el pedido: si el repartidor
# pudiera leerlo, podría marcar la entrega sin ver al comprador y cobrar la liquidación. Solo lo ven el
# comprador dueño del pedido y el admin (EnvioConPin, en GET /envios/{pedido_id}).
class EnvioSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repartidor_id: uuid.UUID | None = None
    pedido_id: uuid.UUID
    estado: str | None = None
    transportista: str | None = None
    fecha_creacion: datetime | None = None
    repartidor_latitud: str | None = None
    repartidor_longitud: str | None = None
    liquidacion_confirmada: bool | None = None
    certificacion_confirmada: bool | None = None
    productor_id: uuid.UUID | None = None
    motivo_pendiente: str | None = None
    foto_entrega_url: str | None = None
    entrega_latitud: str | None = None
    entrega_longitud: str | None = None


class EnvioConPin(EnvioSalida):
    pin_entrega: str | None = None


class ResumenEntrega(BaseModel):
    """Lo que el repartidor ve de un pedido para decidir si acepta la propuesta (sin datos del comprador)."""
    distancia_km: float | None = None
    peso_total_kg: float | None = None
    costo_envio: float | None = None
    requiere_vehiculo_grande: bool | None = None


class PropuestaSalida(EnvioSalida):
    # None si Pedidos no respondió: la propuesta se muestra igual, solo sin estos datos.
    resumen: ResumenEntrega | None = None


@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "transporte"}

# Solo los envíos del propio repartidor: antes devolvía TODOS los envíos (con su PIN y su ubicación) a
# cualquier repartidor.
@app.get("/envios", response_model=list[EnvioSalida])
def listar_envios(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        return []
    return db.query(models.Envio).filter(models.Envio.repartidor_id == repartidor.id).all()

def _resumenes_de_entrega(pedidos_ids) -> dict:
    """pedido_id -> ResumenEntrega, pidiéndoselo a Pedidos por su ruta interna (con el secreto de servicio). Lo que
    no se pueda obtener queda fuera (la propuesta se muestra sin esos datos): si Pedidos no responde no se insiste
    con las demás, para no sumar un tiempo de espera por cada propuesta."""
    resumenes = {}
    try:
        with httpx.Client() as client:
            for pedido_id in pedidos_ids:
                resp = client.get(
                    f"{PEDIDOS_URL}/pedidos/interno/{pedido_id}/resumen-entrega",
                    headers=cabecera_pedidos(),
                    timeout=5,
                )
                if resp.status_code == 200:
                    try:
                        resumenes[pedido_id] = ResumenEntrega(**resp.json())
                    except (ValueError, TypeError):
                        pass
    except httpx.RequestError:
        pass
    return resumenes


#ENDPOINT PARA VER PROPUESTAS PENDIENTES
@app.get("/envios/propuestas", response_model=list[PropuestaSalida])
def mis_propuestas(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    envios = db.query(models.Envio).filter(
        models.Envio.repartidor_id == repartidor.id,
        models.Envio.estado == "propuesto"
    ).all()
    resumenes = _resumenes_de_entrega([e.pedido_id for e in envios])
    propuestas = []
    for envio in envios:
        propuesta = PropuestaSalida.model_validate(envio)
        propuesta.resumen = resumenes.get(envio.pedido_id)
        propuestas.append(propuesta)
    return propuestas

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
PRODUCTORES_URL = os.getenv("PRODUCTORES_URL", "http://localhost:8001")
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_envio, eliminar_repartidor (DELETE de limpieza QA)

MAX_INTENTOS_PIN = 5        # códigos incorrectos seguidos antes de bloquear la confirmación
BLOQUEO_PIN_MINUTOS = 15    # con 5 intentos cada 15 minutos, probar los 10.000 códigos toma semanas


def _pedido_interno(pedido_id) -> dict:
    """El pedido completo por la ruta interna de Pedidos (con el secreto de servicio). 503 si Pedidos no
    responde, 404 si no existe."""
    try:
        with httpx.Client() as client:
            resp = client.get(f"{PEDIDOS_URL}/pedidos/interno/{pedido_id}", headers=cabecera_pedidos(), timeout=5)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Servicio de Pedidos no disponible")
    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return resp.json()


def _autorizar_envio(envio, usuario: dict, db: Session, pedido: dict | None = None) -> bool:
    """Comprueba que quien pide puede ver este envío y devuelve si además puede ver su PIN.

    - admin: sí, con PIN.
    - comprador: solo si el pedido es suyo (se le pregunta a Pedidos), con PIN.
    - repartidor: solo si el envío está asignado a él, SIN PIN.
    Cualquier otro caso: 403. Antes GET /envios/{pedido_id} y GET /envios/{id}/ruta eran públicos."""
    rol, sub = usuario.get("rol"), str(usuario.get("sub"))
    if rol == "admin":
        return True
    if rol == "comprador":
        pedido = pedido or _pedido_interno(envio.pedido_id)
        if str(pedido.get("usuario_id")) == sub:
            return True
    elif rol == "repartidor":
        repartidor = db.query(models.Repartidor).filter(models.Repartidor.usuario_id == sub).first()
        if repartidor and str(envio.repartidor_id) == str(repartidor.id):
            return False
    raise HTTPException(status_code=403, detail="No tienes permiso para ver este envío")

def _resolver_productor_autenticado(token: str):
    """Resuelve el Productor autenticado llamando a GET /productores/me con el mismo bearer
    token del caller — mismo patrón inline que ya usa Productos en varios de sus endpoints (ej.
    subir_imagen) para lo mismo. Sin circuit breaker a propósito: a diferencia de Productos (que
    llama a Productores en casi todos sus endpoints de escritura), este es el único call site de
    Transporte hacia Productores, no una dependencia de alto tráfico."""
    with httpx.Client() as client:
        try:
            resp = client.get(
                f"{PRODUCTORES_URL}/productores/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Productores no disponible")

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de productor")
    return resp.json()

# Server-side real (a diferencia de GET /envios + filtro en el frontend, ver informe de la
# sub-entrega anterior): filtra por Envio.productor_id, la columna que proponer_envio_a_repartidor
# ya puebla al decidir esta transición (ver logica_repartidores.py) — sin repetir la resolución
# cruzada contra Productos en cada consulta.
@app.get("/envios/pendientes-entrega-directa", response_model=list[EnvioSalida])
def envios_pendientes_entrega_directa(
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("productor")),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    productor = _resolver_productor_autenticado(credenciales.credentials)
    return db.query(models.Envio).filter(
        models.Envio.estado == "pendiente_entrega_directa",
        models.Envio.productor_id == productor["id"],
    ).all()

# Solo los que de verdad necesitan que el Admin registre un vehículo más grande (motivo_pendiente
# == "sin_capacidad_suficiente") — no los que simplemente esperan que se libere cualquier
# repartidor (motivo_pendiente == "sin_repartidor_disponible"), que no requieren ninguna acción
# del Admin y se resuelven solos apenas alguien quede disponible.
@app.get("/envios/pendientes-por-capacidad", response_model=list[EnvioSalida])
def envios_pendientes_por_capacidad(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("admin"))):
    return db.query(models.Envio).filter(
        models.Envio.estado == "pendiente_asignacion",
        models.Envio.motivo_pendiente == "sin_capacidad_suficiente",
    ).all()

@app.get("/envios/{envio_id}/ruta")
def obtener_ruta(envio_id: uuid.UUID, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")

    # A Pedidos solo se le pregunta antes de autorizar cuando hace falta (el comprador: su pedido dice de
    # quién es); el resto se autoriza con la base de Transporte y el pedido se pide después, para la ruta.
    pedido = _pedido_interno(envio.pedido_id) if usuario.get("rol") == "comprador" else None
    _autorizar_envio(envio, usuario, db, pedido=pedido)
    pedido = pedido or _pedido_interno(envio.pedido_id)

    if not pedido.get("destino_latitud") or not pedido.get("destino_longitud"):
        return {"disponible": False, "mensaje": "Este pedido no tiene coordenadas de destino registradas"}

    if not envio.repartidor_latitud or not envio.repartidor_longitud:
        return {"disponible": False, "mensaje": "El repartidor aún no ha compartido su ubicación"}

    distancia = calcular_distancia_km(
        envio.repartidor_latitud, envio.repartidor_longitud,
        pedido["destino_latitud"], pedido["destino_longitud"],
    )

    return {
        "disponible": True,
        "distancia_km": distancia,
        "tiempo_estimado_min": round(distancia / 25 * 60),  # asumiendo ~25 km/h promedio urbano/rural
        "origen": {"latitud": envio.repartidor_latitud, "longitud": envio.repartidor_longitud},
        "destino": {"latitud": pedido["destino_latitud"], "longitud": pedido["destino_longitud"]},
    }
@app.post("/envios/{envio_id}/aceptar", response_model=EnvioSalida)
def aceptar_propuesta(envio_id: str, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    if str(envio.repartidor_id) != str(repartidor.id):
        raise HTTPException(status_code=403, detail="Esta propuesta no es tuya")
    if envio.estado != "propuesto":
        raise HTTPException(status_code=409, detail="Esta propuesta ya no está disponible")
    # Un envío nuevo ya solo nace con el pago confirmado (rabbitmq_consumer.py), pero los creados antes de ese
    # arreglo nacían con el pedido: esto evita que se acepte, se entregue y se liquide un pedido sin pagar.
    if _pedido_interno(envio.pedido_id).get("estado") != "confirmado":
        raise HTTPException(status_code=409, detail="El pago de este pedido todavía no está confirmado: no se puede aceptar")

    envio.estado = "asignado"
    db.commit()
    db.refresh(envio)

    publicar_evento({
        "evento": "envio_actualizado",
        "pedido_id": str(envio.pedido_id),
        "estado": envio.estado,
    })
    return envio


@app.post("/envios/{envio_id}/rechazar")
def rechazar_propuesta(envio_id: str, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    if str(envio.repartidor_id) != str(repartidor.id):
        raise HTTPException(status_code=403, detail="Esta propuesta no es tuya")
    if envio.estado != "propuesto":
        raise HTTPException(status_code=409, detail="Esta propuesta ya no está disponible")

    # Se guarda ANTES de reasignar (misma transacción que el cambio de estado de abajo): a
    # partir de acá, este repartidor queda excluido de este envío para siempre, no solo para
    # este intento — proponer_envio_a_repartidor consulta esta tabla completa, no un ID puntual.
    db.add(models.EnvioRechazo(envio_id=envio.id, repartidor_id=repartidor.id))

    repartidor.estado_disponibilidad = "disponible"
    envio.repartidor_id = None
    envio.estado = "pendiente_asignacion"
    db.commit()

    proponer_envio_a_repartidor(envio.id, db, excluir_id=repartidor.id)
    # El repartidor que acaba de rechazar sigue "disponible" — puede tomar OTRO envío huérfano
    # más antiguo que este (si lo hay). excluir_id evita que, si el envío de arriba no encontró
    # a nadie más y volvió a quedar huérfano, este mismo disparador se lo re-ofrezca de
    # inmediato al mismo repartidor que lo acaba de rechazar.
    intentar_resolver_envio_huerfano(db, excluir_id=repartidor.id)

    return {"mensaje": "Propuesta rechazada, se ofreció a otro repartidor"}

@app.get("/envios/{pedido_id}")
def obtener_envio(pedido_id: uuid.UUID, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    envio = db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado para ese pedido")
    ve_el_pin = _autorizar_envio(envio, usuario, db)
    modelo = EnvioConPin if ve_el_pin else EnvioSalida
    return modelo.model_validate(envio).model_dump(mode="json")

@app.delete("/envios/{envio_id}")
def eliminar_envio(envio_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")

    db.delete(envio)
    db.commit()
    return {"mensaje": "Envío eliminado"}

def _efectos_post_entrega(db: Session, envio: models.Envio):
    """Cadena de efectos secundarios al confirmar una entrega — compartida entre el productor
    (entrega directa, sin PIN/foto/GPS, vía PATCH /estado) y el repartidor (vía POST /entregar).
    Extraída sin cambiar nada de su comportamiento — antes vivía inline dentro de
    actualizar_estado()."""
    # Solo si hubo repartidor (una entrega directa por el productor nunca tuvo uno asignado —
    # envio.repartidor_id sigue en None de punta a punta): no hay a quién liberar ni a quién
    # buscarle un huérfano nuevo, porque nadie quedó libre.
    if envio.repartidor_id:
        repartidor_envio = db.query(models.Repartidor).filter(
            models.Repartidor.id == envio.repartidor_id
        ).first()
        if repartidor_envio:
            repartidor_envio.estado_disponibilidad = "disponible"
            db.commit()
            # Disparador inmediato: este repartidor recién quedó libre, revisa si hay algún
            # envío huérfano esperando (el más antiguo primero) e intenta resolverlo ahora
            # mismo, sin esperar al job periódico de reintento_huerfanos.py.
            intentar_resolver_envio_huerfano(db)

    # TODO: "verificado_punto_venta" quedó a medias — falta geofencing y disparo automático real.
    # Un fallo acá no bloquea nada ni deshace la entrega ya confirmada — igual que con la
    # liquidación de repartidor de abajo, queda un rastro reintentable:
    # envio.certificacion_confirmada sigue en False y reintento_certificacion.py lo va a
    # reintentar cada INTERVALO_SEGUNDOS hasta que Certificación responda. Se dispara igual sin
    # importar quién marcó la entrega (repartidor o productor): la trazabilidad del producto no
    # depende de quién lo entregó.
    try:
        notificar_entrega_a_certificacion(db, envio)
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "No se pudo notificar a Certificación para el pedido %s (queda pendiente para el reintento)",
            envio.pedido_id,
            exc_info=True,
        )

    # Mismo guard que arriba: sin repartidor no hay nada que liquidarle por transporte (una
    # entrega directa no tuvo costo de repartidor) — mandar "repartidor_id": "None" a Pagos
    # crearía una liquidación basura para un repartidor que nunca existió.
    if envio.repartidor_id:
        # Mismo criterio no intrusivo que arriba: un fallo acá no bloquea nada ni deshace la
        # entrega ya confirmada. envio.liquidacion_confirmada sigue en False y
        # reintento_liquidacion_repartidor.py lo va a reintentar cada INTERVALO_SEGUNDOS hasta que
        # Pagos responda.
        try:
            notificar_liquidacion_repartidor(db, envio)
            db.commit()
        except Exception:
            db.rollback()
            logger.error(
                "No se pudo confirmar la liquidación del repartidor para el pedido %s (queda pendiente para el reintento)",
                envio.pedido_id,
                exc_info=True,
            )


@app.patch("/envios/{envio_id}/estado", response_model=EnvioSalida)
def actualizar_estado(
    envio_id: str,
    datos: EstadoEnvio,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("repartidor", "productor")),
    credenciales: HTTPAuthorizationCredentials = Depends(security),
):
    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")

    if usuario.get("rol") == "repartidor":
        # "asignado"/"en_camino" siguen igual acá. "entregado" ya NO — un repartidor tiene que
        # confirmar la entrega con el PIN del comprador y una foto (ver POST
        # /envios/{id}/entregar), no con un simple cambio de estado sin evidencia.
        if datos.estado == "entregado":
            raise HTTPException(
                status_code=400,
                detail="Para marcar un envío como entregado usa POST /envios/{id}/entregar con el código del comprador y una foto.",
            )
        repartidor = db.query(models.Repartidor).filter(
            models.Repartidor.usuario_id == usuario.get("sub")
        ).first()
        if not repartidor:
            raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")
        if str(envio.repartidor_id) != str(repartidor.id):
            raise HTTPException(status_code=403, detail="No puedes actualizar el estado de un envío que no es tuyo")
    else:
        # Productor: solo puede cerrar una entrega directa que ya asumió, y solo hacia
        # "entregado" — a diferencia del repartidor, acá SÍ hay una whitelist estricta, porque
        # no hay ninguna transición previa legítima que preservar (esta es la única que un
        # productor puede hacer por este endpoint). Sin PIN/foto: una entrega directa no tiene
        # repartidor de por medio, así que no aplica el mismo riesgo de "¿de verdad se entregó?".
        if datos.estado != "entregado":
            raise HTTPException(status_code=403, detail="Un productor solo puede marcar un envío como entregado")
        if envio.estado != "pendiente_entrega_directa":
            raise HTTPException(status_code=409, detail="Este envío no está pendiente de entrega directa")
        productor = _resolver_productor_autenticado(credenciales.credentials)
        if str(envio.productor_id) != str(productor["id"]):
            raise HTTPException(status_code=403, detail="No puedes actualizar un envío que no te corresponde")

    envio.estado = datos.estado
    db.commit()
    db.refresh(envio)

    publicar_evento({
        "evento": "envio_actualizado",
        "pedido_id": str(envio.pedido_id),
        "estado": envio.estado,
    })

    if datos.estado == "entregado":
        _efectos_post_entrega(db, envio)

    return envio


@app.post("/envios/{envio_id}/entregar", response_model=EnvioSalida)
def confirmar_entrega(
    envio_id: str,
    pin: str = Form(...),
    foto: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("repartidor")),
):
    """Evidencia de entrega para envíos con repartidor: PIN que el comprador le dicta en la
    puerta (ver pin_entrega, generado al crear el envío), foto del paquete entregado, y la
    última posición GPS que el repartidor ya venía enviando cada 15s (ver PATCH /ubicacion) —
    copiada a entrega_latitud/longitud ANTES de que siga cambiando con el siguiente envío."""
    if foto.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=422, detail="Solo se permiten imágenes JPEG, PNG o WEBP")
    # Tamaño y contenido reales (ver imagenes.py), antes de tocar la base: una foto inválida no gasta un intento
    # del código ni cambia nada.
    datos_foto, tipo_foto = leer_imagen_validada(foto)

    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    # with_for_update: dos intentos simultáneos con el mismo envío se turnan en vez de leer el mismo
    # contador de fallos (sin esto, mandar muchos códigos en paralelo esquivaba el tope de intentos).
    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).with_for_update().first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    if str(envio.repartidor_id) != str(repartidor.id):
        raise HTTPException(status_code=403, detail="No puedes confirmar la entrega de un envío que no es tuyo")
    if envio.estado not in ["asignado", "en_camino"]:
        raise HTTPException(status_code=409, detail="Este envío no está en un estado que permita confirmar la entrega")
    # Sin este tope, el repartidor podía probar los 10.000 códigos posibles y marcar la entrega sin ver
    # al comprador. La comparación va en tiempo constante.
    ahora = datetime.utcnow()
    if envio.pin_bloqueado_hasta and envio.pin_bloqueado_hasta > ahora:
        minutos = -(-int((envio.pin_bloqueado_hasta - ahora).total_seconds()) // 60)
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados códigos incorrectos. Vuelve a intentarlo en {max(minutos, 1)} minuto(s).",
        )
    if not envio.pin_entrega or not hmac.compare_digest(pin.encode("utf-8"), envio.pin_entrega.encode("utf-8")):
        envio.pin_intentos_fallidos = (envio.pin_intentos_fallidos or 0) + 1
        restantes = MAX_INTENTOS_PIN - envio.pin_intentos_fallidos
        if restantes <= 0:
            # El intento que agota el tope ya responde 429: el repartidor lo ve al momento, no en el
            # siguiente intento.
            envio.pin_bloqueado_hasta = ahora + timedelta(minutes=BLOQUEO_PIN_MINUTOS)
            envio.pin_intentos_fallidos = 0
            db.commit()
            raise HTTPException(
                status_code=429,
                detail=f"Demasiados códigos incorrectos. Por seguridad, la confirmación de este envío se bloqueó {BLOQUEO_PIN_MINUTOS} minutos.",
            )
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"El código no coincide con el que le dieron al comprador. Te quedan {restantes} intento{'s' if restantes != 1 else ''} antes de que se bloquee.",
        )

    try:
        url_foto = subir_foto_entrega(io.BytesIO(datos_foto), tipo_foto)
    except Exception:
        raise HTTPException(status_code=502, detail="No se pudo subir la foto al almacenamiento")

    envio.estado = "entregado"
    envio.foto_entrega_url = url_foto
    envio.entrega_latitud = envio.repartidor_latitud
    envio.entrega_longitud = envio.repartidor_longitud
    envio.pin_intentos_fallidos = 0
    envio.pin_bloqueado_hasta = None
    db.commit()
    db.refresh(envio)

    publicar_evento({
        "evento": "envio_actualizado",
        "pedido_id": str(envio.pedido_id),
        "estado": envio.estado,
    })

    _efectos_post_entrega(db, envio)

    return envio

@app.post("/repartidores")
def crear_repartidor(datos: RepartidorCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    if not datos.dni.isdigit() or len(datos.dni) != 8:
        raise HTTPException(status_code=422, detail="El DNI debe tener exactamente 8 dígitos numéricos")

    if datos.tipo_vehiculo not in TIPOS_VEHICULO_VALIDOS:
        raise HTTPException(
            status_code=422,
            detail=f"El tipo de vehículo debe ser uno de: {', '.join(TIPOS_VEHICULO_VALIDOS)}",
        )

    if datos.capacidad_maxima_kg <= 0:
        raise HTTPException(status_code=422, detail="La capacidad máxima debe ser mayor a 0 kg")

    usuario_id = usuario.get("sub")
    existente = db.query(models.Repartidor).filter(models.Repartidor.usuario_id == usuario_id).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya tienes un perfil de repartidor registrado")

    nuevo = models.Repartidor(
        usuario_id=usuario_id,
        nombre=datos.nombre,
        dni=datos.dni,
        tipo_vehiculo=datos.tipo_vehiculo,
        capacidad_maxima_kg=datos.capacidad_maxima_kg,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    # Un repartidor recién registrado arranca "disponible" (default del modelo) — es otra forma
    # más de que uno "se libere", así que dispara el mismo intento inmediato. No estaba en la
    # lista original de lugares donde esto pasa, pero es el mismo principio aplicado de forma
    # consistente, y es justo lo que hace falta para el caso de prueba de "registrar uno nuevo".
    intentar_resolver_envio_huerfano(db)
    return nuevo


@app.get("/repartidores/me")
def mi_perfil_repartidor(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor. Créalo primero.")
    return repartidor

@app.delete("/repartidores/{repartidor_id}")
def eliminar_repartidor(repartidor_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    repartidor = db.query(models.Repartidor).filter(models.Repartidor.id == repartidor_id).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Repartidor no encontrado")

    envios_activos = db.query(models.Envio).filter(
        models.Envio.repartidor_id == repartidor_id,
        models.Envio.estado.in_(["asignado", "en_camino"])
    ).count()
    if envios_activos > 0:
        raise HTTPException(status_code=409, detail="Este repartidor tiene envíos activos, no se puede eliminar")

    db.delete(repartidor)
    db.commit()
    return {"mensaje": "Repartidor eliminado"}

@app.patch("/envios/{envio_id}/ubicacion", response_model=EnvioSalida)
def actualizar_ubicacion(envio_id: str, datos: UbicacionActualizar, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")
    if str(envio.repartidor_id) != str(repartidor.id):
        raise HTTPException(status_code=403, detail="No puedes actualizar la ubicación de un envío que no es tuyo")
    if envio.estado not in ["asignado", "en_camino"]:
        raise HTTPException(status_code=409, detail="Este envío no está en un estado que permita actualizar ubicación")

    envio.repartidor_latitud = datos.latitud
    envio.repartidor_longitud = datos.longitud
    db.commit()
    db.refresh(envio)
    return envio


