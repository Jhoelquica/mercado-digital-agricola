"""
Tests de la entidad Chacra y su vínculo con RegistroProduccion, y de la máquina de estados de
verificación de cosechas (Verificador).

Cubre:
- creación válida de chacra
- código duplicado para el MISMO productor -> 400
- mismo código para dos productores DISTINTOS -> ambos OK (el UniqueConstraint es compuesto)
- crear RegistroProduccion con una chacra que no es del productor autenticado -> 403
- completar-cosecha transiciona directo a pendiente_verificacion (no queda en "cosechado")
- completar-cosecha en un estado no permitido -> 409
- aprobar desde pendiente_verificacion -> ok, deja rastro en HistorialVerificacionCosecha
- aprobar desde otro estado -> 409; sin rol verificador -> 403
- rechazar con motivo -> ok; sin motivo -> 422
- reenvío tras rechazo (mismo endpoint completar-cosecha) -> vuelve a pendiente_verificacion
- listar pendientes-verificacion solo trae lo que corresponde
- aprobar crea el producto en Productos (payload correcto) y devuelve su id
- aprobar cuando la llamada a Productos falla -> 502, NO transiciona ni registra historial,
  y un reintento posterior sí se completa
- aprobar cuando Productos responde 409 (producto ya creado en un intento anterior) se trata
  como éxito idempotente, no como falla
- pendientes-verificacion marca fue_rechazado_antes correctamente (true en un reenvío, false en
  uno que nunca se rechazó)
- historial-verificacion solo trae decisiones del verificador autenticado, ordenadas por fecha
  descendente

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de
postgresql + create_all() al importar main) — no se puede sustituir por SQLite.
crear_registro_produccion no llama a servicios externos cuando el registro aún no está
cosechado (cantidad_cosechada is None), y el test de 403 falla antes de llegar a crear el
registro, así que esos no necesitan mock. completar_cosecha() sí dispara una consulta de
precio de referencia a Productos (_consultar_precio_referencia), y aprobar_cosecha() llama a
Productos para crear el producto (_pedir_crear_producto_desde_cosecha) — como este archivo no
levanta ese servicio, los tests que ejercitan esos caminos mockean esas funciones puntuales (no
el circuit breaker ni httpx) para no depender de una red real ni de sus timeouts.
"""
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)


def _auth(sub: str, rol: str = "productor"):
    """Simula al usuario autenticado. Se sobreescribe `verificar_token` (dependencia estable),
    no `requiere_rol(...)` que genera un closure nuevo por llamada."""
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}


def _nuevo_productor() -> str:
    """Crea un productor con un usuario nuevo y deja ese usuario como el autenticado.
    Devuelve el `sub` (usuario_id) usado."""
    sub = str(uuid.uuid4())
    _auth(sub)
    resp = client.post("/productores", json={"nombre": f"Productor {sub[:8]}"})
    assert resp.status_code == 200, resp.text
    return sub


def _chacra_payload(codigo="CH-01", nombre="Chacra del cerro"):
    return {
        "codigo": codigo,
        "nombre": nombre,
        "ubicacion_latitud": -13.16,
        "ubicacion_longitud": -74.22,
    }


def _registro_payload(chacra_id):
    siembra = datetime(2026, 1, 1)
    return {
        "chacra_id": str(chacra_id),
        "cultivo": "papa",
        "numero_parcelas": 2,
        "costo_semillas": 100.0,
        "costo_insumos": 50.0,
        "unidad_medida": "kg",
        "fecha_siembra": siembra.isoformat(),
        "fecha_cosecha_estimada": (siembra + timedelta(days=120)).isoformat(),
    }


def test_crear_chacra_valida():
    _nuevo_productor()
    resp = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["codigo"] == "CH-01"
    assert cuerpo["nombre"] == "Chacra del cerro"
    assert cuerpo["ubicacion_latitud"] == -13.16
    assert "id" in cuerpo and "productor_id" in cuerpo


def test_codigo_duplicado_mismo_productor_falla_400():
    _nuevo_productor()
    primera = client.post("/chacras", json=_chacra_payload(codigo="CH-07"))
    assert primera.status_code == 200, primera.text

    repetida = client.post("/chacras", json=_chacra_payload(codigo="CH-07"))
    assert repetida.status_code == 400
    assert repetida.json()["detail"] == "Ya tienes una chacra registrada con el código 'CH-07'."


def test_mismo_codigo_dos_productores_distintos_ok():
    _nuevo_productor()
    r1 = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))
    assert r1.status_code == 200, r1.text

    _nuevo_productor()  # segundo productor, usuario distinto
    r2 = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))
    assert r2.status_code == 200, r2.text

    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["productor_id"] != r2.json()["productor_id"]


def test_registro_produccion_con_chacra_ajena_falla_403():
    # Productor A crea una chacra.
    _nuevo_productor()
    chacra_a = client.post("/chacras", json=_chacra_payload(codigo="CH-A"))
    assert chacra_a.status_code == 200, chacra_a.text
    chacra_a_id = chacra_a.json()["id"]

    # Productor B (usuario distinto) intenta usar la chacra de A.
    _nuevo_productor()
    resp = client.post("/productores/produccion", json=_registro_payload(chacra_a_id))

    assert resp.status_code == 403
    assert resp.json()["detail"] == "La chacra especificada no pertenece a tu cuenta."


def test_registro_produccion_con_chacra_propia_ok():
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-OK"))
    assert chacra.status_code == 200, chacra.text
    chacra_id = chacra.json()["id"]

    resp = client.post("/productores/produccion", json=_registro_payload(chacra_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["chacra_id"] == chacra_id


# ============ Verificación de cosechas ============

def _como_verificador() -> str:
    """Cambia al usuario autenticado a uno con rol verificador (sin perfil de Verificador
    asociado — requiere_rol("verificador") no lo necesita, ver comentario en models.py sobre
    por qué verificador_id no es un FK real). Devuelve el `sub` usado."""
    sub = str(uuid.uuid4())
    _auth(sub, rol="verificador")
    return sub


def _completar_cosecha(monkeypatch, registro_id: str, cantidad: float = 50.0):
    """PATCH completar-cosecha sin depender de una llamada de red real a Productos para el
    precio de referencia — mockea _consultar_precio_referencia (no el circuit breaker ni
    httpx), ver docstring del módulo."""
    monkeypatch.setattr(main, "_consultar_precio_referencia", lambda cultivo: (10.0, None))
    return client.patch(
        f"/productores/produccion/{registro_id}/completar-cosecha",
        json={
            "cantidad_cosechada": cantidad,
            "fecha_cosecha_real": datetime(2026, 5, 1).isoformat(),
            "costo_mano_obra": 20.0,
            "costo_envio": 10.0,
        },
    )


class _RespuestaFalsaProductos:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


def _mockear_crear_producto_desde_cosecha(monkeypatch, status_code: int = 200, producto_id: str = "producto-fake-id"):
    """aprobar_cosecha llama a Productos vía
    breaker_crear_producto.call(_pedir_crear_producto_desde_cosecha, payload) — se mockea esa
    función puntual (no el breaker ni httpx), mismo criterio que _completar_cosecha con
    _consultar_precio_referencia. Se fuerza el breaker a cerrado antes de cada uso para que un
    fallo inducido por otro test no deje el circuito abierto y contamine este.
    """
    main.breaker_crear_producto.close()
    monkeypatch.setattr(
        main, "_pedir_crear_producto_desde_cosecha",
        lambda payload: _RespuestaFalsaProductos(status_code, {"id": producto_id}),
    )


def _mockear_crear_producto_desde_cosecha_falla(monkeypatch):
    main.breaker_crear_producto.close()

    def _falla(payload):
        raise main.httpx.RequestError("fallo simulado de red")

    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", _falla)


def _crear_registro_pendiente_verificacion(monkeypatch) -> tuple[str, str]:
    """Crea productor + chacra + registro y lo completa (queda en pendiente_verificacion). Deja
    al productor como usuario autenticado al retornar. Devuelve (registro_id, productor_sub)."""
    sub_productor = _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}"))
    assert chacra.status_code == 200, chacra.text
    registro = client.post("/productores/produccion", json=_registro_payload(chacra.json()["id"]))
    assert registro.status_code == 200, registro.text
    registro_id = registro.json()["id"]

    resp = _completar_cosecha(monkeypatch, registro_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "pendiente_verificacion"
    return registro_id, sub_productor


def test_completar_cosecha_pasa_directo_a_pendiente_verificacion(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    # Releído desde "mi producción" — nunca quedó en "cosechado" como valor de reposo.
    resp = client.get("/productores/produccion/me")
    assert resp.status_code == 200, resp.text
    registro = next(r for r in resp.json() if r["id"] == registro_id)
    assert registro["estado"] == "pendiente_verificacion"
    assert registro["motivo_rechazo"] is None


def test_completar_cosecha_en_estado_no_permitido_falla_409(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    # Ya está en pendiente_verificacion: completar-cosecha de nuevo no está permitido.
    resp = _completar_cosecha(monkeypatch, registro_id)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Esta cosecha no está en un estado que permita completarla"


def test_aprobar_desde_pendiente_verificacion_ok(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    _mockear_crear_producto_desde_cosecha(monkeypatch, producto_id="producto-abc")
    sub_verificador = _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobado"
    assert resp.json()["producto_id"] == "producto-abc"

    db = main.SessionLocal()
    try:
        fila = db.query(main.models.HistorialVerificacionCosecha).filter(
            main.models.HistorialVerificacionCosecha.registro_produccion_id == registro_id
        ).one()
        assert fila.accion == "aprobado"
        assert str(fila.verificador_id) == sub_verificador
        assert fila.motivo is None
    finally:
        db.close()


def test_aprobar_envia_el_payload_correcto_a_productos(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    db = main.SessionLocal()
    try:
        registro = db.query(main.models.RegistroProduccion).filter(
            main.models.RegistroProduccion.id == registro_id
        ).one()
        productor = db.query(main.models.Productor).filter(
            main.models.Productor.id == registro.productor_id
        ).one()
        cultivo_esperado = registro.cultivo
        unidad_esperada = registro.unidad_medida
        stock_esperado = round(float(registro.cantidad_cosechada))
        nombre_productor_esperado = productor.nombre
    finally:
        db.close()

    capturado = {}
    main.breaker_crear_producto.close()

    def _capturar(payload):
        capturado.update(payload)
        return _RespuestaFalsaProductos(200, {"id": "producto-capturado"})

    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", _capturar)

    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")
    assert resp.status_code == 200, resp.text

    assert capturado["registro_produccion_id"] == registro_id
    assert capturado["nombre"] == cultivo_esperado
    assert capturado["unidad_medida"] == unidad_esperada
    assert capturado["stock"] == stock_esperado
    assert capturado["productor_nombre"] == nombre_productor_esperado


def test_aprobar_cuando_productos_falla_no_transiciona_ni_registra_historial(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    _mockear_crear_producto_desde_cosecha_falla(monkeypatch)
    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "No se pudo crear el producto en el catálogo. Intenta aprobar de nuevo en unos momentos."

    # Sigue pendiente_verificacion -> el Verificador puede simplemente reintentar.
    db = main.SessionLocal()
    try:
        registro = db.query(main.models.RegistroProduccion).filter(
            main.models.RegistroProduccion.id == registro_id
        ).one()
        assert registro.estado == "pendiente_verificacion"

        total_historial = db.query(main.models.HistorialVerificacionCosecha).filter(
            main.models.HistorialVerificacionCosecha.registro_produccion_id == registro_id
        ).count()
        assert total_historial == 0
    finally:
        db.close()

    # Reintento posterior con Productos ya funcionando -> se completa normalmente.
    _mockear_crear_producto_desde_cosecha(monkeypatch, producto_id="producto-tras-reintento")
    reintento = client.post(f"/productores/produccion/{registro_id}/aprobar")
    assert reintento.status_code == 200, reintento.text
    assert reintento.json()["estado"] == "aprobado"
    assert reintento.json()["producto_id"] == "producto-tras-reintento"


def test_aprobar_con_409_de_productos_es_idempotente(monkeypatch):
    # Simula: Productos ya había creado el producto en un intento anterior (ver
    # UniqueConstraint sobre registro_produccion_id en productos/models.py), pero la respuesta
    # nunca llegó a confirmarse acá — el Verificador reintenta aprobar.
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    _mockear_crear_producto_desde_cosecha(monkeypatch, status_code=409)
    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobado"
    assert resp.json()["producto_id"] is None  # el 409 no devuelve el id del producto existente

    db = main.SessionLocal()
    try:
        total_historial = db.query(main.models.HistorialVerificacionCosecha).filter(
            main.models.HistorialVerificacionCosecha.registro_produccion_id == registro_id
        ).count()
        assert total_historial == 1
    finally:
        db.close()


def test_aprobar_desde_otro_estado_falla_409():
    sub_productor = _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}"))
    assert chacra.status_code == 200, chacra.text
    registro = client.post("/productores/produccion", json=_registro_payload(chacra.json()["id"]))
    assert registro.status_code == 200, registro.text
    assert registro.json()["estado"] == "planificado"

    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro.json()['id']}/aprobar")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Esta cosecha no está pendiente de verificación"


def test_aprobar_sin_rol_verificador_falla_403(monkeypatch):
    registro_id, _sub_productor = _crear_registro_pendiente_verificacion(monkeypatch)
    # El helper deja autenticado al productor dueño del registro — ni siquiera siendo el dueño
    # alcanza sin el rol correcto.
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")
    assert resp.status_code == 403


def test_rechazar_con_motivo(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    _como_verificador()
    resp = client.post(
        f"/productores/produccion/{registro_id}/rechazar",
        json={"motivo": "La ubicación no coincide con la chacra registrada"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "rechazado"
    assert resp.json()["motivo_rechazo"] == "La ubicación no coincide con la chacra registrada"

    db = main.SessionLocal()
    try:
        fila = db.query(main.models.HistorialVerificacionCosecha).filter(
            main.models.HistorialVerificacionCosecha.registro_produccion_id == registro_id
        ).one()
        assert fila.accion == "rechazado"
        assert fila.motivo == "La ubicación no coincide con la chacra registrada"
    finally:
        db.close()


def test_rechazar_sin_motivo_falla_422(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/rechazar", json={"motivo": "   "})
    assert resp.status_code == 422


def test_reenvio_tras_rechazo_vuelve_a_pendiente_verificacion(monkeypatch):
    registro_id, sub_productor = _crear_registro_pendiente_verificacion(monkeypatch)

    _como_verificador()
    rechazo = client.post(
        f"/productores/produccion/{registro_id}/rechazar",
        json={"motivo": "Falta evidencia de la cosecha"},
    )
    assert rechazo.status_code == 200, rechazo.text
    assert rechazo.json()["estado"] == "rechazado"
    assert rechazo.json()["motivo_rechazo"] == "Falta evidencia de la cosecha"

    # El productor reenvía — mismo endpoint completar-cosecha, no uno nuevo.
    _auth(sub_productor, rol="productor")
    reenvio = _completar_cosecha(monkeypatch, registro_id, cantidad=42.0)
    assert reenvio.status_code == 200, reenvio.text
    assert reenvio.json()["estado"] == "pendiente_verificacion"
    assert reenvio.json()["cantidad_cosechada"] == 42.0
    # El historial del rechazo anterior sigue existiendo (se comprueba abajo), pero
    # motivo_rechazo solo se muestra si el estado ACTUAL es "rechazado" — ya no lo es.
    assert reenvio.json()["motivo_rechazo"] is None

    db = main.SessionLocal()
    try:
        total_historial = db.query(main.models.HistorialVerificacionCosecha).filter(
            main.models.HistorialVerificacionCosecha.registro_produccion_id == registro_id
        ).count()
        assert total_historial == 1  # el rechazo original no se borró ni se sobreescribió
    finally:
        db.close()


def test_listar_pendientes_verificacion_solo_trae_correctos(monkeypatch):
    pendiente_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    # Uno aprobado: no debe aparecer en la lista de pendientes.
    aprobado_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _mockear_crear_producto_desde_cosecha(monkeypatch)
    _como_verificador()
    aprobar = client.post(f"/productores/produccion/{aprobado_id}/aprobar")
    assert aprobar.status_code == 200, aprobar.text

    # Uno que se queda en "planificado" (nunca se completó): tampoco debe aparecer.
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}"))
    assert chacra.status_code == 200, chacra.text
    planificado = client.post("/productores/produccion", json=_registro_payload(chacra.json()["id"]))
    assert planificado.status_code == 200, planificado.text
    planificado_id = planificado.json()["id"]

    _como_verificador()
    resp = client.get("/productores/produccion/pendientes-verificacion")
    assert resp.status_code == 200, resp.text
    ids = {r["id"] for r in resp.json()}

    assert pendiente_id in ids
    assert aprobado_id not in ids
    assert planificado_id not in ids

    fila = next(r for r in resp.json() if r["id"] == pendiente_id)
    assert fila["chacra_codigo"] is not None
    assert fila["productor_nombre"] is not None


def test_pendientes_verificacion_marca_fue_rechazado_antes(monkeypatch):
    # Uno rechazado y reenviado -> fue_rechazado_antes debe ser True.
    reenviado_id, sub_productor = _crear_registro_pendiente_verificacion(monkeypatch)
    _como_verificador()
    rechazo = client.post(
        f"/productores/produccion/{reenviado_id}/rechazar", json={"motivo": "Falta evidencia"},
    )
    assert rechazo.status_code == 200, rechazo.text

    _auth(sub_productor, rol="productor")
    reenvio = _completar_cosecha(monkeypatch, reenviado_id, cantidad=30.0)
    assert reenvio.status_code == 200, reenvio.text
    assert reenvio.json()["estado"] == "pendiente_verificacion"

    # Uno que nunca se rechazó -> fue_rechazado_antes debe ser False.
    nunca_rechazado_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)

    _como_verificador()
    resp = client.get("/productores/produccion/pendientes-verificacion")
    assert resp.status_code == 200, resp.text
    por_id = {r["id"]: r for r in resp.json()}

    assert por_id[reenviado_id]["fue_rechazado_antes"] is True
    assert por_id[nunca_rechazado_id]["fue_rechazado_antes"] is False


# ============ Historial de verificación ============

def test_historial_verificacion_solo_trae_decisiones_del_verificador_autenticado(monkeypatch):
    aprobado_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _mockear_crear_producto_desde_cosecha(monkeypatch, producto_id="producto-hist-1")
    sub_verificador_a = _como_verificador()
    aprobar = client.post(f"/productores/produccion/{aprobado_id}/aprobar")
    assert aprobar.status_code == 200, aprobar.text

    # Otro verificador rechaza un registro distinto — no debe aparecer en el historial de A.
    rechazado_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _como_verificador()
    rechazar = client.post(
        f"/productores/produccion/{rechazado_id}/rechazar", json={"motivo": "No cumple criterio"},
    )
    assert rechazar.status_code == 200, rechazar.text

    _auth(sub_verificador_a, rol="verificador")
    resp = client.get("/productores/produccion/historial-verificacion")
    assert resp.status_code == 200, resp.text
    ids = {fila["registro_produccion_id"] for fila in resp.json()}

    assert aprobado_id in ids
    assert rechazado_id not in ids

    fila_aprobada = next(f for f in resp.json() if f["registro_produccion_id"] == aprobado_id)
    assert fila_aprobada["accion"] == "aprobado"
    assert fila_aprobada["motivo"] is None
    assert fila_aprobada["cultivo"] is not None
    assert fila_aprobada["productor_nombre"] is not None


def test_historial_verificacion_orden_mas_reciente_primero(monkeypatch):
    verificador_sub = str(uuid.uuid4())

    primero_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _auth(verificador_sub, rol="verificador")
    r1 = client.post(f"/productores/produccion/{primero_id}/rechazar", json={"motivo": "primero"})
    assert r1.status_code == 200, r1.text

    segundo_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _auth(verificador_sub, rol="verificador")
    r2 = client.post(f"/productores/produccion/{segundo_id}/rechazar", json={"motivo": "segundo"})
    assert r2.status_code == 200, r2.text

    _auth(verificador_sub, rol="verificador")
    resp = client.get("/productores/produccion/historial-verificacion")
    assert resp.status_code == 200, resp.text
    ids_en_orden = [
        f["registro_produccion_id"] for f in resp.json()
        if f["registro_produccion_id"] in (primero_id, segundo_id)
    ]
    assert ids_en_orden == [segundo_id, primero_id]


def test_historial_verificacion_requiere_rol_verificador():
    _auth(str(uuid.uuid4()), rol="productor")
    resp = client.get("/productores/produccion/historial-verificacion")
    assert resp.status_code == 403
