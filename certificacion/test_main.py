"""
Tests de POST /certificacion/{producto_id}/cosecha-aprobada — el endpoint interno que Productores
llama al completar una cosecha para dejar registrado cosecha_registrada, con la evidencia en origen.

Cubre:
- sin secreto / con secreto incorrecto -> 403 (y falla cerrado si el secreto no está configurado)
- crea el bloque con el texto de la evidencia y la foto; la cadena resultante verifica como íntegra
- la foto queda fuera del hash: lo que la sella es su huella, dentro del texto
- ya no crea certificado_productor (el rol Verificador se retiró); los que ya existen se siguen mostrando
- idempotente: repetir la llamada no duplica el bloque
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
from auth import verificar_token

client = TestClient(main.app)
SECRETO = "secreto-de-prueba"


def _url(producto_id):
    return f"/certificacion/{producto_id}/cosecha-aprobada"


FOTO_URL = "http://localhost:9000/cosechas-evidencias/foto.jpg"


def _cuerpo(foto_url=FOTO_URL):
    return {
        "datos_cosecha": "45.5 kg de Papa Nativa cosechados en chacra CH-01. Foto de la cosecha (SHA-256 " + "a" * 64 + ").",
        "foto_url": foto_url,
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


def test_crea_el_bloque_de_la_cosecha_con_su_foto_y_la_cadena_es_integra(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())

    resp = client.post(_url(producto_id), json=_cuerpo(), headers={"X-Servicio-Secreto": SECRETO})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"creados": 1, "total_bloques": 1}
    bloques = _bloques(producto_id)
    assert [b.evento for b in bloques] == ["cosecha_registrada"]  # ya no hay certificado_productor
    assert bloques[0].foto_url == FOTO_URL and "SHA-256" in bloques[0].datos
    assert bloques[0].verificador_id is None
    db = main.SessionLocal()
    try:
        assert verificar_cadena(db, producto_id)["valido"] is True
    finally:
        db.close()
    historial = client.get(f"/certificacion/{producto_id}/historial").json()
    assert historial[0]["foto_url"] == FOTO_URL


def test_la_foto_no_entra_en_el_hash_pero_su_huella_si(monkeypatch):
    """Cambiar el enlace de la foto no rompe la cadena (no está en el hash); cambiar el texto, donde va la huella, sí."""
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())
    client.post(_url(producto_id), json=_cuerpo(), headers={"X-Servicio-Secreto": SECRETO})

    db = main.SessionLocal()
    try:
        bloque = db.query(main.models.Bloque).filter(main.models.Bloque.producto_id == producto_id).one()
        bloque.foto_url = "http://localhost:9000/cosechas-evidencias/otra.jpg"
        db.commit()
        assert verificar_cadena(db, producto_id)["valido"] is True
        bloque.datos = bloque.datos.replace("a" * 64, "b" * 64)
        db.commit()
        assert verificar_cadena(db, producto_id)["valido"] is False
    finally:
        db.close()


def test_sin_foto_el_bloque_se_crea_igual(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())

    resp = client.post(_url(producto_id), json=_cuerpo(foto_url=None), headers={"X-Servicio-Secreto": SECRETO})

    assert resp.status_code == 200, resp.text
    assert _bloques(producto_id)[0].foto_url is None


def test_rechaza_una_url_de_foto_que_no_es_web_o_es_enorme(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())
    for foto_url in ("javascript:alert(1)", "ftp://x/y.jpg", "http://x/" + "a" * main.FOTO_URL_MAXIMO):
        resp = client.post(_url(producto_id), json=_cuerpo(foto_url), headers={"X-Servicio-Secreto": SECRETO})
        assert resp.status_code == 422, (foto_url[:20], resp.text)
    assert _bloques(producto_id) == []


def test_un_cliente_viejo_que_manda_el_verificador_ya_no_crea_su_certificado(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())
    cuerpo = {**_cuerpo(), "datos_certificado": "Cosecha revisada y aprobada.", "verificador_usuario_id": str(uuid.uuid4())}

    resp = client.post(_url(producto_id), json=cuerpo, headers={"X-Servicio-Secreto": SECRETO})

    assert resp.status_code == 200, resp.text
    assert [b.evento for b in _bloques(producto_id)] == ["cosecha_registrada"]


def test_un_certificado_de_antes_sigue_mostrando_a_su_verificador():
    producto_id = str(uuid.uuid4())
    db = main.SessionLocal()
    try:
        verificador = main.models.Verificador(usuario_id=uuid.uuid4(), nombre="Ana Verificadora", credencial="COL-1")
        db.add(verificador)
        db.commit()
        main.crear_bloque(db, producto_id, "cosecha_registrada", "45 kg de papa")
        main.crear_bloque(db, producto_id, "certificado_productor", "Cosecha revisada y aprobada.", verificador.id)
    finally:
        db.close()

    historial = client.get(f"/certificacion/{producto_id}/historial").json()

    assert historial[1]["verificador_nombre"] == "Ana Verificadora"
    assert historial[1]["verificador_credencial"] == "COL-1"
    assert historial[0]["verificador_nombre"] is None and historial[0]["foto_url"] is None


def test_es_idempotente_no_duplica_bloques(monkeypatch):
    _con_secreto(monkeypatch)
    producto_id = str(uuid.uuid4())
    headers = {"X-Servicio-Secreto": SECRETO}

    client.post(_url(producto_id), json=_cuerpo(), headers=headers)
    resp = client.post(_url(producto_id), json=_cuerpo(), headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"creados": 0, "total_bloques": 1}
    assert len(_bloques(producto_id)) == 1


def test_la_migracion_de_foto_url_es_idempotente():
    assert migraciones.asegurar_columna_foto_url(main.engine) is True
    assert migraciones.asegurar_columna_foto_url(main.engine) is True


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


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("POST", f"/certificacion/{_uuid.uuid4()}/llegada-almacen", {"datos": "llegaron 5 kg"}, "PRODUCTOS_A_CERTIFICACION_SECRETO"),
        ("POST", f"/certificacion/pedido/{_uuid.uuid4()}/certificado", {"productos_ids": []}, "TRANSPORTE_A_CERTIFICACION_SECRETO"),
        ("POST", f"/certificacion/{_uuid.uuid4()}/eventos-internos", {"evento": "verificado_punto_venta"}, "TRANSPORTE_A_CERTIFICACION_SECRETO"),
        ("POST", f"/certificacion/{_uuid.uuid4()}/cosecha-aprobada", {"datos_cosecha": "x"}, "PRODUCTORES_A_CERTIFICACION_SECRETO"),
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



# ============ POST /certificacion/{id}/llegada-almacen (lo llama Productos) ============

SECRETO_PRODUCTOS = "secreto-de-prueba-productos"


def _url_llegada(producto_id):
    return f"/certificacion/{producto_id}/llegada-almacen"


def _con_secreto_de_productos(monkeypatch):
    monkeypatch.setattr(main, "PRODUCTOS_A_CERTIFICACION_SECRETO", SECRETO_PRODUCTOS)


def _sembrar_cadena(producto_id):
    db = main.SessionLocal()
    try:
        main.crear_bloque(db, producto_id, "cosecha_registrada", "45 kg de papa cosechados")
        main.crear_bloque(db, producto_id, "certificado_productor", "revisada por un verificador")
    finally:
        db.close()


def test_la_llegada_sin_secreto_o_con_uno_incorrecto_falla_403_y_no_crea_bloques(monkeypatch):
    _con_secreto_de_productos(monkeypatch)
    producto_id = str(uuid.uuid4())

    assert client.post(_url_llegada(producto_id), json={"datos": "llegaron 5 kg"}).status_code == 403
    assert client.post(
        _url_llegada(producto_id), json={"datos": "llegaron 5 kg"}, headers={"X-Servicio-Secreto": "otro"}
    ).status_code == 403
    # el secreto de Productores o de Transporte tampoco sirve acá: cada par de servicios tiene el suyo
    assert client.post(
        _url_llegada(producto_id), json={"datos": "llegaron 5 kg"}, headers={"X-Servicio-Secreto": SECRETO}
    ).status_code == 403
    assert _bloques(producto_id) == []


def test_la_llegada_se_agrega_al_final_de_la_cadena_y_la_cadena_sigue_integra(monkeypatch):
    _con_secreto_de_productos(monkeypatch)
    producto_id = str(uuid.uuid4())
    _sembrar_cadena(producto_id)

    resp = client.post(
        _url_llegada(producto_id),
        json={"datos": "Llegaron al almacén 48 kg de Papa: 2 kg menos de las 50 declaradas por el productor."},
        headers={"X-Servicio-Secreto": SECRETO_PRODUCTOS},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"creado": True, "indice": 2}
    bloques = _bloques(producto_id)
    assert [b.evento for b in bloques] == ["cosecha_registrada", "certificado_productor", "llegada_almacen"]
    assert "48 kg" in bloques[2].datos
    assert bloques[2].hash_anterior == bloques[1].hash_actual
    db = main.SessionLocal()
    try:
        assert verificar_cadena(db, producto_id)["valido"] is True
    finally:
        db.close()
    historial = client.get(f"/certificacion/{producto_id}/historial").json()
    assert historial[-1]["evento"] == "llegada_almacen"


def test_la_llegada_es_idempotente_un_reintento_no_duplica_el_bloque(monkeypatch):
    _con_secreto_de_productos(monkeypatch)
    producto_id = str(uuid.uuid4())
    _sembrar_cadena(producto_id)
    headers = {"X-Servicio-Secreto": SECRETO_PRODUCTOS}

    primera = client.post(_url_llegada(producto_id), json={"datos": "llegaron 50 kg"}, headers=headers)
    segunda = client.post(_url_llegada(producto_id), json={"datos": "llegaron 50 kg"}, headers=headers)

    assert primera.json() == {"creado": True, "indice": 2}
    assert segunda.status_code == 200 and segunda.json() == {"creado": False, "indice": 2}
    assert len(_bloques(producto_id)) == 3


def test_la_llegada_rechaza_textos_vacios_con_caracteres_nulos_o_enormes(monkeypatch):
    _con_secreto_de_productos(monkeypatch)
    producto_id = str(uuid.uuid4())
    headers = {"X-Servicio-Secreto": SECRETO_PRODUCTOS}

    for datos in ("", "   ", "mal\u0000texto", "x" * (main.DATOS_LLEGADA_MAXIMO + 1)):
        resp = client.post(_url_llegada(producto_id), json={"datos": datos}, headers=headers)
        assert resp.status_code == 422, (datos[:20], resp.text)
    assert _bloques(producto_id) == []


def test_la_llegada_con_un_id_de_producto_mal_formado_falla_422_no_500(monkeypatch):
    _con_secreto_de_productos(monkeypatch)
    resp = client.post(
        _url_llegada("no-es-un-uuid"), json={"datos": "llegaron 5 kg"}, headers={"X-Servicio-Secreto": SECRETO_PRODUCTOS}
    )
    assert resp.status_code == 422




# ============ El perfil del verificador se retiró con el rol (presentación del 2026-10-01) ============

def test_las_rutas_del_perfil_del_verificador_ya_no_existen():
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": str(uuid.uuid4()), "rol": "verificador"}
    try:
        for metodo, ruta, cuerpo in (
            ("POST", "/verificadores", {"nombre": "Ana Pérez"}),
            ("GET", "/verificadores/me", None),
            ("GET", "/verificadores/credencial-asignada", None),
            ("DELETE", f"/verificadores/{uuid.uuid4()}", None),
        ):
            assert client.request(metodo, ruta, json=cuerpo).status_code in (404, 405), ruta
    finally:
        main.app.dependency_overrides.pop(verificar_token, None)
