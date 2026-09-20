"""
Tests de POST /certificacion/{producto_id}/cosecha-aprobada — el endpoint interno que Productores
llama al aprobar una cosecha para dejar registrados cosecha_registrada y certificado_productor.

Cubre:
- sin secreto / con secreto incorrecto -> 403 (y falla cerrado si el secreto no está configurado)
- crea los dos bloques encadenados y la cadena resultante verifica como íntegra
- el bloque certificado_productor lleva el verificador cuando su usuario tiene perfil
- idempotente: repetir la llamada no duplica bloques
- completa solo lo que falta si ya existía cosecha_registrada
- la cadena no puede bifurcarse: un índice único por producto, reintento cuando otro proceso ocupó
  el índice y migración que lo agrega a una base ya creada
- POST /certificacion/{id}/eventos ya no existe: nadie puede agregar bloques a la cadena de un
  producto por la API pública (solo entran por los endpoints internos con secreto)

Requiere Postgres real (tipos UUID + create_all al importar main), igual que el resto de servicios.
"""
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import main
import logica_cadena
import migraciones
from logica_cadena import verificar_cadena

client = TestClient(main.app)
SECRETO = "secreto-de-prueba"


def _url(producto_id):
    return f"/certificacion/{producto_id}/cosecha-aprobada"


def _cuerpo(verificador_usuario_id=None):
    return {
        "datos_cosecha": "45.5 kg de Papa Nativa cosechados en chacra CH-01.",
        "datos_certificado": "Cosecha revisada y aprobada.",
        "verificador_usuario_id": verificador_usuario_id,
    }


def _con_secreto(monkeypatch):
    monkeypatch.setattr(main, "PRODUCTORES_A_CERTIFICACION_SECRETO", SECRETO)


def _bloques(producto_id):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Bloque).filter(
            main.models.Bloque.producto_id == producto_id
        ).order_by(main.models.Bloque.indice).all()
    finally:
        db.close()


def test_sin_secreto_falla_403(monkeypatch):
    _con_secreto(monkeypatch)
    resp = client.post(_url(str(uuid.uuid4())), json=_cuerpo())
    assert resp.status_code == 403


def test_con_secreto_incorrecto_falla_403(monkeypatch):
    _con_secreto(monkeypatch)
    resp = client.post(_url(str(uuid.uuid4())), json=_cuerpo(), headers={"X-Servicio-Secreto": "otro"})
    assert resp.status_code == 403


def test_si_el_secreto_no_esta_configurado_rechaza_todo(monkeypatch):
    monkeypatch.setattr(main, "PRODUCTORES_A_CERTIFICACION_SECRETO", None)
    # Sin el guard "not SECRETO", None == None dejaría pasar esta llamada.
    resp = client.post(_url(str(uuid.uuid4())), json=_cuerpo())
    assert resp.status_code == 403


def test_crea_los_dos_bloques_encadenados_y_la_cadena_es_integra(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())

    resp = client.post(_url(producto_id), json=_cuerpo(), headers={"X-Servicio-Secreto": SECRETO})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"creados": 2, "total_bloques": 2}
    bloques = _bloques(producto_id)
    assert [b.evento for b in bloques] == ["cosecha_registrada", "certificado_productor"]
    assert bloques[1].hash_anterior == bloques[0].hash_actual
    db = main.SessionLocal()
    try:
        assert verificar_cadena(db, producto_id)["valido"] is True
    finally:
        db.close()


def test_el_certificado_lleva_al_verificador_si_tiene_perfil(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())
    usuario_id = uuid.uuid4()
    db = main.SessionLocal()
    try:
        verificador = main.models.Verificador(usuario_id=usuario_id, nombre="Ana Verificadora", credencial="COL-1")
        db.add(verificador)
        db.commit()
        verificador_id = verificador.id
    finally:
        db.close()

    resp = client.post(
        _url(producto_id), json=_cuerpo(str(usuario_id)), headers={"X-Servicio-Secreto": SECRETO}
    )

    assert resp.status_code == 200, resp.text
    certificado = _bloques(producto_id)[1]
    assert certificado.verificador_id == verificador_id
    historial = client.get(f"/certificacion/{producto_id}/historial").json()
    assert historial[1]["verificador_nombre"] == "Ana Verificadora"


def test_sin_perfil_de_verificador_el_bloque_se_crea_igual_sin_nombre(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())

    resp = client.post(
        _url(producto_id), json=_cuerpo(str(uuid.uuid4())), headers={"X-Servicio-Secreto": SECRETO}
    )

    assert resp.status_code == 200, resp.text
    assert _bloques(producto_id)[1].verificador_id is None


def test_es_idempotente_no_duplica_bloques(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())
    headers = {"X-Servicio-Secreto": SECRETO}

    client.post(_url(producto_id), json=_cuerpo(), headers=headers)
    resp = client.post(_url(producto_id), json=_cuerpo(), headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"creados": 0, "total_bloques": 2}
    assert len(_bloques(producto_id)) == 2


def test_completa_solo_el_evento_que_falta(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())
    db = main.SessionLocal()
    try:
        main.crear_bloque(db, producto_id, "cosecha_registrada", "registrada a mano")
    finally:
        db.close()

    resp = client.post(_url(producto_id), json=_cuerpo(), headers={"X-Servicio-Secreto": SECRETO})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"creados": 1, "total_bloques": 2}
    assert [b.evento for b in _bloques(producto_id)] == ["cosecha_registrada", "certificado_productor"]


def test_el_endpoint_publico_de_eventos_ya_no_existe():
    """Antes cualquier productor o verificador podía agregar bloques con texto libre a la cadena de
    CUALQUIER producto (existiera o no). Ahora la ruta no existe y no se crea ningún bloque."""
    producto_id = str(uuid.uuid4())
    for cuerpo in (
        {"evento": "cosecha_registrada", "datos": "texto falso", "latitud": "-13.15", "longitud": "-74.22"},
        {"evento": "certificado_productor", "datos": "certificado falso"},
    ):
        resp = client.post(f"/certificacion/{producto_id}/eventos", json=cuerpo, headers={"Authorization": "Bearer x"})
        assert resp.status_code == 404
    assert _bloques(producto_id) == []


# ============ La cadena no se puede bifurcar ============

def _nuevo_bloque_directo(producto_id, indice):
    db = main.SessionLocal()
    try:
        db.add(main.models.Bloque(
            producto_id=producto_id, indice=indice, evento="cosecha_registrada", datos="x",
            hash_anterior="0" * 64, hash_actual="a" * 64,
        ))
        db.commit()
    finally:
        db.close()


def test_la_base_rechaza_dos_bloques_con_el_mismo_indice():
    producto_id = str(uuid.uuid4())
    _nuevo_bloque_directo(producto_id, 0)
    with pytest.raises(IntegrityError):
        _nuevo_bloque_directo(producto_id, 0)
    _nuevo_bloque_directo(str(uuid.uuid4()), 0)  # otro producto puede usar el índice 0
    assert len(_bloques(producto_id)) == 1


def test_crear_bloque_reintenta_si_otro_proceso_ocupo_el_indice(monkeypatch):
    """Simula la carrera: este proceso leyó "la cadena está vacía" pero otro ya escribió el bloque 0."""
    producto_id = str(uuid.uuid4())
    db = main.SessionLocal()
    try:
        logica_cadena.crear_bloque(db, producto_id, "cosecha_registrada", "primero")

        real = logica_cadena._ultimo_bloque
        lecturas = []

        def lectura_vieja_una_vez(sesion, pid):
            lecturas.append(1)
            return None if len(lecturas) == 1 else real(sesion, pid)

        monkeypatch.setattr(logica_cadena, "_ultimo_bloque", lectura_vieja_una_vez)
        nuevo = logica_cadena.crear_bloque(db, producto_id, "certificado_productor", "segundo")
        assert nuevo.indice == 1
        assert len(lecturas) == 2
        assert verificar_cadena(db, producto_id)["valido"] is True
    finally:
        db.close()
    assert sorted(b.indice for b in _bloques(producto_id)) == [0, 1]


def test_crear_bloque_falla_con_409_si_no_logra_un_indice_libre(monkeypatch):
    producto_id = str(uuid.uuid4())
    db = main.SessionLocal()
    try:
        logica_cadena.crear_bloque(db, producto_id, "cosecha_registrada", "primero")
        monkeypatch.setattr(logica_cadena, "_ultimo_bloque", lambda sesion, pid: None)  # siempre lee "vacía"
        with pytest.raises(logica_cadena.HTTPException) as error:
            logica_cadena.crear_bloque(db, producto_id, "certificado_productor", "segundo")
        assert error.value.status_code == 409
    finally:
        db.close()
    assert len(_bloques(producto_id)) == 1  # no quedó nada a medias


def test_escrituras_simultaneas_no_bifurcan_la_cadena():
    producto_id = str(uuid.uuid4())
    errores = []

    def escribir(numero):
        db = main.SessionLocal()
        try:
            main.crear_bloque(db, producto_id, "verificado_punto_venta", f"evento {numero}")
        except Exception as error:  # noqa: BLE001 - se comprueba abajo que no haya ninguno
            errores.append(error)
        finally:
            db.close()

    hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(4)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert not errores, errores
    assert sorted(b.indice for b in _bloques(producto_id)) == [0, 1, 2, 3]
    db = main.SessionLocal()
    try:
        assert verificar_cadena(db, producto_id)["valido"] is True
    finally:
        db.close()


def _motor_sqlite_con_bloques(filas):
    motor = create_engine("sqlite://")
    with motor.begin() as conexion:
        conexion.execute(text("CREATE TABLE bloques (id INTEGER PRIMARY KEY, producto_id TEXT, indice INTEGER)"))
        for producto_id, indice in filas:
            conexion.execute(text("INSERT INTO bloques (producto_id, indice) VALUES (:p, :i)"), {"p": producto_id, "i": indice})
    return motor


def test_la_migracion_agrega_el_indice_unico_a_una_tabla_existente_y_es_idempotente():
    motor = _motor_sqlite_con_bloques([("p", 0), ("p", 1)])
    assert migraciones.asegurar_indice_unico_de_bloques(motor) is True
    assert migraciones.asegurar_indice_unico_de_bloques(motor) is True
    with pytest.raises(IntegrityError):
        with motor.begin() as conexion:
            conexion.execute(text("INSERT INTO bloques (producto_id, indice) VALUES ('p', 1)"))
    with motor.begin() as conexion:
        conexion.execute(text("INSERT INTO bloques (producto_id, indice) VALUES ('otro', 1)"))  # otro producto: sí


def test_la_migracion_no_tumba_el_arranque_si_ya_hay_bloques_duplicados(caplog):
    motor = _motor_sqlite_con_bloques([("p", 0), ("p", 0)])
    assert migraciones.asegurar_indice_unico_de_bloques(motor) is False
    assert "uq_bloque_producto_indice" in caplog.text
