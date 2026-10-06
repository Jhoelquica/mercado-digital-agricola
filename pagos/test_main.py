"""
Tests de crear_liquidaciones() (pagos/main.py), disparada dentro de POST /pagos/procesar justo
después de confirmar un cargo con Culqi: una Liquidacion por cada productor distinto entre los
items del pedido, con su propio subtotal, comisión de plataforma (10%) y neto (90%).

Cubre:
- pedido con items de un solo productor -> 1 Liquidacion con el monto correcto
- pedido con items de 2 productores distintos -> 2 Liquidaciones, cada una con su propio subtotal
- comision_plataforma/monto_neto calculados correctamente (10%/90%)
- fecha_limite = fecha_creacion + 24 horas

No se levanta Culqi ni Productos reales: crear_cargo se mockea para simular un cargo aprobado
(status_code 201), y httpx.Client (usado por crear_liquidaciones para resolver productor_id) se
mockea con un stub que resuelve producto_id -> productor_id desde un diccionario fijo. Como
crear_liquidaciones vive en su propio módulo (logica_liquidaciones.py, con su propio `import
httpx` — ver el docstring de ese archivo sobre por qué), el mock se aplica sobre
logica_liquidaciones.httpx.Client, no sobre main.httpx.Client. publicar_evento también se mockea
para no depender de RabbitMQ.

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de postgresql +
create_all() al importar main) — no se puede sustituir por SQLite, mismo criterio que el resto de
los servicios de este proyecto.
"""
import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import logica_liquidaciones
from reintento_liquidaciones import reintentar_liquidaciones_pendientes
from auth import verificar_token

client = TestClient(main.app)

main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}

# POST /pagos/procesar le pregunta a Pedidos si el pedido es de quien paga (y reenvía su token). En casi todos
# los tests esa pregunta se da por respondida que sí; los que prueban la propiedad restauran la función real.
ORIGINAL_EXIGIR_DUENO = main._exigir_dueno_para_pagar


@pytest.fixture(autouse=True)
def _el_pedido_es_de_quien_paga(monkeypatch):
    monkeypatch.setattr(main, "_exigir_dueno_para_pagar", lambda *args, **kwargs: None)


class _RespuestaProductoFalsa:
    def __init__(self, productor_id):
        self.status_code = 200
        self._productor_id = productor_id

    def raise_for_status(self):
        pass

    def json(self):
        return {"productor_id": self._productor_id}


class _ClienteProductosFalso:
    """Reemplaza httpx.Client() dentro de crear_liquidaciones: resuelve producto_id -> productor_id
    desde un diccionario fijo, sin tocar la red."""
    def __init__(self, mapa_producto_a_productor):
        self._mapa = mapa_producto_a_productor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        producto_id = url.rstrip("/").split("/")[-1]
        return _RespuestaProductoFalsa(self._mapa[producto_id])


def _mockear_productos(monkeypatch, mapa_producto_a_productor):
    monkeypatch.setattr(logica_liquidaciones.httpx, "Client", lambda *a, **kw: _ClienteProductosFalso(mapa_producto_a_productor))


def _mockear_cargo_aprobado(monkeypatch):
    monkeypatch.setattr(main, "crear_cargo", lambda **kwargs: {"status_code": 201, "data": {"id": "chr_test_123"}})


def _crear_pago_directo(items) -> "main.models.Pago":
    db = main.SessionLocal()
    try:
        monto = sum(i["cantidad"] * i["precio_unitario"] for i in items)
        pago = main.models.Pago(
            pedido_id=uuid.uuid4(),
            monto=monto,
            estado="pendiente",
            items=json.dumps(items),
        )
        db.add(pago)
        db.commit()
        db.refresh(pago)
        db.expunge(pago)
        return pago
    finally:
        db.close()


def _liquidaciones_de(pedido_id):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Liquidacion).filter(
            main.models.Liquidacion.pedido_id == pedido_id
        ).order_by(main.models.Liquidacion.monto_bruto).all()
    finally:
        db.close()


def _procesar(pago):
    # con un header Authorization real: procesar_pago reenvía el token del usuario a Pedidos (ver CON_TOKEN)
    return client.post("/pagos/procesar", json={
        "pedido_id": str(pago.pedido_id),
        "token_culqi": "tkn_test",
        "email": "cliente@test.com",
    }, headers=CON_TOKEN)


def test_pedido_un_solo_productor_crea_una_liquidacion(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto_a = str(uuid.uuid4())
    producto_b = str(uuid.uuid4())
    items = [
        {"producto_id": producto_a, "cantidad": 2, "precio_unitario": 20.0},   # 40.00
        {"producto_id": producto_b, "cantidad": 1, "precio_unitario": 10.0},   # 10.00
    ]
    pago = _crear_pago_directo(items)

    _mockear_cargo_aprobado(monkeypatch)
    _mockear_productos(monkeypatch, {producto_a: productor_id, producto_b: productor_id})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    resp = _procesar(pago)
    assert resp.status_code == 200, resp.text

    liquidaciones = _liquidaciones_de(pago.pedido_id)
    assert len(liquidaciones) == 1
    liq = liquidaciones[0]
    assert str(liq.beneficiario_id) == productor_id
    assert liq.beneficiario_tipo == "productor"
    assert liq.estado == "pendiente"
    assert float(liq.monto_bruto) == 50.00


def test_pedido_dos_productores_crea_dos_liquidaciones_con_su_propio_subtotal(monkeypatch):
    productor_1 = str(uuid.uuid4())
    productor_2 = str(uuid.uuid4())
    producto_a = str(uuid.uuid4())
    producto_b = str(uuid.uuid4())
    items = [
        {"producto_id": producto_a, "cantidad": 3, "precio_unitario": 10.0},   # 30.00 -> productor_1
        {"producto_id": producto_b, "cantidad": 2, "precio_unitario": 15.0},   # 30.00 -> productor_2
    ]
    pago = _crear_pago_directo(items)

    _mockear_cargo_aprobado(monkeypatch)
    _mockear_productos(monkeypatch, {producto_a: productor_1, producto_b: productor_2})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    resp = _procesar(pago)
    assert resp.status_code == 200, resp.text

    liquidaciones = _liquidaciones_de(pago.pedido_id)
    assert len(liquidaciones) == 2
    por_beneficiario = {str(l.beneficiario_id): l for l in liquidaciones}
    assert set(por_beneficiario.keys()) == {productor_1, productor_2}
    assert float(por_beneficiario[productor_1].monto_bruto) == 30.00
    assert float(por_beneficiario[productor_2].monto_bruto) == 30.00


def test_comision_plataforma_y_monto_neto_calculados_correctamente(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto_id = str(uuid.uuid4())
    items = [{"producto_id": producto_id, "cantidad": 1, "precio_unitario": 100.0}]
    pago = _crear_pago_directo(items)

    _mockear_cargo_aprobado(monkeypatch)
    _mockear_productos(monkeypatch, {producto_id: productor_id})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    resp = _procesar(pago)
    assert resp.status_code == 200, resp.text

    liq = _liquidaciones_de(pago.pedido_id)[0]
    assert float(liq.monto_bruto) == 100.00
    assert float(liq.comision_plataforma) == 10.00
    assert float(liq.monto_neto) == 90.00


def test_fecha_limite_es_24h_despues_de_fecha_creacion(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto_id = str(uuid.uuid4())
    items = [{"producto_id": producto_id, "cantidad": 1, "precio_unitario": 50.0}]
    pago = _crear_pago_directo(items)

    _mockear_cargo_aprobado(monkeypatch)
    _mockear_productos(monkeypatch, {producto_id: productor_id})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    resp = _procesar(pago)
    assert resp.status_code == 200, resp.text

    liq = _liquidaciones_de(pago.pedido_id)[0]
    assert liq.fecha_limite - liq.fecha_creacion == timedelta(hours=24)


def test_productos_no_responde_pago_se_confirma_sin_liquidaciones(monkeypatch):
    """El cargo en Culqi ya sucedió para cuando crear_liquidaciones corre — si Productos no
    responde, el pago debe quedar "aprobado" igual (el dinero sí se cobró); lo único que falta
    son las liquidaciones, un problema menor y recuperable a mano, no uno que deba ocultar el
    cobro real. Ver el docstring de crear_liquidaciones en main.py."""
    producto_id = str(uuid.uuid4())
    items = [{"producto_id": producto_id, "cantidad": 1, "precio_unitario": 50.0}]
    pago = _crear_pago_directo(items)

    _mockear_cargo_aprobado(monkeypatch)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    class _ClienteRoto:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *a, **kw):
            raise logica_liquidaciones.httpx.RequestError("boom", request=None)

    monkeypatch.setattr(logica_liquidaciones.httpx, "Client", lambda *a, **kw: _ClienteRoto())

    resp = _procesar(pago)
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobado"

    db = main.SessionLocal()
    try:
        pago_recargado = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).first()
    finally:
        db.close()
    assert pago_recargado.estado == "aprobado"
    assert _liquidaciones_de(pago.pedido_id) == []


# ============ Reintento en segundo plano (reintento_liquidaciones.py) ============
# El hilo en sí (loop infinito + time.sleep) no se testea directamente — se testea
# reintentar_liquidaciones_pendientes(db), la función de "un ciclo" que el hilo llama en cada
# iteración, invocándola una sola vez con una sesión real.

def _crear_pago_aprobado_directo(items) -> "main.models.Pago":
    """A diferencia de _crear_pago_directo (estado "pendiente", pasa por POST /pagos/procesar),
    este simula un pago que YA se confirmó pero se quedó sin liquidaciones — el punto de partida
    real del job de reintento."""
    db = main.SessionLocal()
    try:
        monto = sum(i["cantidad"] * i["precio_unitario"] for i in items)
        pago = main.models.Pago(
            pedido_id=uuid.uuid4(),
            monto=monto,
            estado="aprobado",
            culqi_charge_id="chr_test_ya_cobrado",
            items=json.dumps(items),
        )
        db.add(pago)
        db.commit()
        db.refresh(pago)
        db.expunge(pago)
        return pago
    finally:
        db.close()


def test_reintento_resuelve_pago_sin_liquidaciones(monkeypatch):
    productor_id = str(uuid.uuid4())
    producto_id = str(uuid.uuid4())
    pago = _crear_pago_aprobado_directo([{"producto_id": producto_id, "cantidad": 1, "precio_unitario": 40.0}])

    _mockear_productos(monkeypatch, {producto_id: productor_id})

    db = main.SessionLocal()
    try:
        reintentar_liquidaciones_pendientes(db)
    finally:
        db.close()

    liquidaciones = _liquidaciones_de(pago.pedido_id)
    assert len(liquidaciones) == 1
    assert str(liquidaciones[0].beneficiario_id) == productor_id
    assert float(liquidaciones[0].monto_bruto) == 40.00

    # Ya no debería aparecer en un segundo ciclo — pagos_sin_liquidaciones lo excluye porque ya
    # tiene su Liquidacion.
    db = main.SessionLocal()
    try:
        pendientes_despues = logica_liquidaciones.pagos_sin_liquidaciones(db)
    finally:
        db.close()
    assert pago.pedido_id not in {p.pedido_id for p in pendientes_despues}


def test_reintento_un_pago_que_sigue_fallando_no_afecta_a_los_demas(monkeypatch):
    productor_ok = str(uuid.uuid4())
    producto_ok = str(uuid.uuid4())
    producto_roto = str(uuid.uuid4())  # no está en el mapa -> KeyError al resolver su productor_id

    pago_roto = _crear_pago_aprobado_directo([{"producto_id": producto_roto, "cantidad": 1, "precio_unitario": 10.0}])
    pago_ok = _crear_pago_aprobado_directo([{"producto_id": producto_ok, "cantidad": 2, "precio_unitario": 15.0}])

    _mockear_productos(monkeypatch, {producto_ok: productor_ok})  # producto_roto queda sin mapear a propósito

    db = main.SessionLocal()
    try:
        reintentar_liquidaciones_pendientes(db)
    finally:
        db.close()

    assert _liquidaciones_de(pago_roto.pedido_id) == []
    liquidaciones_ok = _liquidaciones_de(pago_ok.pedido_id)
    assert len(liquidaciones_ok) == 1
    assert float(liquidaciones_ok[0].monto_bruto) == 30.00

    # pago_roto sigue pendiente para el próximo ciclo; pago_ok ya no.
    db = main.SessionLocal()
    try:
        pendientes = {p.pedido_id for p in logica_liquidaciones.pagos_sin_liquidaciones(db)}
    finally:
        db.close()
    assert pago_roto.pedido_id in pendientes
    assert pago_ok.pedido_id not in pendientes


# ============ GET /pagos/sin-liquidaciones ============

def test_listar_sin_liquidaciones_requiere_admin():
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}
    resp = client.get("/pagos/sin-liquidaciones")
    assert resp.status_code == 403
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}


def test_listar_sin_liquidaciones_como_admin():
    pago = _crear_pago_aprobado_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 20.0}])

    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "admin"}
    try:
        resp = client.get("/pagos/sin-liquidaciones")
    finally:
        main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}

    assert resp.status_code == 200, resp.text
    ids = {p["pedido_id"] for p in resp.json()}
    assert str(pago.pedido_id) in ids


# ============ POST /pagos/interno/liquidar-repartidor ============

def test_liquidar_repartidor_crea_liquidacion_90_10():
    pedido_id = str(uuid.uuid4())
    repartidor_id = str(uuid.uuid4())

    resp = client.post(
        "/pagos/interno/liquidar-repartidor",
        json={"pedido_id": pedido_id, "repartidor_id": repartidor_id, "costo_envio": 20.0},
        headers={"X-Servicio-Secreto": main.TRANSPORTE_A_PAGOS_SECRETO},
    )
    assert resp.status_code == 200, resp.text

    db = main.SessionLocal()
    try:
        liquidacion = db.query(main.models.Liquidacion).filter(
            main.models.Liquidacion.pedido_id == pedido_id
        ).first()
    finally:
        db.close()

    assert liquidacion is not None
    assert liquidacion.beneficiario_tipo == "repartidor"
    assert str(liquidacion.beneficiario_id) == repartidor_id
    assert float(liquidacion.monto_bruto) == 20.00
    assert float(liquidacion.comision_plataforma) == 2.00
    assert float(liquidacion.monto_neto) == 18.00
    assert liquidacion.estado == "pendiente"


def test_liquidar_repartidor_sin_secreto_falla_403():
    resp = client.post(
        "/pagos/interno/liquidar-repartidor",
        json={"pedido_id": str(uuid.uuid4()), "repartidor_id": str(uuid.uuid4()), "costo_envio": 10.0},
        headers={"X-Servicio-Secreto": "secreto-incorrecto"},
    )
    assert resp.status_code == 403


def test_liquidar_repartidor_llamado_dos_veces_no_duplica():
    """Simula el reintento de Transporte tras un fallo de red que sí había llegado a completarse
    la primera vez: mismo pedido_id, segunda llamada — debe responder 200 igual (idempotente, no
    un error), y sin crear una segunda fila."""
    pedido_id = str(uuid.uuid4())
    repartidor_id = str(uuid.uuid4())
    payload = {"pedido_id": pedido_id, "repartidor_id": repartidor_id, "costo_envio": 15.0}
    headers = {"X-Servicio-Secreto": main.TRANSPORTE_A_PAGOS_SECRETO}

    primera = client.post("/pagos/interno/liquidar-repartidor", json=payload, headers=headers)
    assert primera.status_code == 200, primera.text

    segunda = client.post("/pagos/interno/liquidar-repartidor", json=payload, headers=headers)
    assert segunda.status_code == 200, segunda.text

    db = main.SessionLocal()
    try:
        liquidaciones = db.query(main.models.Liquidacion).filter(
            main.models.Liquidacion.pedido_id == pedido_id
        ).all()
    finally:
        db.close()
    assert len(liquidaciones) == 1


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/pagos/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
        ("POST", "/pagos/interno/liquidar-repartidor", {"pedido_id": str(_uuid.uuid4()), "repartidor_id": str(_uuid.uuid4()), "costo_envio": 5}, "TRANSPORTE_A_PAGOS_SECRETO"),
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


# ============ GET /pagos/{pedido_id}: ya no es público ============

class _ClientePedidosFalso:
    """Reemplaza httpx.Client() para la pregunta a Pedidos "¿de quién es este pedido?": guarda la
    (url, kwargs) de cada llamada y responde un pedido fijo, sin tocar la red."""
    def __init__(self, llamadas, status=200, usuario_id=None, roto=False):
        self._llamadas, self._status, self._usuario_id, self._roto = llamadas, status, usuario_id, roto

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self._llamadas.append((url, kwargs))
        if self._roto:
            raise main.httpx.RequestError("boom", request=None)
        respuesta = MagicMock()
        respuesta.status_code = self._status
        respuesta.json.return_value = {"usuario_id": self._usuario_id}
        return respuesta


def _mockear_pedidos(monkeypatch, **kwargs):
    llamadas = []
    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _ClientePedidosFalso(llamadas, **kwargs))
    return llamadas


def _como(sub, rol):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(sub), "rol": rol}


CON_TOKEN = {"Authorization": "Bearer token-de-prueba"}


def _restaurar_sesion_por_defecto():
    _como(uuid.uuid4(), "comprador")


def _pago_de_prueba():
    return _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 2, "precio_unitario": 20.0}])


def test_un_pago_no_se_puede_leer_sin_sesion():
    pago = _pago_de_prueba()
    guardado = main.app.dependency_overrides.pop(verificar_token)
    try:
        resp = client.get(f"/pagos/{pago.pedido_id}")
        assert resp.status_code in (401, 403)
        assert "monto" not in resp.text
    finally:
        main.app.dependency_overrides[verificar_token] = guardado


def test_el_comprador_dueno_del_pedido_ve_su_pago(monkeypatch):
    comprador = uuid.uuid4()
    pago = _pago_de_prueba()
    llamadas = _mockear_pedidos(monkeypatch, usuario_id=str(comprador))
    _como(comprador, "comprador")
    try:
        resp = client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN)
    finally:
        _restaurar_sesion_por_defecto()

    assert resp.status_code == 200, resp.text
    assert float(resp.json()["monto"]) == 40.0
    # le pregunta a Pedidos con el mismo token del usuario (no con un secreto de servicio)
    url, kwargs = llamadas[0]
    assert url.endswith(f"/pedidos/{pago.pedido_id}")
    assert kwargs["headers"] == {"Authorization": "Bearer token-de-prueba"}


def test_un_comprador_ajeno_no_ve_el_pago(monkeypatch):
    pago = _pago_de_prueba()
    _mockear_pedidos(monkeypatch, usuario_id=str(uuid.uuid4()))  # el pedido es de otro comprador
    _como(uuid.uuid4(), "comprador")
    try:
        resp = client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN)
    finally:
        _restaurar_sesion_por_defecto()

    assert resp.status_code == 403, resp.text
    assert "monto" not in resp.text


def test_un_repartidor_o_productor_tampoco_ve_el_pago_aunque_puedan_leer_el_pedido(monkeypatch):
    pago = _pago_de_prueba()
    _mockear_pedidos(monkeypatch, usuario_id=str(uuid.uuid4()))  # Pedidos les responde 200 (rol con acceso)
    try:
        for rol in ("repartidor", "productor", "verificador"):
            _como(uuid.uuid4(), rol)
            resp = client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN)
            assert resp.status_code == 403, (rol, resp.text)
    finally:
        _restaurar_sesion_por_defecto()


def test_el_admin_ve_cualquier_pago_sin_preguntarle_a_pedidos(monkeypatch):
    pago = _pago_de_prueba()
    llamadas = _mockear_pedidos(monkeypatch, usuario_id=str(uuid.uuid4()))
    _como(uuid.uuid4(), "admin")
    try:
        resp = client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN)
    finally:
        _restaurar_sesion_por_defecto()

    assert resp.status_code == 200, resp.text
    assert llamadas == []


def test_ver_un_pago_si_pedidos_no_responde_da_503_y_si_no_existe_el_pedido_404(monkeypatch):
    pago = _pago_de_prueba()
    try:
        _como(uuid.uuid4(), "comprador")
        _mockear_pedidos(monkeypatch, roto=True)
        assert client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN).status_code == 503

        _mockear_pedidos(monkeypatch, status=404)
        assert client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN).status_code == 404

        _mockear_pedidos(monkeypatch, status=401)  # Pedidos no acepta el token
        assert client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN).status_code == 403
    finally:
        _restaurar_sesion_por_defecto()


def test_un_id_de_pedido_mal_formado_falla_422_no_500():
    resp = client.get("/pagos/no-es-un-uuid", headers=CON_TOKEN)
    assert resp.status_code == 422


# ============ Un pago rechazado repone el stock una sola vez ============

def _mockear_cargo_rechazado(monkeypatch):
    monkeypatch.setattr(main, "crear_cargo", lambda **kwargs: {"status_code": 400, "data": {"user_message": "Tarjeta rechazada"}})


def _crear_pago_con_estado(estado, items) -> "main.models.Pago":
    db = main.SessionLocal()
    try:
        monto = sum(i["cantidad"] * i["precio_unitario"] for i in items)
        pago = main.models.Pago(pedido_id=uuid.uuid4(), monto=monto, estado=estado, items=json.dumps(items))
        db.add(pago)
        db.commit()
        db.refresh(pago)
        db.expunge(pago)
        return pago
    finally:
        db.close()


def _estado_del_pago(pago):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).first().estado
    finally:
        db.close()


def test_el_primer_rechazo_devuelve_el_stock_y_cada_reintento_fallido_lo_deja_como_estaba(monkeypatch):
    """La interfaz deja reintentar el pago de un pedido rechazado. Antes cada intento fallido volvía a reponer
    las cantidades en Productos: con una tarjeta mala repetida, el stock del producto subía sin límite. Ahora el
    primer rechazo devuelve el stock; cada reintento lo reserva antes de cobrar y, si falla, libera solo eso."""
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 2, "precio_unitario": 20.0}])
    _mockear_cargo_rechazado(monkeypatch)
    compensar, reservar = MagicMock(), MagicMock()
    monkeypatch.setattr(main, "compensar_stock", compensar)
    monkeypatch.setattr(main, "reservar_stock", reservar)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    primero = _procesar(pago)
    segundo = _procesar(pago)
    tercero = _procesar(pago)

    assert (primero.status_code, segundo.status_code, tercero.status_code) == (402, 402, 402)
    # 1 devolución por el primer rechazo + 1 liberación por cada reintento (que antes había reservado): el
    # stock neto devuelto es el de UNA sola vez.
    assert reservar.call_count == 2
    assert compensar.call_count == 3
    assert _estado_del_pago(pago) == "rechazado"


def test_reintentar_un_pago_rechazado_reserva_el_stock_antes_de_cobrar_y_al_aprobar_lo_conserva(monkeypatch):
    """Antes un reintento que salía bien cobraba un pedido cuyo stock ya se había devuelto (y quizá vendido a otro)."""
    producto, productor = str(uuid.uuid4()), str(uuid.uuid4())
    pago = _crear_pago_con_estado("rechazado", [{"producto_id": producto, "cantidad": 3, "unidad": "saco", "cantidad_base": 30, "precio_unitario": 20.0}])
    orden = []
    monkeypatch.setattr(main, "reservar_stock", lambda items: orden.append(("reservar", items)))
    compensar = MagicMock()
    monkeypatch.setattr(main, "compensar_stock", compensar)
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: (orden.append(("cobrar", None)) or {"status_code": 201, "data": {"id": "chr_reintento"}}))
    _mockear_productos(monkeypatch, {producto: productor})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    resp = _procesar(pago)

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobado"
    assert [paso for paso, _ in orden] == ["reservar", "cobrar"]  # reserva ANTES de cobrar
    assert orden[0][1] == pago.items
    compensar.assert_not_called()  # el stock reservado se queda reservado
    assert _estado_del_pago(pago) == "aprobado"
    assert len(_liquidaciones_de(pago.pedido_id)) == 1


def test_si_ya_no_hay_stock_el_reintento_no_cobra_nada_y_el_pago_sigue_rechazado(monkeypatch):
    pago = _crear_pago_con_estado("rechazado", [{"producto_id": str(uuid.uuid4()), "cantidad": 2, "precio_unitario": 20.0}])
    monkeypatch.setattr(main, "reservar_stock", MagicMock(side_effect=HTTPException(
        status_code=409, detail="No se puede reintentar el pago: Stock insuficiente para Papa. Crea un nuevo pedido.")))
    cargo, compensar = MagicMock(), MagicMock()
    monkeypatch.setattr(main, "crear_cargo", cargo)
    monkeypatch.setattr(main, "compensar_stock", compensar)

    resp = _procesar(pago)

    assert resp.status_code == 409, resp.text
    assert "Stock insuficiente para Papa" in resp.json()["detail"]
    cargo.assert_not_called()
    compensar.assert_not_called()
    assert _estado_del_pago(pago) == "rechazado"


def test_si_la_pasarela_no_responde_no_se_cobra_ni_cambia_el_pago(monkeypatch):
    def pasarela_caida(**kwargs):
        raise main.httpx.RequestError("boom")

    monkeypatch.setattr(main, "crear_cargo", pasarela_caida)
    reservar, compensar = MagicMock(), MagicMock()
    monkeypatch.setattr(main, "reservar_stock", reservar)
    monkeypatch.setattr(main, "compensar_stock", compensar)

    primer_intento = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    resp = _procesar(primer_intento)
    assert resp.status_code == 502, resp.text
    assert _estado_del_pago(primer_intento) == "pendiente"
    reservar.assert_not_called()
    compensar.assert_not_called()

    # en un reintento se había reservado stock para este intento: se libera
    reintento = _crear_pago_con_estado("rechazado", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    resp = _procesar(reintento)
    assert resp.status_code == 502, resp.text
    assert _estado_del_pago(reintento) == "rechazado"
    reservar.assert_called_once_with(reintento.items)
    compensar.assert_called_once_with(reintento.items)


def test_un_segundo_intento_sobre_un_pago_ya_aprobado_falla_409_sin_cobrar_otra_vez(monkeypatch):
    producto, productor = str(uuid.uuid4()), str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 1, "precio_unitario": 50.0}])
    cobros = []
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: (cobros.append(1) or {"status_code": 201, "data": {"id": "chr_1"}}))
    _mockear_productos(monkeypatch, {producto: productor})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    assert _procesar(pago).status_code == 200
    segundo = _procesar(pago)

    assert segundo.status_code == 409, segundo.text
    assert len(cobros) == 1


def test_el_id_de_pedido_de_procesar_debe_ser_un_uuid():
    resp = client.post("/pagos/procesar", json={"pedido_id": "no-es-uuid", "token_culqi": "t", "email": "a@b.co"}, headers=CON_TOKEN)
    assert resp.status_code == 422


# ---- solo el comprador dueño del pedido lo paga

def test_solo_el_comprador_dueno_puede_pagar_su_pedido(monkeypatch):
    """Antes cualquier usuario con sesión que conociera el id de un pedido podía cobrar con su tarjeta o, con
    una tarjeta mala, dejar rechazado el pedido de otro (y devolverle el stock)."""
    dueno = uuid.uuid4()
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    monkeypatch.setattr(main, "_exigir_dueno_para_pagar", ORIGINAL_EXIGIR_DUENO)  # estos tests SÍ prueban la propiedad
    cargo = MagicMock(return_value={"status_code": 400, "data": {"user_message": "Tarjeta rechazada"}})
    monkeypatch.setattr(main, "crear_cargo", cargo)
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    llamadas = _mockear_pedidos(monkeypatch, usuario_id=str(dueno))  # el pedido es de `dueno`
    try:
        # otro comprador y el admin: 403, sin cobrar y sin tocar el pago
        for rol in ("comprador", "admin", "repartidor"):
            _como(uuid.uuid4(), rol)
            resp = _procesar(pago)
            assert resp.status_code == 403, (rol, resp.text)
            assert "pagar este pedido" in resp.json()["detail"]
        cargo.assert_not_called()
        assert _estado_del_pago(pago) == "pendiente"

        # el dueño pasa la comprobación (y aquí su tarjeta es rechazada: 402)
        _como(dueno, "comprador")
        assert _procesar(pago).status_code == 402
        cargo.assert_called_once()
        # a Pedidos se le pregunta con el mismo token del usuario
        assert llamadas[-1][1]["headers"] == {"Authorization": "Bearer token-de-prueba"}
    finally:
        _restaurar_sesion_por_defecto()


def test_pagar_un_pedido_que_no_existe_o_con_pedidos_caido(monkeypatch):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    monkeypatch.setattr(main, "_exigir_dueno_para_pagar", ORIGINAL_EXIGIR_DUENO)
    cargo = MagicMock()
    monkeypatch.setattr(main, "crear_cargo", cargo)
    try:
        _mockear_pedidos(monkeypatch, status=404)
        assert _procesar(pago).status_code == 404
        _mockear_pedidos(monkeypatch, roto=True)
        assert _procesar(pago).status_code == 503
        cargo.assert_not_called()
    finally:
        _restaurar_sesion_por_defecto()


# ---- cuánto stock se devuelve y se reserva

class _ClienteStockFalso:
    """Reemplaza httpx.Client() para las llamadas a Productos (reponer y descontar en lote): guarda cada
    (url, kwargs) y responde lo que se le indique, sin tocar la red."""
    def __init__(self, llamadas, status=200, cuerpo=None, roto=False):
        self._llamadas, self._status, self._cuerpo, self._roto = llamadas, status, cuerpo, roto

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self._llamadas.append((url, kwargs))
        if self._roto:
            raise main.httpx.RequestError("boom")
        respuesta = MagicMock()
        respuesta.status_code = self._status
        respuesta.json.return_value = self._cuerpo or {}
        return respuesta


def _mockear_stock(monkeypatch, **kwargs):
    llamadas = []
    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _ClienteStockFalso(llamadas, **kwargs))
    monkeypatch.setattr(main, "PAGOS_A_PRODUCTOS_SECRETO", "secreto-de-prueba-pagos")
    return llamadas


def _items_de_prueba():
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    items = [
        {"producto_id": a, "cantidad": 3, "unidad": "saco", "cantidad_base": 30, "precio_unitario": 20.0},
        {"producto_id": a, "cantidad": 1, "unidad": "saco", "cantidad_base": 10, "precio_unitario": 20.0},
        {"producto_id": b, "cantidad": 1, "unidad": "arroba", "cantidad_base": 11.5, "precio_unitario": 30.0},
        {"producto_id": c, "cantidad": 4, "precio_unitario": 5.0},  # pedido anterior: sin cantidad_base
    ]
    return a, b, c, items


def test_compensar_stock_repone_en_unidad_base_suma_las_lineas_y_redondea_hacia_arriba(monkeypatch):
    """3 sacos de 10 kg descontaban 30 kg y devolvían 3."""
    a, b, c, items = _items_de_prueba()
    llamadas = _mockear_stock(monkeypatch)

    main.compensar_stock(json.dumps(items))

    repuesto = {url.split("/")[-3]: kwargs["json"]["cantidad"] for url, kwargs in llamadas}
    assert repuesto == {a: 40, b: 12, c: 4}  # 30+10 / 11,5 hacia arriba / sin cantidad_base: la cantidad tal cual
    assert all(kwargs["headers"] == {"X-Servicio-Secreto": "secreto-de-prueba-pagos"} for _, kwargs in llamadas)


def test_compensar_stock_sin_items_no_llama_a_nadie(monkeypatch):
    llamadas = _mockear_stock(monkeypatch)
    main.compensar_stock(None)
    main.compensar_stock("")
    assert llamadas == []


def test_reservar_stock_descuenta_en_lote_lo_mismo_que_compensar_stock_repone(monkeypatch):
    a, b, c, items = _items_de_prueba()
    llamadas = _mockear_stock(monkeypatch)

    main.reservar_stock(json.dumps(items))

    assert len(llamadas) == 1
    url, kwargs = llamadas[0]
    assert url.endswith("/productos/interno/descontar-stock-lote")
    assert kwargs["headers"] == {"X-Servicio-Secreto": "secreto-de-prueba-pagos"}
    assert {l["producto_id"]: l["cantidad"] for l in kwargs["json"]["lineas"]} == {a: 40, b: 11.5, c: 4}


def test_reservar_stock_traduce_los_rechazos_de_productos():
    _, _, _, items = _items_de_prueba()
    casos = [
        (dict(status=409, cuerpo={"detail": "Stock insuficiente para Papa"}), 409, "Stock insuficiente para Papa"),
        (dict(status=409, cuerpo={"detail": "Papa aún no está disponible para comprar."}), 409, "aún no está disponible"),
        (dict(status=404, cuerpo={"detail": "Producto x no existe"}), 409, "no existe"),
        (dict(status=500, cuerpo={}), 503, "No se pudo reservar"),
        (dict(roto=True), 503, "no disponible"),
    ]
    for kwargs, esperado, texto in casos:
        with pytest.MonkeyPatch.context() as mp:
            _mockear_stock(mp, **kwargs)
            with pytest.raises(HTTPException) as error:
                main.reservar_stock(json.dumps(items))
        assert error.value.status_code == esperado, kwargs
        assert texto in error.value.detail, (kwargs, error.value.detail)
        if esperado == 409:
            assert "Crea un nuevo pedido" in error.value.detail

    with pytest.raises(HTTPException) as error:
        main.reservar_stock(None)  # un pago viejo sin detalle de productos: no se puede reservar
    assert error.value.status_code == 409


# ============ GET /pagos/condiciones: lo que se le muestra al comprador y al repartidor ============

def test_las_condiciones_de_pago_son_publicas_y_usan_la_misma_comision_que_las_liquidaciones():
    resp = client.get("/pagos/condiciones")

    assert resp.status_code == 200, resp.text
    porcentaje = resp.json()["comision_plataforma_porcentaje"]
    assert porcentaje == float(logica_liquidaciones.COMISION_PLATAFORMA_PORCENTAJE * 100) == 10.0
    # el repartidor ve "ganas S/ 5.40" por un envío de S/ 6.00: lo mismo que se le liquida
    from decimal import Decimal
    bruto, comision, neto = logica_liquidaciones.calcular_comision_y_neto(Decimal("6.00"))
    assert (comision, neto) == (Decimal("0.60"), Decimal("5.40"))


# ============ Límite de Yape por operación ============

def _procesar_con(pago, metodo):
    return client.post("/pagos/procesar", json={
        "pedido_id": str(pago.pedido_id), "token_culqi": "tkn_test", "email": "cliente@test.com", "metodo": metodo,
    }, headers=CON_TOKEN)


def test_yape_por_encima_del_limite_se_rechaza_sin_cobrar_ni_tocar_el_stock(monkeypatch):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 500.01}])
    cargo, reservar = MagicMock(), MagicMock()
    monkeypatch.setattr(main, "crear_cargo", cargo)
    monkeypatch.setattr(main, "reservar_stock", reservar)

    resp = _procesar_con(pago, "yape")

    assert resp.status_code == 422, resp.text
    assert "hasta S/ 500.00" in resp.json()["detail"] and "500.01" in resp.json()["detail"]
    cargo.assert_not_called()
    reservar.assert_not_called()
    assert _estado_del_pago(pago) == "pendiente"


def test_yape_justo_en_el_limite_se_cobra_y_queda_guardado_el_metodo(monkeypatch):
    producto, productor = str(uuid.uuid4()), str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 2, "precio_unitario": 250.0}])
    _mockear_cargo_aprobado(monkeypatch)
    _mockear_productos(monkeypatch, {producto: productor})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    resp = _procesar_con(pago, "yape")

    assert resp.status_code == 200, resp.text
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        assert (fila.estado, fila.metodo) == ("aprobado", "yape")
    finally:
        db.close()


def test_con_tarjeta_no_hay_limite_y_un_metodo_desconocido_falla_422(monkeypatch):
    producto, productor = str(uuid.uuid4()), str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 3, "precio_unitario": 400.0}])
    _mockear_cargo_aprobado(monkeypatch)
    _mockear_productos(monkeypatch, {producto: productor})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    assert _procesar_con(pago, "bitcoin").status_code == 422
    assert _procesar_con(pago, "tarjeta").status_code == 200


def test_las_condiciones_dicen_el_limite_de_yape():
    assert client.get("/pagos/condiciones").json()["yape_maximo_por_operacion"] == 500.0


# ============ Transferencia bancaria: el comprador sube la constancia y un admin la revisa ============

JPEG_CONSTANCIA = b"\xff\xd8\xff\xe0" + b"constancia de transferencia" * 8
CUENTA_DE_EJEMPLO = {"banco": "Banco de ejemplo", "titular": "Chakra Shop S.A.C.", "cuenta": "000-0000000-0-00", "cci": "000-000-000000000000-00"}


@pytest.fixture
def transferencia(monkeypatch):
    """Cuenta configurada, MinIO simulado (guarda en un dict) y los eventos/stock/liquidaciones capturados."""
    monkeypatch.setattr(main, "TRANSFERENCIA_DATOS", dict(CUENTA_DE_EJEMPLO))
    guardadas = {}

    def _subir(datos, tipo):
        nombre = f"{uuid.uuid4()}.jpg"
        guardadas[nombre] = datos
        return nombre

    monkeypatch.setattr(main, "subir_constancia", _subir)
    monkeypatch.setattr(main, "leer_constancia", lambda nombre: guardadas[nombre])
    eventos, reservar, compensar = [], MagicMock(), MagicMock()
    monkeypatch.setattr(main, "publicar_evento", eventos.append)
    monkeypatch.setattr(main, "reservar_stock", reservar)
    monkeypatch.setattr(main, "compensar_stock", compensar)
    return {"guardadas": guardadas, "eventos": eventos, "reservar": reservar, "compensar": compensar}


def _enviar_constancia(pago, numero="OP123456", datos=JPEG_CONSTANCIA, tipo="image/jpeg"):
    return client.post(
        "/pagos/transferencia",
        data={"pedido_id": str(pago.pedido_id), "numero_operacion": numero},
        files={"archivo": ("constancia.jpg", datos, tipo)},
        headers=CON_TOKEN,
    )


def _sesion_con_rol(rol):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": rol}


def _fila_pago(pago):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
    finally:
        db.close()


def test_la_transferencia_va_a_revision_y_el_admin_la_aprueba_como_un_pago_de_culqi(transferencia, monkeypatch):
    producto, productor = str(uuid.uuid4()), str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 4, "precio_unitario": 200.0}])  # S/ 800: más que Yape
    _mockear_productos(monkeypatch, {producto: productor})

    enviada = _enviar_constancia(pago, numero=" OP-778899 ")

    assert enviada.status_code == 200, enviada.text
    fila = _fila_pago(pago)
    assert (fila.estado, fila.metodo, fila.numero_operacion) == ("en_revision", "transferencia", "OP-778899")
    assert transferencia["guardadas"][fila.constancia_objeto] == JPEG_CONSTANCIA
    assert transferencia["eventos"] == []  # todavía no se confirma nada

    _sesion_con_rol("admin")
    try:
        lista = client.get("/pagos/admin/transferencias").json()
        constancia = client.get(f"/pagos/admin/transferencias/{pago.pedido_id}/constancia")
        aprobada = client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/aprobar")
        otra_vez = client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/aprobar")
    finally:
        _sesion_con_rol("comprador")

    item = next(t for t in lista if t["pedido_id"] == str(pago.pedido_id))
    assert (item["monto"], item["numero_operacion"]) == (800.0, "OP-778899")
    assert constancia.status_code == 200 and constancia.content == JPEG_CONSTANCIA
    assert constancia.headers["content-type"] == "image/jpeg" and constancia.headers["cache-control"] == "no-store"
    assert aprobada.status_code == 200, aprobada.text
    assert _fila_pago(pago).estado == "aprobado" and _fila_pago(pago).revisado_por is not None
    assert transferencia["eventos"] == [{"evento": "pago_confirmado", "pedido_id": str(pago.pedido_id), "monto": "800.00"}]
    assert len(_liquidaciones_de(pago.pedido_id)) == 1
    assert otra_vez.status_code == 409  # dos admins a la vez: el segundo ve que ya no está en revisión
    transferencia["compensar"].assert_not_called()


def test_rechazada_devuelve_el_stock_deja_el_motivo_y_el_comprador_puede_volver_a_enviarla(transferencia):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 900.0}])
    assert _enviar_constancia(pago).status_code == 200

    _sesion_con_rol("admin")
    try:
        sin_motivo = client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/rechazar", json={"motivo": "  "})
        rechazada = client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/rechazar",
                                json={"motivo": "El monto transferido no coincide con el total"})
    finally:
        _sesion_con_rol("comprador")

    assert sin_motivo.status_code == 422
    assert rechazada.status_code == 200, rechazada.text
    fila = _fila_pago(pago)
    assert (fila.estado, fila.motivo_rechazo) == ("rechazado", "El monto transferido no coincide con el total")
    transferencia["compensar"].assert_called_once_with(fila.items)
    assert transferencia["eventos"] == [{"evento": "pago_rechazado", "pedido_id": str(pago.pedido_id),
                                         "motivo": "El monto transferido no coincide con el total"}]

    # el comprador corrige y vuelve a enviar: se reserva otra vez el stock y vuelve a revisión, sin el motivo viejo
    otra = _enviar_constancia(pago, numero="OP999000", datos=JPEG_CONSTANCIA + b"nueva")
    assert otra.status_code == 200, otra.text
    transferencia["reservar"].assert_called_once_with(fila.items)
    fila = _fila_pago(pago)
    assert (fila.estado, fila.numero_operacion, fila.motivo_rechazo) == ("en_revision", "OP999000", None)


def test_una_constancia_mal_formada_o_repetida_no_se_acepta(transferencia):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}])

    assert _enviar_constancia(pago, numero="12").status_code == 422          # muy corto
    assert _enviar_constancia(pago, numero="OP 12 34").status_code == 422    # con espacios
    assert _enviar_constancia(pago, tipo="application/pdf").status_code == 422
    assert _enviar_constancia(pago, datos=b"<html>no soy una foto</html>").status_code == 422
    assert _fila_pago(pago).estado == "pendiente"

    assert _enviar_constancia(pago).status_code == 200
    repetida = _enviar_constancia(pago, numero="OP000111")
    assert repetida.status_code == 409 and "revisión" in repetida.json()["detail"]
    # mientras está en revisión tampoco se puede pagar con tarjeta (se cobraría dos veces)
    assert _procesar(pago).status_code == 409


def test_un_pedido_pagado_no_acepta_constancia_y_sin_cuenta_configurada_no_se_ofrece(transferencia, monkeypatch):
    pagado = _crear_pago_con_estado("aprobado", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}])
    assert _enviar_constancia(pagado).status_code == 409

    monkeypatch.setattr(main, "TRANSFERENCIA_DATOS", {"banco": "X", "titular": None, "cuenta": None, "cci": None})
    pendiente = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}])
    assert _enviar_constancia(pendiente).status_code == 503
    assert client.get("/pagos/condiciones").json()["transferencia"] is None


def test_las_condiciones_traen_la_cuenta_para_transferir(transferencia):
    assert client.get("/pagos/condiciones").json()["transferencia"] == CUENTA_DE_EJEMPLO


def test_si_no_se_puede_guardar_la_constancia_responde_502_y_un_reintento_libera_lo_reservado(transferencia, monkeypatch):
    pago = _crear_pago_con_estado("rechazado", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}])
    monkeypatch.setattr(main, "subir_constancia", MagicMock(side_effect=Exception("MinIO caído")))

    resp = _enviar_constancia(pago)

    assert resp.status_code == 502
    transferencia["reservar"].assert_called_once()
    transferencia["compensar"].assert_called_once()  # lo reservado para este intento se devuelve
    assert _fila_pago(pago).estado == "rechazado"


def test_solo_un_admin_revisa_transferencias(transferencia):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}])
    assert _enviar_constancia(pago).status_code == 200
    for rol in ("comprador", "productor", "repartidor"):
        _sesion_con_rol(rol)
        try:
            assert client.get("/pagos/admin/transferencias").status_code == 403, rol
            assert client.get(f"/pagos/admin/transferencias/{pago.pedido_id}/constancia").status_code == 403, rol
            assert client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/aprobar").status_code == 403, rol
            assert client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/rechazar", json={"motivo": "no"}).status_code == 403, rol
        finally:
            _sesion_con_rol("comprador")
    assert _fila_pago(pago).estado == "en_revision"


def test_una_constancia_de_mas_de_5_mb_falla_413(transferencia):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}])
    resp = _enviar_constancia(pago, datos=b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024))
    assert resp.status_code == 413
    assert _fila_pago(pago).estado == "pendiente"



# ============ Pedidos en camión: adelanto al pedir (70%) y saldo al llegar (30%) ============

import rabbitmq_consumer  # noqa: E402


def _pago_en_camion(total=1000.0, adelanto=700.0, estado="pendiente"):
    producto = str(uuid.uuid4())
    items = [{"producto_id": producto, "cantidad": 1, "precio_unitario": total}]
    db = main.SessionLocal()
    try:
        pago = main.models.Pago(pedido_id=uuid.uuid4(), monto=total, monto_adelanto=adelanto, estado=estado, items=json.dumps(items))
        db.add(pago)
        db.commit()
        db.refresh(pago)
        db.expunge(pago)
        return pago, producto
    finally:
        db.close()


def _capturar(monkeypatch):
    cobros, eventos, compensar = [], [], MagicMock()
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: cobros.append(kw) or {"status_code": 201, "data": {"id": f"chr_{len(cobros)}"}})
    monkeypatch.setattr(main, "publicar_evento", eventos.append)
    monkeypatch.setattr(main, "compensar_stock", compensar)
    return cobros, eventos, compensar


def test_el_primer_cobro_es_el_adelanto_y_el_segundo_el_saldo(monkeypatch):
    pago, producto = _pago_en_camion()
    cobros, eventos, _ = _capturar(monkeypatch)
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})

    primero = _procesar(pago)
    assert primero.status_code == 200 and primero.json()["parte"] == "adelanto"
    assert cobros[0]["monto_soles"] == 700.0 and "(adelanto)" in cobros[0]["descripcion"]
    assert _estado_del_pago(pago) == "adelanto_pagado"
    assert eventos == [{"evento": "pago_confirmado", "pedido_id": str(pago.pedido_id), "adelanto": True,
                        "monto": "700.00", "saldo": "300.00"}]
    assert _liquidaciones_de(pago.pedido_id) == []  # todavía no está pagado entero

    _como(str(uuid.uuid4()), "admin")
    try:
        visto = client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN).json()
    finally:
        _restaurar_sesion_por_defecto()
    assert (visto["parte_a_pagar"], visto["monto_a_pagar"]) == ("saldo", 300.0)

    segundo = _procesar(pago)
    assert segundo.status_code == 200 and segundo.json()["parte"] == "saldo"
    assert cobros[1]["monto_soles"] == 300.0
    fila = _fila_pago(pago)
    assert fila.estado == "aprobado" and fila.culqi_charge_id == "chr_1" and fila.culqi_charge_id_saldo == "chr_2"
    assert eventos[-1] == {"evento": "saldo_pagado", "pedido_id": str(pago.pedido_id), "monto": "300.00"}
    assert len(_liquidaciones_de(pago.pedido_id)) == 1
    assert _procesar(pago).status_code == 409  # ya está todo pagado


def test_si_el_saldo_se_rechaza_el_pedido_sigue_en_el_camion_y_no_se_devuelve_stock(monkeypatch):
    pago, _ = _pago_en_camion(estado="adelanto_pagado")
    _, eventos, compensar = _capturar(monkeypatch)
    _mockear_cargo_rechazado(monkeypatch)

    resp = _procesar(pago)
    assert resp.status_code == 402
    assert _estado_del_pago(pago) == "adelanto_pagado"
    compensar.assert_not_called()
    assert eventos == []  # ni pago_rechazado: el pedido no se cancela


def test_el_limite_de_yape_se_mira_en_la_parte_que_se_paga(monkeypatch):
    pago, _ = _pago_en_camion(total=1000.0, adelanto=700.0)
    cobros, _, _ = _capturar(monkeypatch)
    assert _procesar_con(pago, "yape").status_code == 422  # adelanto de S/ 700 > S/ 500
    assert cobros == []

    con_adelanto, _ = _pago_en_camion(total=1000.0, adelanto=700.0, estado="adelanto_pagado")
    assert _procesar_con(con_adelanto, "yape").status_code == 200  # saldo de S/ 300
    assert cobros[0]["monto_soles"] == 300.0
    assert _fila_pago(con_adelanto).metodo_saldo == "yape"


def test_adelanto_y_saldo_tambien_por_transferencia(transferencia, monkeypatch):
    pago, producto = _pago_en_camion()
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})

    enviada = _enviar_constancia(pago, numero="OP-ADELANTO")
    assert enviada.status_code == 200 and enviada.json()["parte"] == "adelanto"
    _sesion_con_rol("admin")
    try:
        [item] = [t for t in client.get("/pagos/admin/transferencias").json() if t["pedido_id"] == str(pago.pedido_id)]
        assert (item["parte"], item["monto"], item["monto_total"]) == ("adelanto", 700.0, 1000.0)
        assert client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/aprobar").status_code == 200
    finally:
        _sesion_con_rol("comprador")
    assert _estado_del_pago(pago) == "adelanto_pagado"
    assert transferencia["eventos"] == [{"evento": "pago_confirmado", "pedido_id": str(pago.pedido_id), "adelanto": True,
                                         "monto": "700.00", "saldo": "300.00"}]

    assert _enviar_constancia(pago, numero="OP-SALDO-1").json()["parte"] == "saldo"
    _sesion_con_rol("admin")
    try:
        rechazo = client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/rechazar", json={"motivo": "El monto no coincide"})
        assert rechazo.status_code == 200 and rechazo.json()["parte"] == "saldo"
    finally:
        _sesion_con_rol("comprador")
    assert _estado_del_pago(pago) == "adelanto_pagado"  # vuelve a esperar el saldo
    transferencia["compensar"].assert_not_called()
    assert all(e["evento"] != "pago_rechazado" for e in transferencia["eventos"])
    # el comprador sí recibe un aviso con el motivo (saldo_rechazado no cancela nada)
    assert transferencia["eventos"][-1] == {"evento": "saldo_rechazado", "pedido_id": str(pago.pedido_id), "motivo": "El monto no coincide"}

    assert _enviar_constancia(pago, numero="OP-SALDO-2").status_code == 200
    _sesion_con_rol("admin")
    try:
        assert client.post(f"/pagos/admin/transferencias/{pago.pedido_id}/aprobar").status_code == 200
    finally:
        _sesion_con_rol("comprador")
    fila = _fila_pago(pago)
    assert fila.estado == "aprobado" and fila.metodo_saldo == "transferencia"
    assert transferencia["eventos"][-1] == {"evento": "saldo_pagado", "pedido_id": str(pago.pedido_id), "monto": "300.00"}
    assert len(_liquidaciones_de(pago.pedido_id)) == 1


def test_un_pedido_en_camion_crea_su_pago_con_el_adelanto():
    def recibir(evento):
        canal = MagicMock()
        rabbitmq_consumer._procesar_mensaje(canal, MagicMock(), None, json.dumps(evento).encode())
        canal.basic_ack.assert_called_once()

    item = [{"producto_id": str(uuid.uuid4()), "cantidad": 2, "precio_unitario": 500.0}]
    camion, local = str(uuid.uuid4()), str(uuid.uuid4())
    recibir({"evento": "pedido_creado", "pedido_id": camion, "items": item, "costo_envio": 200.0,
             "modalidad_envio": "camion", "monto_adelanto": 840.0})
    recibir({"evento": "pedido_creado", "pedido_id": local, "items": item, "costo_envio": 5.0,
             "modalidad_envio": "local", "monto_adelanto": None})
    db = main.SessionLocal()
    try:
        pago_camion = db.query(main.models.Pago).filter(main.models.Pago.pedido_id == camion).one()
        pago_local = db.query(main.models.Pago).filter(main.models.Pago.pedido_id == local).one()
    finally:
        db.close()
    assert float(pago_camion.monto) == 1200.0 and float(pago_camion.monto_adelanto) == 840.0
    assert pago_local.monto_adelanto is None


# ============ Saga del pedido: pago idempotente y plazo para pagar ============

def test_el_mismo_pedido_creado_dos_veces_no_crea_dos_pagos_y_trae_su_plazo():
    from datetime import datetime, timedelta
    import rabbitmq_consumer

    pedido_id = str(uuid.uuid4())
    evento = {"evento": "pedido_creado", "pedido_id": pedido_id, "costo_envio": 5,
              "items": [{"producto_id": str(uuid.uuid4()), "cantidad": 2, "precio_unitario": 10.0, "cantidad_base": 2}]}
    for _ in range(2):  # RabbitMQ entrega "al menos una vez" y Pedidos reenvía su bandeja de salida
        canal = MagicMock()
        rabbitmq_consumer._procesar_mensaje(canal, MagicMock(), None, json.dumps(evento).encode())
        canal.basic_ack.assert_called_once()
    db = main.SessionLocal()
    try:
        pagos = db.query(main.models.Pago).filter(main.models.Pago.pedido_id == pedido_id).all()
    finally:
        db.close()
    assert len(pagos) == 1
    plazo = pagos[0].vence_en - datetime.utcnow()
    assert timedelta(minutes=rabbitmq_consumer.PLAZO_PARA_PAGAR_MINUTOS - 1) < plazo <= timedelta(minutes=rabbitmq_consumer.PLAZO_PARA_PAGAR_MINUTOS)


def _con_vencimiento(pago, vence_en, estado="pendiente"):
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).first()
        fila.vence_en, fila.estado = vence_en, estado
        db.commit()
    finally:
        db.close()


def test_un_pedido_sin_pagar_vence_devuelve_su_stock_y_avisa(monkeypatch):
    from datetime import datetime, timedelta

    items = [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0, "cantidad_base": 1}]
    vencido = _crear_pago_directo(items)
    al_dia = _crear_pago_directo(items)
    de_antes = _crear_pago_directo(items)  # sin plazo: los pagos de antes no vencen solos
    en_revision = _crear_pago_directo(items)
    ahora = datetime.utcnow()
    _con_vencimiento(vencido, ahora - timedelta(minutes=1))
    _con_vencimiento(al_dia, ahora + timedelta(minutes=30))
    _con_vencimiento(en_revision, ahora - timedelta(minutes=1), estado="en_revision")
    eventos, compensar = [], MagicMock()
    monkeypatch.setattr(main, "publicar_evento", eventos.append)
    monkeypatch.setattr(main, "compensar_stock", compensar)

    db = main.SessionLocal()
    try:
        main.vencer_pagos_sin_pagar(db, ahora=ahora)
    finally:
        db.close()

    fila = _fila_pago(vencido)
    assert fila.estado == "rechazado" and fila.vencido_en is not None and "volvieron al catálogo" in fila.motivo_rechazo
    compensar.assert_called_once_with(fila.items)
    assert [e for e in eventos if e["pedido_id"] == str(vencido.pedido_id)] == [
        {"evento": "pago_rechazado", "pedido_id": str(vencido.pedido_id), "motivo": main.MOTIVO_VENCIDO, "vencido": True}]
    assert _estado_del_pago(al_dia) == "pendiente"
    assert _estado_del_pago(de_antes) == "pendiente"
    assert _estado_del_pago(en_revision) == "en_revision"


# ============ Recojo en la chacra: lo cobra el repartidor y se descuenta de las ventas del productor ============

def test_el_recojo_se_liquida_al_repartidor_y_se_descuenta_de_las_ventas_del_productor(monkeypatch):
    monkeypatch.setattr(main, "TRANSPORTE_A_PAGOS_SECRETO", "secreto-recojo")
    recojo_id, repartidor_id, productor_id, producto_id = (str(uuid.uuid4()) for _ in range(4))
    cuerpo = {"recojo_id": recojo_id, "repartidor_id": repartidor_id, "productor_id": productor_id,
              "producto_id": producto_id, "costo": 30.0}
    assert client.post("/pagos/interno/liquidar-recojo", json=cuerpo).status_code == 403
    for _ in range(2):  # un reintento no cobra dos veces
        assert client.post("/pagos/interno/liquidar-recojo", json=cuerpo, headers={"X-Servicio-Secreto": "secreto-recojo"}).status_code == 200
    [del_repartidor] = _liquidaciones_de(recojo_id)
    assert (del_repartidor.concepto, float(del_repartidor.monto_bruto), float(del_repartidor.monto_neto)) == ("recojo", 30.0, 27.0)

    # primera venta: el productor gana S/ 18 (20 - 10%) y se le cobran los 18 de los 30 que debe
    _mockear_productos(monkeypatch, {producto_id: productor_id})
    db = main.SessionLocal()
    try:
        for precio in (20.0, 50.0):
            pago = _crear_pago_directo([{"producto_id": producto_id, "cantidad": 1, "precio_unitario": precio}])
            fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
            main.crear_liquidaciones(db, fila)
            db.commit()
            [venta] = _liquidaciones_de(pago.pedido_id)
            if precio == 20.0:
                assert (float(venta.monto_neto), float(venta.descuento_recojo)) == (0.0, 18.0)
            else:  # segunda venta: gana 45 y se cobra lo que faltaba (12)
                assert (float(venta.monto_neto), float(venta.descuento_recojo)) == (33.0, 12.0)
        deuda = db.query(main.models.DescuentoProductor).filter(main.models.DescuentoProductor.recojo_id == recojo_id).one()
        assert (float(deuda.monto), float(deuda.aplicado)) == (30.0, 30.0)
    finally:
        db.close()



# ============ Devolver lo pagado de un pedido que se cancela ============

def _devolver(pedido_id, motivo="El repartidor no pudo llevarlo"):
    _sesion_con_rol("admin")
    try:
        return client.post(f"/pagos/admin/{pedido_id}/devolver", json={"motivo": motivo})
    finally:
        _sesion_con_rol("comprador")


def _aprobado_con_tarjeta(pago, cargo="chr_test_1", metodo="tarjeta"):
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        fila.estado, fila.metodo, fila.culqi_charge_id = "aprobado", metodo, cargo
        db.add(main.models.Liquidacion(pedido_id=fila.pedido_id, beneficiario_tipo="productor", beneficiario_id=uuid.uuid4(),
                                       monto_bruto=40, comision_plataforma=4, monto_neto=36, estado="pendiente",
                                       fecha_limite=main.datetime.utcnow()))
        db.commit()
    finally:
        db.close()


def test_devolver_un_pago_con_tarjeta_anula_las_ganancias_y_devuelve_el_stock(monkeypatch):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    _aprobado_con_tarjeta(pago)
    devoluciones, eventos, compensar = [], [], MagicMock()
    monkeypatch.setattr(main, "crear_devolucion", lambda cargo, monto: devoluciones.append((cargo, monto)) or {"status_code": 201, "data": {"id": "ref_1"}})
    monkeypatch.setattr(main, "publicar_evento", eventos.append)
    monkeypatch.setattr(main, "compensar_stock", compensar)

    assert client.post(f"/pagos/admin/{pago.pedido_id}/devolver", json={"motivo": "x x x"}).status_code == 403  # solo el admin
    resp = _devolver(pago.pedido_id)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"estado": "reembolsado", "monto_reembolsado": 40.0, "reembolso_manual": None,
                           "reembolso_ids": [{"cargo": "chr_test_1", "devolucion": "ref_1", "monto": 40.0}]}
    assert devoluciones == [("chr_test_1", 40.0)]
    compensar.assert_called_once()
    assert [l.estado for l in _liquidaciones_de(pago.pedido_id)] == ["anulada"]
    assert eventos == [{"evento": "pago_reembolsado", "pedido_id": str(pago.pedido_id), "motivo": "El repartidor no pudo llevarlo",
                        "monto": "40.00", "manual": None}]
    # repetir no devuelve dos veces, y ya no se puede volver a pagar
    assert _devolver(pago.pedido_id).json()["estado"] == "reembolsado" and len(devoluciones) == 1
    assert _procesar(pago).status_code == 409


def test_lo_pagado_por_transferencia_queda_para_devolver_a_mano(monkeypatch):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 25.0}])
    _aprobado_con_tarjeta(pago, cargo=None, metodo="transferencia")
    monkeypatch.setattr(main, "crear_devolucion", MagicMock(side_effect=AssertionError("no hay cargo en Culqi")))
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    cuerpo = _devolver(pago.pedido_id).json()
    assert (cuerpo["monto_reembolsado"], cuerpo["reembolso_manual"]) == (0.0, 25.0)


def test_un_pedido_en_camion_devuelve_el_adelanto_y_el_saldo_por_separado(monkeypatch):
    pago, _ = _pago_en_camion(total=1000.0, adelanto=700.0, estado="aprobado")
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        fila.metodo, fila.culqi_charge_id, fila.metodo_saldo, fila.culqi_charge_id_saldo = "tarjeta", "chr_a", "yape", "chr_s"
        db.commit()
    finally:
        db.close()
    devoluciones = []
    monkeypatch.setattr(main, "crear_devolucion", lambda cargo, monto: devoluciones.append((cargo, monto)) or {"status_code": 201, "data": {"id": f"ref_{cargo}"}})
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    assert _devolver(pago.pedido_id).json()["monto_reembolsado"] == 1000.0
    assert devoluciones == [("chr_a", 700.0), ("chr_s", 300.0)]


def test_si_culqi_rechaza_la_devolucion_no_se_cancela_nada(monkeypatch):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    _aprobado_con_tarjeta(pago)
    compensar = MagicMock()
    monkeypatch.setattr(main, "crear_devolucion", lambda cargo, monto: {"status_code": 400, "data": {"user_message": "Cargo no encontrado"}})
    monkeypatch.setattr(main, "compensar_stock", compensar)
    resp = _devolver(pago.pedido_id)
    assert resp.status_code == 502 and "Cargo no encontrado" in resp.json()["detail"]
    assert _estado_del_pago(pago) == "aprobado" and [l.estado for l in _liquidaciones_de(pago.pedido_id)] == ["pendiente"]
    compensar.assert_not_called()
    assert _devolver(_crear_pago_directo([]).pedido_id).status_code == 409  # sin pago confirmado no hay qué devolver
