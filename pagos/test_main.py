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
logica_liquidaciones.httpx.Client, no sobre main.httpx.Client. Los eventos no tocan RabbitMQ: quedan en
la bandeja de salida (comun/bandeja_salida.py), y las pruebas que los miran los anotan con anotar_eventos.

Requiere una base de datos Postgres real accesible según comun/database.py (tipos UUID de postgresql +
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
from comun.auth import verificar_token

from comun.conftest import anotar_eventos

client = TestClient(main.app)

main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}

# POST /pagos/procesar le pregunta a Pedidos si el pedido es de quien paga (y reenvía su token). En casi todos
# los tests esa pregunta se da por respondida que sí; los que prueban la propiedad restauran la función real.
ORIGINAL_EXIGIR_DUENO = main._exigir_dueno_para_pagar


@pytest.fixture(autouse=True)
def _el_pedido_es_de_quien_paga(monkeypatch):
    monkeypatch.setattr(main, "_exigir_dueno_para_pagar", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def _culqi_sin_devoluciones_previas(monkeypatch):
    """Antes de devolver, Pagos pregunta a Culqi cuánto llevaba devuelto el cargo: en las pruebas, nada (salvo que la
    prueba diga otra cosa)."""
    monkeypatch.setattr(main, "devuelto_del_cargo", lambda cargo_id: 0)


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
    monkeypatch.setattr(main, "crear_cargo", lambda **kwargs: {"status_code": 201, "data": {"id": _cargo_nuevo()}})


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

    resp = _procesar(pago)
    assert resp.status_code == 200, resp.text

    liquidaciones = _liquidaciones_de(pago.pedido_id)
    assert len(liquidaciones) == 1
    liq = liquidaciones[0]
    assert str(liq.beneficiario_id) == productor_id
    assert liq.beneficiario_tipo == "productor"
    assert liq.estado == "pendiente"
    assert float(liq.monto_bruto) == 50.00


from comun.conftest import bandeja  # noqa: E402,F401 - la fixture de los eventos que quedaron en la bandeja de salida


def _sin_rabbitmq(monkeypatch):
    """RabbitMQ caído: si algo de la petición intentara conectarse, la prueba lo vería."""
    monkeypatch.setattr("pika.BlockingConnection",
                        MagicMock(side_effect=AssertionError("la petición no debe conectarse a RabbitMQ")))


def test_con_rabbitmq_caido_el_pago_con_tarjeta_responde_bien_y_su_aviso_queda_en_la_bandeja(monkeypatch, bandeja):
    producto = str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 2, "precio_unitario": 20.0}])
    _mockear_cargo_aprobado(monkeypatch)
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})
    _sin_rabbitmq(monkeypatch)

    resp = _procesar(pago)

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobado"
    [aviso] = [m for m in bandeja("pago_confirmado") if m["pedido_id"] == str(pago.pedido_id)]
    assert aviso["monto"] == "40.00" and aviso["evento_id"]
    assert len(_liquidaciones_de(pago.pedido_id)) == 1


def test_con_rabbitmq_caido_una_tarjeta_rechazada_da_402_y_su_aviso_queda_en_la_bandeja(monkeypatch, bandeja):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}])
    monkeypatch.setattr(main, "crear_cargo", lambda **kwargs: {"status_code": 400, "data": {"user_message": "Tarjeta rechazada"}})
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    _sin_rabbitmq(monkeypatch)

    resp = _procesar(pago)

    assert resp.status_code == 402, resp.text
    [aviso] = [m for m in bandeja("pago_rechazado") if m["pedido_id"] == str(pago.pedido_id)]
    assert aviso["motivo"] == "Tarjeta rechazada"


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
            culqi_charge_id=_cargo_nuevo(),
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
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: (orden.append(("cobrar", None)) or {"status_code": 201, "data": {"id": _cargo_nuevo()}}))
    _mockear_productos(monkeypatch, {producto: productor})

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


def test_si_la_pasarela_no_responde_el_pago_queda_procesando_sin_tocar_el_stock(monkeypatch):
    # Puede que Culqi haya cobrado y se perdiera la respuesta: no vuelve a "pendiente" (otro cobro sería doble) ni se
    # devuelve el stock; la conciliación lo averigua (ver las pruebas de conciliar_intentos_en_curso).
    def pasarela_caida(**kwargs):
        raise main.httpx.RequestError("boom")

    monkeypatch.setattr(main, "crear_cargo", pasarela_caida)
    reservar, compensar = MagicMock(), MagicMock()
    monkeypatch.setattr(main, "reservar_stock", reservar)
    monkeypatch.setattr(main, "compensar_stock", compensar)

    primer_intento = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    resp = _procesar(primer_intento)
    assert resp.status_code == 502 and "no vuelvas a pagar" in resp.json()["detail"], resp.text
    assert _estado_del_pago(primer_intento) == "procesando"
    reservar.assert_not_called()
    compensar.assert_not_called()
    assert _procesar(primer_intento).status_code == 409  # mientras tanto, otro cobro no

    # en un reintento se reservó stock para este intento: sigue reservado hasta que la conciliación decida
    reintento = _crear_pago_con_estado("rechazado", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    resp = _procesar(reintento)
    assert resp.status_code == 502, resp.text
    assert _estado_del_pago(reintento) == "procesando"
    reservar.assert_called_once_with(reintento.items)
    compensar.assert_not_called()


def test_un_segundo_intento_sobre_un_pago_ya_aprobado_falla_409_sin_cobrar_otra_vez(monkeypatch):
    producto, productor = str(uuid.uuid4()), str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 1, "precio_unitario": 50.0}])
    cobros = []
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: (cobros.append(1) or {"status_code": 201, "data": {"id": _cargo_nuevo()}}))
    _mockear_productos(monkeypatch, {producto: productor})

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
    anotar_eventos(monkeypatch, main, eventos)
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
    # A Pedidos solo va que está en revisión (pasa a "en_revision"): todavía no se confirma nada.
    assert transferencia["eventos"] == [{"evento": "transferencia_enviada", "pedido_id": str(pago.pedido_id), "parte": "total"}]

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
    assert transferencia["eventos"][1:] == [{"evento": "pago_confirmado", "pedido_id": str(pago.pedido_id), "monto": "800.00"}]
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
    transferencia["compensar"].assert_called_once_with(fila.items, pedido_id=pago.pedido_id)
    assert transferencia["eventos"] == [{"evento": "transferencia_enviada", "pedido_id": str(pago.pedido_id), "parte": "total"},
                                        {"evento": "pago_rechazado", "pedido_id": str(pago.pedido_id),
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
    # Un cargo distinto en cada cobro (un cargo nunca se asigna a dos pagos): queda anotado en el cobro.
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: cobros.append({**kw, "cargo": _cargo_nuevo()}) or
                        {"status_code": 201, "data": {"id": cobros[-1]["cargo"]}})
    anotar_eventos(monkeypatch, main, eventos)
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
    assert fila.estado == "aprobado" and fila.culqi_charge_id == cobros[0]["cargo"] and fila.culqi_charge_id_saldo == cobros[1]["cargo"]
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
    assert transferencia["eventos"] == [{"evento": "transferencia_enviada", "pedido_id": str(pago.pedido_id), "parte": "adelanto"},
                                        {"evento": "pago_confirmado", "pedido_id": str(pago.pedido_id), "adelanto": True,
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
    anotar_eventos(monkeypatch, main, eventos)
    monkeypatch.setattr(main, "compensar_stock", compensar)

    db = main.SessionLocal()
    try:
        main.vencer_pagos_sin_pagar(db, ahora=ahora)
    finally:
        db.close()

    fila = _fila_pago(vencido)
    assert fila.estado == "rechazado" and fila.vencido_en is not None and "volvieron al catálogo" in fila.motivo_rechazo
    # La base de pruebas guarda los pagos de corridas anteriores (sus "al día" ya vencieron): solo se mira este.
    assert [c.args for c in compensar.call_args_list].count((fila.items,)) == 1
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


def test_un_recojo_con_transporte_contratado_solo_se_descuenta_al_productor(monkeypatch):
    """H8: el camión lo contrató el admin y se le paga por fuera: no hay liquidación de repartidor, pero el flete
    acordado (S/ 350) sí se le descuenta al productor. Idempotente igual que el de un repartidor."""
    monkeypatch.setattr(main, "TRANSPORTE_A_PAGOS_SECRETO", "secreto-recojo")
    recojo_id, productor_id, producto_id = (str(uuid.uuid4()) for _ in range(3))
    cuerpo = {"recojo_id": recojo_id, "repartidor_id": None, "productor_id": productor_id, "producto_id": producto_id,
              "costo": 350.0}
    for _ in range(2):
        assert client.post("/pagos/interno/liquidar-recojo", json=cuerpo, headers={"X-Servicio-Secreto": "secreto-recojo"}).status_code == 200
    assert _liquidaciones_de(recojo_id) == []
    db = main.SessionLocal()
    try:
        [deuda] = db.query(main.models.DescuentoProductor).filter(main.models.DescuentoProductor.recojo_id == recojo_id).all()
        assert (str(deuda.productor_id), float(deuda.monto)) == (productor_id, 350.0)
    finally:
        db.close()



# ============ Devolver lo pagado de un pedido que se cancela ============

def _devolver(pedido_id, motivo="El repartidor no pudo llevarlo"):
    _sesion_con_rol("admin")
    try:
        return client.post(f"/pagos/admin/{pedido_id}/devolver", json={"motivo": motivo})
    finally:
        _sesion_con_rol("comprador")


def _pago_recargado(pago):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
    finally:
        db.close()


def _aprobado_con_tarjeta(pago, cargo=None, metodo="tarjeta"):
    # Un cargo distinto en cada prueba: un cargo se devuelve una sola vez (el índice único de las devoluciones).
    cargo = cargo or f"chr_test_{uuid.uuid4().hex[:10]}"
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
    cargo = f"chr_test_{uuid.uuid4().hex[:10]}"
    _aprobado_con_tarjeta(pago, cargo=cargo)
    devoluciones, eventos, compensar = [], [], MagicMock()
    monkeypatch.setattr(main, "crear_devolucion", lambda cargo, monto, **kw: devoluciones.append((cargo, monto)) or {"status_code": 201, "data": {"id": "ref_1"}})
    anotar_eventos(monkeypatch, main, eventos)
    monkeypatch.setattr(main, "compensar_stock", compensar)

    assert client.post(f"/pagos/admin/{pago.pedido_id}/devolver", json={"motivo": "x x x"}).status_code == 403  # solo el admin
    resp = _devolver(pago.pedido_id)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"estado": "reembolsado", "monto_reembolsado": 40.0, "reembolso_manual": None,
                           "reembolso_ids": [{"cargo": cargo, "devolucion": "ref_1", "monto": 40.0}]}
    assert devoluciones == [(cargo, 40.0)]
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
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    cuerpo = _devolver(pago.pedido_id).json()
    assert (cuerpo["monto_reembolsado"], cuerpo["reembolso_manual"]) == (0.0, 25.0)


def test_un_pedido_en_camion_devuelve_el_adelanto_y_el_saldo_por_separado(monkeypatch):
    pago, _ = _pago_en_camion(total=1000.0, adelanto=700.0, estado="aprobado")
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        sufijo = uuid.uuid4().hex[:10]  # un cargo se devuelve una sola vez en toda la base: cada prueba, los suyos
        cargo_a, cargo_s = f"chr_a_{sufijo}", f"chr_s_{sufijo}"
        fila.metodo, fila.culqi_charge_id, fila.metodo_saldo, fila.culqi_charge_id_saldo = "tarjeta", cargo_a, "yape", cargo_s
        db.commit()
    finally:
        db.close()
    devoluciones = []
    monkeypatch.setattr(main, "crear_devolucion", lambda cargo, monto, **kw: devoluciones.append((cargo, monto)) or {"status_code": 201, "data": {"id": f"ref_{cargo}"}})
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    assert _devolver(pago.pedido_id).json()["monto_reembolsado"] == 1000.0
    assert devoluciones == [(cargo_a, 700.0), (cargo_s, 300.0)]


def test_si_culqi_rechaza_la_devolucion_queda_a_medias_y_se_reintenta_con_tope(monkeypatch):
    # El envío ya se canceló antes (paso 1 de la saga): la devolución no se deshace, queda a medias y se reintenta.
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    _aprobado_con_tarjeta(pago)
    compensar, llamadas = MagicMock(), []
    monkeypatch.setattr(main, "crear_devolucion", lambda cargo, monto, **kw: llamadas.append(cargo) or {"status_code": 400, "data": {"user_message": "Cargo no encontrado"}})
    monkeypatch.setattr(main, "compensar_stock", compensar)
    resp = _devolver(pago.pedido_id)
    cargo = _pago_recargado(pago).culqi_charge_id
    assert resp.status_code == 202 and resp.json()["pendientes"] == [{"cargo": cargo, "estado": "rechazado"}], resp.text
    assert _estado_del_pago(pago) == "reembolso_procesando" and [l.estado for l in _liquidaciones_de(pago.pedido_id)] == ["pendiente"]
    compensar.assert_not_called()
    for _ in range(main.MAX_INTENTOS_DEVOLUCION + 2):  # se reintenta, pero con tope
        _devolver(pago.pedido_id)
    assert len(llamadas) == main.MAX_INTENTOS_DEVOLUCION
    assert _devolver(pago.pedido_id).json()["pendientes"] == [{"cargo": cargo, "estado": "revision"}]
    assert _devolver(_crear_pago_directo([]).pedido_id).status_code == 409  # sin pago confirmado no hay qué devolver



# ============ Cobros semanales de agricultores y repartidores (logica_cobros.py) ============
from datetime import date as _fecha, datetime as _momento

import logica_cobros
import rabbitmq_consumer


def _liquidacion(tipo, beneficiario_id, neto, cuando, pedido_id=None):
    db = main.SessionLocal()
    try:
        fila = main.models.Liquidacion(pedido_id=pedido_id or uuid.uuid4(), beneficiario_tipo=tipo, beneficiario_id=beneficiario_id,
                                       monto_bruto=neto, comision_plataforma=0, monto_neto=neto, estado="pendiente",
                                       fecha_creacion=cuando, fecha_limite=cuando)
        db.add(fila)
        db.commit()
        db.refresh(fila)
        db.expunge(fila)
        return fila
    finally:
        db.close()


def _entrega(pedido_id, cuando):
    db = main.SessionLocal()
    try:
        db.add(main.models.EntregaPedido(pedido_id=pedido_id, estado="entregado", fecha=cuando))
        db.commit()
    finally:
        db.close()


def _como_beneficiario(monkeypatch, tipo):
    yo = {"tipo": tipo, "id": uuid.uuid4(), "nombre": f"QA {tipo}", "usuario_id": str(uuid.uuid4())}
    monkeypatch.setattr(main, "_beneficiario_autenticado", lambda usuario, token: yo)
    return yo


def test_el_corte_es_el_lunes_a_medianoche_en_peru_y_se_paga_el_lunes_siguiente():
    # miércoles 7 oct 2026, 10:00 en Perú (15:00 UTC): el corte fue el lunes 5 a las 00:00 de Perú (05:00 UTC)
    assert logica_cobros.corte_de_la_semana(_momento(2026, 10, 7, 15)) == _momento(2026, 10, 5, 5)
    # lunes 5 a las 03:00 UTC todavía es domingo 4 en Perú: sigue la semana anterior
    assert logica_cobros.corte_de_la_semana(_momento(2026, 10, 5, 3)) == _momento(2026, 9, 28, 5)
    assert logica_cobros.dia_de_pago(_momento(2026, 10, 7, 15)) == _fecha(2026, 10, 12)
    assert logica_cobros.dia_de_pago(_momento(2026, 10, 5, 3)) == _fecha(2026, 10, 5)


def test_lo_vendido_se_cobra_recien_cuando_el_pedido_se_entrega(monkeypatch):
    yo = _como_beneficiario(monkeypatch, "productor")
    hace_dos_semanas = main.datetime.utcnow() - timedelta(days=14)
    venta = _liquidacion("productor", yo["id"], 30, hace_dos_semanas)
    cuerpo = client.get("/ganancias/me", headers=CON_TOKEN).json()
    assert (cuerpo["resumen"]["esperando_entrega"], cuerpo["resumen"]["por_cobrar"]) == (30.0, 0.0)
    assert cuerpo["movimientos"][0]["estado"] == "esperando_entrega" and cuerpo["cuenta"] is None

    _entrega(venta.pedido_id, hace_dos_semanas + timedelta(days=1))
    cuerpo = client.get("/ganancias/me", headers=CON_TOKEN).json()
    movimiento = cuerpo["movimientos"][0]
    assert (movimiento["estado"], movimiento["concepto"]) == ("por_cobrar", "venta")
    assert cuerpo["proximos_pagos"] == [{"fecha": movimiento["se_paga_el"], "monto": 30.0}]


def test_la_cuenta_de_cobro_pide_un_medio_completo(monkeypatch):
    assert client.get("/ganancias/me", headers=CON_TOKEN).status_code == 403  # un comprador no tiene ganancias
    _como_beneficiario(monkeypatch, "repartidor")
    guardar = lambda datos: client.put("/ganancias/me/cuenta", json=datos, headers=CON_TOKEN)
    assert guardar({}).status_code == 422
    assert "9 dígitos" in guardar({"yape_celular": "12345", "yape_titular": "Ana"}).json()["detail"]
    assert "20 dígitos" in guardar({"banco": "BCP", "cci": "123", "cuenta_titular": "Ana Quispe"}).json()["detail"]
    assert "lista" in guardar({"banco": "Banco Inventado", "cci": "0" * 20, "cuenta_titular": "Ana Quispe"}).json()["detail"]
    cuenta = guardar({"yape_celular": "987 654 321", "yape_titular": "  Ana   Quispe ",
                      "banco": "BCP", "cci": "002-19100-1234567890-12", "cuenta_titular": "Ana Quispe"}).json()
    assert (cuenta["yape_celular"], cuenta["yape_titular"], cuenta["cci"]) == ("987654321", "Ana Quispe", "00219100123456789012")


def test_el_admin_paga_lo_cumplido_hasta_el_corte_y_se_le_avisa(monkeypatch):
    yo = _como_beneficiario(monkeypatch, "repartidor")
    client.put("/ganancias/me/cuenta", json={"yape_celular": "987654321", "yape_titular": "Rosa Huamán"}, headers=CON_TOKEN)
    ahora = main.datetime.utcnow()
    corte = logica_cobros.corte_de_la_semana(ahora)
    _liquidacion("repartidor", yo["id"], 12.5, corte - timedelta(days=2))
    _liquidacion("repartidor", yo["id"], 8, corte + (ahora - corte) / 2)  # de esta semana
    eventos = []
    anotar_eventos(monkeypatch, main, eventos)
    url = f"/ganancias/admin/repartidor/{yo['id']}/pagar"
    _sesion_con_rol("admin")
    try:
        fila = next(f for f in client.get("/ganancias/admin/por-pagar").json()["beneficiarios"] if f["beneficiario_id"] == str(yo["id"]))
        assert (fila["a_pagar"], fila["esta_semana"], fila["cantidad_a_pagar"]) == (12.5, 8.0, 1)
        assert fila["nombre"] == "QA repartidor" and fila["cuenta"]["yape_celular"] == "987654321"
        assert "cuenta bancaria" in client.post(url, json={"metodo": "transferencia", "numero_operacion": "OP12345", "monto": 12.5}).json()["detail"]
        assert "cambió" in client.post(url, json={"metodo": "yape", "numero_operacion": "OP12345", "monto": 20}).json()["detail"]
        pago = client.post(url, json={"metodo": "yape", "numero_operacion": "OP12345", "monto": 12.5})
        assert pago.status_code == 200 and pago.json()["cantidad"] == 1 and pago.json()["destino"] == "Yape 987654321 (Rosa Huamán)"
        # lo de esta semana va en el pago del lunes siguiente, salvo que el admin lo adelante
        assert client.post(url, json={"metodo": "yape", "numero_operacion": "OP99999", "monto": 8}).status_code == 409
        adelanto = client.post(url, json={"metodo": "yape", "numero_operacion": "OP99999", "monto": 8, "incluir_semana_actual": True})
        assert adelanto.status_code == 200
    finally:
        _sesion_con_rol("comprador")
    assert eventos[0] == {"evento": "pago_a_beneficiario", "usuario_id": yo["usuario_id"], "beneficiario_tipo": "repartidor",
                          "monto": "12.50", "metodo": "yape", "numero_operacion": "OP12345", "cantidad": 1}
    cuerpo = client.get("/ganancias/me", headers=CON_TOKEN).json()
    assert [m["estado"] for m in cuerpo["movimientos"]] == ["pagado", "pagado"] and cuerpo["resumen"]["cobrado"] == 20.5
    assert len(cuerpo["pagos"]) == 2


def test_por_yape_mas_de_500_pide_un_numero_por_operacion(monkeypatch):
    yo = _como_beneficiario(monkeypatch, "productor")
    client.put("/ganancias/me/cuenta", json={"yape_celular": "912345678", "yape_titular": "Juan Quispe"}, headers=CON_TOKEN)
    corte = logica_cobros.corte_de_la_semana()
    venta = _liquidacion("productor", yo["id"], 750, corte - timedelta(days=3))
    _entrega(venta.pedido_id, corte - timedelta(days=2))
    url = f"/ganancias/admin/productor/{yo['id']}/pagar"
    _sesion_con_rol("admin")
    try:
        una = client.post(url, json={"metodo": "yape", "numero_operacion": "OP11111", "monto": 750})
        assert una.status_code == 422 and "2 números de operación" in una.json()["detail"]
        assert client.post(url, json={"metodo": "yape", "numero_operacion": "OP11111, OP22222", "monto": 750}).status_code == 200
    finally:
        _sesion_con_rol("comprador")


def test_la_entrega_llega_por_el_evento_del_envio_y_vale_la_primera_vez():
    pedido = str(uuid.uuid4())
    canal = MagicMock()
    for estado in ("entregado", "entregado", "en_camino"):
        rabbitmq_consumer._procesar_mensaje(canal, MagicMock(delivery_tag=1), None,
                                            json.dumps({"evento": "envio_actualizado", "pedido_id": pedido, "estado": estado}))
    otro = str(uuid.uuid4())
    rabbitmq_consumer._procesar_mensaje(canal, MagicMock(delivery_tag=1), None,
                                        json.dumps({"evento": "envio_actualizado", "pedido_id": otro, "estado": "en_camino"}))
    db = main.SessionLocal()
    try:
        assert db.query(main.models.EntregaPedido).filter(main.models.EntregaPedido.pedido_id == pedido).count() == 1
        assert db.query(main.models.EntregaPedido).filter(main.models.EntregaPedido.pedido_id == otro).count() == 0
    finally:
        db.close()
    assert canal.basic_ack.call_count == 4


# ============ Devolución de stock que no entra a la primera (pedido de maíz del 2026-10-17) ============

class _ClienteReponer:
    """Productos para /stock/reponer: responde según `modo` ("caido": no conecta; "lento": se corta esperando la respuesta;
    un número: ese código HTTP) y guarda cada (url, cantidad)."""
    def __init__(self, llamadas, modo):
        self._llamadas, self._modo = llamadas, modo

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self._llamadas.append((url, kwargs["json"]["cantidad"]))
        if self._modo == "caido":
            raise main.httpx.ConnectError("[Errno 111] Connection refused")
        if self._modo == "lento":
            raise main.httpx.ReadTimeout("timed out")
        respuesta = MagicMock()
        respuesta.status_code = self._modo
        return respuesta


def _productos_reponer(monkeypatch, modo, llamadas):
    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _ClienteReponer(llamadas, modo))


def _devoluciones_de(pedido_id):
    db = main.SessionLocal()
    try:
        return db.query(main.models.DevolucionStock).filter(main.models.DevolucionStock.pedido_id == pedido_id).all()
    finally:
        db.close()


def test_si_productos_no_responde_al_vencer_la_devolucion_queda_pendiente_y_se_reintenta(monkeypatch):
    from datetime import datetime, timedelta

    maiz = str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": maiz, "cantidad": 5, "precio_unitario": 6.0, "cantidad_base": 5}])
    ahora = datetime.utcnow()
    _con_vencimiento(pago, ahora - timedelta(minutes=1))
    llamadas = []
    _productos_reponer(monkeypatch, "caido", llamadas)  # Docker recién vuelve: Productos todavía no acepta conexiones

    db = main.SessionLocal()
    try:
        main.vencer_pagos_sin_pagar(db, ahora=ahora)
    finally:
        db.close()

    assert _estado_del_pago(pago) == "rechazado"
    [devolucion] = _devoluciones_de(pago.pedido_id)
    assert (devolucion.producto_id, devolucion.cantidad, devolucion.estado, devolucion.intentos) == (maiz, 5, "pendiente", 1)
    assert "ConnectError" in devolucion.ultimo_error

    # Productos sigue caído: sigue pendiente, con un intento más
    db = main.SessionLocal()
    try:
        main.reintentar_devoluciones_pendientes(db)
    finally:
        db.close()
    [devolucion] = _devoluciones_de(pago.pedido_id)
    assert (devolucion.estado, devolucion.intentos) == ("pendiente", 2)

    # Productos vuelve: la devolución entra una sola vez
    llamadas.clear()
    _productos_reponer(monkeypatch, 200, llamadas)
    for _ in range(2):
        db = main.SessionLocal()
        try:
            main.reintentar_devoluciones_pendientes(db)
        finally:
            db.close()
    [devolucion] = _devoluciones_de(pago.pedido_id)
    assert devolucion.estado == "devuelta" and devolucion.fecha_devuelta is not None
    assert [(url.split("/")[-3], cantidad) for url, cantidad in llamadas] == [(maiz, 5)]


def test_un_503_se_reintenta_y_una_respuesta_cortada_queda_dudosa_sin_reintentar(monkeypatch):
    pedido_503, pedido_cortado = uuid.uuid4(), uuid.uuid4()
    llamadas = []
    _productos_reponer(monkeypatch, 503, llamadas)
    assert main.compensar_stock(json.dumps([{"producto_id": "p-503", "cantidad": 2}]), pedido_id=pedido_503) == {"p-503": "pendiente"}
    _productos_reponer(monkeypatch, "lento", llamadas)
    assert main.compensar_stock(json.dumps([{"producto_id": "p-lento", "cantidad": 3}]), pedido_id=pedido_cortado) == {"p-lento": "dudosa"}
    [cortada] = _devoluciones_de(pedido_cortado)
    assert cortada.estado == "dudosa" and "ReadTimeout" in cortada.ultimo_error

    # al reintentar: la del 503 sí se vuelve a pedir; la dudosa no (pudo haber entrado y se sumaría dos veces)
    llamadas.clear()
    _productos_reponer(monkeypatch, 200, llamadas)
    db = main.SessionLocal()
    try:
        main.reintentar_devoluciones_pendientes(db)
    finally:
        db.close()
    assert ("p-503", 2) in [(url.split("/")[-3], c) for url, c in llamadas]
    assert all(url.split("/")[-3] != "p-lento" for url, _ in llamadas)
    assert _devoluciones_de(pedido_503)[0].estado == "devuelta"
    assert _devoluciones_de(pedido_cortado)[0].estado == "dudosa"


def test_si_productos_responde_no_se_anota_ninguna_devolucion(monkeypatch):
    pedido = uuid.uuid4()
    _productos_reponer(monkeypatch, 200, [])
    assert main.compensar_stock(json.dumps([{"producto_id": "p-ok", "cantidad": 1}]), pedido_id=pedido) == {"p-ok": "devuelta"}
    assert _devoluciones_de(pedido) == []



def test_un_token_con_un_rol_retirado_no_vale_aunque_no_haya_vencido():
    """El rol verificador se retiró del todo y sus cuentas están desactivadas: un token que todavía lo lleve se rechaza
    en este servicio igual que uno vencido, antes de mirar qué pide."""
    from comun import auth
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from comun.conftest import token_de_prueba

    def credenciales(rol):
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_de_prueba(rol=rol))

    assert auth.verificar_token(credenciales("comprador"))["rol"] == "comprador"
    try:
        auth.verificar_token(credenciales("verificador"))
    except HTTPException as error:
        assert error.status_code == 401 and "desactivada" in error.detail
    else:
        raise AssertionError("un token con rol verificador pasó la validación")


def test_dos_entregas_a_la_vez_del_mismo_pedido_creado_crean_un_solo_pago(monkeypatch):
    import threading
    pedido_id = str(uuid.uuid4())
    evento = {"evento": "pedido_creado", "pedido_id": pedido_id, "costo_envio": 5.0,
              "items": [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 30.0}]}
    # Las dos ya miraron que no hay pago cuando la primera lo inserta: solo el índice único puede frenar a la segunda.
    las_dos_miraron = threading.Barrier(2, timeout=10)
    calcular = rabbitmq_consumer._calcular_monto

    def calcular_cuando_las_dos_miraron(items, costo_envio=0):
        las_dos_miraron.wait()
        return calcular(items, costo_envio)

    monkeypatch.setattr(rabbitmq_consumer, "_calcular_monto", calcular_cuando_las_dos_miraron)
    canales = [MagicMock(), MagicMock()]
    hilos = [threading.Thread(target=rabbitmq_consumer._procesar_mensaje,
                              args=(canal, MagicMock(delivery_tag=1), None, json.dumps(evento).encode())) for canal in canales]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=20)

    db = main.SessionLocal()
    try:
        assert db.query(main.models.Pago).filter(main.models.Pago.pedido_id == pedido_id).count() == 1
    finally:
        db.close()
    for canal in canales:  # las dos confirman su mensaje: la segunda no falla, cede
        canal.basic_ack.assert_called_once_with(delivery_tag=1)


def test_una_pasada_de_vencimientos_sigue_aunque_la_primera_parte_y_su_rollback_fallen(monkeypatch):
    from comun import database

    class SesionQueFalla:
        def rollback(self):
            raise RuntimeError("tampoco se pudo deshacer")

        def close(self):
            pass

    hechas = []

    def devolver_que_falla(_db):
        raise RuntimeError("Productos no respondió")

    monkeypatch.setattr(database, "SessionLocal", SesionQueFalla)
    monkeypatch.setattr(main, "reintentar_devoluciones_pendientes", devolver_que_falla)
    monkeypatch.setattr(main, "vencer_pagos_sin_pagar", lambda db: hechas.append("vencimientos"))
    main.una_pasada_de_vencimientos()  # no lanza
    assert hechas == ["vencimientos"]


# ============ Cobro y devolución en dos fases: nada abierto mientras se llama a Culqi, y la conciliación ============
import threading as _hilos  # noqa: E402
import time as _tiempo  # noqa: E402

from sqlalchemy import text as _sql  # noqa: E402
from sqlalchemy.exc import OperationalError as _ErrorDeLaBase  # noqa: E402

from comun.database import engine as _engine  # noqa: E402


def _cargo_nuevo():
    return f"chr_{uuid.uuid4().hex[:12]}"


def _intentos_de(pago, tipo="cobro"):
    db = main.SessionLocal()
    try:
        return (db.query(main.models.IntentoPago)
                .filter(main.models.IntentoPago.pago_id == pago.id, main.models.IntentoPago.tipo == tipo)
                .order_by(main.models.IntentoPago.fecha_inicio).all())
    finally:
        db.close()


def _transacciones_abiertas():
    """Cuántas conexiones a esta base tienen una transacción abierta, sin contar la que pregunta."""
    with _engine.connect() as conexion:
        return conexion.execute(_sql("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                     "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()")).scalar()


def _conciliar(buscar, pasadas=1):
    db = main.SessionLocal()
    try:
        ahora = main.datetime.utcnow() + main.timedelta(minutes=3)
        return [main.conciliar_intentos_en_curso(db, ahora=ahora, buscar=buscar) for _ in range(pasadas)]
    finally:
        db.close()


def _solo(intento, encontrado=None, completo=True):
    """Un buscador falso de Culqi que solo sabe de este intento (de los de otras pruebas no se sabe nada)."""
    def buscar(caso):
        if caso["referencia"] != str(intento.id):
            return {"encontrado": None, "completo": False}
        assert (caso["tipo"], caso["cargo_id"], float(caso["monto"])) == (intento.tipo, intento.cargo_id, float(intento.monto))
        return {"encontrado": encontrado, "completo": completo}
    return buscar


def _base_caida(*_args, **_kwargs):
    raise _ErrorDeLaBase("COMMIT", {}, Exception("la base se cayó a mitad"))


def test_ninguna_llamada_a_culqi_ocurre_con_una_transaccion_abierta(monkeypatch):
    producto, cargo = str(uuid.uuid4()), _cargo_nuevo()
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 1, "precio_unitario": 40.0}])
    vistas = []

    def crear_cargo(**kwargs):
        vistas.append({"abiertas": _transacciones_abiertas(), **kwargs})
        return {"status_code": 201, "data": {"id": cargo}}

    def crear_devolucion(cargo_a_devolver, monto, **kwargs):
        vistas.append({"abiertas": _transacciones_abiertas(), **kwargs})
        return {"status_code": 201, "data": {"id": "ref_mirada"}}

    monkeypatch.setattr(main, "crear_cargo", crear_cargo)
    monkeypatch.setattr(main, "crear_devolucion", crear_devolucion)
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})

    assert _procesar(pago).status_code == 200
    assert _devolver(pago.pedido_id).status_code == 200
    cobro, devolucion = vistas
    assert cobro["abiertas"] == 0 and devolucion["abiertas"] == 0
    [intento] = _intentos_de(pago)
    assert cobro["metadata"]["referencia"] == str(intento.id) and str(intento.id)[:8] in cobro["descripcion"]
    assert (intento.estado, intento.cargo_id) == ("aprobado", cargo)
    [intento_devolucion] = _intentos_de(pago, "devolucion")
    assert devolucion["metadata"]["referencia"] == str(intento_devolucion.id)


def test_ninguna_consulta_a_culqi_ocurre_con_una_transaccion_abierta(monkeypatch):
    """Las consultas también: la fase 0 de una devolución, la búsqueda de la conciliación y la validación del admin."""
    vistas = []

    def mirar(nombre, respuesta):
        def llamada(*args, **kwargs):
            vistas.append((nombre, _transacciones_abiertas()))
            return respuesta(*args, **kwargs) if callable(respuesta) else respuesta
        return llamada

    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    pago, cargo = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]), _cargo_nuevo()
    _aprobado_con_tarjeta(pago, cargo=cargo)
    monkeypatch.setattr(main, "devuelto_del_cargo", mirar("cuánto llevaba devuelto", 0))
    monkeypatch.setattr(main, "crear_devolucion", lambda c, m, **kw: {"status_code": 201, "data": {"id": "ref_ok"}})
    assert _devolver(pago.pedido_id).status_code == 200

    def culqi_caido(**kwargs):
        raise main.httpx.ConnectError("Culqi no contesta")

    atascado = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    monkeypatch.setattr(main, "crear_cargo", culqi_caido)
    assert _procesar(atascado).status_code == 502
    _conciliar(mirar("búsqueda de la conciliación", {"encontrado": None, "completo": False}))

    _, referencia, _ = _cobro_atascado()
    monkeypatch.setattr(main, "obtener_cargo", mirar("validar el cargo", {"status_code": 404, "data": {}}))
    assert _resolver(referencia, resultado="cobrado", cargo_id="chr_noexiste01", motivo="Lo vi en el panel de Culqi").status_code == 422
    monkeypatch.setattr(main.culqi_client, "buscar_cargo", mirar("buscar antes de no cobrado", {"encontrado": None, "completo": True}))
    assert _resolver(referencia, resultado="no_cobrado", motivo="No aparece en el panel de Culqi").status_code == 200
    assert {nombre for nombre, _ in vistas} == {"cuánto llevaba devuelto", "búsqueda de la conciliación", "validar el cargo",
                                                "buscar antes de no cobrado"}
    assert all(abiertas == 0 for _, abiertas in vistas), vistas


def test_dos_cobros_a_la_vez_del_mismo_pedido_hacen_un_solo_cobro_en_culqi(monkeypatch):
    producto = str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 1, "precio_unitario": 40.0}])
    cobros = []

    def crear_cargo(**kwargs):
        cobros.append(kwargs["metadata"]["referencia"])
        _tiempo.sleep(0.5)  # el segundo llega mientras el primero espera a Culqi
        return {"status_code": 201, "data": {"id": _cargo_nuevo()}}

    monkeypatch.setattr(main, "crear_cargo", crear_cargo)
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})
    respuestas = []
    hilos = [_hilos.Thread(target=lambda: respuestas.append(_procesar(pago).status_code)) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    assert len(cobros) == 1 and sorted(respuestas) == [200, 409]
    assert _estado_del_pago(pago) == "aprobado"


def test_si_culqi_cobra_y_no_se_puede_guardar_queda_procesando_y_la_conciliacion_lo_aprueba_una_vez(monkeypatch):
    producto, cargo = str(uuid.uuid4()), _cargo_nuevo()
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 1, "precio_unitario": 40.0}])
    cobros, eventos = [], []
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: cobros.append(kw) or {"status_code": 201, "data": {"id": cargo}})
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})
    anotar_eventos(monkeypatch, main, eventos)
    cerrar_de_verdad = main._cerrar_cobro
    monkeypatch.setattr(main, "_cerrar_cobro", _base_caida)

    resp = _procesar(pago)
    assert resp.status_code == 502 and "no vuelvas a pagar" in resp.json()["detail"], resp.text
    assert _estado_del_pago(pago) == "procesando"
    assert _procesar(pago).status_code == 409 and len(cobros) == 1  # mientras tanto, un segundo intento: sin cobro

    monkeypatch.setattr(main, "_cerrar_cobro", cerrar_de_verdad)
    [intento] = _intentos_de(pago)
    hallado = {"id": cargo, "outcome": {"type": "venta_exitosa"}, "metadata": {"referencia": str(intento.id)}}
    _conciliar(_solo(intento, hallado), pasadas=2)
    fila = _pago_recargado(pago)
    assert (fila.estado, fila.culqi_charge_id) == ("aprobado", cargo)
    assert [e["evento"] for e in eventos if e.get("pedido_id") == str(pago.pedido_id)] == ["pago_confirmado"]
    assert len(_liquidaciones_de(pago.pedido_id)) == 1


def test_un_cobro_que_culqi_no_tiene_va_a_revision_manual_y_nunca_se_rechaza_solo(monkeypatch):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    compensar, eventos = MagicMock(), []

    def culqi_caido(**kwargs):
        raise main.httpx.ConnectError("Culqi no contesta")

    monkeypatch.setattr(main, "crear_cargo", culqi_caido)
    monkeypatch.setattr(main, "compensar_stock", compensar)
    anotar_eventos(monkeypatch, main, eventos)
    assert _procesar(pago).status_code == 502
    [intento] = _intentos_de(pago)
    _conciliar(_solo(intento), pasadas=main.PASADAS_SIN_RASTRO - 1)
    [intento] = _intentos_de(pago)
    assert _estado_del_pago(pago) == "procesando" and not intento.revision_manual  # todavía se busca
    _conciliar(_solo(intento), pasadas=3)
    [intento] = _intentos_de(pago)
    # Sin el cargo no se concluye "no hubo cobro": queda para el admin, con el stock reservado y sin avisos.
    assert (intento.estado, intento.revision_manual) == ("en_curso", True) and "admin" in intento.mensaje
    assert intento.conciliaciones == main.PASADAS_SIN_RASTRO  # en revisión, la conciliación ya no lo busca
    assert _estado_del_pago(pago) == "procesando"
    compensar.assert_not_called()
    assert [e for e in eventos if e.get("pedido_id") == str(pago.pedido_id)] == []


def test_si_no_se_puede_averiguar_en_culqi_queda_para_revision_manual(monkeypatch):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])

    def culqi_caido(*args, **kwargs):
        raise main.httpx.ConnectError("Culqi no contesta")

    monkeypatch.setattr(main, "crear_cargo", culqi_caido)
    assert _procesar(pago).status_code == 502
    [intento] = _intentos_de(pago)

    def buscar(caso):
        if caso["referencia"] == str(intento.id):
            raise main.httpx.ConnectError("Culqi no contesta")
        return {"encontrado": None, "completo": False}

    _conciliar(buscar, pasadas=main.PASADAS_MAXIMAS)
    [intento] = _intentos_de(pago)
    assert intento.revision_manual and intento.estado == "en_curso" and _estado_del_pago(pago) == "procesando"


def test_el_vencimiento_y_la_reposicion_de_stock_no_tocan_un_pago_en_proceso(monkeypatch):
    producto = str(uuid.uuid4())
    pago = _crear_pago_con_estado("procesando", [{"producto_id": producto, "cantidad": 1, "precio_unitario": 40.0}])
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        fila.vence_en = main.datetime.utcnow() - main.timedelta(hours=2)
        db.add(main.models.DevolucionStock(pedido_id=pago.pedido_id, producto_id=producto, cantidad=1, estado="pendiente",
                                           intentos=1))
        db.commit()
        repuestos = []
        monkeypatch.setattr(main, "_reponer_en_productos",
                            lambda cliente, pid, cantidad: repuestos.append(str(pid)) or ("devuelta", None))
        monkeypatch.setattr(main, "compensar_stock", MagicMock())
        main.vencer_pagos_sin_pagar(db)
        main.reintentar_devoluciones_pendientes(db)
    finally:
        db.close()
    assert _estado_del_pago(pago) == "procesando"
    assert producto not in repuestos


def test_si_el_pago_no_existe_se_crea_con_lo_que_dice_pedidos_y_el_monto_no_lo_pone_el_cliente(monkeypatch):
    pedido_id, producto = str(uuid.uuid4()), str(uuid.uuid4())
    del_pedido = {"evento": "pedido_creado", "pedido_id": pedido_id, "usuario_id": str(uuid.uuid4()), "comprador_nombre": "QA",
                  "costo_envio": 5.0, "modalidad_envio": "local", "monto_adelanto": None,
                  "items": [{"producto_id": producto, "cantidad": 2, "unidad": "kg", "cantidad_base": 2, "precio_unitario": 10.0}]}
    pedidas, cobros = [], []

    def get(url, headers=None, timeout=None):
        pedidas.append((url, headers))
        return MagicMock(status_code=200, json=lambda: del_pedido)

    monkeypatch.setattr(main.httpx, "get", get)
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: cobros.append(kw) or {"status_code": 201, "data": {"id": _cargo_nuevo()}})
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})
    resp = client.post("/pagos/procesar", json={"pedido_id": pedido_id, "token_culqi": "tkn_test", "email": "cliente@test.com",
                                                "monto": 1}, headers=CON_TOKEN)
    assert resp.status_code == 200, resp.text
    url, cabeceras = pedidas[0]
    assert url.endswith(f"/pedidos/interno/{pedido_id}/para-cobrar")
    assert cabeceras["X-Servicio-Secreto"] == main.PAGOS_A_PEDIDOS_SECRETO
    assert cobros[0]["monto_soles"] == 25.0  # 2 × S/ 10 + S/ 5 de envío, lo que dijo Pedidos; el "monto" del cuerpo no cuenta


def test_si_culqi_devuelve_y_no_se_puede_guardar_no_se_devuelve_dos_veces(monkeypatch):
    pago, cargo = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]), _cargo_nuevo()
    _aprobado_con_tarjeta(pago, cargo=cargo)
    llamadas, eventos = [], []
    monkeypatch.setattr(main, "crear_devolucion",
                        lambda c, m, **kw: llamadas.append(c) or {"status_code": 201, "data": {"id": "ref_hecha"}})
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    anotar_eventos(monkeypatch, main, eventos)
    cerrar_de_verdad = main._cerrar_devolucion
    monkeypatch.setattr(main, "_cerrar_devolucion", _base_caida)

    resp = _devolver(pago.pedido_id)
    assert resp.status_code == 202 and resp.json()["pendientes"] == [{"cargo": cargo, "estado": "en_curso"}], resp.text
    assert _devolver(pago.pedido_id).status_code == 202 and llamadas == [cargo]  # repetir no vuelve a devolver

    monkeypatch.setattr(main, "_cerrar_devolucion", cerrar_de_verdad)
    [intento] = _intentos_de(pago, "devolucion")
    _conciliar(_solo(intento, {"id": "ref_hecha", "metadata": {"referencia": str(intento.id)}}), pasadas=2)
    assert llamadas == [cargo] and _estado_del_pago(pago) == "reembolsado"
    assert [e["evento"] for e in eventos if e.get("pedido_id") == str(pago.pedido_id)] == ["pago_reembolsado"]


def test_el_admin_ve_los_cobros_atascados_con_la_referencia_para_buscarlos_en_culqi():
    pago = _crear_pago_con_estado("procesando", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    referencia = uuid.uuid4()
    db = main.SessionLocal()
    try:
        hace_rato = main.datetime.utcnow() - main.timedelta(minutes=main.MINUTOS_PARA_AVISAR + 5)
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        fila.procesando_desde, fila.parte_en_proceso = hace_rato, "total"
        db.add(main.models.IntentoPago(id=referencia, pago_id=pago.id, pedido_id=pago.pedido_id, tipo="cobro", parte="total",
                                       monto=40, metodo="tarjeta", estado="en_curso", estado_anterior="pendiente",
                                       fecha_inicio=hace_rato, conciliaciones=10, revision_manual=True,
                                       mensaje="Culqi no respondió a la búsqueda"))
        db.commit()
    finally:
        db.close()
    _sesion_con_rol("comprador")
    assert client.get("/pagos/admin/por-revisar").status_code == 403
    _sesion_con_rol("admin")
    filas = [f for f in client.get("/pagos/admin/por-revisar").json() if f["pedido_id"] == str(pago.pedido_id)]
    assert len(filas) == 1
    fila = filas[0]
    assert (fila["tipo"], fila["referencia"], fila["revision_manual"], fila["pasadas"]) == ("cobro", str(referencia), True, 10)
    assert fila["monto"] == 40.0 and fila["mensaje"] == "Culqi no respondió a la búsqueda"
    assert fila["puede_resolver"] is True
    _sesion_con_rol("comprador")


# ============ Confirmaciones de la conciliación (2026-11-19) ============
# 1) el cargo se identifica por la referencia de la metadata y nunca se asigna a dos pagos; 2) sin el cargo, revisión
# manual (nunca "no hubo cobro" solo); 3) las devoluciones comparan lo acumulado; 4) el admin resuelve cada caso.

class _ClienteCulqiYProductos:
    """httpx.Client falso para la conciliación de punta a punta: lo de Culqi (la lista de cargos, filtrada por la ventana
    como la real) y lo de Productos (el productor de cada producto, para las liquidaciones)."""

    def __init__(self, cargos, productores):
        self.cargos, self.productores = cargos, productores

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        if url.startswith(main.culqi_client.CULQI_API_BASE):
            if url.rstrip("/").endswith("/charges") and params:
                desde, hasta = params["creation_date_from"], params["creation_date_to"]
                datos = [c for c in self.cargos if desde <= c["creation_date"] <= hasta]
                return main.httpx.Response(200, json={"data": datos}, request=main.httpx.Request("GET", url))
            raise main.httpx.ConnectError("solo la lista de cargos")
        return _RespuestaProductoFalsa(self.productores[url.rstrip("/").split("/")[-1]])


def test_dos_pedidos_del_mismo_cliente_por_el_mismo_monto_cada_pago_queda_con_su_propio_cargo(monkeypatch):
    producto = str(uuid.uuid4())
    items = [{"producto_id": producto, "cantidad": 1, "precio_unitario": 40.0}]
    pagos_del_cliente = [_crear_pago_directo(items), _crear_pago_directo(items)]
    cargos, eventos = [], []

    def crear_cargo(**kw):  # Culqi cobra los dos: mismo correo, mismo monto, el mismo minuto
        cargo = {"id": _cargo_nuevo(), "amount": main.culqi_client.centimos(kw["monto_soles"]), "email": kw["email"],
                 "currency_code": "PEN", "metadata": {k: str(v) for k, v in kw["metadata"].items()},  # Culqi guarda texto
                 "outcome": {"type": "venta_exitosa"},
                 "creation_date": main.culqi_client._milisegundos(main.datetime.utcnow())}
        cargos.append(cargo)
        return {"status_code": 201, "data": cargo}

    monkeypatch.setattr(main, "crear_cargo", crear_cargo)
    anotar_eventos(monkeypatch, main, eventos)
    cerrar_de_verdad = main._cerrar_cobro
    monkeypatch.setattr(main, "_cerrar_cobro", _base_caida)  # ninguno de los dos se pudo guardar
    assert [_procesar(p).status_code for p in pagos_del_cliente] == [502, 502]
    monkeypatch.setattr(main, "_cerrar_cobro", cerrar_de_verdad)
    assert len({(c["email"], c["amount"]) for c in cargos}) == 1  # indistinguibles por correo y monto

    monkeypatch.setattr(main.httpx, "Client", _ClienteCulqiYProductos(cargos, {producto: str(uuid.uuid4())}))
    _conciliar(None, pasadas=2)  # la búsqueda real (averiguar_en_culqi) sobre la lista de Culqi
    asignados = []
    for pago in pagos_del_cliente:
        [intento] = _intentos_de(pago)
        suyo = next(c for c in cargos if c["metadata"]["referencia"] == str(intento.id))
        fila = _pago_recargado(pago)
        assert (fila.estado, fila.culqi_charge_id, intento.cargo_id) == ("aprobado", suyo["id"], suyo["id"]), intento.mensaje
        asignados.append(fila.culqi_charge_id)
    assert len(set(asignados)) == 2
    assert sorted(e["pedido_id"] for e in eventos if e["evento"] == "pago_confirmado") == sorted(
        str(p.pedido_id) for p in pagos_del_cliente)


def test_un_cargo_nunca_se_asigna_a_dos_pagos(monkeypatch):
    items = [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]
    cargo, primero, segundo = _cargo_nuevo(), _crear_pago_directo(items), _crear_pago_directo(items)
    db = main.SessionLocal()
    try:
        for columna in (main.models.Pago.culqi_charge_id, main.models.Pago.culqi_charge_id_saldo):
            repetido = _cargo_nuevo()
            db.query(main.models.Pago).filter(main.models.Pago.id == primero.id).update({columna: repetido})
            db.commit()
            with pytest.raises(main.IntegrityError):  # el mismo cargo en otro pago: ni en el total ni en el saldo
                db.query(main.models.Pago).filter(main.models.Pago.id == segundo.id).update({columna: repetido})
                db.commit()
            db.rollback()
        # Entre pagos y partes (el adelanto de uno no puede ser el saldo de otro): todo cargo asignado pasa por su intento
        # de cobro, y el índice de los intentos es uno solo para todos.
        for pago, parte in ((primero, "adelanto"), (segundo, "saldo")):
            db.add(main.models.IntentoPago(pago_id=pago.id, pedido_id=pago.pedido_id, tipo="cobro", parte=parte, monto=40,
                                           estado="aprobado", cargo_id=cargo))
            if parte == "saldo":
                with pytest.raises(main.IntegrityError):
                    db.commit()
                db.rollback()
            else:
                db.commit()
    finally:
        db.close()

    # Si la búsqueda devolviera para otro intento un cargo ya asignado, no se asigna: ese intento queda para el admin.
    tercero = _crear_pago_directo(items)

    def culqi_caido(**kwargs):
        raise main.httpx.ConnectError("Culqi no contesta")

    monkeypatch.setattr(main, "crear_cargo", culqi_caido)
    assert _procesar(tercero).status_code == 502
    [intento] = _intentos_de(tercero)
    _conciliar(_solo(intento, {"id": cargo, "outcome": {"type": "venta_exitosa"}, "metadata": {"referencia": str(intento.id)}}))
    [intento] = _intentos_de(tercero)
    assert (intento.estado, intento.revision_manual) == ("en_curso", True) and "otro pago" in intento.mensaje
    assert _estado_del_pago(tercero) == "procesando" and _pago_recargado(tercero).culqi_charge_id is None


def test_un_caso_que_falla_al_cerrarse_no_frena_a_los_demas(monkeypatch):
    items = [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]
    trabado, sano = _crear_pago_directo(items), _crear_pago_directo(items)

    def culqi_caido(**kwargs):
        raise main.httpx.ConnectError("Culqi no contesta")

    monkeypatch.setattr(main, "crear_cargo", culqi_caido)
    assert [_procesar(p).status_code for p in (trabado, sano)] == [502, 502]
    [intento_trabado], [intento_sano] = _intentos_de(trabado), _intentos_de(sano)
    cargos = {str(intento_trabado.id): _cargo_nuevo(), str(intento_sano.id): _cargo_nuevo()}

    def buscar(caso):
        if caso["referencia"] not in cargos:
            return {"encontrado": None, "completo": False}
        return {"encontrado": {"id": cargos[caso["referencia"]], "outcome": {"type": "venta_exitosa"}}, "completo": True}

    cerrar_de_verdad = main._cerrar_cobro

    def cerrar(db, referencia, *args, **kwargs):
        if str(referencia) == str(intento_trabado.id):
            raise _ErrorDeLaBase("UPDATE", {}, Exception("la base rechazó este cierre"))
        return cerrar_de_verdad(db, referencia, *args, **kwargs)

    monkeypatch.setattr(main, "_cerrar_cobro", cerrar)
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    _mockear_productos(monkeypatch, {items[0]["producto_id"]: str(uuid.uuid4())})
    _conciliar(buscar)
    assert _estado_del_pago(trabado) == "procesando" and _estado_del_pago(sano) == "aprobado"
    monkeypatch.setattr(main, "_cerrar_cobro", cerrar_de_verdad)
    _conciliar(buscar)  # en la pasada siguiente, el que falló se resuelve
    assert _estado_del_pago(trabado) == "aprobado"


def test_la_devolucion_anota_lo_que_el_cargo_ya_tenia_devuelto(monkeypatch):
    pago, cargo = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]), _cargo_nuevo()
    _aprobado_con_tarjeta(pago, cargo=cargo)
    monkeypatch.setattr(main, "devuelto_del_cargo", lambda c: 1500 if c == cargo else 0)
    monkeypatch.setattr(main, "crear_devolucion", lambda c, m, **kw: {"status_code": 201, "data": {"id": f"ref_{uuid.uuid4().hex[:8]}"}})
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    assert _devolver(pago.pedido_id).status_code == 200
    [intento] = _intentos_de(pago, "devolucion")
    assert intento.devuelto_antes == 1500


def test_si_no_se_sabe_cuanto_llevaba_devuelto_el_cargo_no_se_empieza_la_devolucion(monkeypatch):
    pago, cargo = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]), _cargo_nuevo()
    _aprobado_con_tarjeta(pago, cargo=cargo)

    def no_contesta(cargo_id):
        raise main.httpx.ConnectError("Culqi no contesta")

    llamadas = []
    monkeypatch.setattr(main, "devuelto_del_cargo", no_contesta)
    monkeypatch.setattr(main, "crear_devolucion", lambda c, m, **kw: llamadas.append(c) or {"status_code": 201, "data": {"id": "ref_x"}})
    resp = _devolver(pago.pedido_id)
    assert resp.status_code == 202 and resp.json()["pendientes"] == [{"cargo": cargo, "estado": "pendiente"}], resp.text
    assert llamadas == [] and _intentos_de(pago, "devolucion") == []


def _devolucion_sin_guardar(monkeypatch, devuelto_antes=0):
    """Un pago aprobado cuya devolución salió en Culqi (o no) pero no se pudo guardar: el intento queda en curso."""
    pago, cargo = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]), _cargo_nuevo()
    _aprobado_con_tarjeta(pago, cargo=cargo)
    monkeypatch.setattr(main, "devuelto_del_cargo", lambda c: devuelto_antes)
    monkeypatch.setattr(main, "crear_devolucion", lambda c, m, **kw: {"status_code": 201, "data": {"id": "ref_perdida"}})
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    cerrar_de_verdad = main._cerrar_devolucion
    monkeypatch.setattr(main, "_cerrar_devolucion", _base_caida)
    assert _devolver(pago.pedido_id).status_code == 202
    monkeypatch.setattr(main, "_cerrar_devolucion", cerrar_de_verdad)
    return pago, cargo


def _culqi_dice_que_el_cargo_lleva_devuelto(monkeypatch, cargo, devuelto):
    """GET /charges/{cargo}: solo sabe de ese cargo (los intentos de otras pruebas no reciben respuesta)."""
    def obtener(cargo_id):
        if cargo_id != cargo:
            raise main.httpx.ConnectError("de ese no sé")
        return {"status_code": 200, "data": {"id": cargo_id, "amount": 4000, "amount_refunded": devuelto}}
    monkeypatch.setattr(main.culqi_client, "obtener_cargo", obtener)
    monkeypatch.setattr(main.culqi_client, "_listar_en_ventana", lambda recurso, desde, hasta: [])


def test_la_conciliacion_de_una_devolucion_compara_lo_acumulado(monkeypatch):
    # El cargo ya tenía S/ 40 devueltos (fuera del sistema) cuando se intentó devolverlo y la respuesta se perdió. Que
    # amount_refunded diga 4000 no prueba que ESTA devolución salió: lo de antes + esta serían 8000.
    pago, cargo = _devolucion_sin_guardar(monkeypatch, devuelto_antes=4000)
    [intento] = _intentos_de(pago, "devolucion")
    assert intento.devuelto_antes == 4000
    _culqi_dice_que_el_cargo_lleva_devuelto(monkeypatch, cargo, 4000)
    _conciliar(None, pasadas=main.PASADAS_SIN_RASTRO)
    [intento] = _intentos_de(pago, "devolucion")
    assert intento.estado == "rechazado" and _estado_del_pago(pago) == "reembolso_procesando"

    # Con lo acumulado (antes + esta) sí está hecha.
    otro, otro_cargo = _devolucion_sin_guardar(monkeypatch, devuelto_antes=4000)
    _culqi_dice_que_el_cargo_lleva_devuelto(monkeypatch, otro_cargo, 8000)
    _conciliar(None)
    [intento] = _intentos_de(otro, "devolucion")
    assert intento.estado == "aprobado" and _estado_del_pago(otro) == "reembolsado"


# ---- 4) Resolver a mano desde el panel ----
ADMIN_QUE_RESUELVE = uuid.uuid4()


def _resolver(referencia, **cuerpo):
    _como(ADMIN_QUE_RESUELVE, "admin")
    try:
        return client.post(f"/pagos/admin/por-revisar/{referencia}/resolver", json=cuerpo)
    finally:
        _sesion_con_rol("comprador")


def _cobro_atascado(minutos=15):
    """Un cobro en revisión manual: el pago "procesando" y su intento en curso desde hace `minutos`."""
    producto = str(uuid.uuid4())
    pago = _crear_pago_con_estado("procesando", [{"producto_id": producto, "cantidad": 1, "precio_unitario": 40.0}])
    referencia = uuid.uuid4()
    db = main.SessionLocal()
    try:
        hace = main.datetime.utcnow() - main.timedelta(minutes=minutos)
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        fila.procesando_desde, fila.parte_en_proceso, fila.estado_antes_de_procesar = hace, "total", "pendiente"
        db.add(main.models.IntentoPago(id=referencia, pago_id=pago.id, pedido_id=pago.pedido_id, tipo="cobro", parte="total",
                                       monto=40, metodo="tarjeta", estado="en_curso", estado_anterior="pendiente",
                                       fecha_inicio=hace, conciliaciones=main.PASADAS_SIN_RASTRO, revision_manual=True))
        db.commit()
    finally:
        db.close()
    return pago, str(referencia), producto


def _cargo_de_culqi(cargo_id, referencia, pedido_id, **cambios):
    cargo = {"id": cargo_id, "amount": 4000, "currency_code": "PEN", "outcome": {"type": "venta_exitosa"},
             "metadata": {"referencia": referencia, "pedido_id": str(pedido_id), "parte": "total"}}
    cargo.update(cambios)
    return cargo


def test_el_admin_resuelve_un_cobro_como_cobrado_con_el_cargo_validado_en_culqi(monkeypatch):
    pago, referencia, producto = _cobro_atascado()
    cargo, eventos = _cargo_nuevo(), []
    anotar_eventos(monkeypatch, main, eventos)
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})
    monkeypatch.setattr(main, "obtener_cargo", lambda c: {"status_code": 200, "data": _cargo_de_culqi(c, referencia, pago.pedido_id)})

    _sesion_con_rol("comprador")
    assert client.post(f"/pagos/admin/por-revisar/{referencia}/resolver",
                       json={"resultado": "cobrado", "cargo_id": cargo, "motivo": "Lo encontré en el panel"}).status_code == 403
    assert _resolver(referencia, resultado="cobrado", cargo_id=cargo, motivo="corto").status_code == 422  # el motivo es obligatorio
    assert _resolver(referencia, resultado="devuelto", devolucion_id="ref_123456", motivo="No es una devolución").status_code == 422
    assert _resolver(referencia, resultado="cobrado", motivo="Sin el id del cargo no se puede").status_code == 422

    resp = _resolver(referencia, resultado="cobrado", cargo_id=cargo, motivo="Lo encontré en el panel de Culqi por la referencia")
    assert resp.status_code == 200 and resp.json()["estado_del_pago"] == "aprobado", resp.text
    fila = _pago_recargado(pago)
    assert (fila.estado, fila.culqi_charge_id, fila.procesando_desde) == ("aprobado", cargo, None)
    [intento] = _intentos_de(pago)
    assert (intento.estado, intento.cargo_id, intento.resuelto_por) == ("aprobado", cargo, ADMIN_QUE_RESUELVE)
    assert intento.motivo_resolucion == "Lo encontré en el panel de Culqi por la referencia" and intento.fecha_resolucion
    assert [e["evento"] for e in eventos if e.get("pedido_id") == str(pago.pedido_id)] == ["pago_confirmado"]
    assert len(_liquidaciones_de(pago.pedido_id)) == 1
    assert _resolver(referencia, resultado="cobrado", cargo_id=cargo, motivo="Otra vez, por si acaso").status_code == 409


def test_el_cargo_que_da_el_admin_se_valida_contra_culqi(monkeypatch):
    pago, referencia, _ = _cobro_atascado()
    usado, ajeno = _cargo_nuevo(), _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    _aprobado_con_tarjeta(ajeno, cargo=usado)
    respuestas = {
        "chr_noexiste01": (404, {}),
        "chr_nocobrado1": (200, _cargo_de_culqi("chr_nocobrado1", referencia, pago.pedido_id, outcome={"type": "venta_rechazada"})),
        "chr_otromonto1": (200, _cargo_de_culqi("chr_otromonto1", referencia, pago.pedido_id, amount=3999)),
        "chr_otroref001": (200, _cargo_de_culqi("chr_otroref001", str(uuid.uuid4()), pago.pedido_id)),
        "chr_otroped001": (200, _cargo_de_culqi("chr_otroped001", referencia, uuid.uuid4())),
        "chr_dolares001": (200, _cargo_de_culqi("chr_dolares001", referencia, pago.pedido_id, currency_code="USD")),
        # Así responde Culqi de verdad a un id que no existe (sandbox, 2026-11-19): 401 con «Ruta inválida», no 404.
        "chr_rutainval1": (401, {"object": "error", "type": "authentication_error",
                                 "merchant_message": "Ruta inválida, entérate de las rutas en https://apidocs.culqi.com."}),
        "chr_sinllave01": (401, {"object": "error", "type": "authentication_error", "merchant_message": "Llave no válida"}),
        usado: (200, _cargo_de_culqi(usado, referencia, pago.pedido_id, metadata={})),
    }
    monkeypatch.setattr(main, "obtener_cargo", lambda c: {"status_code": respuestas[c][0], "data": respuestas[c][1]})
    for cargo in list(respuestas)[:-2]:
        resp = _resolver(referencia, resultado="cobrado", cargo_id=cargo, motivo="Lo vi en el panel de Culqi")
        assert resp.status_code == 422, (cargo, resp.text)
    resp = _resolver(referencia, resultado="cobrado", cargo_id="chr_sinllave01", motivo="Lo vi en el panel de Culqi")
    assert resp.status_code == 503  # un 401 que no es «Ruta inválida» es nuestra llave: no se culpa al id
    resp = _resolver(referencia, resultado="cobrado", cargo_id=usado, motivo="Lo vi en el panel de Culqi")
    assert resp.status_code == 409 and "otro pago" in resp.json()["detail"]
    assert _estado_del_pago(pago) == "procesando"
    [intento] = _intentos_de(pago)
    assert intento.estado == "en_curso" and intento.resuelto_por is None


def test_el_admin_resuelve_un_cobro_como_no_cobrado_libera_el_stock_y_se_puede_volver_a_pagar(monkeypatch):
    pago, referencia, producto = _cobro_atascado()
    compensar, eventos = MagicMock(), []
    monkeypatch.setattr(main, "compensar_stock", compensar)
    anotar_eventos(monkeypatch, main, eventos)
    monkeypatch.setattr(main.culqi_client, "buscar_cargo", lambda ref, d, h: {"encontrado": {"id": "chr_hallado"}, "completo": True})
    resp = _resolver(referencia, resultado="no_cobrado", motivo="No aparece en el panel de Culqi")
    assert resp.status_code == 409 and "chr_hallado" in resp.json()["detail"]  # Culqi lo tiene: es "cobrado"

    monkeypatch.setattr(main.culqi_client, "buscar_cargo", lambda ref, d, h: {"encontrado": None, "completo": True})
    resp = _resolver(referencia, resultado="no_cobrado", motivo="No aparece en el panel de Culqi ni en el banco")
    assert resp.status_code == 200 and resp.json()["estado_del_pago"] == "rechazado", resp.text
    compensar.assert_called_once()
    assert [e["evento"] for e in eventos if e.get("pedido_id") == str(pago.pedido_id)] == ["pago_rechazado"]
    [intento] = _intentos_de(pago)
    assert (intento.estado, intento.resuelto_por) == ("rechazado", ADMIN_QUE_RESUELVE)

    # El comprador puede volver a pagar: el reintento vuelve a reservar el stock antes de cobrar.
    reservas = []
    monkeypatch.setattr(main, "reservar_stock", lambda items: reservas.append(items))
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: {"status_code": 201, "data": {"id": _cargo_nuevo()}})
    _mockear_productos(monkeypatch, {producto: str(uuid.uuid4())})
    assert _procesar(pago).status_code == 200 and _estado_del_pago(pago) == "aprobado" and len(reservas) == 1


def test_un_cobro_que_todavia_se_esta_procesando_no_se_resuelve_a_mano():
    pago, referencia, _ = _cobro_atascado(minutos=0)
    resp = _resolver(referencia, resultado="no_cobrado", motivo="Todavía no debería poder")
    assert resp.status_code == 409 and _estado_del_pago(pago) == "procesando"


def _devolucion_en_revision(monkeypatch):
    """Un pago reembolso_procesando cuya devolución quedó en curso hace rato (la respuesta de Culqi se perdió)."""
    pago, cargo = _devolucion_sin_guardar(monkeypatch)
    db = main.SessionLocal()
    try:
        hace = main.datetime.utcnow() - main.timedelta(minutes=15)
        intento = db.query(main.models.IntentoPago).filter(main.models.IntentoPago.pago_id == pago.id,
                                                           main.models.IntentoPago.tipo == "devolucion").one()
        intento.fecha_inicio, intento.revision_manual = hace, True
        db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).update({main.models.Pago.procesando_desde: hace})
        referencia = str(intento.id)
        db.commit()
    finally:
        db.close()
    return pago, cargo, referencia


def test_el_admin_resuelve_una_devolucion_como_devuelta_con_su_id_validado(monkeypatch):
    pago, cargo, referencia = _devolucion_en_revision(monkeypatch)
    eventos = []
    anotar_eventos(monkeypatch, main, eventos)
    devoluciones = {"ref_panel_ok": {"charge_id": cargo, "amount": 4000, "status": "completa"},
                    "ref_otrocargo": {"charge_id": _cargo_nuevo(), "amount": 4000, "status": "completa"},
                    "ref_otromonto": {"charge_id": cargo, "amount": 1000, "status": "completa"},
                    "ref_fallida01": {"charge_id": cargo, "amount": 4000, "status": "fallida"}}
    ruta_invalida = {"object": "error", "type": "authentication_error", "merchant_message": "Ruta inválida, entérate de las rutas"}
    monkeypatch.setattr(main, "obtener_devolucion", lambda d: {"status_code": 200, "data": {"id": d, **devoluciones[d]}}
                        if d in devoluciones else {"status_code": 401, "data": ruta_invalida})
    for malo in ("ref_noexiste1", "ref_otrocargo", "ref_otromonto", "ref_fallida01"):
        assert _resolver(referencia, resultado="devuelto", devolucion_id=malo, motivo="La vi en el panel").status_code == 422, malo
    assert _resolver(referencia, resultado="cobrado", cargo_id=cargo, motivo="No es un cobro").status_code == 422

    resp = _resolver(referencia, resultado="devuelto", devolucion_id="ref_panel_ok", motivo="La devolución está en el panel de Culqi")
    assert resp.status_code == 200 and resp.json()["estado_del_pago"] == "reembolsado", resp.text
    [intento] = _intentos_de(pago, "devolucion")
    assert (intento.estado, intento.devolucion_id, intento.resuelto_por) == ("aprobado", "ref_panel_ok", ADMIN_QUE_RESUELVE)
    assert [e["evento"] for e in eventos if e.get("pedido_id") == str(pago.pedido_id)] == ["pago_reembolsado"]


def test_una_devolucion_que_llego_al_tope_se_resuelve_a_mano_y_no_devuelta_vuelve_a_intentarse(monkeypatch):
    pago, cargo = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]), _cargo_nuevo()
    _aprobado_con_tarjeta(pago, cargo=cargo)
    llamadas = []
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    monkeypatch.setattr(main, "crear_devolucion", lambda c, m, **kw: llamadas.append(c) or
                        {"status_code": 400, "data": {"user_message": "No se pudo devolver"}})
    for _ in range(main.MAX_INTENTOS_DEVOLUCION + 1):
        assert _devolver(pago.pedido_id).status_code == 202
    assert len(llamadas) == main.MAX_INTENTOS_DEVOLUCION  # al tope, ya no llama a Culqi
    db = main.SessionLocal()
    try:
        main._marcar_devolucion_para_revision(db, pago.id, cargo)
        db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).update(
            {main.models.Pago.procesando_desde: main.datetime.utcnow() - main.timedelta(minutes=15)})
        db.commit()
    finally:
        db.close()
    _sesion_con_rol("admin")
    try:
        [fila] = [f for f in client.get("/pagos/admin/por-revisar").json() if f["pedido_id"] == str(pago.pedido_id)]
    finally:
        _sesion_con_rol("comprador")
    assert (fila["tipo"], fila["revision_manual"], fila["puede_resolver"]) == ("devolucion", True, True)

    # "No devuelta" pero Culqi dice que sí se devolvió: 409.
    monkeypatch.setattr(main, "devuelto_del_cargo", lambda c: 4000)
    assert _resolver(fila["referencia"], resultado="no_devuelto", motivo="No la veo en el panel de Culqi").status_code == 409
    # "No devuelta": la decisión queda registrada y el tope vuelve a contar; la devolución se reintenta y sale.
    monkeypatch.setattr(main, "devuelto_del_cargo", lambda c: 0)
    resp = _resolver(fila["referencia"], resultado="no_devuelto", motivo="No la veo en el panel de Culqi: que se reintente")
    assert resp.status_code == 200 and resp.json()["estado_del_pago"] == "reembolso_procesando", resp.text
    decidido = next(i for i in _intentos_de(pago, "devolucion") if str(i.id) == fila["referencia"])
    assert decidido.resuelto_por == ADMIN_QUE_RESUELVE and decidido.motivo_resolucion.startswith("No la veo")
    monkeypatch.setattr(main, "crear_devolucion", lambda c, m, **kw: llamadas.append(c) or {"status_code": 201, "data": {"id": "ref_al_fin"}})
    assert _devolver(pago.pedido_id).status_code == 200 and _estado_del_pago(pago) == "reembolsado"
    assert len(llamadas) == main.MAX_INTENTOS_DEVOLUCION + 1


def test_una_devolucion_al_tope_tambien_se_resuelve_como_devuelta(monkeypatch):
    pago, cargo = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]), _cargo_nuevo()
    _aprobado_con_tarjeta(pago, cargo=cargo)
    monkeypatch.setattr(main, "compensar_stock", MagicMock())
    monkeypatch.setattr(main, "crear_devolucion", lambda c, m, **kw: {"status_code": 400, "data": {"user_message": "No"}})
    for _ in range(main.MAX_INTENTOS_DEVOLUCION):
        _devolver(pago.pedido_id)
    ultimo = max(_intentos_de(pago, "devolucion"), key=lambda i: i.fecha_inicio)
    monkeypatch.setattr(main, "obtener_devolucion", lambda d: {"status_code": 200, "data": {
        "id": d, "charge_id": cargo, "amount": 4000, "status": "completa"}})
    resp = _resolver(str(ultimo.id), resultado="devuelto", devolucion_id="ref_desde_panel", motivo="La hice desde el panel de Culqi")
    assert resp.status_code == 200 and resp.json()["estado_del_pago"] == "reembolsado", resp.text
    hechas = [i for i in _intentos_de(pago, "devolucion") if i.estado == "aprobado"]
    assert len(hechas) == 1 and hechas[0].devolucion_id == "ref_desde_panel" and hechas[0].resuelto_por == ADMIN_QUE_RESUELVE


# ============ El comprador cancela su pedido sin pagar (Mis pedidos y "Reemplazar" en el checkout) ============

@pytest.fixture
def cancelacion(monkeypatch):
    """El pedido es de quien cancela; el stock (Productos), los eventos y la llamada a Pedidos, anotados."""
    monkeypatch.setattr(main, "_exigir_dueno_para_cancelar", lambda *args, **kwargs: None)
    eventos, en_pedidos = [], []
    anotar_eventos(monkeypatch, main, eventos)
    stock = _mockear_stock(monkeypatch)
    monkeypatch.setattr(main, "_cancelar_en_pedidos", lambda pedido_id: en_pedidos.append(str(pedido_id)) or True)
    return {"eventos": eventos, "stock": stock, "en_pedidos": en_pedidos}


def _cancelar(pedido_id):
    return client.post(f"/pagos/{pedido_id}/cancelar", headers=CON_TOKEN)


def _repuesto(llamadas) -> dict:
    return {url.split("/")[-3]: kwargs["json"]["cantidad"] for url, kwargs in llamadas if "reponer" in url}


def test_cancelar_un_pedido_sin_pagar_devuelve_su_stock_en_el_acto_y_lo_cancela_en_pedidos(cancelacion):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": a, "cantidad": 3, "cantidad_base": 30, "precio_unitario": 20.0},
                                {"producto_id": b, "cantidad": 2, "precio_unitario": 10.0}])
    resp = _cancelar(pago.pedido_id)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"pedido_id": str(pago.pedido_id), "estado": "cancelado", "stock": "devuelto",
                           "pedido_cancelado": True}
    fila = _fila_pago(pago)
    assert fila.estado == "cancelado" and fila.cancelado_en is not None
    assert _repuesto(cancelacion["stock"]) == {a: 30, b: 2}  # dentro de la misma petición: en el acto
    assert cancelacion["eventos"] == [{"evento": "pedido_cancelado_sin_pagar", "pedido_id": str(pago.pedido_id)}]
    assert cancelacion["en_pedidos"] == [str(pago.pedido_id)]

    otra_vez = _cancelar(pago.pedido_id)  # idempotente: ni stock ni aviso dos veces
    assert otra_vez.status_code == 200 and otra_vez.json()["stock"] == "ya_devuelto"
    assert len(cancelacion["stock"]) == 2 and len(cancelacion["eventos"]) == 1


def test_con_un_cobro_en_curso_o_una_transferencia_en_revision_no_se_cancela_nada(cancelacion):
    for estado in ("procesando", "en_revision"):
        pago = _crear_pago_con_estado(estado, [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
        resp = _cancelar(pago.pedido_id)
        assert resp.status_code == 409, resp.text
        esperado = main.MENSAJE_TRANSFERENCIA_EN_REVISION if estado == "en_revision" else main.MENSAJE_PAGO_SE_ESTA_VERIFICANDO
        assert resp.json()["detail"] == esperado
        assert _estado_del_pago(pago) == estado
    assert cancelacion["stock"] == [] and cancelacion["eventos"] == [] and cancelacion["en_pedidos"] == []


def test_un_pedido_pagado_o_devuelto_no_se_cancela_desde_aqui(cancelacion):
    for estado in ("aprobado", "adelanto_pagado", "reembolsado", "reembolso_procesando"):
        pago = _crear_pago_con_estado(estado, [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
        assert _cancelar(pago.pedido_id).status_code == 409
        assert _estado_del_pago(pago) == estado
    assert cancelacion["stock"] == [] and cancelacion["eventos"] == []


def test_cancelar_un_pago_rechazado_o_vencido_no_devuelve_el_stock_otra_vez(cancelacion):
    pago = _crear_pago_con_estado("rechazado", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    resp = _cancelar(pago.pedido_id)
    assert resp.status_code == 200 and resp.json()["stock"] == "ya_devuelto", resp.text
    assert _estado_del_pago(pago) == "cancelado"
    assert cancelacion["stock"] == []  # ya volvió al rechazarse
    assert [e["evento"] for e in cancelacion["eventos"]] == ["pedido_cancelado_sin_pagar"]


def test_un_pedido_cancelado_ya_no_se_cobra_ni_acepta_constancia_ni_vence(monkeypatch, transferencia):
    pago = _crear_pago_con_estado("cancelado", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    cobros = []
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: cobros.append(kw) or {"status_code": 201, "data": {"id": "x"}})
    cobro = _procesar(pago)
    assert cobro.status_code == 409 and cobro.json()["detail"] == main.MENSAJE_CANCELADO_POR_EL_COMPRADOR
    assert cobros == []
    constancia = _enviar_constancia(pago)
    assert constancia.status_code == 409 and constancia.json()["detail"] == main.MENSAJE_CANCELADO_POR_EL_COMPRADOR
    assert transferencia["guardadas"] == {} and not transferencia["reservar"].called
    db = main.SessionLocal()
    try:
        db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).update(
            {"vence_en": main.datetime.utcnow() - main.timedelta(minutes=5)}, synchronize_session=False)
        db.commit()
        main.vencer_pagos_sin_pagar(db)
    finally:
        db.close()
    assert _estado_del_pago(pago) == "cancelado" and not transferencia["compensar"].called


def _culqi_que_anota(monkeypatch) -> list:
    cobros = []
    monkeypatch.setattr(main, "crear_cargo", lambda **kw: cobros.append(kw) or {"status_code": 201, "data": {"id": "chr_x"}})
    return cobros


def test_abrir_culqi_cancelar_en_otra_pestana_y_enviar_la_tarjeta_da_409_y_no_llega_a_culqi(monkeypatch, cancelacion):
    """El comprador abre Culqi en una pestaña (el checkout pide el plazo), cancela el pedido en otra y después envía la
    tarjeta en la primera. Su pago queda "cancelado", que no está entre los estados desde los que se cobra: 409, sin
    intento y sin llamar a Culqi."""
    pago = _pago_con_plazo(creado_hace_min=2, vence_a_los_min=15)
    cobros = _culqi_que_anota(monkeypatch)
    assert _plazo(pago, "culqi").status_code == 200  # pestaña 1: se abre Culqi
    assert _cancelar(pago.pedido_id).status_code == 200  # pestaña 2: "Cancelar pedido"
    resp = _procesar(pago)  # pestaña 1: llega el token de la tarjeta
    assert resp.status_code == 409 and resp.json()["detail"] == main.MENSAJE_CANCELADO_POR_EL_COMPRADOR, resp.text
    assert cobros == [] and _intentos_de(pago) == [] and _estado_del_pago(pago) == "cancelado"


def test_una_cancelacion_que_entra_justo_antes_del_cobro_gana_y_no_se_llama_a_culqi(monkeypatch):
    """La carrera fina: el cobro ya leyó el pago "pendiente" cuando la otra pestaña confirma la cancelación. El UPDATE
    condicional de la fase 1 solo toma el pago desde los estados desde los que se cobra, y "cancelado" no es uno de
    ellos: no cambia ninguna fila, el cobro responde 409 con el mensaje de la cancelación y Culqi no se entera."""
    assert all("cancelado" not in desde for desde in main.DESDE_DONDE_SE_COBRA.values())
    pago = _pago_con_plazo(creado_hace_min=2, vence_a_los_min=15)
    cobros = _culqi_que_anota(monkeypatch)
    empezar = main._empezar_cobro

    def con_la_cancelacion_justo_antes(db, *args, **kwargs):
        otra = main.SessionLocal()  # la otra pestaña, con su propia conexión
        try:
            otra.query(main.models.Pago).filter(main.models.Pago.id == pago.id).update(
                {"estado": "cancelado", "cancelado_en": main.datetime.utcnow()}, synchronize_session=False)
            otra.commit()
        finally:
            otra.close()
        return empezar(db, *args, **kwargs)

    monkeypatch.setattr(main, "_empezar_cobro", con_la_cancelacion_justo_antes)
    resp = _procesar(pago)
    assert resp.status_code == 409 and resp.json()["detail"] == main.MENSAJE_CANCELADO_POR_EL_COMPRADOR, resp.text
    assert cobros == [] and _intentos_de(pago) == [] and _estado_del_pago(pago) == "cancelado"


def test_si_pedido_creado_no_llego_cancelar_crea_el_pago_y_devuelve_su_stock(monkeypatch, cancelacion):
    pedido_id, producto = str(uuid.uuid4()), str(uuid.uuid4())
    del_pedido = {"evento": "pedido_creado", "pedido_id": pedido_id, "usuario_id": str(uuid.uuid4()), "comprador_nombre": "QA",
                  "costo_envio": 5.0, "modalidad_envio": "local", "monto_adelanto": None,
                  "items": [{"producto_id": producto, "cantidad": 2, "unidad": "kg", "cantidad_base": 2, "precio_unitario": 10.0}]}
    monkeypatch.setattr(main.httpx, "get", lambda url, headers=None, timeout=None: MagicMock(status_code=200, json=lambda: del_pedido))
    resp = _cancelar(pedido_id)
    assert resp.status_code == 200 and resp.json()["stock"] == "devuelto", resp.text
    assert _repuesto(cancelacion["stock"]) == {producto: 2}
    db = main.SessionLocal()
    try:
        assert db.query(main.models.Pago.estado).filter(main.models.Pago.pedido_id == pedido_id).scalar() == "cancelado"
    finally:
        db.close()


def test_si_productos_no_responde_la_devolucion_queda_anotada_y_el_pedido_igual_se_cancela(monkeypatch, cancelacion):
    _mockear_stock(monkeypatch, roto=True)  # Productos no contesta: la devolución queda anotada (devoluciones_stock)
    producto = str(uuid.uuid4())
    pago = _crear_pago_directo([{"producto_id": producto, "cantidad": 4, "precio_unitario": 10.0}])
    resp = _cancelar(pago.pedido_id)
    assert resp.status_code == 200 and resp.json()["stock"] == "por_devolver", resp.text
    db = main.SessionLocal()
    try:
        anotadas = db.query(main.models.DevolucionStock).filter(main.models.DevolucionStock.pedido_id == pago.pedido_id).all()
    finally:
        db.close()
    assert [(d.producto_id, d.cantidad) for d in anotadas] == [(producto, 4)]
    assert anotadas[0].estado in ("pendiente", "dudosa")
    assert _estado_del_pago(pago) == "cancelado"


def test_solo_el_comprador_dueno_cancela_su_pedido(monkeypatch):
    pago = _pago_de_prueba()
    _mockear_pedidos(monkeypatch, usuario_id=str(uuid.uuid4()))  # el pedido es de otro
    assert _cancelar(pago.pedido_id).status_code == 403
    _como(uuid.uuid4(), "admin")  # ni el admin: él cancela con devolución, desde su panel
    try:
        assert _cancelar(pago.pedido_id).status_code == 403
    finally:
        _restaurar_sesion_por_defecto()
    assert _estado_del_pago(pago) == "pendiente"


def test_una_constancia_que_se_sube_a_la_vez_se_turna_con_la_cancelacion(cancelacion):
    """La transferencia toma la fila con FOR UPDATE mientras sube la constancia: la cancelación espera y, cuando la
    constancia queda en revisión, responde 409 sin tocar nada."""
    import threading
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    db = main.SessionLocal()
    fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).with_for_update().one()
    respuestas = []
    hilo = threading.Thread(target=lambda: respuestas.append(TestClient(main.app).post(
        f"/pagos/{pago.pedido_id}/cancelar", headers=CON_TOKEN)))
    hilo.start()
    hilo.join(timeout=1.5)
    assert hilo.is_alive()  # esperando la fila
    fila.estado = "en_revision"
    db.commit()
    db.close()
    hilo.join(timeout=30)
    assert respuestas and respuestas[0].status_code == 409, respuestas and respuestas[0].text
    assert _estado_del_pago(pago) == "en_revision" and cancelacion["stock"] == []


def test_las_condiciones_dicen_el_plazo_para_pagar_y_un_pedido_cancelado_no_tiene_nada_que_pagar(monkeypatch):
    assert client.get("/pagos/condiciones").json()["plazo_para_pagar_minutos"] == main.PLAZO_PARA_PAGAR_MINUTOS
    comprador = uuid.uuid4()
    pago = _crear_pago_con_estado("cancelado", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    _mockear_pedidos(monkeypatch, usuario_id=str(comprador))
    _como(comprador, "comprador")
    try:
        datos = client.get(f"/pagos/{pago.pedido_id}", headers=CON_TOKEN).json()
    finally:
        _restaurar_sesion_por_defecto()
    assert datos["estado"] == "cancelado" and datos["monto_a_pagar"] is None and datos["parte_a_pagar"] is None


# ============ Plazos para pagar: guardados en el pago, alargados al elegir transferencia o al abrir Culqi ============

def _pago_con_plazo(creado_hace_min, vence_a_los_min, estado="pendiente", usuario_id=None):
    """Un pago creado hace `creado_hace_min` minutos con su plazo a `vence_a_los_min` minutos de la creación."""
    db = main.SessionLocal()
    try:
        creacion = main.datetime.utcnow() - main.timedelta(minutes=creado_hace_min)
        pago = main.models.Pago(pedido_id=uuid.uuid4(), monto=40, estado=estado, usuario_id=usuario_id,
                                items=json.dumps([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]),
                                fecha_creacion=creacion, vence_en=creacion + main.timedelta(minutes=vence_a_los_min))
        db.add(pago)
        db.commit()
        db.refresh(pago)
        db.expunge(pago)
        return pago
    finally:
        db.close()


def _plazo(pago, motivo):
    return client.post(f"/pagos/{pago.pedido_id}/plazo", json={"motivo": motivo}, headers=CON_TOKEN)


def _extensiones(pago):
    db = main.SessionLocal()
    try:
        return db.query(main.models.ExtensionPlazo).filter(main.models.ExtensionPlazo.pago_id == pago.id).order_by(
            main.models.ExtensionPlazo.fecha).all()
    finally:
        db.close()


def _correr_reloj(pago, minutos):
    """Como si pasaran `minutos`: la creación y el plazo del pago se corren hacia atrás."""
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Pago).filter(main.models.Pago.id == pago.id).one()
        fila.fecha_creacion -= main.timedelta(minutes=minutos)
        fila.vence_en -= main.timedelta(minutes=minutos)
        db.commit()
    finally:
        db.close()


def test_un_pago_nuevo_vence_a_los_15_minutos_y_al_vencer_devuelve_su_stock(monkeypatch):
    pedido_id, comprador = str(uuid.uuid4()), str(uuid.uuid4())
    rabbitmq_consumer.crear_pago_si_falta({"evento": "pedido_creado", "pedido_id": pedido_id, "usuario_id": comprador,
                                           "costo_envio": 5.0, "items": [{"producto_id": str(uuid.uuid4()), "cantidad": 3,
                                                                          "precio_unitario": 10.0}]})
    db = main.SessionLocal()
    try:
        pago = db.query(main.models.Pago).filter(main.models.Pago.pedido_id == pedido_id).one()
        assert pago.vence_en - pago.fecha_creacion == main.timedelta(minutes=15)
        assert str(pago.usuario_id) == comprador
        compensar = MagicMock(return_value={})
        monkeypatch.setattr(main, "compensar_stock", compensar)
        main.vencer_pagos_sin_pagar(db, ahora=pago.fecha_creacion + main.timedelta(minutes=14, seconds=59))
        db.refresh(pago)
        # (la base de pruebas guarda pagos de otras pruebas que sí vencen en esa pasada: solo se mira este)
        assert pago.estado == "pendiente" and all(c.args[0] != pago.items for c in compensar.call_args_list)
        main.vencer_pagos_sin_pagar(db, ahora=pago.fecha_creacion + main.timedelta(minutes=15, seconds=1))
        db.refresh(pago)
        assert pago.estado == "rechazado" and pago.vencido_en is not None
        assert any(c.args[0] == pago.items for c in compensar.call_args_list)
    finally:
        db.close()


def test_abrir_culqi_a_los_12_minutos_alarga_el_plazo_y_nunca_pasa_del_tope():
    pago = _pago_con_plazo(creado_hace_min=12, vence_a_los_min=15)
    resp = _plazo(pago, "culqi")
    assert resp.status_code == 200, resp.text
    fila = _fila_pago(pago)
    esperado = main.datetime.utcnow() + main.timedelta(minutes=10)
    assert abs((fila.vence_en - esperado).total_seconds()) < 5
    assert resp.json()["vence_en"].startswith(fila.vence_en.isoformat()[:16])
    for minutos in (20, 15, 30):  # pasa el tiempo y vuelve a abrir Culqi
        _correr_reloj(pago, minutos)
        assert _plazo(pago, "culqi").status_code == 200
        fila = _fila_pago(pago)
        assert fila.vence_en <= fila.fecha_creacion + main.timedelta(minutes=45)
    assert [e.motivo for e in _extensiones(pago)] == ["culqi", "culqi"]  # pasado el tope ya no se alarga
    assert all(e.vence_despues > e.vence_antes for e in _extensiones(pago))


def test_con_mas_de_10_minutos_por_delante_abrir_culqi_no_cambia_el_plazo():
    pago = _pago_con_plazo(creado_hace_min=2, vence_a_los_min=15)
    antes = _fila_pago(pago).vence_en
    assert _plazo(pago, "culqi").status_code == 200
    assert _fila_pago(pago).vence_en == antes and _extensiones(pago) == []


def test_elegir_transferencia_da_dos_horas_desde_la_creacion_y_cambiar_de_metodo_no_lo_alarga_mas():
    pago = _pago_con_plazo(creado_hace_min=5, vence_a_los_min=15)
    assert _plazo(pago, "transferencia").status_code == 200
    fila = _fila_pago(pago)
    assert fila.vence_en == fila.fecha_creacion + main.timedelta(minutes=120)
    for motivo in ("culqi", "transferencia", "culqi", "transferencia"):
        assert _plazo(pago, motivo).status_code == 200
        assert _fila_pago(pago).vence_en == fila.vence_en
    [extension] = _extensiones(pago)
    assert extension.motivo == "transferencia" and extension.vence_antes == fila.fecha_creacion + main.timedelta(minutes=15)


def test_un_pago_de_antes_conserva_sus_60_minutos_y_el_plazo_nunca_se_acorta():
    pago = _pago_con_plazo(creado_hace_min=55, vence_a_los_min=60)
    antes = _fila_pago(pago).vence_en
    assert _plazo(pago, "culqi").status_code == 200  # le quedan 5, pero el tope (45) ya pasó: no lo baja
    assert _fila_pago(pago).vence_en == antes


def test_solo_se_alarga_el_plazo_de_un_pago_pendiente():
    for estado in ("procesando", "en_revision", "rechazado", "aprobado", "cancelado"):
        pago = _pago_con_plazo(creado_hace_min=12, vence_a_los_min=15, estado=estado)
        antes = _fila_pago(pago).vence_en
        for motivo in ("culqi", "transferencia"):
            resp = _plazo(pago, motivo)
            assert resp.status_code == 200 and resp.json()["estado"] == estado
        assert _fila_pago(pago).vence_en == antes and _extensiones(pago) == []


def test_si_pedido_creado_no_llego_pedir_el_plazo_crea_el_pago_con_sus_15_minutos(monkeypatch):
    pedido_id = str(uuid.uuid4())
    del_pedido = {"evento": "pedido_creado", "pedido_id": pedido_id, "usuario_id": str(uuid.uuid4()), "comprador_nombre": "QA",
                  "costo_envio": 5.0, "modalidad_envio": "local", "monto_adelanto": None,
                  "items": [{"producto_id": str(uuid.uuid4()), "cantidad": 3, "cantidad_base": 3, "precio_unitario": 10.0}]}
    monkeypatch.setattr(main.httpx, "get", lambda url, headers=None, timeout=None: MagicMock(status_code=200, json=lambda: del_pedido))
    resp = client.post(f"/pagos/{pedido_id}/plazo", json={"motivo": "culqi"}, headers=CON_TOKEN)
    assert resp.status_code == 200 and resp.json()["estado"] == "pendiente", resp.text
    db = main.SessionLocal()
    try:
        pago = db.query(main.models.Pago).filter(main.models.Pago.pedido_id == pedido_id).one()
        assert pago.vence_en - pago.fecha_creacion == main.timedelta(minutes=15)
    finally:
        db.close()


def test_solo_el_comprador_dueno_alarga_el_plazo_y_el_motivo_se_valida(monkeypatch):
    pago = _pago_con_plazo(creado_hace_min=12, vence_a_los_min=15)
    assert client.post(f"/pagos/{pago.pedido_id}/plazo", json={"motivo": "porque si"}, headers=CON_TOKEN).status_code == 422
    monkeypatch.setattr(main, "_exigir_dueno_para_pagar", ORIGINAL_EXIGIR_DUENO)
    _mockear_pedidos(monkeypatch, usuario_id=str(uuid.uuid4()))  # el pedido es de otro
    assert _plazo(pago, "culqi").status_code == 403
    assert _extensiones(pago) == []


def test_las_condiciones_dicen_los_dos_plazos():
    condiciones = client.get("/pagos/condiciones").json()
    assert condiciones["plazo_para_pagar_minutos"] == 15 and condiciones["plazo_transferencia_minutos"] == 120


# ============ Transferencias en revisión: el aviso a Pedidos, el tope por comprador y no se cancelan ============

def _pago_del_comprador(comprador, estado="pendiente"):
    db = main.SessionLocal()
    try:
        pago = main.models.Pago(pedido_id=uuid.uuid4(), usuario_id=comprador, monto=40, estado=estado,
                                items=json.dumps([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}]))
        db.add(pago)
        db.commit()
        db.refresh(pago)
        db.expunge(pago)
        return pago
    finally:
        db.close()


def test_la_constancia_deja_transferencia_enviada_en_la_bandeja_junto_con_el_en_revision(transferencia):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 2, "precio_unitario": 30.0}])
    assert _enviar_constancia(pago).status_code == 200
    assert {"evento": "transferencia_enviada", "pedido_id": str(pago.pedido_id), "parte": "total"} in transferencia["eventos"]
    db = main.SessionLocal()
    try:
        en_bandeja = db.execute(main.text("SELECT count(*) FROM eventos_salientes WHERE tipo = 'transferencia_enviada' AND "
                                          "cuerpo::json->>'pedido_id' = :p"), {"p": str(pago.pedido_id)}).scalar()
    finally:
        db.close()
    assert en_bandeja == 1 and _estado_del_pago(pago) == "en_revision"


def test_con_dos_transferencias_en_revision_la_tercera_constancia_da_409(transferencia):
    comprador = uuid.uuid4()
    _pago_del_comprador(comprador, "en_revision")
    segunda, tercera = _pago_del_comprador(comprador), _pago_del_comprador(comprador)
    _pago_del_comprador(uuid.uuid4(), "en_revision")  # las de otro comprador no cuentan
    _como(comprador, "comprador")
    try:
        assert _enviar_constancia(segunda).status_code == 200  # con una en revisión, la segunda entra
        resp = _enviar_constancia(tercera)
    finally:
        _restaurar_sesion_por_defecto()
    assert resp.status_code == 409, resp.text
    assert "2 transferencias en revisión" in resp.json()["detail"] and "tarjeta o Yape" in resp.json()["detail"]
    assert _estado_del_pago(tercera) == "pendiente" and len(transferencia["guardadas"]) == 1
    assert not any(e["evento"] == "transferencia_enviada" and e["pedido_id"] == str(tercera.pedido_id) for e in transferencia["eventos"])


def test_un_pago_de_antes_anota_su_comprador_al_enviar_la_constancia(transferencia):
    pago = _crear_pago_directo([{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    comprador = uuid.uuid4()
    _como(comprador, "comprador")
    try:
        assert _enviar_constancia(pago).status_code == 200
    finally:
        _restaurar_sesion_por_defecto()
    assert _fila_pago(pago).usuario_id == comprador


def test_un_pedido_en_revision_no_se_cancela_ni_se_reemplaza_porque_el_dinero_puede_estar_transferido(cancelacion):
    pago = _crear_pago_con_estado("en_revision", [{"producto_id": str(uuid.uuid4()), "cantidad": 1, "precio_unitario": 40.0}])
    resp = _cancelar(pago.pedido_id)
    assert resp.status_code == 409 and resp.json()["detail"] == main.MENSAJE_TRANSFERENCIA_EN_REVISION
    assert "puede estar ya transferido" in resp.json()["detail"]
    assert _estado_del_pago(pago) == "en_revision"
    assert cancelacion["stock"] == [] and cancelacion["eventos"] == [] and cancelacion["en_pedidos"] == []
