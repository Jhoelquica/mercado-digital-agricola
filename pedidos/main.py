from prometheus_fastapi_instrumentator import Instrumentator
import os
import re
import math
import uuid
import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field, field_validator
from rabbitmq_publisher import publicar_evento
from auth import verificar_token
from auth import secreto_de_servicio_valido
from rabbitmq_consumer import lanzar_consumidor_en_hilo
from auth import verificar_token, requiere_rol

from database import Base, engine, SessionLocal
import models

Base.metadata.create_all(bind=engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

# evento de aranque
@app.on_event("startup")
def iniciar():
    lanzar_consumidor_en_hilo()

CLAVE_ALMACEN = "almacen_ubicacion"
# Aproximación del Mercado Nery García Zárate, en Ayacucho — valor inicial hasta que el Admin lo
# cambie desde PATCH /configuracion/almacen. Sembrado una sola vez al arrancar (ver
# sembrar_configuracion_almacen); un cambio posterior por el Admin nunca se pisa con esto.
ALMACEN_LATITUD_DEFAULT = -13.1553
ALMACEN_LONGITUD_DEFAULT = -74.2287

@app.on_event("startup")
def sembrar_configuracion_almacen():
    db = SessionLocal()
    try:
        # Idempotente por existencia de la clave, no por sus valores — así un cambio hecho por el
        # Admin en un arranque anterior nunca se pisa en el siguiente restart.
        ya_existe = db.query(models.ConfiguracionSistema).filter(
            models.ConfiguracionSistema.clave == CLAVE_ALMACEN
        ).first()
        if ya_existe:
            return

        nueva = models.ConfiguracionSistema(
            clave=CLAVE_ALMACEN,
            valor_latitud=ALMACEN_LATITUD_DEFAULT,
            valor_longitud=ALMACEN_LONGITUD_DEFAULT,
        )
        db.add(nueva)
        try:
            db.commit()
        except IntegrityError:
            # Mismo caso de carrera que sembrar_admin_inicial en usuarios/main.py: con varias
            # réplicas arrancando a la vez sobre una BD recién creada, más de una puede pasar el
            # chequeo "no existe" antes de que cualquiera haga commit — el UNIQUE en clave deja
            # pasar solo a la primera, las demás ceden la siembra sin romper el arranque del pod.
            db.rollback()
    finally:
        db.close()

CLAVE_UMBRAL_PEDIDO_GRANDE = "umbral_pedido_grande_kg"
# Aproximación del peso de un saco chico — valor inicial hasta que el Admin lo cambie desde
# PATCH /configuracion/umbral-pedido-grande. Mismo criterio de siembra que el almacén: una sola
# vez al arrancar, un cambio posterior del Admin nunca se pisa.
UMBRAL_PEDIDO_GRANDE_KG_DEFAULT = 25.0

@app.on_event("startup")
def sembrar_configuracion_umbral_pedido_grande():
    db = SessionLocal()
    try:
        ya_existe = db.query(models.ConfiguracionSistema).filter(
            models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
        ).first()
        if ya_existe:
            return

        nueva = models.ConfiguracionSistema(
            clave=CLAVE_UMBRAL_PEDIDO_GRANDE,
            valor_numerico=UMBRAL_PEDIDO_GRANDE_KG_DEFAULT,
        )
        db.add(nueva)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    finally:
        db.close()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PRODUCTOS_URL = os.getenv("PRODUCTOS_URL", "http://localhost:8002")
# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
PEDIDOS_A_PRODUCTOS_SECRETO = os.getenv("PEDIDOS_A_PRODUCTOS_SECRETO")  # para llamar a descontar_stock en Productos
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_pedido (DELETE de limpieza QA)
TRANSPORTE_A_PEDIDOS_SECRETO = os.getenv("TRANSPORTE_A_PEDIDOS_SECRETO")  # obtener_pedido_interno (lo llama Transporte)

# Rango aproximado del departamento de Ayacucho — el pedido solo se acepta si el destino de
# entrega cae dentro de esta caja (no es el polígono real del departamento, es un rectángulo que
# lo contiene; suficiente para la validación que se pidió, sin traer una librería de geometría).
AYACUCHO_LAT_MIN = -15.20
AYACUCHO_LAT_MAX = -12.85
AYACUCHO_LNG_MIN = -75.10
AYACUCHO_LNG_MAX = -73.00

MONTO_MINIMO_PEDIDO = 30.00  # soles, sobre el subtotal (precio × cantidad de cada item, sin envío)

# Copiada tal cual de transporte/main.py (calcular_distancia_km) — cada microservicio de este
# proyecto es autosuficiente, no hay una librería compartida entre servicios (confirmado: la
# misma fórmula también vive, con otra variante numérica equivalente, en
# certificacion/logica_cadena.py para el geofencing de cosechas).
def calcular_distancia_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    R = 6371  # radio de la Tierra en km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

# Tramos de envío por distancia entre el almacén (ConfiguracionSistema) y el destino del pedido.
# Cada tupla es (límite_km, costo) — el primer límite que la distancia no supera define el costo;
# si supera todos, se cobra COSTO_ENVIO_MAS_DE_25KM.
COSTO_ENVIO_TRAMOS = [
    (10, 5.00),
    (25, 8.00),
]
COSTO_ENVIO_MAS_DE_25KM = 12.00

def calcular_costo_envio(distancia_km: float) -> float:
    for limite_km, costo in COSTO_ENVIO_TRAMOS:
        if distancia_km <= limite_km:
            return costo
    return COSTO_ENVIO_MAS_DE_25KM

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Validación fail-fast: el destino de entrega debe caer dentro de la región de Ayacucho. La usan
# crear_pedido y la cotización del envío, para que ambas rechacen exactamente lo mismo.
def validar_destino_en_region(latitud: float, longitud: float):
    if not (AYACUCHO_LAT_MIN <= latitud <= AYACUCHO_LAT_MAX) or \
       not (AYACUCHO_LNG_MIN <= longitud <= AYACUCHO_LNG_MAX):
        raise HTTPException(
            status_code=400,
            detail="El destino del pedido debe estar dentro de la región de Ayacucho.",
        )

# Distancia y costo de envío desde la ubicación ACTUAL del almacén (nunca hardcodeada: un cambio del
# Admin en /configuracion/almacen afecta a los pedidos nuevos sin tocar código). Es la única
# fuente del costo: la usan crear_pedido (que lo guarda) y la cotización (que lo muestra antes de
# pagar), así que lo que ve el comprador es siempre lo que se le cobra.
def calcular_envio_a_destino(db: Session, latitud: float, longitud: float):
    almacen = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_ALMACEN
    ).first()
    if not almacen:
        # No debería pasar nunca en la práctica (sembrar_configuracion_almacen corre al arrancar
        # cada réplica), pero sin esto se devolvería/guardaría un costo de envío inventado.
        raise HTTPException(status_code=503, detail="No se pudo calcular el costo de envío: falta la configuración del almacén")

    distancia_km = calcular_distancia_km(almacen.valor_latitud, almacen.valor_longitud, latitud, longitud)
    return distancia_km, calcular_costo_envio(distancia_km)

# Topes de un pedido. Sin límite inferior, una línea con cantidad negativa restaba dinero al total (el
# cargo se calcula sumando cantidad x precio) y "descontaba" stock al revés; con una segunda línea
# positiva para pasar el mínimo de compra, el pedido salía casi gratis y el stock del producto subía.
CANTIDAD_MAXIMA_POR_LINEA = 100_000
LINEAS_MAXIMAS_POR_PEDIDO = 50
NOMBRE_COMPRADOR_MAXIMO = 100


class ItemPedido(BaseModel):
    # UUID y no str: el valor se pega en la URL que se le pide a Productos, y un texto libre podía
    # colar rutas ("../") en esa petición.
    producto_id: uuid.UUID
    cantidad: int = Field(ge=1, le=CANTIDAD_MAXIMA_POR_LINEA)
    # None = unidad base del producto (comportamiento de siempre). Un valor (ej. "saco") busca esa
    # unidad dentro de producto["unidades_alternativas"] al validar (ver crear_pedido) — nunca se
    # acepta un precio del cliente, solo el nombre de la unidad; el precio siempre sale de
    # Productos.
    unidad: str | None = Field(default=None, max_length=30)

class PedidoCrear(BaseModel):
    comprador_nombre: str

    @field_validator("comprador_nombre")
    @classmethod
    def _nombre_valido(cls, valor: str) -> str:
        limpio = (valor or "").strip()
        if not limpio:
            raise ValueError("Indica el nombre de quien recibe el pedido")
        if "\x00" in limpio:
            raise ValueError("El nombre contiene caracteres no válidos")
        if len(limpio) > NOMBRE_COMPRADOR_MAXIMO:
            raise ValueError(f"El nombre no puede superar {NOMBRE_COMPRADOR_MAXIMO} caracteres")
        return limpio

    # Obligatorio: es el único canal para que el repartidor coordine la entrega con el
    # comprador (ver el bloque de contacto en Gestionar Envíos). Celular peruano: 9 dígitos
    # que empiezan con 9 — se normaliza (sin espacios) antes de guardarse.
    comprador_telefono: str

    @field_validator("comprador_telefono")
    @classmethod
    def _telefono_valido(cls, valor: str) -> str:
        limpio = re.sub(r"\s+", "", valor or "")
        if not re.fullmatch(r"9\d{8}", limpio):
            raise ValueError("El teléfono de contacto debe tener 9 dígitos y empezar con 9 (ej. 987654321)")
        return limpio

    destino_latitud: float
    destino_longitud: float
    items: list[ItemPedido] = Field(min_length=1, max_length=LINEAS_MAXIMAS_POR_PEDIDO)

class AlmacenActualizar(BaseModel):
    latitud: float
    longitud: float

class UmbralActualizar(BaseModel):
    umbral_kg: float

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "pedidos"}

# Público a propósito (sin Depends de auth): el comprador necesita poder ver de dónde sale su
# pedido antes de pagar, sin que eso exija tener sesión iniciada.
@app.get("/configuracion/almacen")
def obtener_ubicacion_almacen(db: Session = Depends(get_db)):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_ALMACEN
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del almacén")
    return {
        "latitud": config.valor_latitud,
        "longitud": config.valor_longitud,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

@app.patch("/configuracion/almacen")
def actualizar_ubicacion_almacen(
    datos: AlmacenActualizar,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("admin")),
):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_ALMACEN
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del almacén")

    config.valor_latitud = datos.latitud
    config.valor_longitud = datos.longitud
    db.commit()
    db.refresh(config)
    return {
        "latitud": config.valor_latitud,
        "longitud": config.valor_longitud,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

# Público a propósito, mismo criterio que /configuracion/almacen: el comprador (o cualquier
# integración) puede querer saber desde cuánto peso un pedido va a requerir vehículo grande, sin
# necesitar sesión iniciada.
@app.get("/configuracion/umbral-pedido-grande")
def obtener_umbral_pedido_grande(db: Session = Depends(get_db)):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del umbral de pedido grande")
    return {
        "umbral_kg": config.valor_numerico,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

@app.patch("/configuracion/umbral-pedido-grande")
def actualizar_umbral_pedido_grande(
    datos: UmbralActualizar,
    db: Session = Depends(get_db),
    usuario: dict = Depends(requiere_rol("admin")),
):
    config = db.query(models.ConfiguracionSistema).filter(
        models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="No se encontró la configuración del umbral de pedido grande")

    config.valor_numerico = datos.umbral_kg
    db.commit()
    db.refresh(config)
    return {
        "umbral_kg": config.valor_numerico,
        "fecha_actualizacion": config.fecha_actualizacion,
    }

@app.post("/pedidos")
def crear_pedido(datos: PedidoCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("comprador"))):
    # Validación 1 (fail-fast, antes de tocar la base de datos): el destino de entrega debe caer
    # dentro de la región de Ayacucho.
    validar_destino_en_region(datos.destino_latitud, datos.destino_longitud)

    # El costo de envío se calcula acá (no depende de los items, así que no hace falta esperar a
    # validarlos) — ver calcular_envio_a_destino.
    _, costo_envio = calcular_envio_a_destino(db, datos.destino_latitud, datos.destino_longitud)

    items_validados = []

    with httpx.Client() as client:
        for item in datos.items:
            try:
                resp = client.get(f"{PRODUCTOS_URL}/productos/{item.producto_id}", timeout=5)
            except httpx.RequestError:
                raise HTTPException(status_code=503, detail="Servicio de Productos no disponible")

            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail=f"Producto {item.producto_id} no existe")

            producto = resp.json()

            # Un borrador (sin precio) o un producto que aún viaja al almacén no se compra. Esta es la
            # revisión rápida para dar un mensaje claro antes de tocar la base; la definitiva es atómica,
            # dentro de Productos, al descontar el stock (más abajo).
            if producto.get("estado", "disponible") != "disponible" or producto.get("precio") is None:
                raise HTTPException(status_code=409, detail=f"{producto.get('nombre', 'El producto')} aún no está disponible para comprar")

            if item.unidad is None:
                # Comportamiento de siempre, sin cambios: precio y cantidad ya están en la unidad
                # base del producto.
                precio_unitario = producto["precio"]
                cantidad_base = item.cantidad
            else:
                # Busca la unidad alternativa DENTRO de la respuesta fresca de Productos — nunca
                # se confía en un precio que mande el cliente (acá ni siquiera existe ese campo
                # en ItemPedido), solo en el nombre de la unidad.
                unidad_alt = next(
                    (u for u in producto.get("unidades_alternativas", []) if u["unidad"] == item.unidad),
                    None,
                )
                if unidad_alt is None:
                    raise HTTPException(status_code=400, detail="Este producto no está disponible en esa unidad.")
                precio_unitario = unidad_alt["precio"]
                # Conversión a unidad base SOLO para validar stock y para lo que se le manda a
                # descontar_stock (que sigue recibiendo todo en unidad base, sin cambios de su
                # lado) — precio_unitario y la cantidad guardada en PedidoItem quedan en la
                # unidad que eligió el comprador.
                cantidad_base = item.cantidad * unidad_alt["factor_a_base"]

            if producto["stock"] < cantidad_base:
                raise HTTPException(status_code=409, detail=f"Stock insuficiente para {producto['nombre']}")

            # Peso real de esta línea (cantidad ya en unidad base): directo si la unidad base del
            # producto es "kg" (1 unidad base = 1 kg, no hace falta factor). Si no es "kg", se
            # convierte con producto["equivalencia_kg"] cuando el producto lo tiene (llega desde
            # RegistroProduccion.equivalencia_kg vía aprobar_cosecha — ver Producto.equivalencia_kg
            # en productos/models.py); si el producto no lo tiene (ej. uno creado a mano, sin ese
            # dato), el peso sigue siendo indeterminable.
            if producto.get("unidad_medida") == "kg":
                peso_kg = cantidad_base
            elif producto.get("equivalencia_kg") is not None:
                peso_kg = cantidad_base * producto["equivalencia_kg"]
            else:
                peso_kg = None

            items_validados.append((item, producto, precio_unitario, cantidad_base, peso_kg))

        # Validación 2 (fail-fast, todavía antes de tocar la base de datos): el subtotal (precio ×
        # cantidad de cada item, sin contar envío) debe alcanzar el mínimo de compra. precio_unitario
        # y cantidad ya están en la MISMA unidad (la que eligió el comprador), así que la cuenta es
        # correcta sin importar si es la unidad base o una alternativa.
        subtotal = sum(precio_unitario * item.cantidad for item, producto, precio_unitario, cantidad_base, peso_kg in items_validados)
        if subtotal < MONTO_MINIMO_PEDIDO:
            raise HTTPException(
                status_code=400,
                detail="El monto mínimo de compra es S/30.00. Agrega más productos para continuar.",
            )

        # Peso total del pedido: null si CUALQUIER línea tiene peso desconocido — no se aproxima
        # ni se ignora la línea faltante, porque no podemos afirmar un total si falta un dato.
        pesos = [peso_kg for _, _, _, _, peso_kg in items_validados]
        peso_total_kg = sum(pesos) if all(p is not None for p in pesos) else None

        # Mismo criterio que el almacén: el umbral se lee de ConfiguracionSistema (nunca
        # hardcodeado), así un cambio del Admin en /configuracion/umbral-pedido-grande afecta a
        # los pedidos nuevos sin tocar código.
        umbral_config = db.query(models.ConfiguracionSistema).filter(
            models.ConfiguracionSistema.clave == CLAVE_UMBRAL_PEDIDO_GRANDE
        ).first()
        if not umbral_config:
            # No debería pasar nunca en la práctica (sembrar_configuracion_umbral_pedido_grande
            # corre al arrancar cada réplica), pero sin esto un pedido se crearía con un criterio
            # de vehículo grande inventado.
            raise HTTPException(status_code=503, detail="No se pudo determinar el umbral de pedido grande: falta la configuración")

        requiere_vehiculo_grande = peso_total_kg is not None and peso_total_kg > umbral_config.valor_numerico

        # Todo validado: ahora sí descontamos stock y creamos el pedido
        nuevo_pedido = models.Pedido(
            usuario_id=usuario.get("sub"),
            comprador_nombre=datos.comprador_nombre,
            comprador_telefono=datos.comprador_telefono,
            destino_latitud=datos.destino_latitud,
            destino_longitud=datos.destino_longitud,
            costo_envio=costo_envio,
            peso_total_kg=peso_total_kg,
            requiere_vehiculo_grande=requiere_vehiculo_grande,
        )
        db.add(nuevo_pedido)
        db.flush()  # genera el id del pedido sin cerrar la transacción todavía

        # Todas las líneas se descuentan juntas, en una sola llamada: si una no alcanza (otro comprador se
        # llevó el stock justo antes, dos líneas del mismo producto que juntas superan el stock, un
        # producto que ya no está disponible) Productos responde 409 y no descuenta nada. Antes se llamaba
        # línea por línea sin mirar la respuesta y el pedido se creaba igual.
        try:
            resp_stock = client.post(
                f"{PRODUCTOS_URL}/productos/interno/descontar-stock-lote",
                json={"lineas": [
                    {"producto_id": str(item.producto_id), "cantidad": cantidad_base}
                    for item, _, _, cantidad_base, _ in items_validados
                ]},
                headers={"X-Servicio-Secreto": PEDIDOS_A_PRODUCTOS_SECRETO or ""},
                timeout=5,
            )
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Servicio de Productos no disponible")
        if resp_stock.status_code != 200:
            try:
                detalle = resp_stock.json().get("detail")
            except ValueError:
                detalle = None
            if resp_stock.status_code in (404, 409) and isinstance(detalle, str):
                raise HTTPException(status_code=resp_stock.status_code, detail=detalle)
            raise HTTPException(status_code=503, detail="No se pudo reservar el stock de los productos")

        for item, producto, precio_unitario, cantidad_base, peso_kg in items_validados:
            nuevo_item = models.PedidoItem(
                pedido_id=nuevo_pedido.id,
                producto_id=item.producto_id,
                cantidad=item.cantidad,
                unidad=item.unidad,
                precio_unitario=precio_unitario,
                peso_kg=peso_kg,
            )
            db.add(nuevo_item)

    db.commit()
    db.refresh(nuevo_pedido)
    publicar_evento({
        "evento": "pedido_creado",
        "pedido_id": str(nuevo_pedido.id),
        "usuario_id": str(usuario.get("sub")),
        "comprador_nombre": nuevo_pedido.comprador_nombre,
        "costo_envio": float(nuevo_pedido.costo_envio),
        "items": [
            {
                "producto_id": str(i.producto_id),
                "cantidad": i.cantidad,
                "precio_unitario": float(i.precio_unitario),
            }
            for i in nuevo_pedido.items
        ],
    })
    return nuevo_pedido

# Público a propósito (mismo criterio que /configuracion/almacen): el comprador ve cuánto le va a
# costar el envío ANTES de pagar, sin crear todavía el pedido. DEBE declararse antes de
# /pedidos/{pedido_id}: si no, esa ruta con parámetro se comería "costo-envio" como si fuera un id.
@app.get("/pedidos/costo-envio")
def cotizar_costo_envio(latitud: float, longitud: float, db: Session = Depends(get_db)):
    validar_destino_en_region(latitud, longitud)
    distancia_km, costo_envio = calcular_envio_a_destino(db, latitud, longitud)
    return {"costo_envio": costo_envio, "distancia_km": distancia_km}

def _pedido_o_404(db: Session, pedido_id: uuid.UUID):
    pedido = db.query(models.Pedido).filter(models.Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    pedido.items  # fuerza la carga de la relación para que se incluya en la respuesta
    return pedido


# Ruta INTERNA para Transporte (proponer envíos, liquidar al repartidor, certificar la entrega y comprobar
# de quién es un pedido antes de mostrar un envío): esos procesos no tienen a ningún usuario detrás, así que
# se protege con el secreto de servicio TRANSPORTE_A_PEDIDOS_SECRETO. Va antes de /pedidos/{pedido_id}.
@app.get("/pedidos/interno/{pedido_id}")
def obtener_pedido_interno(
    pedido_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_servicio_secreto: str = Header(None),
):
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_PEDIDOS_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")
    return _pedido_o_404(db, pedido_id)


# Quién puede ver un pedido: el comprador que lo hizo y los roles que participan de la entrega (repartidor,
# productor) o administran (admin). Antes era público: bastaba conocer el id de un pedido para leer el
# nombre, el teléfono y las coordenadas exactas del comprador. pedido_id va tipado como UUID: un id mal
# formado da 422 en vez de un error de la base.
ROLES_CON_ACCESO_A_PEDIDOS = ("admin", "repartidor", "productor")


@app.get("/pedidos/{pedido_id}")
def obtener_pedido(pedido_id: uuid.UUID, db: Session = Depends(get_db), usuario: dict = Depends(verificar_token)):
    pedido = _pedido_o_404(db, pedido_id)
    es_el_comprador = str(pedido.usuario_id) == str(usuario.get("sub"))
    if not es_el_comprador and usuario.get("rol") not in ROLES_CON_ACCESO_A_PEDIDOS:
        raise HTTPException(status_code=403, detail="No tienes permiso para ver este pedido")
    return pedido

@app.delete("/pedidos/{pedido_id}")
def eliminar_pedido(pedido_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    pedido = db.query(models.Pedido).filter(models.Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    db.query(models.PedidoItem).filter(models.PedidoItem.pedido_id == pedido_id).delete()
    db.delete(pedido)
    db.commit()
    return {"mensaje": "Pedido eliminado"}