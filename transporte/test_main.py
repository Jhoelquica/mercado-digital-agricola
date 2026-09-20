"""
Tests de las dos notificaciones al entregar un envío: liquidación de repartidor
(transporte/main.py + logica_liquidacion_repartidor.py + reintento_liquidacion_repartidor.py) y
certificación (logica_certificacion.py + reintento_certificacion.py) — mismo patrón para las dos,
implementado primero para liquidación y calcado después para certificación.

Cubre, para cada una:
- PATCH /envios/{id}/estado a "entregado" con el servicio externo respondiendo bien ->
  *_confirmada=True
- lo mismo con el servicio externo fallando -> *_confirmada queda False, pero la respuesta al
  repartidor sigue siendo 200 (la transición de estado no se bloquea)
- la función de "un ciclo" del job de fondo correspondiente (sin loop ni sleep) resuelve un envío
  "entregado" que había quedado sin confirmar

No se levantan Pedidos, Pagos, Certificación ni RabbitMQ reales:
- httpx.Client (usado por logica_liquidacion_repartidor para GET /pedidos/{id} y
  POST /pagos/interno/liquidar-repartidor, y por logica_certificacion para GET /pedidos/{id} y
  las llamadas a Certificación) se mockea con stubs en cada módulo por separado — cada uno tiene
  su propio `import httpx`, ver el docstring de logica_certificacion.py sobre por qué.
- publicar_evento se mockea para no depender de RabbitMQ.

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de postgresql +
create_all() al importar main) — no se puede sustituir por SQLite, mismo criterio que el resto de
los servicios de este proyecto.
"""
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import main
import logica_liquidacion_repartidor
import logica_certificacion
import logica_repartidores
from reintento_liquidacion_repartidor import reintentar_liquidaciones_repartidor_pendientes
from reintento_certificacion import reintentar_certificaciones_pendientes
from auth import verificar_token, security

client = TestClient(main.app)


def _auth(sub: str, rol: str = "repartidor"):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}
    # actualizar_estado y envios_pendientes_entrega_directa además dependen de `security`
    # (HTTPBearer crudo) para reenviar el token a Productores — sin esto, TestClient (que no
    # manda header Authorization por defecto) recibe 401 "Not authenticated" ANTES de llegar al
    # código, sin importar el override de verificar_token de arriba.
    main.app.dependency_overrides[security] = lambda: HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="token-de-prueba"
    )


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


class _ClienteCertificacionFalso:
    """Reemplaza httpx.Client() dentro de logica_certificacion: GET /pedidos/{id} devuelve items
    con un producto_id fijo, cualquier POST a Certificación devuelve 200, sin tocar la red."""
    def __init__(self, producto_id):
        self._producto_id = producto_id

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return _RespuestaFalsa(200, {"items": [{"producto_id": self._producto_id}]})

    def post(self, url, **kwargs):
        return _RespuestaFalsa(200, {"mensaje": "ok"})


def _mockear_certificacion_ok(monkeypatch, producto_id=None):
    monkeypatch.setattr(
        logica_certificacion.httpx, "Client",
        lambda *a, **kw: _ClienteCertificacionFalso(producto_id or str(uuid.uuid4())),
    )


def _mockear_certificacion_rota(monkeypatch):
    monkeypatch.setattr(logica_certificacion.httpx, "Client", lambda *a, **kw: _ClienteRoto())


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


def _crear_envio_directo(repartidor_id, estado="asignado", pedido_id=None, productor_id=None, motivo_pendiente=None, pin_entrega="1234") -> "main.models.Envio":
    db = main.SessionLocal()
    try:
        envio = main.models.Envio(
            repartidor_id=repartidor_id,
            pedido_id=pedido_id or uuid.uuid4(),
            estado=estado,
            productor_id=productor_id,
            motivo_pendiente=motivo_pendiente,
            pin_entrega=pin_entrega,
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


def _confirmar_entrega(envio_id, pin="1234"):
    """POST /envios/{id}/entregar con una foto falsa — subir_foto_entrega se mockea en cada test
    que llama a esto, así que el content_type/bytes reales no importan, solo pasar la validación
    de content_type del endpoint."""
    return client.post(
        f"/envios/{envio_id}/entregar",
        data={"pin": pin},
        files={"foto": ("foto.jpg", b"contenido-falso", "image/jpeg")},
    )


def test_entregar_envio_con_pagos_ok_marca_liquidacion_confirmada(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="asignado")

    _mockear_pagos_ok(monkeypatch, costo_envio=8.0)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "subir_foto_entrega", lambda *a, **kw: "http://fake/foto.jpg")

    _auth(usuario_id)
    resp = _confirmar_entrega(envio.id)
    assert resp.status_code == 200, resp.text

    assert _envio_recargado(envio.id).liquidacion_confirmada is True


def test_entregar_envio_con_pagos_fallando_no_bloquea_respuesta(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="asignado")

    _mockear_pagos_roto(monkeypatch)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "subir_foto_entrega", lambda *a, **kw: "http://fake/foto.jpg")

    _auth(usuario_id)
    resp = _confirmar_entrega(envio.id)
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


def test_entregar_envio_con_certificacion_ok_marca_certificacion_confirmada(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="asignado")

    _mockear_certificacion_ok(monkeypatch)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "subir_foto_entrega", lambda *a, **kw: "http://fake/foto.jpg")

    _auth(usuario_id)
    resp = _confirmar_entrega(envio.id)
    assert resp.status_code == 200, resp.text

    assert _envio_recargado(envio.id).certificacion_confirmada is True


def test_entregar_envio_con_certificacion_fallando_no_bloquea_respuesta(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="asignado")

    _mockear_certificacion_rota(monkeypatch)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "subir_foto_entrega", lambda *a, **kw: "http://fake/foto.jpg")

    _auth(usuario_id)
    resp = _confirmar_entrega(envio.id)
    assert resp.status_code == 200, resp.text

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "entregado"  # la transición ocurrió igual
    assert envio_recargado.certificacion_confirmada is False  # queda pendiente para el reintento


def test_reintento_resuelve_envio_sin_certificar(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    # Ya "entregado" de entrada (certificacion_confirmada=False por default) — simula el
    # resultado de una entrega cuya notificación a Certificación falló la primera vez.
    envio = _crear_envio_directo(repartidor.id, estado="entregado")

    _mockear_certificacion_ok(monkeypatch)

    db = main.SessionLocal()
    try:
        reintentar_certificaciones_pendientes(db)
    finally:
        db.close()

    assert _envio_recargado(envio.id).certificacion_confirmada is True


# ============ POST /envios/{id}/entregar (PIN + foto + GPS) ============

def test_confirmar_entrega_con_pin_correcto_marca_entregado_y_copia_gps(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="en_camino", pin_entrega="7391")

    # repartidor_latitud/longitud ya vienen actualizándose por PATCH /ubicacion (cada 15s) antes
    # de que el repartidor confirme la entrega — simulado acá escribiéndolos directo en la fila.
    db = main.SessionLocal()
    try:
        envio_db = db.query(main.models.Envio).filter(main.models.Envio.id == envio.id).first()
        envio_db.repartidor_latitud = "-12.0464"
        envio_db.repartidor_longitud = "-77.0428"
        db.commit()
    finally:
        db.close()

    _mockear_pagos_ok(monkeypatch, costo_envio=6.0)
    _mockear_certificacion_ok(monkeypatch)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "subir_foto_entrega", lambda *a, **kw: "http://fake/foto-entrega.jpg")

    _auth(usuario_id)
    resp = _confirmar_entrega(envio.id, pin="7391")
    assert resp.status_code == 200, resp.text

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "entregado"
    assert envio_recargado.foto_entrega_url == "http://fake/foto-entrega.jpg"
    # Copiado de repartidor_latitud/longitud EN ESE INSTANTE, no una referencia — si el
    # repartidor sigue moviéndose después, esto no debe cambiar.
    assert envio_recargado.entrega_latitud == "-12.0464"
    assert envio_recargado.entrega_longitud == "-77.0428"


def test_confirmar_entrega_con_pin_incorrecto_falla_400(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="en_camino", pin_entrega="7391")

    _auth(usuario_id)
    resp = _confirmar_entrega(envio.id, pin="0000")

    assert resp.status_code == 400, resp.text
    assert _envio_recargado(envio.id).estado == "en_camino"  # no se movió el estado


def test_confirmar_entrega_de_envio_de_otro_repartidor_falla_403(monkeypatch):
    dueño = _crear_repartidor_directo(usuario_id=str(uuid.uuid4()))
    envio = _crear_envio_directo(dueño.id, estado="en_camino", pin_entrega="7391")

    otro_usuario_id = str(uuid.uuid4())
    _crear_repartidor_directo(usuario_id=otro_usuario_id)

    _auth(otro_usuario_id)
    resp = _confirmar_entrega(envio.id, pin="7391")

    assert resp.status_code == 403, resp.text


def test_confirmar_entrega_en_estado_pendiente_falla_409(monkeypatch):
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="propuesto", pin_entrega="7391")

    _auth(usuario_id)
    resp = _confirmar_entrega(envio.id, pin="7391")

    assert resp.status_code == 409, resp.text


def test_actualizar_estado_repartidor_a_entregado_ya_no_funciona_falla_400(monkeypatch):
    """Regresión intencional: desde este cambio, un repartidor NO puede marcar "entregado" por
    el PATCH /estado de siempre — tiene que confirmar con PIN + foto vía POST /entregar."""
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="en_camino")

    _auth(usuario_id)
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "entregado"})

    assert resp.status_code == 400, resp.text
    assert _envio_recargado(envio.id).estado == "en_camino"


# ============ Matching por peso/capacidad (proponer_envio_a_repartidor) ============

class _ClienteRepartidoresFalso:
    """Reemplaza httpx.Client() dentro de logica_repartidores: distingue la URL de Pedidos de la
    de Productos por el path pedido, sin tocar la red. pedido: dict que devuelve GET
    /pedidos/{id}. productor_por_producto: dict producto_id -> productor_id para GET
    /productos/{id} (usado solo por _pedido_es_de_un_solo_productor)."""
    def __init__(self, pedido, productor_por_producto=None, pedidos_no_disponible=False):
        self._pedido = pedido
        self._productor_por_producto = productor_por_producto or {}
        self._pedidos_no_disponible = pedidos_no_disponible

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        if "/pedidos/" in url:
            if self._pedidos_no_disponible:
                raise logica_repartidores.httpx.RequestError("boom", request=None)
            return _RespuestaFalsa(200, self._pedido)
        producto_id = url.rstrip("/").split("/")[-1]
        return _RespuestaFalsa(200, {"productor_id": self._productor_por_producto.get(producto_id)})


def _mockear_pedido(monkeypatch, pedido, productor_por_producto=None, pedidos_no_disponible=False):
    monkeypatch.setattr(
        logica_repartidores.httpx, "Client",
        lambda *a, **kw: _ClienteRepartidoresFalso(pedido, productor_por_producto, pedidos_no_disponible),
    )


def _pedido_falso(peso_total_kg=None, requiere_vehiculo_grande=False, items=None):
    return {
        "peso_total_kg": peso_total_kg,
        "requiere_vehiculo_grande": requiere_vehiculo_grande,
        "items": items if items is not None else [{"producto_id": str(uuid.uuid4())}],
    }


def _crear_repartidor_disponible(capacidad_maxima_kg=None, tipo_vehiculo="moto") -> "main.models.Repartidor":
    """A diferencia de _crear_repartidor_directo (que arranca "ocupado", pensado para los tests
    de liquidación/certificación de arriba), este arranca "disponible" — el estado que
    proponer_envio_a_repartidor exige para considerar un candidato."""
    db = main.SessionLocal()
    try:
        repartidor = main.models.Repartidor(
            usuario_id=uuid.uuid4(),
            nombre="Repartidor Test",
            dni="12345678",
            estado_disponibilidad="disponible",
            tipo_vehiculo=tipo_vehiculo,
            capacidad_maxima_kg=capacidad_maxima_kg,
        )
        db.add(repartidor)
        db.commit()
        db.refresh(repartidor)
        db.expunge(repartidor)
        return repartidor
    finally:
        db.close()


@pytest.fixture
def matching():
    """Los tests de matching de abajo, a diferencia de _crear_repartidor_directo (que arranca
    "ocupado" justamente para NUNCA ser un candidato real para otro test), crean repartidores
    "disponible" a propósito — es lo que proponer_envio_a_repartidor necesita para encontrarlos.
    Sin este archivo aislar tests por transacción (comparten un único Postgres real, ver docstring
    del módulo), un repartidor "disponible" que un test deja sin consumir queda como candidato
    fantasma para CUALQUIER test posterior que dispare intentar_resolver_envio_huerfano —
    crear_repartidor siempre lo hace al registrar uno nuevo. Este fixture borra al terminar (pase
    o falle el test) todo lo que el test haya registrado en las listas que devuelve.

    Antes de arrancar, además, neutraliza cualquier repartidor "disponible" que ya exista en la
    tabla: los tests de liquidación/certificación de más arriba en este archivo dejan sus propios
    repartidores "disponible" otra vez al completar una entrega real (ver actualizar_estado en
    main.py, líneas 200/263) — sin capacidad declarada (NULL), esos son candidatos fantasma
    igual de válidos que cualquier otro cuando un test de acá abajo prueba el caso "peso
    desconocido" (sin filtro de capacidad, exactamente el hueco por el que se cuelan). No hace
    falta revertir este cambio al terminar: son repartidores de tests ya completados, que no
    vuelven a mirar su propio estado_disponibilidad después de terminar."""
    db_previo = main.SessionLocal()
    try:
        db_previo.query(main.models.Repartidor).filter(
            main.models.Repartidor.estado_disponibilidad == "disponible"
        ).update({"estado_disponibilidad": "ocupado"}, synchronize_session=False)
        db_previo.commit()
    finally:
        db_previo.close()

    repartidor_ids, envio_ids = [], []
    yield repartidor_ids, envio_ids
    db = main.SessionLocal()
    try:
        if repartidor_ids:
            db.query(main.models.Repartidor).filter(main.models.Repartidor.id.in_(repartidor_ids)).delete(synchronize_session=False)
        if envio_ids:
            db.query(main.models.Envio).filter(main.models.Envio.id.in_(envio_ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_proponer_envio_con_peso_bajo_capacidad_de_todos_prioriza_el_mas_chico(monkeypatch, matching):
    repartidor_ids, envio_ids = matching
    chico = _crear_repartidor_disponible(capacidad_maxima_kg=50)
    grande = _crear_repartidor_disponible(capacidad_maxima_kg=200)
    envio = _crear_envio_directo(None, estado="pendiente")
    repartidor_ids += [chico.id, grande.id]
    envio_ids.append(envio.id)

    _mockear_pedido(monkeypatch, _pedido_falso(peso_total_kg=20))

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "propuesto"
    assert str(envio_recargado.repartidor_id) == str(chico.id)  # el más chico que alcanza, no cualquiera


def test_proponer_envio_con_peso_que_solo_un_vehiculo_grande_puede_llevar(monkeypatch, matching):
    repartidor_ids, envio_ids = matching
    pequeno = _crear_repartidor_disponible(capacidad_maxima_kg=10)  # insuficiente, aunque se crea primero
    grande = _crear_repartidor_disponible(capacidad_maxima_kg=100)  # suficiente
    envio = _crear_envio_directo(None, estado="pendiente")
    repartidor_ids += [pequeno.id, grande.id]
    envio_ids.append(envio.id)

    _mockear_pedido(monkeypatch, _pedido_falso(peso_total_kg=50))

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "propuesto"
    assert str(envio_recargado.repartidor_id) == str(grande.id)  # NO el primero disponible (el pequeño)


def test_proponer_envio_sin_capacidad_suficiente_y_un_solo_productor_queda_pendiente_entrega_directa(monkeypatch, matching):
    repartidor_ids, envio_ids = matching
    insuficiente = _crear_repartidor_disponible(capacidad_maxima_kg=10)  # insuficiente para los 50 kg de abajo
    envio = _crear_envio_directo(None, estado="pendiente")
    repartidor_ids.append(insuficiente.id)
    envio_ids.append(envio.id)

    producto_a, producto_b = str(uuid.uuid4()), str(uuid.uuid4())
    mismo_productor = str(uuid.uuid4())
    pedido = _pedido_falso(
        peso_total_kg=50,
        requiere_vehiculo_grande=True,
        items=[{"producto_id": producto_a}, {"producto_id": producto_b}],
    )
    _mockear_pedido(monkeypatch, pedido, productor_por_producto={producto_a: mismo_productor, producto_b: mismo_productor})

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "pendiente_entrega_directa"
    assert envio_recargado.repartidor_id is None
    # El mismo productor_id ya resuelto para decidir la transición queda guardado — no se
    # descarta, para que GET /envios/pendientes-entrega-directa lo pueda filtrar server-side.
    assert str(envio_recargado.productor_id) == mismo_productor


def test_proponer_envio_sin_capacidad_suficiente_y_varios_productores_sigue_pendiente_asignacion(monkeypatch, matching):
    repartidor_ids, envio_ids = matching
    insuficiente = _crear_repartidor_disponible(capacidad_maxima_kg=10)  # insuficiente para los 50 kg de abajo
    envio = _crear_envio_directo(None, estado="pendiente")
    repartidor_ids.append(insuficiente.id)
    envio_ids.append(envio.id)

    producto_a, producto_b = str(uuid.uuid4()), str(uuid.uuid4())
    pedido = _pedido_falso(
        peso_total_kg=50,
        requiere_vehiculo_grande=True,
        items=[{"producto_id": producto_a}, {"producto_id": producto_b}],
    )
    # Dos productores DISTINTOS — no se puede resolver un único productor.
    _mockear_pedido(monkeypatch, pedido, productor_por_producto={producto_a: str(uuid.uuid4()), producto_b: str(uuid.uuid4())})

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    # Comportamiento actual: NO se inventa un estado nuevo cuando no se puede confirmar un solo
    # productor — el hilo de reintento de huérfanos ya existente lo sigue intentando después.
    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "pendiente_asignacion"
    # Había un candidato disponible (insuficiente), pero ninguno con capacidad suficiente — no
    # "nadie disponible en absoluto".
    assert envio_recargado.motivo_pendiente == "sin_capacidad_suficiente"


def test_proponer_envio_con_peso_desconocido_no_aplica_filtro_de_capacidad_regresion(monkeypatch, matching):
    """Regresión: un repartidor SIN capacidad_maxima_kg declarada (legado, previo a esta
    sub-entrega) sigue siendo un candidato válido cuando el peso del pedido es desconocido — el
    comportamiento debe ser idéntico al de antes de este cambio, sin ningún filtro."""
    repartidor_ids, envio_ids = matching
    sin_capacidad = _crear_repartidor_disponible(capacidad_maxima_kg=None)
    envio = _crear_envio_directo(None, estado="pendiente")
    repartidor_ids.append(sin_capacidad.id)
    envio_ids.append(envio.id)

    _mockear_pedido(monkeypatch, _pedido_falso(peso_total_kg=None))

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "propuesto"
    assert str(envio_recargado.repartidor_id) == str(sin_capacidad.id)


def test_proponer_envio_con_pedidos_inalcanzable_degrada_a_peso_desconocido(monkeypatch, matching):
    """Si Pedidos no responde, no se bloquea la asignación — se comporta como si el peso fuera
    desconocido (mismo criterio que peso_total_kg null)."""
    repartidor_ids, envio_ids = matching
    disponible = _crear_repartidor_disponible(capacidad_maxima_kg=None)
    envio = _crear_envio_directo(None, estado="pendiente")
    repartidor_ids.append(disponible.id)
    envio_ids.append(envio.id)

    _mockear_pedido(monkeypatch, pedido=None, pedidos_no_disponible=True)

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "propuesto"
    assert str(envio_recargado.repartidor_id) == str(disponible.id)


# ============ motivo_pendiente + GET /envios/pendientes-por-capacidad ============

def test_motivo_pendiente_sin_repartidor_disponible_cuando_nadie_esta_disponible(monkeypatch, matching):
    """Cero repartidores disponibles, aunque el peso SÍ se conoce — la ausencia total de
    candidatos es "sin_repartidor_disponible", no "sin_capacidad_suficiente" (que implicaría que
    había alguien, solo que no alcanzaba)."""
    _, envio_ids = matching
    envio = _crear_envio_directo(None, estado="pendiente")
    envio_ids.append(envio.id)

    _mockear_pedido(monkeypatch, _pedido_falso(peso_total_kg=50))

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "pendiente_asignacion"
    assert envio_recargado.motivo_pendiente == "sin_repartidor_disponible"


def test_motivo_pendiente_se_limpia_al_asignar_exitosamente(monkeypatch, matching):
    repartidor_ids, envio_ids = matching
    suficiente = _crear_repartidor_disponible(capacidad_maxima_kg=100)
    # Simula un intento previo fallido: el envío ya venía con un motivo_pendiente asignado.
    envio = _crear_envio_directo(None, estado="pendiente_asignacion", motivo_pendiente="sin_repartidor_disponible")
    repartidor_ids.append(suficiente.id)
    envio_ids.append(envio.id)

    _mockear_pedido(monkeypatch, _pedido_falso(peso_total_kg=50))

    db = main.SessionLocal()
    try:
        logica_repartidores.proponer_envio_a_repartidor(envio.id, db)
    finally:
        db.close()

    envio_recargado = _envio_recargado(envio.id)
    assert envio_recargado.estado == "propuesto"
    assert str(envio_recargado.repartidor_id) == str(suficiente.id)
    assert envio_recargado.motivo_pendiente is None


def test_envios_pendientes_por_capacidad_filtra_correctamente():
    sin_capacidad = _crear_envio_directo(None, estado="pendiente_asignacion", motivo_pendiente="sin_capacidad_suficiente")
    _crear_envio_directo(None, estado="pendiente_asignacion", motivo_pendiente="sin_repartidor_disponible")
    _crear_envio_directo(None, estado="propuesto", motivo_pendiente=None)

    _auth(str(uuid.uuid4()), rol="admin")
    resp = client.get("/envios/pendientes-por-capacidad")

    assert resp.status_code == 200, resp.text
    ids = {e["id"] for e in resp.json()}
    assert str(sin_capacidad.id) in ids
    for e in resp.json():
        assert e["motivo_pendiente"] == "sin_capacidad_suficiente"
        assert e["estado"] == "pendiente_asignacion"


# ============ POST /repartidores: tipo_vehiculo / capacidad_maxima_kg ============

def _payload_repartidor(**overrides):
    datos = {
        "nombre": "Repartidor Test",
        "dni": "12345678",
        "tipo_vehiculo": "moto",
        "capacidad_maxima_kg": 30.0,
    }
    datos.update(overrides)
    return datos


def test_crear_repartidor_con_vehiculo_y_capacidad_validos_funciona():
    _auth(str(uuid.uuid4()))
    resp = client.post("/repartidores", json=_payload_repartidor())

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["tipo_vehiculo"] == "moto"
    assert float(cuerpo["capacidad_maxima_kg"]) == 30.0


def test_crear_repartidor_con_tipo_vehiculo_invalido_falla_422():
    _auth(str(uuid.uuid4()))
    resp = client.post("/repartidores", json=_payload_repartidor(tipo_vehiculo="bicicleta"))

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "El tipo de vehículo debe ser uno de: moto, auto, camioneta, camion"


def test_crear_repartidor_con_capacidad_cero_falla_422():
    _auth(str(uuid.uuid4()))
    resp = client.post("/repartidores", json=_payload_repartidor(capacidad_maxima_kg=0))

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "La capacidad máxima debe ser mayor a 0 kg"


def test_crear_repartidor_con_capacidad_negativa_falla_422():
    _auth(str(uuid.uuid4()))
    resp = client.post("/repartidores", json=_payload_repartidor(capacidad_maxima_kg=-5))

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "La capacidad máxima debe ser mayor a 0 kg"


# ============ Entrega directa: GET /envios/pendientes-entrega-directa y PATCH por productor ============

class _ClienteProductoresFalso:
    """Reemplaza httpx.Client() dentro de main: GET /productores/me devuelve un productor fijo,
    sin tocar la red — mismo espíritu que _ClienteRepartidoresFalso, pero para el único call
    site de Transporte hacia Productores."""
    def __init__(self, productor_id, status_code=200):
        self._productor_id = productor_id
        self._status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return _RespuestaFalsa(self._status_code, {"id": self._productor_id})


def _mockear_productor_autenticado(monkeypatch, productor_id, status_code=200):
    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _ClienteProductoresFalso(productor_id, status_code))


def test_envios_pendientes_entrega_directa_filtra_por_productor_correcto(monkeypatch):
    productor_a, productor_b = str(uuid.uuid4()), str(uuid.uuid4())
    envio_de_a = _crear_envio_directo(None, estado="pendiente_entrega_directa", productor_id=productor_a)
    _crear_envio_directo(None, estado="pendiente_entrega_directa", productor_id=productor_b)
    # Mismo productor A, pero en otro estado — no debe aparecer aunque el productor coincida.
    _crear_envio_directo(None, estado="propuesto", productor_id=productor_a)

    _mockear_productor_autenticado(monkeypatch, productor_a)
    _auth(str(uuid.uuid4()), rol="productor")
    resp = client.get("/envios/pendientes-entrega-directa")

    assert resp.status_code == 200, resp.text
    ids = {e["id"] for e in resp.json()}
    assert ids == {str(envio_de_a.id)}


class _ClienteEntregaDirectaFalso:
    """httpx.Client es el MISMO objeto de módulo compartido por TODO archivo que hace
    `import httpx` (main.py, logica_certificacion.py, etc. — Python cachea el módulo una sola
    vez en sys.modules) — mockearlo dos veces con dos propósitos distintos en un solo test (uno
    para /productores/me, otro para /certificacion/...) hace que el segundo pise al primero. Este
    cliente combinado responde según la URL, cubriendo TODAS las llamadas salientes de la
    request completa "productor marca entregado": la propia (GET /productores/me) y la que
    dispara notificar_entrega_a_certificacion (GET /pedidos/{id}, POST a Certificación)."""
    def __init__(self, productor_id, producto_id=None):
        self._productor_id = productor_id
        self._producto_id = producto_id or str(uuid.uuid4())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        if "/productores/me" in url:
            return _RespuestaFalsa(200, {"id": self._productor_id})
        return _RespuestaFalsa(200, {"items": [{"producto_id": self._producto_id}]})

    def post(self, url, **kwargs):
        return _RespuestaFalsa(200, {"mensaje": "ok"})


def test_actualizar_estado_productor_marca_entregado_si_le_corresponde(monkeypatch):
    productor_id = str(uuid.uuid4())
    envio = _crear_envio_directo(None, estado="pendiente_entrega_directa", productor_id=productor_id)

    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _ClienteEntregaDirectaFalso(productor_id))
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    _auth(str(uuid.uuid4()), rol="productor")
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "entregado"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "entregado"


def test_actualizar_estado_productor_sobre_envio_de_otro_productor_falla_403(monkeypatch):
    envio = _crear_envio_directo(None, estado="pendiente_entrega_directa", productor_id=str(uuid.uuid4()))

    _mockear_productor_autenticado(monkeypatch, str(uuid.uuid4()))  # un productor DISTINTO
    _auth(str(uuid.uuid4()), rol="productor")
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "entregado"})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "No puedes actualizar un envío que no te corresponde"


def test_actualizar_estado_productor_sobre_envio_no_pendiente_entrega_directa_falla_409(monkeypatch):
    productor_id = str(uuid.uuid4())
    envio = _crear_envio_directo(None, estado="propuesto", productor_id=productor_id)

    _mockear_productor_autenticado(monkeypatch, productor_id)
    _auth(str(uuid.uuid4()), rol="productor")
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "entregado"})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "Este envío no está pendiente de entrega directa"


def test_actualizar_estado_productor_con_estado_distinto_de_entregado_falla_403(monkeypatch):
    productor_id = str(uuid.uuid4())
    envio = _crear_envio_directo(None, estado="pendiente_entrega_directa", productor_id=productor_id)

    _mockear_productor_autenticado(monkeypatch, productor_id)
    _auth(str(uuid.uuid4()), rol="productor")
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "en_camino"})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Un productor solo puede marcar un envío como entregado"


def test_actualizar_estado_repartidor_sigue_funcionando_igual_regresion(monkeypatch):
    """El repartidor no queda sujeto a la whitelist nueva del productor — puede seguir
    transicionando a estados intermedios como "en_camino", exactamente igual que antes."""
    usuario_id = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_id)
    envio = _crear_envio_directo(repartidor.id, estado="asignado")

    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    _auth(usuario_id)  # rol="repartidor" por defecto
    resp = client.patch(f"/envios/{envio.id}/estado", json={"estado": "en_camino"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "en_camino"


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/envios/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
        ("DELETE", f"/repartidores/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
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



# ============ Privacidad: quién ve un envío, el PIN de entrega y el tope de intentos ============

class _ClientePedidosFalso:
    """Reemplaza httpx.Client() para las llamadas a Pedidos: guarda cada (url, kwargs) para poder
    comprobar que se usó la ruta interna con el secreto, y responde un pedido fijo."""
    def __init__(self, pedido, llamadas, status=200, roto=False):
        self._pedido, self._llamadas, self._status, self._roto = pedido, llamadas, status, roto

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self._llamadas.append((url, kwargs))
        if self._roto:
            raise main.httpx.RequestError("boom", request=None)
        return _RespuestaFalsa(self._status, self._pedido)


def _pedido_de(comprador_sub, **extra):
    return {"id": str(uuid.uuid4()), "usuario_id": str(comprador_sub), "destino_latitud": "-13.16",
            "destino_longitud": "-74.22", **extra}


def _mockear_pedidos(monkeypatch, pedido, status=200, roto=False):
    llamadas = []
    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _ClientePedidosFalso(pedido, llamadas, status, roto))
    return llamadas


def _sin_sesion():
    main.app.dependency_overrides.pop(verificar_token, None)
    main.app.dependency_overrides.pop(security, None)


def test_un_envio_no_se_puede_leer_sin_sesion():
    envio = _crear_envio_directo(_crear_repartidor_directo().id, estado="en_camino", pin_entrega="7391")
    _sin_sesion()
    try:
        for ruta in (f"/envios/{envio.pedido_id}", f"/envios/{envio.id}/ruta"):
            resp = client.get(ruta)
            assert resp.status_code in (401, 403), (ruta, resp.status_code)
            assert "7391" not in resp.text
    finally:
        _auth(str(uuid.uuid4()))


def test_el_comprador_dueno_ve_su_envio_con_el_pin(monkeypatch):
    comprador = str(uuid.uuid4())
    envio = _crear_envio_directo(_crear_repartidor_directo().id, estado="en_camino", pin_entrega="7391")
    _mockear_pedidos(monkeypatch, _pedido_de(comprador))

    _auth(comprador, "comprador")
    resp = client.get(f"/envios/{envio.pedido_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["pin_entrega"] == "7391"
    assert resp.json()["id"] == str(envio.id)


def test_un_comprador_ajeno_no_ve_el_envio_ni_su_pin_ni_su_ruta(monkeypatch):
    envio = _crear_envio_directo(_crear_repartidor_directo().id, estado="en_camino", pin_entrega="7391")
    _mockear_pedidos(monkeypatch, _pedido_de(uuid.uuid4()))  # el pedido es de otro comprador

    _auth(str(uuid.uuid4()), "comprador")
    for ruta in (f"/envios/{envio.pedido_id}", f"/envios/{envio.id}/ruta"):
        resp = client.get(ruta)
        assert resp.status_code == 403, (ruta, resp.text)
        assert "7391" not in resp.text


def test_el_repartidor_asignado_ve_el_envio_pero_sin_el_pin():
    """El PIN es la prueba de que el comprador recibió el pedido: si el repartidor lo leyera, podría
    marcar la entrega sin verlo y cobrar la liquidación."""
    usuario = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario)
    envio = _crear_envio_directo(repartidor.id, estado="en_camino", pin_entrega="7391")

    _auth(usuario)
    resp = client.get(f"/envios/{envio.pedido_id}")

    assert resp.status_code == 200, resp.text
    assert "pin_entrega" not in resp.json()
    assert "7391" not in resp.text


def test_otro_repartidor_no_ve_un_envio_que_no_es_suyo():
    dueno = _crear_repartidor_directo(usuario_id=str(uuid.uuid4()))
    envio = _crear_envio_directo(dueno.id, estado="en_camino", pin_entrega="7391")
    otro = str(uuid.uuid4())
    _crear_repartidor_directo(usuario_id=otro)

    _auth(otro)
    for ruta in (f"/envios/{envio.pedido_id}", f"/envios/{envio.id}/ruta"):
        resp = client.get(ruta)
        assert resp.status_code == 403, (ruta, resp.text)
        assert "7391" not in resp.text


def test_el_admin_ve_el_envio_con_el_pin_y_un_productor_no_lo_ve():
    envio = _crear_envio_directo(_crear_repartidor_directo().id, estado="en_camino", pin_entrega="7391")

    _auth(str(uuid.uuid4()), "admin")
    admin = client.get(f"/envios/{envio.pedido_id}")
    assert admin.status_code == 200 and admin.json()["pin_entrega"] == "7391"

    _auth(str(uuid.uuid4()), "productor")
    productor = client.get(f"/envios/{envio.pedido_id}")
    assert productor.status_code == 403 and "7391" not in productor.text


def test_la_ruta_del_envio_solo_la_ven_el_comprador_dueno_y_el_repartidor_asignado(monkeypatch):
    comprador, usuario_repartidor = str(uuid.uuid4()), str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario_repartidor)
    envio = _crear_envio_directo(repartidor.id, estado="en_camino", pin_entrega="7391")
    _mockear_pedidos(monkeypatch, _pedido_de(comprador))

    _auth(comprador, "comprador")
    duenio = client.get(f"/envios/{envio.id}/ruta")
    assert duenio.status_code == 200, duenio.text
    assert "7391" not in duenio.text

    _auth(usuario_repartidor)
    assert client.get(f"/envios/{envio.id}/ruta").status_code == 200


def test_transporte_le_pide_el_pedido_a_pedidos_por_la_ruta_interna_con_el_secreto(monkeypatch):
    import pedidos_interno
    monkeypatch.setattr(pedidos_interno, "TRANSPORTE_A_PEDIDOS_SECRETO", "secreto-de-prueba-pedidos")
    comprador = str(uuid.uuid4())
    envio = _crear_envio_directo(_crear_repartidor_directo().id, estado="en_camino")
    llamadas = _mockear_pedidos(monkeypatch, _pedido_de(comprador))

    _auth(comprador, "comprador")
    assert client.get(f"/envios/{envio.pedido_id}").status_code == 200

    url, kwargs = llamadas[0]
    assert url.endswith(f"/pedidos/interno/{envio.pedido_id}")
    assert kwargs["headers"] == {"X-Servicio-Secreto": "secreto-de-prueba-pedidos"}


def test_los_procesos_internos_tambien_usan_la_ruta_interna_de_pedidos(monkeypatch):
    """Proponer un envío, liquidar al repartidor y certificar la entrega no tienen a un usuario detrás:
    si siguieran llamando a GET /pedidos/{id} (que ahora exige sesión), dejarían de funcionar."""
    import pedidos_interno
    monkeypatch.setattr(pedidos_interno, "TRANSPORTE_A_PEDIDOS_SECRETO", "secreto-de-prueba-pedidos")
    llamadas = []

    class _Captura:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            llamadas.append((url, kwargs))
            return _RespuestaFalsa(200, {"costo_envio": 5.0, "items": []})

        def post(self, url, **kwargs):
            return _RespuestaFalsa(200, {"mensaje": "ok"})

    monkeypatch.setattr(main.httpx, "Client", lambda *a, **kw: _Captura())
    repartidor = _crear_repartidor_directo()
    envio = _crear_envio_directo(repartidor.id, estado="entregado")

    db = main.SessionLocal()
    try:
        logica_repartidores._obtener_pedido(str(envio.pedido_id))
        logica_certificacion.notificar_entrega_a_certificacion(db, _envio_recargado(envio.id))
        logica_liquidacion_repartidor.notificar_liquidacion_repartidor(db, _envio_recargado(envio.id))
    finally:
        db.close()

    assert len(llamadas) >= 3
    for url, kwargs in llamadas:
        if "/pedidos/" in url:
            assert "/pedidos/interno/" in url, url
            assert kwargs["headers"] == {"X-Servicio-Secreto": "secreto-de-prueba-pedidos"}


def test_si_pedidos_no_responde_ver_un_envio_da_503_no_un_error_del_servidor(monkeypatch):
    comprador = str(uuid.uuid4())
    envio = _crear_envio_directo(_crear_repartidor_directo().id, estado="en_camino")
    _mockear_pedidos(monkeypatch, None, roto=True)

    _auth(comprador, "comprador")
    assert client.get(f"/envios/{envio.pedido_id}").status_code == 503


def test_un_id_de_pedido_o_envio_mal_formado_falla_422(monkeypatch):
    _auth(str(uuid.uuid4()), "admin")
    assert client.get("/envios/no-es-un-uuid").status_code == 422
    assert client.get("/envios/no-es-un-uuid/ruta").status_code == 422


def test_un_repartidor_solo_lista_sus_propios_envios_y_sin_pin():
    usuario = str(uuid.uuid4())
    mio = _crear_envio_directo(_crear_repartidor_directo(usuario_id=usuario).id, estado="en_camino", pin_entrega="7391")
    ajeno = _crear_envio_directo(_crear_repartidor_directo(usuario_id=str(uuid.uuid4())).id, estado="en_camino", pin_entrega="5150")

    _auth(usuario)
    resp = client.get("/envios")

    assert resp.status_code == 200, resp.text
    ids = [e["id"] for e in resp.json()]
    assert ids == [str(mio.id)]
    assert str(ajeno.id) not in ids
    assert "7391" not in resp.text and "5150" not in resp.text
    assert all("pin_entrega" not in e for e in resp.json())


def test_un_usuario_repartidor_sin_perfil_lista_cero_envios():
    _auth(str(uuid.uuid4()))
    resp = client.get("/envios")
    assert resp.status_code == 200 and resp.json() == []


def test_las_respuestas_que_devuelven_un_envio_no_llevan_el_pin(monkeypatch):
    usuario = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario)
    propuesto = _crear_envio_directo(repartidor.id, estado="propuesto", pin_entrega="7391")
    monkeypatch.setattr(main, "publicar_evento", MagicMock())

    _auth(usuario)
    propuestas = client.get("/envios/propuestas")
    assert propuestas.status_code == 200 and propuestas.text.count("pin_entrega") == 0

    aceptado = client.post(f"/envios/{propuesto.id}/aceptar")
    assert aceptado.status_code == 200, aceptado.text
    assert "pin_entrega" not in aceptado.json() and "7391" not in aceptado.text

    ubicacion = client.patch(f"/envios/{propuesto.id}/ubicacion", json={"latitud": "-13.16", "longitud": "-74.22"})
    assert ubicacion.status_code == 200, ubicacion.text
    assert "pin_entrega" not in ubicacion.json()


# ---- tope de intentos con el PIN

def _preparar_entrega_con_pin(monkeypatch, pin="7391"):
    usuario = str(uuid.uuid4())
    repartidor = _crear_repartidor_directo(usuario_id=usuario)
    envio = _crear_envio_directo(repartidor.id, estado="en_camino", pin_entrega=pin)
    _mockear_pagos_ok(monkeypatch)
    _mockear_certificacion_ok(monkeypatch)
    monkeypatch.setattr(main, "publicar_evento", MagicMock())
    monkeypatch.setattr(main, "subir_foto_entrega", lambda *a, **kw: "http://fake/foto-entrega.jpg")
    _auth(usuario)
    return envio


def test_tras_cinco_codigos_incorrectos_el_envio_se_bloquea_aunque_el_sexto_sea_el_bueno(monkeypatch):
    """Sin tope, un repartidor podía probar los 10.000 códigos y marcar la entrega sin ver al comprador."""
    envio = _preparar_entrega_con_pin(monkeypatch)

    for _ in range(main.MAX_INTENTOS_PIN):
        assert _confirmar_entrega(envio.id, pin="0000").status_code == 400

    bloqueado = _confirmar_entrega(envio.id, pin="7391")  # el código correcto, pero ya está bloqueado
    assert bloqueado.status_code == 429, bloqueado.text
    assert "minuto" in bloqueado.json()["detail"]
    recargado = _envio_recargado(envio.id)
    assert recargado.estado == "en_camino"
    assert recargado.pin_bloqueado_hasta is not None


def test_el_bloqueo_del_pin_se_levanta_solo_y_una_entrega_correcta_reinicia_el_contador(monkeypatch):
    envio = _preparar_entrega_con_pin(monkeypatch)
    for _ in range(main.MAX_INTENTOS_PIN):
        _confirmar_entrega(envio.id, pin="0000")

    # pasan los minutos de bloqueo
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.Envio).filter(main.models.Envio.id == envio.id).first()
        fila.pin_bloqueado_hasta = main.datetime.utcnow() - main.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    resp = _confirmar_entrega(envio.id, pin="7391")
    assert resp.status_code == 200, resp.text
    recargado = _envio_recargado(envio.id)
    assert recargado.estado == "entregado"
    assert recargado.pin_intentos_fallidos == 0
    assert recargado.pin_bloqueado_hasta is None


def test_menos_de_cinco_fallos_no_bloquean_y_el_codigo_correcto_sigue_funcionando(monkeypatch):
    envio = _preparar_entrega_con_pin(monkeypatch)
    for _ in range(main.MAX_INTENTOS_PIN - 1):
        assert _confirmar_entrega(envio.id, pin="0000").status_code == 400

    assert _envio_recargado(envio.id).pin_bloqueado_hasta is None
    assert _confirmar_entrega(envio.id, pin="7391").status_code == 200
