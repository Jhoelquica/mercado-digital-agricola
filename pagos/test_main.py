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

