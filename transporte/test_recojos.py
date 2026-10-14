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
    # La base de pruebas se reusa entre corridas: un recojo que quedó en curso se llevaría a los repartidores de este test.
    db.query(models.Recojo).filter(models.Recojo.estado.in_(logica_recojos.ESTADOS_EN_CURSO)).update(
        {models.Recojo.estado: "cancelado"}, synchronize_session=False)
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
    avisos = []
    monkeypatch.setattr(logica_recojos, "publicar_evento", avisos.append)
    return {"productor_id": productor_id, "producto_id": producto_id, "producto": producto, "liquidados": liquidados, "avisos": avisos}


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


AGRICULTOR = str(uuid.uuid4())  # el usuario del agricultor: a él le llegan los avisos


def _pedir(mundo, **extra):
    _como(AGRICULTOR, "productor")
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
    # el agricultor se enteró de cada paso (no de la propuesta: eso es entre el sistema y el repartidor)
    assert [a["estado"] for a in mundo["avisos"]] == ["asignado", "recogido", "en_almacen", "completado"]
    assert {a["usuario_id"] for a in mundo["avisos"]} == {AGRICULTOR}
    assert mundo["avisos"][0] == {"evento": "recojo_actualizado", "recojo_id": recojo_id, "producto_id": mundo["producto_id"],
                                  "usuario_id": AGRICULTOR, "estado": "asignado", "modalidad": "repartidor",
                                  "producto_nombre": "Papa amarilla QA", "repartidor_nombre": "QA rep 1000", "costo": 42.5,
                                  "contratado": False, "transportista_telefono": None, "transportista_placa": None,
                                  "fecha_programada": None}


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
    assert [a["estado"] for a in mundo["avisos"]] == ["cancelado"]


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



def test_un_recojo_sin_respuesta_pasa_al_siguiente_repartidor(mundo):
    callado = _repartidor(450)
    otro = _repartidor(900)
    recojo_id = _pedir(mundo).json()["id"]
    assert str(_fila(recojo_id).repartidor_id) == str(callado.id)
    assert client.get("/recojos/mios").json()[0]["responder_antes_de"] is not None

    db = main.SessionLocal()
    try:
        logica_recojos.pasar_recojos_sin_respuesta(db, ahora=datetime.utcnow() + timedelta(minutes=logica_recojos.PLAZO_RESPUESTA_MINUTOS + 1))
    finally:
        db.close()
    fila = _fila(recojo_id)
    assert (fila.estado, str(fila.repartidor_id)) == ("propuesto", str(otro.id))
    assert _disponibilidad(callado.id) == "disponible"


# ============ H8 (validación con usuarios del 2026-10-13): transporte contratado por el admin ============

def _mas_pesado_que_todo_vehiculo(mundo, kg_por_saco=50):
    """La cosecha pesa más que cualquier vehículo registrado en la base de pruebas (que se reusa entre corridas), y se
    vende también por saco."""
    db = main.SessionLocal()
    try:
        maximo = max([float(c or 0) for (c,) in db.query(models.Repartidor.capacidad_maxima_kg).all()] + [0])
    finally:
        db.close()
    peso = int(maximo) + 1600
    mundo["producto"].update(stock=peso, productor_nombre="Agro Andina QA",
                             unidades_alternativas=[{"unidad": "saco", "factor_a_base": kg_por_saco}])
    return peso


def _camion(peso, **cambios):
    manana = (datetime.utcnow() + timedelta(days=1)).replace(hour=13, minute=30, second=0, microsecond=0)
    return {"nombre": "Juan Quispe", "telefono": "987 654 321", "placa": "abc-123", "capacidad_kg": peso + 1000,
            "capacidad_sacos": 2000, "fecha_programada": manana.isoformat(), "flete": 350, **cambios}


def test_sin_vehiculo_que_cargue_el_peso_el_agricultor_lo_sabe_y_se_avisa_a_los_admins(mundo):
    peso = _mas_pesado_que_todo_vehiculo(mundo)
    _como(AGRICULTOR, "productor")
    cotizacion = client.get(f"/recojos/cotizacion?producto_id={mundo['producto_id']}&latitud=-13.1&longitud=-74.25").json()
    assert (cotizacion["hay_vehiculo"], cotizacion["sacos"]) == (False, -(-peso // 50))

    recojo = _pedir(mundo).json()
    assert (recojo["estado"], recojo["sin_vehiculo_registrado"], recojo["sacos"]) == ("pendiente_asignacion", True, -(-peso // 50))
    [aviso] = [a for a in mundo["avisos"] if a["evento"] == "recojo_sin_vehiculo"]
    assert aviso == {"evento": "recojo_sin_vehiculo", "recojo_id": recojo["id"], "producto_id": mundo["producto_id"],
                     "producto_nombre": "Papa amarilla QA", "productor_nombre": "Agro Andina QA", "peso_kg": float(peso),
                     "sacos": -(-peso // 50), "distancia_km": 6.5}


def test_el_admin_contrata_un_camion_y_el_recojo_se_completa_al_pesarlo(mundo):
    peso = _mas_pesado_que_todo_vehiculo(mundo)
    recojo_id = _pedir(mundo).json()["id"]
    contratar = lambda **c: client.post(f"/recojos/{recojo_id}/transporte-contratado", json=_camion(peso, **c))

    _como(str(uuid.uuid4()), "productor")
    assert contratar().status_code == 403  # solo el admin
    _como(str(uuid.uuid4()), "admin")
    assert "no alcanza" in contratar(capacidad_kg=peso - 1).json()["detail"]
    assert "sacos" in contratar(capacidad_sacos=10).json()["detail"]
    assert contratar(telefono="12345").status_code == 422
    assert contratar(placa="??").status_code == 422
    assert contratar(fecha_programada=(datetime.utcnow() + timedelta(days=90)).isoformat()).status_code == 422

    resp = contratar()
    assert resp.status_code == 200, resp.text
    recojo = resp.json()
    assert (recojo["estado"], recojo["costo"], recojo["repartidor"]) == ("asignado", 350.0, None)
    assert recojo["transporte_contratado"]["placa"] == "ABC-123" and recojo["transporte_contratado"]["telefono"] == "987654321"
    assert contratar().status_code == 409  # ya tiene quién lo recoja
    aviso = mundo["avisos"][-1]
    assert (aviso["estado"], aviso["contratado"], aviso["repartidor_nombre"], aviso["transportista_placa"], aviso["costo"]) == (
        "asignado", True, "Juan Quispe", "ABC-123", 350.0)
    assert aviso["fecha_programada"].endswith("13:30:00")
    _como(AGRICULTOR, "productor")
    assert client.get("/recojos/mios").json()[0]["transporte_contratado"]["nombre"] == "Juan Quispe"

    _como(str(uuid.uuid4()), "admin")
    assert client.post(f"/recojos/{recojo_id}/recogido-contratado").json()["estado"] == "recogido"
    assert client.post(f"/recojos/{recojo_id}/recogido-contratado").status_code == 409
    _llega_al_almacen(mundo["producto_id"])
    fila = _fila(recojo_id)
    assert (fila.estado, fila.liquidacion_confirmada, fila.repartidor_id) == ("completado", True, None)
    assert mundo["liquidados"] == [(recojo_id, 350.0)]  # el flete acordado se le descuenta al agricultor
    assert [a["estado"] for a in mundo["avisos"] if a["evento"] == "recojo_actualizado"] == ["asignado", "recogido", "completado"]


def test_si_el_agricultor_anula_el_camion_contratado_se_avisa_a_los_admins(mundo):
    peso = _mas_pesado_que_todo_vehiculo(mundo)
    recojo_id = _pedir(mundo).json()["id"]
    _como(str(uuid.uuid4()), "admin")
    assert client.post(f"/recojos/{recojo_id}/transporte-contratado", json=_camion(peso)).status_code == 200

    _como(AGRICULTOR, "productor")
    assert client.post(f"/recojos/{recojo_id}/cancelar").json()["estado"] == "cancelado"
    [aviso] = [a for a in mundo["avisos"] if a["evento"] == "recojo_anulado_contratado"]
    assert (aviso["transportista_nombre"], aviso["transportista_telefono"]) == ("Juan Quispe", "987654321")


def test_un_recojo_ofrecido_a_un_repartidor_tambien_se_puede_contratar_y_lo_libera(mundo):
    repartidor = _repartidor(1000)
    _como(AGRICULTOR, "productor")
    assert client.get(f"/recojos/cotizacion?producto_id={mundo['producto_id']}&latitud=-13.1&longitud=-74.25").json()["hay_vehiculo"] is True
    recojo = _pedir(mundo).json()
    assert recojo["estado"] == "propuesto" and recojo["sin_vehiculo_registrado"] is False
    assert not [a for a in mundo["avisos"] if a["evento"] == "recojo_sin_vehiculo"]

    _como(str(uuid.uuid4()), "admin")
    assert client.post(f"/recojos/{recojo['id']}/transporte-contratado", json=_camion(400, capacidad_kg=500, capacidad_sacos=None)).status_code == 200
    assert _disponibilidad(repartidor.id) == "disponible" and _fila(recojo["id"]).repartidor_id is None


def test_pagos_recibe_el_recojo_contratado_sin_repartidor(monkeypatch):
    enviado = {}

    class ClienteFalso:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None, headers=None, timeout=None):
            enviado.update(json)
            return MagicMock(raise_for_status=lambda: None)

    monkeypatch.setattr(logica_recojos.httpx, "Client", ClienteFalso)
    recojo = models.Recojo(id=uuid.uuid4(), producto_id=uuid.uuid4(), productor_id=uuid.uuid4(), modalidad="repartidor",
                           estado="completado", costo=350, transportista_nombre="Juan Quispe")
    logica_recojos.liquidar(recojo)
    assert enviado["repartidor_id"] is None and enviado["costo"] == 350.0
