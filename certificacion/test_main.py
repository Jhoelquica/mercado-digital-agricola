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
from auth import verificar_token

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


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/verificadores/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
        ("POST", f"/certificacion/pedido/{_uuid.uuid4()}/certificado", {"productos_ids": []}, "TRANSPORTE_A_CERTIFICACION_SECRETO"),
        ("POST", f"/certificacion/{_uuid.uuid4()}/eventos-internos", {"evento": "verificado_punto_venta"}, "TRANSPORTE_A_CERTIFICACION_SECRETO"),
        ("POST", f"/certificacion/{_uuid.uuid4()}/llegada-almacen", {"datos": "llegaron 5 kg"}, "PRODUCTOS_A_CERTIFICACION_SECRETO"),
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



# ============ Perfil del verificador: POST /verificadores y GET /verificadores/me ============
# Sin perfil, lo que el verificador aprueba sale en el certificado sin "Certificado por" (el bloque se crea igual, sin
# nombre) y esa cadena no se puede corregir después. El nombre lo escribe él; la CREDENCIAL no: la asignó el admin al
# invitarlo (Usuarios la guarda en la invitación) y Certificación se la pide por su ruta interna, así que nadie puede
# inventarla. En estos tests esa consulta está simulada; los de más abajo prueban la consulta real.

_credencial_real = main._credencial_asignada_por_el_admin


@pytest.fixture(autouse=True)
def _credencial_del_admin(monkeypatch):
    """Por defecto se da por hecho que el admin le asignó "COL-ASIGNADA" a quien pregunte."""
    monkeypatch.setattr(main, "_credencial_asignada_por_el_admin", lambda usuario_id: "COL-ASIGNADA")


@pytest.fixture
def como_verificador():
    def _como(usuario_id=None, rol="verificador"):
        usuario_id = str(usuario_id or uuid.uuid4())
        main.app.dependency_overrides[verificar_token] = lambda: {"sub": usuario_id, "rol": rol}
        return usuario_id

    yield _como
    main.app.dependency_overrides.pop(verificar_token, None)


def test_un_verificador_crea_su_perfil_y_despues_lo_consulta(como_verificador):
    usuario_id = como_verificador()
    assert client.get("/verificadores/me").status_code == 404

    resp = client.post("/verificadores", json={"nombre": "  Ana Pérez  "})

    assert resp.status_code == 200, resp.text
    assert resp.json()["nombre"] == "Ana Pérez"  # sin espacios de sobra
    assert resp.json()["credencial"] == "COL-ASIGNADA"  # la del admin
    mio = client.get("/verificadores/me")
    assert mio.status_code == 200
    assert mio.json()["usuario_id"] == usuario_id and mio.json()["credencial"] == "COL-ASIGNADA"


def test_una_credencial_mandada_por_el_verificador_se_ignora(como_verificador):
    como_verificador()

    resp = client.post("/verificadores", json={"nombre": "Ana Pérez", "credencial": "INVENTADA-999"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["credencial"] == "COL-ASIGNADA"
    assert client.get("/verificadores/me").json()["credencial"] == "COL-ASIGNADA"


def test_no_se_puede_crear_un_segundo_perfil(como_verificador):
    como_verificador()
    assert client.post("/verificadores", json={"nombre": "Ana Pérez"}).status_code == 200

    segundo = client.post("/verificadores", json={"nombre": "Otra Persona"})

    assert segundo.status_code == 409
    assert client.get("/verificadores/me").json()["nombre"] == "Ana Pérez"


def test_el_perfil_rechaza_un_nombre_corto_vacio_enorme_o_con_nulos(como_verificador):
    como_verificador()
    invalidos = ["A", "   ", "", "x" * (main.VERIFICADOR_NOMBRE_MAXIMO + 1), "Ana\u0000Pérez"]

    for nombre in invalidos:
        resp = client.post("/verificadores", json={"nombre": nombre})
        assert resp.status_code == 422, (nombre[:12], resp.text)

    assert client.get("/verificadores/me").status_code == 404  # no quedó nada guardado


def test_el_perfil_acepta_justo_el_largo_maximo_del_nombre(como_verificador):
    como_verificador()
    assert client.post("/verificadores", json={"nombre": "n" * main.VERIFICADOR_NOMBRE_MAXIMO}).status_code == 200


def test_solo_un_verificador_crea_o_consulta_un_perfil_y_su_credencial(como_verificador):
    for rol in ("comprador", "productor", "repartidor", "admin"):
        como_verificador(rol=rol)
        assert client.post("/verificadores", json={"nombre": "Ana Pérez"}).status_code == 403, rol
        assert client.get("/verificadores/me").status_code == 403, rol
        assert client.get("/verificadores/credencial-asignada").status_code == 403, rol


def test_el_verificador_ve_su_credencial_asignada_antes_de_crear_el_perfil(como_verificador):
    como_verificador()
    resp = client.get("/verificadores/credencial-asignada")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"credencial": "COL-ASIGNADA"}


def test_el_certificado_de_una_cosecha_lleva_nombre_y_credencial_de_quien_creo_su_perfil(como_verificador, monkeypatch):
    _con_secreto(monkeypatch)
    usuario_id = como_verificador()
    assert client.post("/verificadores", json={"nombre": "Ana Pérez"}).status_code == 200
    producto_id = str(uuid.uuid4())

    resp = client.post(_url(producto_id), json=_cuerpo(usuario_id), headers={"X-Servicio-Secreto": SECRETO})

    assert resp.status_code == 200, resp.text
    historial = client.get(f"/certificacion/{producto_id}/historial").json()
    assert historial[1]["verificador_nombre"] == "Ana Pérez"
    assert historial[1]["verificador_credencial"] == "COL-ASIGNADA"
    assert historial[0]["verificador_nombre"] is None and historial[0]["verificador_credencial"] is None


# ---- la consulta real a Usuarios (sin el simulacro de arriba) ----

class _RespuestaDeUsuarios:
    def __init__(self, status_code, cuerpo=None):
        self.status_code = status_code
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


def _usuarios_responde(monkeypatch, status_code=None, cuerpo=None, error=None):
    """Usa la consulta real y simula lo que responde Usuarios. Devuelve la lista de llamadas que se hicieron."""
    monkeypatch.setattr(main, "_credencial_asignada_por_el_admin", _credencial_real)
    monkeypatch.setattr(main, "USUARIOS_URL", "http://usuarios.prueba")
    monkeypatch.setattr(main, "CERTIFICACION_A_USUARIOS_SECRETO", "secreto-de-prueba")
    llamadas = []

    def _get(url, **kwargs):
        llamadas.append((url, kwargs))
        if error:
            raise error
        return _RespuestaDeUsuarios(status_code, cuerpo)

    monkeypatch.setattr(main.httpx, "get", _get)
    return llamadas


def test_la_credencial_se_le_pide_a_usuarios_con_el_secreto_de_servicio(monkeypatch, como_verificador):
    usuario_id = como_verificador()
    llamadas = _usuarios_responde(monkeypatch, 200, {"credencial": "  COL-5  "})

    resp = client.post("/verificadores", json={"nombre": "Ana Pérez"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["credencial"] == "COL-5"  # la de Usuarios, sin espacios de sobra
    url, kwargs = llamadas[0]
    assert url == f"http://usuarios.prueba/usuarios/interno/{usuario_id}/credencial"
    assert kwargs["headers"] == {"X-Servicio-Secreto": "secreto-de-prueba"}


def test_sin_credencial_asignada_no_hay_perfil_y_se_dice_por_que(monkeypatch, como_verificador):
    como_verificador()

    for respuesta in ((404, None), (200, {"credencial": ""}), (200, {})):
        _usuarios_responde(monkeypatch, *respuesta)
        crear = client.post("/verificadores", json={"nombre": "Ana Pérez"})
        ver = client.get("/verificadores/credencial-asignada")
        assert crear.status_code == 409 and "administrador" in crear.json()["detail"], (respuesta, crear.text)
        assert ver.status_code == 409, (respuesta, ver.text)

    assert client.get("/verificadores/me").status_code == 404  # no quedó un perfil sin credencial


def test_si_usuarios_no_responde_no_se_crea_el_perfil(monkeypatch, como_verificador):
    como_verificador()

    for falla in ({"error": main.httpx.RequestError("sin red")}, {"status_code": 500}, {"status_code": 403}):
        _usuarios_responde(monkeypatch, **falla)
        assert client.post("/verificadores", json={"nombre": "Ana Pérez"}).status_code == 503, falla
        assert client.get("/verificadores/credencial-asignada").status_code == 503, falla

    assert client.get("/verificadores/me").status_code == 404


def test_un_perfil_que_ya_existe_responde_409_sin_molestar_a_usuarios(monkeypatch, como_verificador):
    como_verificador()
    assert client.post("/verificadores", json={"nombre": "Ana Pérez"}).status_code == 200
    llamadas = _usuarios_responde(monkeypatch, error=main.httpx.RequestError("sin red"))

    assert client.post("/verificadores", json={"nombre": "Otra"}).status_code == 409
    assert llamadas == []
