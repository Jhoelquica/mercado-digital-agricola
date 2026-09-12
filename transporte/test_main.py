"""
Tests de la liquidación de repartidor al entregar un envío (transporte/main.py +
logica_liquidacion_repartidor.py + reintento_liquidacion_repartidor.py).

Cubre:
- PATCH /envios/{id}/estado a "entregado" con Pagos respondiendo bien -> liquidacion_confirmada=True
- lo mismo con Pagos fallando -> liquidacion_confirmada queda False, pero la respuesta al
  repartidor sigue siendo 200 (la transición de estado no se bloquea)
- reintentar_liquidaciones_repartidor_pendientes(db) (la función de "un ciclo" del job de fondo,
  sin loop ni sleep) resuelve un envío "entregado" que había quedado sin liquidar

No se levantan Pedidos, Pagos, Certificación ni RabbitMQ reales:
- httpx.Client (usado por logica_liquidacion_repartidor para GET /pedidos/{id} y
  POST /pagos/interno/liquidar-repartidor) se mockea con stubs.
- notificar_entrega_a_certificacion usa httpx.get/httpx.post crudos contra PEDIDOS_URL/
  CERTIFICACION_URL (default localhost, sin nada escuchando en el contenedor de test) — falla
  con ConnectError, que esa función ya atrapa y ver con un print(); no hace falta mockearla para
  estos tests, que no dependen de ese resultado.
- publicar_evento se mockea para no depender de RabbitMQ.

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de postgresql +
create_all() al importar main) — no se puede sustituir por SQLite, mismo criterio que el resto de
los servicios de este proyecto.
"""
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import main
import logica_liquidacion_repartidor
from reintento_liquidacion_repartidor import reintentar_liquidaciones_repartidor_pendientes
from auth import verificar_token

client = TestClient(main.app)


def _auth(sub: str, rol: str = "repartidor"):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}


class _RespuestaFalsa:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise logica_liquidacion_repartidor.httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._data


class _ClienteLiquidacionFalso:
    """Reemplaza httpx.Client() dentro de logica_liquidacion_repartidor: GET /pedidos/{id}
    devuelve un costo_envio fijo, POST a Pagos devuelve 200, sin tocar la red."""
    def __init__(self, costo_envio):
        self._costo_envio = costo_envio

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return _RespuestaFalsa(200, {"costo_envio": self._costo_envio})

    def post(self, url, **kwargs):
        return _RespuestaFalsa(200, {"mensaje": "ok"})


class _ClienteRoto:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, *a, **kw):
        raise logica_liquidacion_repartidor.httpx.RequestError("boom", request=None)


def _mockear_pagos_ok(monkeypatch, costo_envio=8.0):
    monkeypatch.setattr(
        logica_liquidacion_repartidor.httpx, "Client",
        lambda *a, **kw: _ClienteLiquidacionFalso(costo_envio),
    )


def _mockear_pagos_roto(monkeypatch):
    monkeypatch.setattr(logica_liquidacion_repartidor.httpx, "Client", lambda *a, **kw: _ClienteRoto())


def _crear_repartidor_directo(usuario_id=None) -> "main.models.Repartidor":
    db = main.SessionLocal()
    try:
        repartidor = main.models.Repartidor(
            usuario_id=usuario_id or uuid.uuid4(),
            nombre="Repartidor Test",
            dni="12345678",
            estado_disponibilidad="ocupado",
        )
        db.add(repartidor)
        db.commit()
        db.refresh(repartidor)
        db.expunge(repartidor)
        return repartidor
    finally:
        db.close()


def _crear_envio_directo(repartidor_id, estado="asignado", pedido_id=None) -> "main.models.Envio":
    db = main.SessionLocal()
    try:
        envio = main.models.Envio(
            repartidor_id=repartidor_id,
            pedido_id=pedido_id or uuid.uuid4(),
            estado=estado,
        )
        db.add(envio)
        db.commit()
        db.refresh(envio)
        db.expunge(envio)
        return envio
    finally:
        db.close()


def _envio_recargado(envio_id):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Envio).filter(main.models.Envio.id == envio_id).first()
    finally:
        db.close()


def test_entregar_envio_con_pagos_ok_marca_liquidacion_confirmada(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="asignado")

    _mockear_pagos_ok(monkeypatch, costo_envio=8.0)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    _auth(usuario_id)
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "entregado"})
    assert resp.status_code == 200, resp.text

    assert _envio_recargado(envio.id).liquidacion_confirmada is True


def test_entregar_envio_con_pagos_fallando_no_bloquea_respuesta(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="asignado")

    _mockear_pagos_roto(monkeypatch)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    _auth(usuario_id)
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "entregado"})
    assert resp.status_code == 200, resp.text

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "entregado"  # la transición ocurrió igual
    assert envio_recargado.liquidacion_confirmada is False  # queda pendiente para el reintento


def test_reintento_resuelve_envio_entregado_pendiente(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    # Ya "entregado" de entrada (liquidacion_confirmada=False por default) — simula el resultado
    # de una entrega cuya notificación a Pagos falló la primera vez.
    envio = _crear_envio_directo(repartidor.id, estado="entregado")

    _mockear_pagos_ok(monkeypatch, costo_envio=5.0)

    db = main.SessionLocal()
    try:
        reintentar_liquidaciones_repartidor_pendientes(db)
    finally:
        db.close()

    assert _envio_recargado(envio.id).liquidacion_confirmada is True
