from prometheus_fastapi_instrumentator import Instrumentator
import os
import uuid

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
import qrcode
import io
from fastapi.responses import StreamingResponse

from database import Base, engine, SessionLocal
import models
from migraciones import asegurar_indice_unico_de_bloques
from logica_cadena import crear_bloque, verificar_cadena, generar_certificado_pedido
from auth import requiere_rol
from auth import secreto_de_servicio_valido
from fastapi import Header
import json

Base.metadata.create_all(bind=engine)
# create_all() no agrega restricciones a una tabla que ya existe: ver migraciones.py.
asegurar_indice_unico_de_bloques(engine)

app = FastAPI()

Instrumentator().instrument(app).expose(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Sin fallback hardcodeado a propósito (ver mismo comentario en usuarios/main.py).
TRANSPORTE_A_CERTIFICACION_SECRETO = os.getenv("TRANSPORTE_A_CERTIFICACION_SECRETO")  # crear_certificado_pedido, registrar_evento_interno (los llama Transporte)
QA_LIMPIEZA_SECRETO = os.getenv("QA_LIMPIEZA_SECRETO")  # eliminar_verificador (DELETE de limpieza QA)
PRODUCTORES_A_CERTIFICACION_SECRETO = os.getenv("PRODUCTORES_A_CERTIFICACION_SECRETO")  # registrar_cosecha_aprobada (lo llama Productores)
PRODUCTOS_A_CERTIFICACION_SECRETO = os.getenv("PRODUCTOS_A_CERTIFICACION_SECRETO")  # registrar_llegada_almacen (lo llama Productos)
USUARIOS_URL = os.getenv("USUARIOS_URL", "http://localhost:8006")
CERTIFICACION_A_USUARIOS_SECRETO = os.getenv("CERTIFICACION_A_USUARIOS_SECRETO")  # _credencial_asignada_por_el_admin (Certificación envía, Usuarios recibe)

class EventoInterno(BaseModel):
    evento: str
    datos: str | None = None

class CertificadoPedidoCrear(BaseModel):
    productos_ids: list[str]

DATOS_LLEGADA_MAXIMO = 600


class LlegadaAlmacenInterno(BaseModel):
    datos: str

    @field_validator("datos")
    @classmethod
    def _datos_validos(cls, valor: str) -> str:
        limpio = (valor or "").strip()
        if not limpio:
            raise ValueError("Indica qué llegó al almacén")
        if "\x00" in limpio:
            raise ValueError("El texto contiene caracteres no válidos")
        if len(limpio) > DATOS_LLEGADA_MAXIMO:
            raise ValueError(f"El texto no puede superar {DATOS_LLEGADA_MAXIMO} caracteres")
        return limpio


class CosechaAprobadaInterno(BaseModel):
    datos_cosecha: str | None = None
    datos_certificado: str | None = None
    # Usuario (no Verificador.id) que aprobó — Productores solo conoce el sub del token; acá se
    # resuelve al perfil de Verificador para dejar su nombre visible en la cadena. Si ese usuario
    # nunca completó su perfil de verificador, el bloque igual se crea, solo sin nombre.
    verificador_usuario_id: str | None = None

@app.post("/certificacion/pedido/{pedido_id}/certificado")
def crear_certificado_pedido(
        pedido_id: str,
        datos: CertificadoPedidoCrear,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    existente = db.query(models.CertificadoPedido).filter(
        models.CertificadoPedido.pedido_id == pedido_id
    ).first()
    if existente:
        return existente  # idempotente: si ya se generó, se devuelve el mismo

    resultado = generar_certificado_pedido(db, pedido_id, datos.productos_ids)

    nuevo_certificado = models.CertificadoPedido(
        pedido_id=pedido_id,
        merkle_root=resultado["merkle_root"],
        detalle=json.dumps(resultado["detalle"]),
    )
    db.add(nuevo_certificado)
    db.commit()
    db.refresh(nuevo_certificado)
    return nuevo_certificado


@app.get("/certificacion/pedido/{pedido_id}/certificado")
def obtener_certificado_pedido(pedido_id: str, db: Session = Depends(get_db)):
    certificado = db.query(models.CertificadoPedido).filter(
        models.CertificadoPedido.pedido_id == pedido_id
    ).first()
    if not certificado:
        raise HTTPException(status_code=404, detail="Este pedido aún no tiene certificado generado")

    return {
        "pedido_id": str(certificado.pedido_id),
        "merkle_root": certificado.merkle_root,
        "detalle": json.loads(certificado.detalle),
        "fecha_generado": certificado.fecha_generado,
    }

@app.post("/certificacion/{producto_id}/eventos-internos")
def registrar_evento_interno(
        producto_id: str,
        datos: EventoInterno,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    if not secreto_de_servicio_valido(x_servicio_secreto, TRANSPORTE_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    if datos.evento != "verificado_punto_venta":
        raise HTTPException(status_code=422, detail="Este endpoint solo acepta el evento verificado_punto_venta")

    bloque = crear_bloque(db, producto_id, datos.evento, datos.datos)
    return bloque

@app.post("/certificacion/{producto_id}/cosecha-aprobada")
def registrar_cosecha_aprobada(
        producto_id: str,
        datos: CosechaAprobadaInterno,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    """Llamado por Productores al aprobar una cosecha (aprobar_cosecha): registra en un solo paso
    los dos primeros eventos de la cadena del producto recién creado — cosecha_registrada y
    certificado_productor. Hasta ahora ningún flujo de la app los creaba (solo existía
    POST /eventos, que nadie invocaba), así que el QR de un producto salía con la cadena vacía.

    Idempotente por evento: un reintento (o un aprobar que se repite tras un fallo parcial) solo
    crea los que falten, nunca duplica. Falla cerrado: si el secreto no está configurado en este
    servicio, rechaza todo (a diferencia de comparar None == None)."""
    if not secreto_de_servicio_valido(x_servicio_secreto, PRODUCTORES_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    existentes = {
        b.evento for b in db.query(models.Bloque).filter(models.Bloque.producto_id == producto_id).all()
    }
    creados = []
    if "cosecha_registrada" not in existentes:
        creados.append(crear_bloque(db, producto_id, "cosecha_registrada", datos.datos_cosecha))
    if "certificado_productor" not in existentes:
        verificador_id = None
        if datos.verificador_usuario_id:
            verificador = db.query(models.Verificador).filter(
                models.Verificador.usuario_id == datos.verificador_usuario_id
            ).first()
            verificador_id = verificador.id if verificador else None
        creados.append(crear_bloque(db, producto_id, "certificado_productor", datos.datos_certificado, verificador_id))

    total = db.query(models.Bloque).filter(models.Bloque.producto_id == producto_id).count()
    return {"creados": len(creados), "total_bloques": total}

@app.post("/certificacion/{producto_id}/llegada-almacen")
def registrar_llegada_almacen(
        producto_id: uuid.UUID,
        datos: LlegadaAlmacenInterno,
        db: Session = Depends(get_db),
        x_servicio_secreto: str = Header(None)
):
    """Llamado por Productos cuando un Admin confirma que el producto llegó al almacén: deja el evento
    llegada_almacen (con lo declarado, lo recibido y las observaciones, en texto) al final de la cadena.
    Hasta ahora esa llegada no dejaba ningún rastro: el producto pasaba a "disponible" sin cantidad
    recibida y sin evento en la cadena que escanea el comprador.

    Idempotente: un producto tiene un solo evento de llegada, así que un reintento (por ejemplo, tras un
    fallo de Productos justo después de esta llamada) no duplica el bloque. Falla cerrado si el secreto
    no está configurado."""
    if not secreto_de_servicio_valido(x_servicio_secreto, PRODUCTOS_A_CERTIFICACION_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    existente = db.query(models.Bloque).filter(
        models.Bloque.producto_id == producto_id,
        models.Bloque.evento == "llegada_almacen",
    ).first()
    if existente:
        return {"creado": False, "indice": existente.indice}

    bloque = crear_bloque(db, str(producto_id), "llegada_almacen", datos.datos)
    return {"creado": True, "indice": bloque.indice}

@app.get("/salud")
def salud():
    return {"estado": "ok", "servicio": "certificacion"}

# POST /certificacion/{producto_id}/eventos se eliminó: dejaba a cualquier productor o verificador
# agregar bloques a la cadena de CUALQUIER producto (no comprobaba que existiera ni que fuera suyo),
# con texto libre, y dos escrituras simultáneas bifurcaban la cadena. Ningún flujo lo usaba: los
# eventos de la cadena solo entran por los endpoints internos con secreto de servicio
# (/cosecha-aprobada desde Productores y /eventos-internos desde Transporte).

@app.get("/certificacion/{producto_id}/historial")
def historial(producto_id: str, db: Session = Depends(get_db)):
    bloques = db.query(models.Bloque).filter(
        models.Bloque.producto_id == producto_id
    ).order_by(models.Bloque.indice).all()

    verificador_ids = {b.verificador_id for b in bloques if b.verificador_id}
    nombres_por_id = {}
    credenciales_por_id = {}
    if verificador_ids:
        verificadores = db.query(models.Verificador).filter(
            models.Verificador.id.in_(verificador_ids)
        ).all()
        nombres_por_id = {str(v.id): v.nombre for v in verificadores}
        credenciales_por_id = {str(v.id): v.credencial for v in verificadores}

    return [
        {
            "id": b.id,
            "producto_id": b.producto_id,
            "indice": b.indice,
            "evento": b.evento,
            "datos": b.datos,
            "fecha": b.fecha,
            "hash_anterior": b.hash_anterior,
            "hash_actual": b.hash_actual,
            "verificador_id": b.verificador_id,
            "verificador_nombre": nombres_por_id.get(str(b.verificador_id)) if b.verificador_id else None,
            "verificador_credencial": credenciales_por_id.get(str(b.verificador_id)) if b.verificador_id else None,
        }
        for b in bloques
    ]

@app.get("/certificacion/{producto_id}/verificar")
def verificar(producto_id: str, db: Session = Depends(get_db)):
    return verificar_cadena(db, producto_id)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5500")

@app.get("/certificacion/{producto_id}/qr")
def generar_qr(producto_id: str):
    url_verificacion = f"{FRONTEND_URL}/verificar.html?producto_id={producto_id}"

    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url_verificacion)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(buffer, media_type="image/png")


# El nombre sale en el certificado que ve quien escanea el QR del producto, junto con la credencial.
VERIFICADOR_NOMBRE_MINIMO = 2
VERIFICADOR_NOMBRE_MAXIMO = 120


class VerificadorCrear(BaseModel):
    # La credencial NO viene en el cuerpo: la asignó el admin al invitarlo (ver _credencial_asignada_por_el_admin);
    # si un cliente la manda, se ignora.
    nombre: str


def _credencial_asignada_por_el_admin(usuario_id: str) -> str:
    """La credencial del verificador la escribe el admin al invitarlo (Usuarios la guarda en la invitación con la que se
    registró) y nadie puede inventarla: se le pide a Usuarios por su ruta interna, con el secreto de servicio. Sin
    credencial asignada no hay perfil; si Usuarios no responde tampoco (mejor reintentar que dejar un perfil sin ella)."""
    no_se_pudo = HTTPException(status_code=503, detail="No se pudo comprobar tu credencial. Intenta de nuevo en un momento.")
    sin_credencial = HTTPException(
        status_code=409,
        detail="El administrador todavía no te asignó una credencial. Pídesela para poder completar tu perfil.",
    )
    try:
        resp = httpx.get(
            f"{USUARIOS_URL}/usuarios/interno/{usuario_id}/credencial",
            headers={"X-Servicio-Secreto": CERTIFICACION_A_USUARIOS_SECRETO or ""},
            timeout=5,
        )
    except httpx.RequestError:
        raise no_se_pudo
    if resp.status_code == 404:
        raise sin_credencial
    if resp.status_code != 200:
        raise no_se_pudo
    credencial = str((resp.json() or {}).get("credencial") or "").strip()
    if not credencial:
        raise sin_credencial
    return credencial


@app.get("/verificadores/credencial-asignada")
def mi_credencial_asignada(usuario: dict = Depends(requiere_rol("verificador"))):
    """La credencial que el admin le asignó al verificador autenticado: la pantalla de su perfil se la muestra (solo
    de lectura) antes de que cree el perfil."""
    return {"credencial": _credencial_asignada_por_el_admin(usuario.get("sub"))}


@app.post("/verificadores")
def crear_verificador(datos: VerificadorCrear, db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("verificador"))):
    nombre = (datos.nombre or "").strip()
    if len(nombre) < VERIFICADOR_NOMBRE_MINIMO:
        raise HTTPException(status_code=422, detail=f"El nombre debe tener al menos {VERIFICADOR_NOMBRE_MINIMO} caracteres")
    if len(nombre) > VERIFICADOR_NOMBRE_MAXIMO:
        raise HTTPException(status_code=422, detail=f"El nombre no puede superar {VERIFICADOR_NOMBRE_MAXIMO} caracteres")
    if "\x00" in nombre:
        raise HTTPException(status_code=422, detail="El nombre contiene caracteres no válidos")

    usuario_id = usuario.get("sub")
    existente = db.query(models.Verificador).filter(models.Verificador.usuario_id == usuario_id).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya tienes un perfil de verificador registrado")

    nuevo = models.Verificador(
        usuario_id=usuario_id,
        nombre=nombre,
        credencial=_credencial_asignada_por_el_admin(usuario_id),
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


@app.get("/verificadores/me")
def mi_perfil_verificador(db: Session = Depends(get_db), usuario: dict = Depends(requiere_rol("verificador"))):
    verificador = db.query(models.Verificador).filter(
        models.Verificador.usuario_id == usuario.get("sub")
    ).first()
    if not verificador:
        raise HTTPException(status_code=404, detail="Aún no tienes un perfil de verificador. Créalo primero.")
    return verificador

@app.delete("/verificadores/{verificador_id}")
def eliminar_verificador(verificador_id: str, db: Session = Depends(get_db), x_servicio_secreto: str = Header(None)):
    if not secreto_de_servicio_valido(x_servicio_secreto, QA_LIMPIEZA_SECRETO):
        raise HTTPException(status_code=403, detail="No autorizado")

    verificador = db.query(models.Verificador).filter(models.Verificador.id == verificador_id).first()
    if not verificador:
        raise HTTPException(status_code=404, detail="Verificador no encontrado")

    db.delete(verificador)
    db.commit()
    return {"mensaje": "Verificador eliminado"}