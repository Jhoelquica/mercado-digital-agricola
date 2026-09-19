"""
Tests de POST /certificacion/{producto_id}/cosecha-aprobada — el endpoint interno que Productores
llama al aprobar una cosecha para dejar registrados cosecha_registrada y certificado_productor.

Cubre:
- sin secreto / con secreto incorrecto -> 403 (y falla cerrado si el secreto no está configurado)
- crea los dos bloques encadenados y la cadena resultante verifica como íntegra
- el bloque certificado_productor lleva el verificador cuando su usuario tiene perfil
- idempotente: repetir la llamada no duplica bloques
- completa solo lo que falta si ya existía cosecha_registrada

Requiere Postgres real (tipos UUID + create_all al importar main), igual que el resto de servicios.
"""
import uuid

from fastapi.testclient import TestClient

import main
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
