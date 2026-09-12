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

from fastapi.testclient import TestClient

import main
import logica_liquidaciones
from reintento_liquidaciones import reintentar_liquidaciones_pendientes
from auth import verificar_token

client = TestClient(main.app)

main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "comprador"}


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
    return client.post("/pagos/procesar", json={
        "pedido_id": str(pago.pedido_id),
        "token_culqi": "tkn_test",
        "email": "cliente@test.com",
    })


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
