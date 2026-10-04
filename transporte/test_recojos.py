"""Recojo de cosechas en la chacra (logica_recojos.py y las rutas /recojos de main.py). Productos, Pedidos y Pagos se
reemplazan por funciones falsas; la base es la Postgres de pruebas (no la del clúster)."""
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import main
import models
import logica_recojos
import rabbitmq_consumer
from auth import verificar_token, security

client = TestClient(main.app)


def _como(sub, rol, productor_id=None):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}
    main.app.dependency_overrides[security] = lambda: HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")


@pytest.fixture
def mundo(monkeypatch):
    """Un productor con una cosecha de 400 kg en tránsito y Pagos que anota lo que se liquida. Los repartidores de otros
    tests quedan desconectados para que la elección sea predecible."""
    db = main.SessionLocal()
    db.query(models.Repartidor).update({models.Repartidor.estado_disponibilidad: "desconectado"})
    db.commit()
    db.close()
    productor_id, producto_id = str(uuid.uuid4()), str(uuid.uuid4())
    producto = {"id": producto_id, "productor_id": productor_id, "nombre": "Papa amarilla QA", "estado": "en_transito",
                "stock": 400, "unidad_medida": "kg"}
    monkeypatch.setattr(main, "_resolver_productor_autenticado", lambda token: {"id": productor_id})
    monkeypatch.setattr(logica_recojos, "pedir_producto", lambda pid: dict(producto, id=str(pid)) if str(pid) == producto_id else {**producto, "productor_id": str(uuid.uuid4())})
    monkeypatch.setattr(logica_recojos, "cotizar", lambda lat, lng, peso: {"distancia_km": 6.5, "peso_kg": peso, "tarifa_distancia": 5.0, "recargo_peso": 37.5, "total": 42.5})
    liquidados = []
    monkeypatch.setattr(logica_recojos, "liquidar", lambda recojo: liquidados.append((str(recojo.id), float(recojo.costo))))
    monkeypatch.setattr(main, "intentar_resolver_envio_huerfano", MagicMock())
    return {"productor_id": productor_id, "producto_id": producto_id, "producto": producto, "liquidados": liquidados}


def _repartidor(capacidad, estado="disponible"):
    db = main.SessionLocal()
    try:
        r = models.Repartidor(usuario_id=uuid.uuid4(), nombre=f"QA rep {capacidad}", dni="00000001", tipo_vehiculo="camioneta",
                              capacidad_maxima_kg=capacidad, estado_disponibilidad=estado)
        db.add(r)
        db.commit()
        db.refresh(r)
        return r
    finally:
        db.close()


def _pedir(mundo, **extra):
    _como(str(uuid.uuid4()), "productor")
    cuerpo = {"producto_id": mundo["producto_id"], "modalidad": "repartidor", "latitud": -13.10, "longitud": -74.25,
              "referencia": "Junto al puente", "telefono": "987 654 321", **extra}
    return client.post("/recojos", json=cuerpo)


def _fila(recojo_id):
    db = main.SessionLocal()
    try:
        return db.query(models.Recojo).filter(models.Recojo.id == recojo_id).one()
    finally:
        db.close()


def _disponibilidad(repartidor_id):
    db = main.SessionLocal()
    try:
        return db.query(models.Repartidor).filter(models.Repartidor.id == repartidor_id).one().estado_disponibilidad
    finally:
        db.close()


def _como_repartidor(repartidor):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(repartidor.usuario_id), "rol": "repartidor"}


def _llega_al_almacen(producto_id):
    canal = MagicMock()
    rabbitmq_consumer._procesar_mensaje(canal, MagicMock(), None, json.dumps({"evento": "llegada_almacen", "producto_id": producto_id}).encode())
    canal.basic_ack.assert_called_once()


def test_el_recojo_se_propone_al_repartidor_mas_chico_que_carga_el_peso(mundo):
    _repartidor(200)  # no carga 400 kg
    justo = _repartidor(500)
    _repartidor(3000)

    resp = _pedir(mundo)

    assert resp.status_code == 200, resp.text
    recojo = resp.json()
    assert (recojo["estado"], recojo["costo"], recojo["peso_kg"]) == ("propuesto", 42.5, 400.0)
    assert recojo["repartidor"]["id"] == str(justo.id) and _disponibilidad(justo.id) == "ocupado"
    assert _fila(recojo["id"]).telefono == "987654321"
    assert [r["id"] for r in client.get("/recojos/mios").json()] == [recojo["id"]]
    assert _pedir(mundo).status_code == 409  # una cosecha, un recojo en curso


def test_de_punta_a_punta_se_cobra_recien_con_la_cosecha_pesada(mundo):
    repartidor = _repartidor(1000)
    recojo_id = _pedir(mundo).json()["id"]
    _como_repartidor(repartidor)

    assert [r["id"] for r in client.get("/recojos/propuestas").json()] == [recojo_id]
    assert client.post(f"/recojos/{recojo_id}/recogido").status_code == 409  # primero se acepta
    assert client.post(f"/recojos/{recojo_id}/aceptar").json()["estado"] == "asignado"
    assert client.post(f"/recojos/{recojo_id}/recogido").json()["estado"] == "recogido"
    assert client.post(f"/recojos/{recojo_id}/en-almacen").json()["estado"] == "en_almacen"
    assert _disponibilidad(repartidor.id) == "disponible"
    assert mundo["liquidados"] == []  # todavía nadie pesó la cosecha

    _llega_al_almacen(mundo["producto_id"])
    _llega_al_almacen(mundo["producto_id"])  # un segundo aviso no cobra dos veces
    fila = _fila(recojo_id)
    assert (fila.estado, fila.liquidacion_confirmada) == ("completado", True)
    assert mundo["liquidados"] == [(recojo_id, 42.5)]


def test_sin_repartidor_con_capacidad_espera_y_el_job_lo_asigna_despues(mundo):
    _repartidor(100)
    recojo_id = _pedir(mundo).json()["id"]
    assert _fila(recojo_id).estado == "pendiente_asignacion"

    nuevo = _repartidor(800)
    db = main.SessionLocal()
    try:
        logica_recojos.asignar_recojos_pendientes(db)
    finally:
        db.close()
    fila = _fila(recojo_id)
    assert (fila.estado, str(fila.repartidor_id)) == ("propuesto", str(nuevo.id))


def test_un_rechazo_pasa_al_siguiente_y_no_vuelve_a_quien_rechazo(mundo):
    primero = _repartidor(450)
    segundo = _repartidor(900)
    recojo_id = _pedir(mundo).json()["id"]
    assert str(_fila(recojo_id).repartidor_id) == str(primero.id)

    _como_repartidor(primero)
    assert client.post(f"/recojos/{recojo_id}/rechazar").status_code == 200
    fila = _fila(recojo_id)
    assert (fila.estado, str(fila.repartidor_id)) == ("propuesto", str(segundo.id))
    assert _disponibilidad(primero.id) == "disponible"

    _como_repartidor(segundo)
    client.post(f"/recojos/{recojo_id}/rechazar")
    assert _fila(recojo_id).estado == "pendiente_asignacion"  # el primero ya lo rechazó: no se le vuelve a ofrecer


def test_el_agricultor_anula_antes_de_que_lo_recojan_y_libera_al_repartidor(mundo):
    repartidor = _repartidor(1000)
    recojo_id = _pedir(mundo).json()["id"]
    _como(str(uuid.uuid4()), "productor")
    assert client.post(f"/recojos/{recojo_id}/cancelar").json()["estado"] == "cancelado"
    assert _disponibilidad(repartidor.id) == "disponible"

    otro = _pedir(mundo).json()["id"]  # anulado, ya puede pedir otro
    _como_repartidor(repartidor)
    client.post(f"/recojos/{otro}/aceptar")
    client.post(f"/recojos/{otro}/recogido")
    _como(str(uuid.uuid4()), "productor")
    assert client.post(f"/recojos/{otro}/cancelar").status_code == 409


def test_si_la_cosecha_llega_antes_de_recogerla_el_recojo_se_anula_sin_cobrar(mundo):
    repartidor = _repartidor(1000)
    recojo_id = _pedir(mundo).json()["id"]  # propuesto, nadie la recogió

    _llega_al_almacen(mundo["producto_id"])

    fila = _fila(recojo_id)
    assert fila.estado == "cancelado" and "antes de que la recogieran" in fila.motivo_cancelacion
    assert _disponibilidad(repartidor.id) == "disponible" and mundo["liquidados"] == []


def test_si_la_lleva_el_agricultor_dice_cuando_y_no_cuesta_nada(mundo):
    _como(str(uuid.uuid4()), "productor")
    sin_fecha = client.post("/recojos", json={"producto_id": mundo["producto_id"], "modalidad": "productor"})
    assert sin_fecha.status_code == 422
    manana = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat()
    resp = client.post("/recojos", json={"producto_id": mundo["producto_id"], "modalidad": "productor", "fecha_estimada": manana})
    assert resp.status_code == 200 and resp.json()["estado"] == "en_camino" and resp.json()["costo"] is None

    _llega_al_almacen(mundo["producto_id"])
    assert _fila(resp.json()["id"]).estado == "completado" and mundo["liquidados"] == []


def test_solo_el_dueno_y_solo_con_la_cosecha_en_transito(mundo, monkeypatch):
    _como(str(uuid.uuid4()), "productor")
    ajeno = client.post("/recojos", json={"producto_id": str(uuid.uuid4()), "modalidad": "productor",
                                          "fecha_estimada": datetime.utcnow().isoformat()})
    assert ajeno.status_code == 403
    monkeypatch.setattr(logica_recojos, "pedir_producto", lambda pid: {**mundo["producto"], "estado": "disponible"})
    assert _pedir(mundo).status_code == 409
    assert _pedir(mundo, telefono="123").status_code in (409, 422)
