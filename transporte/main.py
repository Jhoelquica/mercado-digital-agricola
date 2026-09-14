from prometheus_fastapi_instrumentator import Instrumentator
import os
import logging

# Sin esto, el proceso nunca queda con un handler configurado y Python cae al "handler de
# último recurso": solo WARNING y superior salen por stderr — cualquier logger.info()/error() de
# acá o de reintento_liquidacion_repartidor.py (cuyo hilo corre en este mismo proceso) queda mudo
# sin ningún aviso, simplemente no aparece en `kubectl logs`. Mismo problema que ya resolvimos en
# pagos/main.py — va antes de crear cualquier logger o importar cualquier módulo del proyecto.
logging.basicConfig(level=logging.INFO)

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
from auth import verificar_token, requiere_rol, security

from database import Base, engine, SessionLocal
import models
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

app = FastAPI()

Instrumentator().instrument(app).expose(app)

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


@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "transporte"}

@app.get("/envios")
def listar_envios(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    return db.query(models.Envio).all()

#ENDPOINT PARA VER PROPUESTAS PENDIENTES
@app.get("/envios/propuestas")
def mis_propuestas(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("repartidor"))):
    repartidor = db.query(models.Repartidor).filter(
        models.Repartidor.usuario_id == usuario.get("sub")
    ).first()
    if not repartidor:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de repartidor")

    return db.query(models.Envio).filter(
        models.Envio.repartidor_id == repartidor.id,
        models.Envio.estado == "propuesto"
    ).all()

PEDIDOS_URL = os.getenv("PEDIDOS_URL", "http://localhost:8003")
PRODUCTORES_URL = os.getenv("PRODUCTORES_URL", "http://localhost:8001")
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_envio, eliminar_repartidor (DELETE de limpieza QA)

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
@app.get("/envios/pendientes-entrega-directa")
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

@app.get("/envios/{envio_id}/ruta")
def obtener_ruta(envio_id: str, db: Session = Depends(get_db)):
    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")

    with httpx.Client() as client:
        try:
            resp_pedido = client.get(f"{PEDIDOS_URL}/pedidos/{envio.pedido_id}", timeout=5)
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Pedidos no disponible")

    if resp_pedido.status_code != 200:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    pedido = resp_pedido.json()

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
@app.post("/envios/{envio_id}/aceptar")
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
def obtener_envio(pedido_id: str, db: Session = Depends(get_db)):
    envio = db.query(models.Envio).filter(models.Envio.pedido_id == pedido_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado para ese pedido")
    return envio

@app.delete("/envios/{envio_id}")
def eliminar_envio(envio_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
        raise HTTPException(status_code=403, detail="No autorizado")

    envio = db.query(models.Envio).filter(models.Envio.id == envio_id).first()
    if not envio:
        raise HTTPException(status_code=404, detail="Envío no encontrado")

    db.delete(envio)
    db.commit()
    return {"mensaje": "Envío eliminado"}

@app.patch("/envios/{envio_id}/estado")
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
        # Comportamiento actual, sin cambios: cualquier estado vale (asignado/en_camino/
        # entregado, ver el <select> del panel de repartidor en el frontend), mientras el envío
        # sea suyo. Una whitelist de transiciones acá rompería esas transiciones legítimas —
        # queda para una sub-entrega aparte que enumere la máquina de estados completa.
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
        # productor puede hacer por este endpoint).
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
        # Solo si hubo repartidor (una entrega directa por el productor nunca tuvo uno asignado
        # — envio.repartidor_id sigue en None de punta a punta): no hay a quién liberar ni a
        # quién buscarle un huérfano nuevo, porque nadie quedó libre.
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
        # reintentar cada INTERVALO_SEGUNDOS hasta que Certificación responda. Se dispara igual
        # sin importar quién marcó la entrega (repartidor o productor): la trazabilidad del
        # producto no depende de quién lo entregó.
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
            # reintento_liquidacion_repartidor.py lo va a reintentar cada INTERVALO_SEGUNDOS hasta
            # que Pagos responda.
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
    if x_servicio_secreto != QA_LIMPIEZA_SECRETO:
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

@app.patch("/envios/{envio_id}/ubicacion")
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


