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
- datos personales: las rutas públicas de productores no devuelven teléfono, usuario_id ni las
  coordenadas exactas (van redondeadas); el dueño lo ve todo en /me y el admin en /admin/todos
- el servidor exige lo que el formulario solo sugiere: números finitos y acotados (sin costos
  negativos, NaN ni Infinity), textos con largo máximo, coordenadas válidas y fechas coherentes
  (cosecha no anterior a la siembra ni en el futuro)

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
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)

# Referencia a la función real ANTES de que el fixture de abajo la reemplace en cada test — los
# tests del propio helper la necesitan intacta.
_registrar_certificacion_real = main._registrar_certificacion_cosecha


@pytest.fixture(autouse=True)
def _sin_certificacion_real(monkeypatch):
    """aprobar_cosecha ahora avisa a Certificación (best-effort) — ningún test de acá levanta ese
    servicio, así que por defecto se reemplaza por un stub que responde "registrado"."""
    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", MagicMock(return_value=True))


# Referencia a la comprobación real ANTES de que el fixture de abajo la reemplace: los tests de esa exigencia la usan.
_exigir_perfil_real = main._exigir_perfil_de_verificador


@pytest.fixture(autouse=True)
def _verificador_con_perfil(monkeypatch):
    """aprobar_cosecha exige que el verificador tenga perfil (se lo pregunta a Certificación): ningún test de acá
    levanta ese servicio, así que por defecto se da por hecho que lo tiene."""
    monkeypatch.setattr(main, "_exigir_perfil_de_verificador", MagicMock(return_value=None))


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


def test_crear_registro_produccion_con_unidad_chica_valida_funciona():
    """kg, unidad y litro son las únicas unidades chicas/granulares permitidas como base de una
    cosecha — las de empaque (saco, arroba) se agregan después en Productos, como unidad
    alternativa del producto ya publicado."""
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-UNI"))
    assert chacra.status_code == 200, chacra.text
    chacra_id = chacra.json()["id"]

    for unidad in ["kg", "unidad", "litro"]:
        payload = _registro_payload(chacra_id)
        payload["unidad_medida"] = unidad
        if unidad != "kg":
            payload["equivalencia_kg"] = 1.5
        resp = client.post("/productores/produccion", json=payload)
        assert resp.status_code == 200, resp.text
        assert resp.json()["unidad_medida"] == unidad


def test_crear_registro_produccion_con_unidad_de_empaque_falla_422():
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-EMP"))
    assert chacra.status_code == 200, chacra.text
    chacra_id = chacra.json()["id"]

    for unidad in ["saco", "arroba"]:
        payload = _registro_payload(chacra_id)
        payload["unidad_medida"] = unidad
        payload["equivalencia_kg"] = 10.0  # aunque venga, la unidad en sí ya no es válida como base
        resp = client.post("/productores/produccion", json=payload)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == (
            "La unidad de la cosecha debe ser kg, unidad o litro. Las unidades de empaque "
            "(saco, arroba) se agregan después, como unidad de venta alternativa del producto."
        )


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


def _crear_registro_pendiente_verificacion(monkeypatch, unidad_medida="kg", equivalencia_kg=None) -> tuple[str, str]:
    """Crea productor + chacra + registro y lo completa (queda en pendiente_verificacion). Deja
    al productor como usuario autenticado al retornar. Devuelve (registro_id, productor_sub)."""
    sub_productor = _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}"))
    assert chacra.status_code == 200, chacra.text
    payload = _registro_payload(chacra.json()["id"])
    payload["unidad_medida"] = unidad_medida
    if equivalencia_kg is not None:
        payload["equivalencia_kg"] = equivalencia_kg
    registro = client.post("/productores/produccion", json=payload)
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


def test_aprobar_registra_los_eventos_de_certificacion_del_producto_creado(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _mockear_crear_producto_desde_cosecha(monkeypatch, producto_id="producto-cert")
    mock_cert = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", mock_cert)

    sub_verificador = _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")

    assert resp.status_code == 200, resp.text
    assert resp.json()["certificacion_registrada"] is True
    mock_cert.assert_called_once()
    _registro, _productor, _chacra, verificador_usuario_id, producto_id = mock_cert.call_args.args
    assert verificador_usuario_id == sub_verificador
    assert producto_id == "producto-cert"


def test_aprobar_no_falla_si_certificacion_no_responde(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _mockear_crear_producto_desde_cosecha(monkeypatch, producto_id="producto-sin-cert")
    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", MagicMock(return_value=False))

    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")

    # La cosecha queda aprobada y el producto creado igual: el certificado es reintentable
    # (endpoint idempotente), no motivo para deshacer la aprobación.
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobado"
    assert resp.json()["certificacion_registrada"] is False


def test_aprobar_con_409_de_productos_no_intenta_certificar_sin_id(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _mockear_crear_producto_desde_cosecha(monkeypatch, status_code=409)
    mock_cert = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", mock_cert)

    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")

    assert resp.status_code == 200, resp.text
    assert resp.json()["certificacion_registrada"] is False
    mock_cert.assert_not_called()


def test_registrar_certificacion_cosecha_manda_secreto_y_descripcion(monkeypatch):
    capturado = {}

    def _post_falso(url, json=None, headers=None, timeout=None):
        capturado.update(url=url, json=json, headers=headers)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(main.httpx, "post", _post_falso)
    monkeypatch.setattr(main, "PRODUCTORES_A_CERTIFICACION_SECRETO", "secreto-de-prueba")
    registro = SimpleNamespace(
        cantidad_cosechada=45.5, unidad_medida="kg", cultivo="Papa Nativa",
        fecha_siembra=datetime(2026, 1, 1), fecha_cosecha_real=datetime(2026, 8, 20),
    )
    productor = SimpleNamespace(nombre="Sol Naciente")
    chacra = SimpleNamespace(codigo="CH-01", nombre="Parcela norte")

    ok = _registrar_certificacion_real(registro, productor, chacra, "usuario-verificador", "producto-1")

    assert ok is True
    assert capturado["url"].endswith("/certificacion/producto-1/cosecha-aprobada")
    assert capturado["headers"]["X-Servicio-Secreto"] == "secreto-de-prueba"
    assert capturado["json"]["verificador_usuario_id"] == "usuario-verificador"
    assert "45.5 kg de Papa Nativa" in capturado["json"]["datos_cosecha"]
    assert "CH-01 (Parcela norte)" in capturado["json"]["datos_cosecha"]
    assert "Sol Naciente" in capturado["json"]["datos_cosecha"]


def test_registrar_certificacion_cosecha_devuelve_false_si_la_red_falla(monkeypatch):
    def _post_roto(*a, **kw):
        raise main.httpx.RequestError("fallo simulado")

    monkeypatch.setattr(main.httpx, "post", _post_roto)
    registro = SimpleNamespace(
        cantidad_cosechada=10, unidad_medida="kg", cultivo="Habas",
        fecha_siembra=None, fecha_cosecha_real=None,
    )

    assert _registrar_certificacion_real(registro, SimpleNamespace(nombre="X"), None, "u", "p") is False


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
    assert capturado["equivalencia_kg"] is None  # cosecha en "kg" — no hace falta factor


def test_aprobar_envia_equivalencia_kg_cuando_la_cosecha_no_es_en_kg(monkeypatch):
    """Antes de este cambio, equivalencia_kg (ya validado y guardado en el propio
    RegistroProduccion) se perdía en el camino hacia Productos, dejando el peso de estos
    productos indeterminable en Pedidos."""
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch, unidad_medida="litro", equivalencia_kg=1.03)

    capturado = {}
    main.breaker_crear_producto.close()

    def _capturar(payload):
        capturado.update(payload)
        return _RespuestaFalsaProductos(200, {"id": "producto-capturado"})

    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", _capturar)

    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/aprobar")
    assert resp.status_code == 200, resp.text

    assert capturado["unidad_medida"] == "litro"
    assert capturado["equivalencia_kg"] == 1.03


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


# ============ Validación de los formularios: el servidor no confía en el frontend ============

def _con_json_crudo(metodo, ruta, texto_json):
    """Manda el cuerpo tal cual: JSON.stringify no puede producir NaN ni Infinity, pero cualquier
    otro cliente de la API sí."""
    return client.request(metodo, ruta, content=texto_json, headers={"Content-Type": "application/json"})


def _nueva_chacra() -> str:
    _nuevo_productor()
    resp = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}"))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_chacra_con_datos_invalidos_falla_422_y_no_guarda_nada():
    _nuevo_productor()
    invalidas = [
        {**_chacra_payload(), "ubicacion_latitud": 95},
        {**_chacra_payload(), "ubicacion_longitud": -181},
        {**_chacra_payload(), "codigo": "   "},
        {**_chacra_payload(), "codigo": "X" * 31},
        {**_chacra_payload(), "nombre": "N" * 81},
        {**_chacra_payload(), "codigo": "A\u0000B"},
    ]
    for cuerpo in invalidas:
        resp = client.post("/chacras", json=cuerpo)
        assert resp.status_code == 422, (cuerpo, resp.text)
    for coordenada in ("NaN", "Infinity", "-Infinity"):
        crudo = '{"codigo": "CH-NAN", "ubicacion_latitud": %s, "ubicacion_longitud": -74.2}' % coordenada
        assert _con_json_crudo("POST", "/chacras", crudo).status_code == 422, coordenada
    assert client.get("/chacras/me").json() == []


def test_codigo_de_chacra_se_recorta():
    _nuevo_productor()
    resp = client.post("/chacras", json=_chacra_payload(codigo="  CH-99  "))
    assert resp.status_code == 200, resp.text
    assert resp.json()["codigo"] == "CH-99"


def test_perfil_de_productor_con_datos_invalidos_falla_422():
    _auth(str(uuid.uuid4()))
    for cuerpo in (
        {"nombre": ""}, {"nombre": "   "}, {"nombre": "N" * 121}, {"nombre": "ok", "contacto": "9" * 61},
        {"nombre": "ok", "latitud": "abc"}, {"nombre": "ok", "latitud": "95", "longitud": "-74"},
        {"nombre": "ok", "longitud": "NaN"}, {"nombre": "ok", "comunidad": "C" * 121},
    ):
        resp = client.post("/productores", json=cuerpo)
        assert resp.status_code == 422, (cuerpo, resp.text)
    ok = client.post("/productores", json={"nombre": "Con ubicación", "latitud": "-13.16", "longitud": "-74.22"})
    assert ok.status_code == 200, ok.text


def test_siembra_con_numeros_o_textos_invalidos_falla_422_y_no_guarda_nada():
    chacra_id = _nueva_chacra()
    cambios = [
        {"costo_semillas": -1}, {"costo_insumos": -0.01}, {"costo_semillas": 10_000_000}, {"numero_parcelas": 1001},
        {"numero_parcelas": 10**11}, {"unidad_medida": "litro", "equivalencia_kg": -2},
        {"unidad_medida": "litro", "equivalencia_kg": 5001}, {"cultivo": "   "}, {"cultivo": "P" * 81},
        {"cultivo": "pa\u0000pa"}, {"ubicacion_cosecha": "U" * 201},
    ]
    for cambio in cambios:
        resp = client.post("/productores/produccion", json={**_registro_payload(chacra_id), **cambio})
        assert resp.status_code == 422, (cambio, resp.text)
    for campo in ("costo_semillas", "costo_insumos"):
        for valor in ("NaN", "Infinity", "-Infinity"):
            crudo = json.dumps({**_registro_payload(chacra_id), campo: 0}).replace(f'"{campo}": 0', f'"{campo}": {valor}')
            assert _con_json_crudo("POST", "/productores/produccion", crudo).status_code == 422, (campo, valor)
    assert client.get("/productores/produccion/me").json() == []


def test_siembra_con_fechas_absurdas_falla_422_pero_planificar_a_futuro_cercano_si():
    chacra_id = _nueva_chacra()
    hoy = datetime.utcnow()
    absurdas = [
        (datetime(1999, 12, 31), datetime(2000, 6, 1)),
        (hoy + timedelta(days=731), hoy + timedelta(days=900)),
        (datetime(2026, 1, 1), datetime(2026, 1, 1) + timedelta(days=1826)),
        (datetime(2026, 3, 1), datetime(2026, 2, 1)),
    ]
    for siembra, estimada in absurdas:
        payload = {**_registro_payload(chacra_id), "fecha_siembra": siembra.isoformat(), "fecha_cosecha_estimada": estimada.isoformat()}
        resp = client.post("/productores/produccion", json=payload)
        assert resp.status_code == 422, (siembra, estimada, resp.text)

    planificada = {
        **_registro_payload(chacra_id),
        "fecha_siembra": (hoy + timedelta(days=30)).isoformat(),
        "fecha_cosecha_estimada": (hoy + timedelta(days=150)).isoformat(),
    }
    assert client.post("/productores/produccion", json=planificada).status_code == 200


def test_fechas_con_zona_horaria_no_rompen_la_comparacion():
    """Una fecha con zona ("...Z") junto a una sin zona lanzaba un error 500 al compararlas."""
    chacra_id = _nueva_chacra()
    payload = {**_registro_payload(chacra_id), "fecha_siembra": "2026-01-01T00:00:00Z", "fecha_cosecha_estimada": "2026-05-01T00:00:00"}
    resp = client.post("/productores/produccion", json=payload)
    assert resp.status_code == 200, resp.text
    payload["fecha_cosecha_estimada"] = "2026-01-01T04:00:00+00:00"
    assert client.post("/productores/produccion", json=payload).status_code == 200


def test_la_ganancia_ya_no_incluye_un_costo_de_envio(monkeypatch):
    """El "costo de envío final" se quitó del formulario de completar cosecha (presentación del 2026-10-01): si un
    cliente viejo lo manda se ignora, y un registro antiguo que lo tenga guardado no cambia su ganancia."""
    chacra_id = _nueva_chacra()
    registro_id = client.post("/productores/produccion", json=_registro_payload(chacra_id)).json()["id"]
    monkeypatch.setattr(main, "_consultar_precio_referencia", lambda cultivo: (10.0, None))

    resp = client.patch(f"/productores/produccion/{registro_id}/completar-cosecha", json={
        "cantidad_cosechada": 50.0, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat(),
        "costo_mano_obra": 20.0, "costo_envio": 999.0,  # campo retirado: se ignora
    })
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert "costo_envio" not in cuerpo
    # semillas 100 + insumos 50 + mano de obra 20; 50 kg x S/ 10 = 500
    assert cuerpo["costo_total"] == 170.0 and cuerpo["ganancia_estimada"] == 330.0

    db = main.SessionLocal()
    try:
        fila = db.query(main.models.RegistroProduccion).filter(main.models.RegistroProduccion.id == registro_id).first()
        assert float(fila.costo_envio) == 0  # no se guardó el 999
        fila.costo_envio = 40  # como un registro de antes del cambio
        db.commit()
    finally:
        db.close()
    actual = next(r for r in client.get("/productores/produccion/me").json() if r["id"] == registro_id)
    assert actual["costo_total"] == 170.0 and actual["ganancia_estimada"] == 330.0


def test_completar_cosecha_con_datos_invalidos_falla_422_y_no_cambia_nada(monkeypatch):
    chacra_id = _nueva_chacra()
    registro = client.post("/productores/produccion", json=_registro_payload(chacra_id))
    assert registro.status_code == 200, registro.text
    registro_id = registro.json()["id"]
    monkeypatch.setattr(main, "_consultar_precio_referencia", lambda cultivo: (10.0, None))

    url = f"/productores/produccion/{registro_id}/completar-cosecha"
    base = {"cantidad_cosechada": 50.0, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat(), "costo_mano_obra": 20.0}
    hoy = datetime.utcnow()
    invalidos = [
        {"cantidad_cosechada": 1e12}, {"cantidad_cosechada": 1_000_001}, {"cantidad_cosechada": 0}, {"cantidad_cosechada": -5},
        {"costo_mano_obra": -1}, {"costo_mano_obra": 10_000_000},
        {"fecha_cosecha_real": datetime(2025, 12, 31).isoformat()},      # antes de la siembra (2026-01-01)
        {"fecha_cosecha_real": (hoy + timedelta(days=5)).isoformat()},   # en el futuro
    ]
    for cambio in invalidos:
        resp = client.patch(url, json={**base, **cambio})
        assert resp.status_code == 422, (cambio, resp.text)
    for campo in ("cantidad_cosechada", "costo_mano_obra"):
        for valor in ("NaN", "Infinity"):
            crudo = json.dumps(base).replace(f'"{campo}": {json.dumps(base[campo])}', f'"{campo}": {valor}')
            assert _con_json_crudo("PATCH", url, crudo).status_code == 422, (campo, valor)

    actual = next(r for r in client.get("/productores/produccion/me").json() if r["id"] == registro_id)
    assert actual["estado"] == "planificado" and actual["cantidad_cosechada"] is None

    sano = client.patch(url, json=base)
    assert sano.status_code == 200, sano.text
    assert sano.json()["estado"] == "pendiente_verificacion"


def test_rechazar_con_motivo_demasiado_largo_falla_422(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _como_verificador()
    resp = client.post(f"/productores/produccion/{registro_id}/rechazar", json={"motivo": "m" * 501})
    assert resp.status_code == 422, resp.text


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/productores/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
        ("DELETE", f"/productores/produccion/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
    ]
    for metodo, ruta, cuerpo, variable in protegidos:
        for valor in (None, ""):
            monkeypatch.setattr(main, variable, valor)
            sin_cabecera = client.request(metodo, ruta, json=cuerpo)
            assert sin_cabecera.status_code == 403, (ruta, valor, sin_cabecera.text)
            cabecera_vacia = client.request(metodo, ruta, json=cuerpo, headers={"X-Servicio-Secreto": ""})
            assert cabecera_vacia.status_code == 403, (ruta, valor, cabecera_vacia.text)

    # con el secreto configurado: el incorrecto no pasa y el correcto sí llega al endpoint
    metodo, ruta, cuerpo, variable = protegidos[0]
    monkeypatch.setattr(main, variable, "secreto-de-prueba")
    assert client.request(metodo, ruta, json=cuerpo, headers={"X-Servicio-Secreto": "otro"}).status_code == 403
    assert client.request(metodo, ruta, json=cuerpo, headers={"X-Servicio-Secreto": "secreto-de-prueba"}).status_code != 403


# ============ Datos personales: lo público, lo del dueño y lo del admin ============

CAMPOS_PUBLICOS = {"id", "nombre", "comunidad", "ubicacion", "fecha_registro", "latitud", "longitud"}


def _productor_con_datos_personales():
    """Crea un productor con teléfono y coordenadas exactas y deja a su usuario como el autenticado.
    Devuelve (usuario_id, productor_id)."""
    sub = str(uuid.uuid4())
    _auth(sub)
    resp = client.post("/productores", json={
        "nombre": "Finca de prueba", "comunidad": "Quinua", "contacto": "987654321",
        "latitud": "-13.158812", "longitud": "-74.223940",
    })
    assert resp.status_code == 200, resp.text
    return sub, resp.json()["id"]


def test_las_rutas_publicas_de_productores_no_exponen_telefono_usuario_ni_coordenadas_exactas():
    sub, productor_id = _productor_con_datos_personales()

    lista = client.get("/productores")
    assert lista.status_code == 200
    en_lista = next(p for p in lista.json() if p["id"] == productor_id)
    detalle = client.get(f"/productores/{productor_id}")
    assert detalle.status_code == 200

    for vista in (en_lista, detalle.json()):
        assert set(vista) == CAMPOS_PUBLICOS
        assert vista["nombre"] == "Finca de prueba" and vista["comunidad"] == "Quinua"
        # una zona aproximada (2 decimales ~ 1 km), no la finca
        assert (vista["latitud"], vista["longitud"]) == ("-13.16", "-74.22")
        texto = json.dumps(vista)
        assert "987654321" not in texto and sub not in texto and "-13.158812" not in texto


def test_un_productor_sin_coordenadas_validas_se_muestra_sin_mapa():
    db = main.SessionLocal()
    try:
        sin_datos = main.models.Productor(nombre="Sin mapa", latitud=None, longitud=None)
        sucio = main.models.Productor(nombre="Coordenadas sucias", latitud="abc", longitud="nan")
        db.add_all([sin_datos, sucio])
        db.commit()
        ids = [str(sin_datos.id), str(sucio.id)]
    finally:
        db.close()

    for productor_id in ids:
        vista = client.get(f"/productores/{productor_id}").json()
        assert vista["latitud"] is None and vista["longitud"] is None


def test_el_dueno_sigue_viendo_su_perfil_completo():
    sub, _ = _productor_con_datos_personales()
    resp = client.get("/productores/me")
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["contacto"] == "987654321"
    assert (cuerpo["latitud"], cuerpo["longitud"]) == ("-13.158812", "-74.223940")
    assert cuerpo["usuario_id"] == sub


def test_el_admin_ve_la_lista_completa_y_los_demas_roles_no():
    sub, productor_id = _productor_con_datos_personales()

    _auth(str(uuid.uuid4()), rol="admin")
    resp = client.get("/productores/admin/todos")
    assert resp.status_code == 200, resp.text
    item = next(p for p in resp.json() if p["id"] == productor_id)
    assert item["contacto"] == "987654321"
    assert (item["latitud"], item["longitud"]) == ("-13.158812", "-74.223940")
    assert item["usuario_id"] == sub

    for rol in ("productor", "comprador", "verificador"):
        _auth(str(uuid.uuid4()), rol=rol)
        assert client.get("/productores/admin/todos").status_code == 403, rol


def test_un_id_de_productor_mal_formado_falla_422_no_500():
    assert client.get("/productores/no-es-un-uuid").status_code == 422
    assert client.get(f"/productores/{uuid.uuid4()}").status_code == 404



# ============ Aprobar exige que el verificador tenga perfil (nombre + credencial) ============
# Sin perfil, el certificado_productor se guardaba con verificador_id nulo: el QR público no decía quién verificó la
# cosecha y esa cadena no se puede corregir después. Productores se lo pregunta a Certificación con el token del propio
# verificador (GET /verificadores/me).

class _RespuestaPerfil:
    def __init__(self, status_code):
        self.status_code = status_code


def _perfil_segun_certificacion(monkeypatch, status_code=None, error=None):
    """Usa la comprobación real y simula lo que responde Certificación. Devuelve la lista de llamadas que se hicieron."""
    monkeypatch.setattr(main, "_exigir_perfil_de_verificador", _exigir_perfil_real)
    llamadas = []

    def _get(url, **kwargs):
        llamadas.append((url, kwargs))
        if error:
            raise error
        return _RespuestaPerfil(status_code)

    monkeypatch.setattr(main.httpx, "get", _get)
    return llamadas


def _estado_y_decisiones(registro_id):
    db = main.SessionLocal()
    try:
        registro = db.query(main.models.RegistroProduccion).filter(main.models.RegistroProduccion.id == registro_id).one()
        decisiones = db.query(main.models.HistorialVerificacionCosecha).filter(
            main.models.HistorialVerificacionCosecha.registro_produccion_id == registro_id
        ).count()
        return registro.estado, decisiones
    finally:
        db.close()


def _crear_producto_que_no_debe_llamarse(monkeypatch):
    crear = MagicMock()
    main.breaker_crear_producto.close()
    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", crear)
    return crear


def test_aprobar_sin_perfil_de_verificador_falla_409_y_no_crea_nada(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    crear = _crear_producto_que_no_debe_llamarse(monkeypatch)
    llamadas = _perfil_segun_certificacion(monkeypatch, status_code=404)
    _como_verificador()

    resp = client.post(f"/productores/produccion/{registro_id}/aprobar", headers={"Authorization": "Bearer token-del-verificador"})

    assert resp.status_code == 409, resp.text
    assert "perfil de verificador" in resp.json()["detail"]
    crear.assert_not_called()  # ni se creó el producto en el catálogo
    assert _estado_y_decisiones(registro_id) == ("pendiente_verificacion", 0)
    url, kwargs = llamadas[0]
    assert url.endswith("/verificadores/me")
    assert kwargs["headers"]["Authorization"] == "Bearer token-del-verificador"  # con el token del propio verificador


def test_aprobar_con_perfil_de_verificador_se_completa_normalmente(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    _mockear_crear_producto_desde_cosecha(monkeypatch, producto_id="producto-con-perfil")
    _perfil_segun_certificacion(monkeypatch, status_code=200)
    _como_verificador()

    resp = client.post(f"/productores/produccion/{registro_id}/aprobar", headers={"Authorization": "Bearer token-del-verificador"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobado" and resp.json()["producto_id"] == "producto-con-perfil"
    assert _estado_y_decisiones(registro_id) == ("aprobado", 1)


def test_aprobar_si_no_se_puede_comprobar_el_perfil_falla_503_y_no_aprueba(monkeypatch):
    for falla in ({"error": main.httpx.RequestError("sin red")}, {"status_code": 500}, {"status_code": 401}):
        registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
        crear = _crear_producto_que_no_debe_llamarse(monkeypatch)
        _perfil_segun_certificacion(monkeypatch, **falla)
        _como_verificador()

        resp = client.post(f"/productores/produccion/{registro_id}/aprobar", headers={"Authorization": "Bearer token-del-verificador"})

        assert resp.status_code == 503, (falla, resp.text)
        crear.assert_not_called()
        assert _estado_y_decisiones(registro_id) == ("pendiente_verificacion", 0)


def test_aprobar_sin_token_falla_401_no_500(monkeypatch):
    """Sin cabecera Authorization no hay a quién preguntarle por el perfil (solo pasa si la dependencia se simula)."""
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    monkeypatch.setattr(main, "_exigir_perfil_de_verificador", _exigir_perfil_real)
    _como_verificador()

    assert client.post(f"/productores/produccion/{registro_id}/aprobar").status_code == 401
    assert _estado_y_decisiones(registro_id) == ("pendiente_verificacion", 0)


def test_rechazar_no_exige_perfil_de_verificador(monkeypatch):
    registro_id, _ = _crear_registro_pendiente_verificacion(monkeypatch)
    llamadas = _perfil_segun_certificacion(monkeypatch, status_code=404)
    _como_verificador()

    resp = client.post(f"/productores/produccion/{registro_id}/rechazar", json={"motivo": "No coincide la cantidad"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "rechazado"
    assert llamadas == []  # rechazar no consulta el perfil
