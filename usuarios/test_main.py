"""
Tests del registro (solo roles abiertos) y del login/registro con Google (POST /usuarios/oauth/google).

Cubre: POST /usuarios/registro (comprador, productor y repartidor abiertos; admin, "verificador" —rol retirado en la
presentación del 2026-10-01— o un rol inventado -> 400 y no se crea nada, aunque llegue un código de invitación de
antes), las rutas de invitaciones y de la credencial del verificador ya no existen, y POST /usuarios/oauth/google
(login por google_id existente, vinculación automática por email de una cuenta con contraseña, registro nuevo con rol
abierto y el mismo filtro de rol que el registro normal, id_token inválido -> 401, y el login normal contra una cuenta
sin contraseña -> 401 sin excepción).

Requiere Postgres real según database.py (UUID de postgresql + create_all() al importar main). No se mockea nada
salvo google_id_token.verify_oauth2_token (única llamada saliente real de este servicio) — nunca se golpea la red de
Google de verdad. Los usuarios de un rol que no se registra (admin) se insertan directo por SessionLocal.
"""
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from jose import jwt as jose_jwt

import main
import seguridad
from auth import verificar_token

client = TestClient(main.app)


def _decodificar_jwt(token: str) -> dict:
    return jose_jwt.decode(token, seguridad.JWT_SECRET, algorithms=[seguridad.JWT_ALGORITHM])


def _mockear_google(monkeypatch, *, sub=None, email=None, nombre="Usuario Google", verificado=True):
    """Reemplaza main.google_id_token.verify_oauth2_token: nunca golpea la red de Google de
    verdad. Devuelve el payload usado, para que el test pueda referenciar el mismo sub/email."""
    payload = {"sub": sub or f"google-{uuid.uuid4().hex}", "email": email or _email(), "name": nombre,
               "email_verified": verificado}
    monkeypatch.setattr(main.google_id_token, "verify_oauth2_token", lambda *a, **kw: payload)
    return payload


def _mockear_google_invalido(monkeypatch):
    def _falla(*a, **kw):
        raise ValueError("Token inválido o expirado")
    monkeypatch.setattr(main.google_id_token, "verify_oauth2_token", _falla)


def _auth(user_id: str, rol: str):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": user_id, "rol": rol}


def _email():
    # dominio real (no .test/.example, que email-validator rechaza como reservados)
    return f"qa-{uuid.uuid4().hex}@chakrashop.pe"


def _crear_usuario(rol: str) -> str:
    """Roles abiertos -> vía POST /usuarios/registro. Los demás (admin) -> insert directo, porque ese endpoint no
    deja registrarse con ellos."""
    if rol in main.ROLES_DE_REGISTRO_ABIERTO:
        resp = client.post("/usuarios/registro", json={
            "nombre": f"Usuario {rol}", "email": _email(), "password": "test1234", "rol": rol,
        })
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    db = main.SessionLocal()
    try:
        u = main.models.Usuario(nombre=f"Usuario {rol}", email=_email(),
                                password_hash="x", rol=rol)
        db.add(u)
        db.commit()
        db.refresh(u)
        return str(u.id)
    finally:
        db.close()


def _admin_autenticado() -> str:
    admin_id = _crear_usuario("admin")
    _auth(admin_id, "admin")
    return admin_id


# ============ Registro: solo comprador, productor o repartidor ============

def _registro(rol, **extra):
    return client.post("/usuarios/registro", json={
        "nombre": f"Reg {rol}", "email": _email(), "password": "test1234", "rol": rol, **extra,
    })


def _total_usuarios():
    db = main.SessionLocal()
    try:
        return db.query(main.models.Usuario).count()
    finally:
        db.close()


def test_registro_con_un_rol_abierto_funciona():
    for rol in ("comprador", "productor", "repartidor"):
        resp = _registro(rol)
        assert resp.status_code == 200, resp.text
        assert resp.json()["rol"] == rol


def test_no_se_puede_registrar_como_admin_verificador_ni_un_rol_inventado():
    antes = _total_usuarios()
    for rol in ("admin", "verificador", "superusuario", ""):
        # un cliente viejo que mande un código de invitación tampoco pasa: ya no existen
        for extra in ({}, {"codigo_invitacion": "cod-de-antes"}):
            resp = _registro(rol, **extra)
            assert resp.status_code == 400, (rol, extra, resp.text)
            assert resp.json()["detail"] == "Solo puedes registrarte como comprador, productor o repartidor."
    assert _total_usuarios() == antes  # no se creó nadie


def test_las_rutas_de_invitaciones_y_de_la_credencial_ya_no_existen():
    _admin_autenticado()
    for metodo, ruta, cuerpo in (
        ("POST", "/admin/invitaciones", {"rol_destino": "verificador", "credencial": "COL-1"}),
        ("GET", "/admin/invitaciones", None),
        ("GET", "/admin/invitaciones/validar/cualquier-codigo", None),
        ("DELETE", f"/admin/invitaciones/{uuid.uuid4()}", None),
        ("GET", f"/usuarios/interno/{uuid.uuid4()}/credencial", None),
    ):
        assert client.request(metodo, ruta, json=cuerpo).status_code in (404, 405), ruta


# ============ Login/registro con Google (POST /usuarios/oauth/google) ============

def test_oauth_google_login_cuenta_existente_por_google_id(monkeypatch):
    """Ya hay un Usuario vinculado a este google_id (de un login/registro anterior) — el
    endpoint debe reconocerlo y emitir un JWT con SU rol de siempre, sin tocar nada más."""
    google_sub = f"google-{uuid.uuid4().hex}"
    db = main.SessionLocal()
    try:
        u = main.models.Usuario(nombre="Ya Vinculado", email=_email(), password_hash=None,
                                 google_id=google_sub, rol="productor")
        db.add(u)
        db.commit()
        db.refresh(u)
        usuario_id = str(u.id)
    finally:
        db.close()

    _mockear_google(monkeypatch, sub=google_sub)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 200, resp.text
    payload = _decodificar_jwt(resp.json()["access_token"])
    assert payload["sub"] == usuario_id
    assert payload["rol"] == "productor"


def test_oauth_google_vincula_automaticamente_cuenta_con_password_existente(monkeypatch):
    """No hay match por google_id, pero SÍ por email — una cuenta creada con contraseña antes
    de esta sub-entrega — debe vincularse (guardar google_id) y devolver login con su rol de
    siempre, no un registro nuevo."""
    usuario_id = _crear_usuario("productor")
    db = main.SessionLocal()
    try:
        u = db.query(main.models.Usuario).filter(main.models.Usuario.id == usuario_id).one()
        email = u.email
        assert u.google_id is None
    finally:
        db.close()

    google_sub = f"google-{uuid.uuid4().hex}"
    _mockear_google(monkeypatch, sub=google_sub, email=email)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 200, resp.text
    payload = _decodificar_jwt(resp.json()["access_token"])
    assert payload["sub"] == usuario_id
    assert payload["rol"] == "productor"

    db = main.SessionLocal()
    try:
        u = db.query(main.models.Usuario).filter(main.models.Usuario.id == usuario_id).one()
        assert u.google_id == google_sub
    finally:
        db.close()


def test_oauth_google_registro_nuevo_con_rol_abierto_funciona(monkeypatch):
    for rol in ("comprador", "productor", "repartidor"):
        email = _email()
        _mockear_google(monkeypatch, email=email, nombre=f"Nuevo {rol}")
        resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token", "rol": rol})

        assert resp.status_code == 200, resp.text
        payload = _decodificar_jwt(resp.json()["access_token"])
        assert payload["rol"] == rol

        db = main.SessionLocal()
        try:
            u = db.query(main.models.Usuario).filter(main.models.Usuario.email == email).one()
            assert u.password_hash is None  # cuenta 100% Google, sin contraseña propia
            assert u.google_id is not None
            assert u.rol == rol
        finally:
            db.close()


def test_oauth_google_sin_rol_en_registro_nuevo_falla_400(monkeypatch):
    """Sin cuenta previa (ni por google_id ni por email) y sin rol elegido — el caso típico de
    intentar "Continuar con Google" desde el tab de Login sin tener cuenta todavía."""
    _mockear_google(monkeypatch)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Selecciona un rol antes de continuar con Google"


def test_oauth_google_no_registra_con_un_rol_que_no_es_abierto_mismo_mensaje_que_registro_normal(monkeypatch):
    antes = _total_usuarios()
    for rol in ("admin", "verificador"):
        _mockear_google(monkeypatch)
        resp = client.post("/usuarios/oauth/google", json={
            "id_token": "fake-token", "rol": rol, "codigo_invitacion": "cod-de-antes",
        })
        assert resp.status_code == 400, (rol, resp.text)
        assert resp.json()["detail"] == "Solo puedes registrarte como comprador, productor o repartidor."
    assert _total_usuarios() == antes


def test_oauth_google_con_correo_sin_verificar_no_vincula_una_cuenta_existente(monkeypatch):
    """Una cuenta de Google que declara el correo de otra persona sin haberlo verificado no debe entrar a la cuenta
    de esa persona (la vinculación automática por email era una toma de cuenta, la del admin incluida)."""
    usuario_id = _crear_usuario("admin")
    db = main.SessionLocal()
    try:
        email = db.query(main.models.Usuario).filter(main.models.Usuario.id == usuario_id).one().email
    finally:
        db.close()

    _mockear_google(monkeypatch, email=email, verificado=False)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 401, resp.text
    assert "verificado" in resp.json()["detail"]
    assert "access_token" not in resp.text
    db = main.SessionLocal()
    try:
        assert db.query(main.models.Usuario).filter(main.models.Usuario.id == usuario_id).one().google_id is None
    finally:
        db.close()


def test_oauth_google_con_correo_sin_verificar_tampoco_registra_una_cuenta_nueva(monkeypatch):
    email = _email()
    for payload_extra in ({"email_verified": False}, {"email_verified": None}, {"email_verified": "false"}):
        payload = {"sub": f"google-{uuid.uuid4().hex}", "email": email, "name": "Sin verificar", **payload_extra}
        monkeypatch.setattr(main.google_id_token, "verify_oauth2_token", lambda *a, _p=payload, **kw: _p)
        resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token", "rol": "comprador"})
        assert resp.status_code == 401, (payload_extra, resp.text)

    # sin la afirmación `email_verified` o sin correo en el token tampoco se confía
    for payload in ({"sub": "g-1", "email": email, "name": "x"}, {"sub": "g-2", "name": "x", "email_verified": True}):
        monkeypatch.setattr(main.google_id_token, "verify_oauth2_token", lambda *a, _p=payload, **kw: _p)
        resp = client.post("/usuarios/oauth/google", json={"id_token": "fake-token", "rol": "comprador"})
        assert resp.status_code == 401, (payload, resp.text)

    db = main.SessionLocal()
    try:
        assert db.query(main.models.Usuario).filter(main.models.Usuario.email == email).count() == 0
    finally:
        db.close()


def test_oauth_google_una_cuenta_ya_vinculada_sigue_entrando_aunque_el_token_no_traiga_el_correo_verificado(monkeypatch):
    """La regla es solo para vincular o crear por correo: quien ya está vinculado por google_id entra igual."""
    google_sub = f"google-{uuid.uuid4().hex}"
    db = main.SessionLocal()
    try:
        u = main.models.Usuario(nombre="Ya Vinculado", email=_email(), password_hash=None, google_id=google_sub, rol="comprador")
        db.add(u)
        db.commit()
    finally:
        db.close()

    _mockear_google(monkeypatch, sub=google_sub, verificado=False)
    assert client.post("/usuarios/oauth/google", json={"id_token": "fake-token"}).status_code == 200


def test_oauth_google_id_token_invalido_falla_401(monkeypatch):
    _mockear_google_invalido(monkeypatch)
    resp = client.post("/usuarios/oauth/google", json={"id_token": "token-basura"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token de Google inválido o expirado"


def test_login_normal_con_password_hash_none_falla_401_sin_excepcion():
    """Una cuenta 100% Google (sin contraseña propia) no debe poder loguearse por el endpoint
    normal — y sobre todo no debe explotar con una excepción sin capturar al intentarlo."""
    email = _email()
    db = main.SessionLocal()
    try:
        u = main.models.Usuario(nombre="Solo Google", email=email, password_hash=None,
                                 google_id=f"google-{uuid.uuid4().hex}", rol="comprador")
        db.add(u)
        db.commit()
    finally:
        db.close()

    resp = client.post("/usuarios/login", json={"email": email, "password": "cualquier-cosa"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Email o contraseña incorrectos"


# ============ Bootstrap del primer admin (sembrar_admin_inicial) ============
# Se llama a la función directo (no vía TestClient/lifespan ASGI, cuyo timing de startup varía
# entre versiones de starlette) — sigue siendo la función real, sin mockear la BD.
#
# `_borrar_todos_los_admins()` deja la tabla sin admins para poder probar "no hay ninguno" de
# forma determinística en una BD que comparten todos los tests de este archivo. Es seguro:
# ningún otro test de este archivo asume que los admins de OTROS tests sigan existiendo (cada
# uno crea el suyo con email random cuando lo necesita) — por eso estos tests van al final.

def _borrar_todos_los_admins():
    """Deja la tabla usuarios sin ningún rol=admin. En una base de antes de quitar las invitaciones, primero hay que
    soltar las filas de "invitaciones" que los referencian (creado_por / usado_por) o el DELETE choca con la FK; ya no
    hay modelo para esa tabla, así que va por SQL y solo si la tabla existe."""
    db = main.SessionLocal()
    try:
        admin_ids = [a.id for a in db.query(main.models.Usuario).filter(
            main.models.Usuario.rol == "admin"
        ).all()]
        if admin_ids:
            if db.execute(text("SELECT to_regclass('invitaciones')")).scalar():
                db.execute(text("DELETE FROM invitaciones WHERE creado_por = ANY(:ids) OR usado_por = ANY(:ids)"),
                           {"ids": admin_ids})
            db.query(main.models.Usuario).filter(
                main.models.Usuario.id.in_(admin_ids)
            ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_bootstrap_sin_env_vars_no_hace_nada(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", None)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", None)
    _borrar_todos_los_admins()

    main.sembrar_admin_inicial()

    db = main.SessionLocal()
    try:
        assert db.query(main.models.Usuario).filter(main.models.Usuario.rol == "admin").first() is None
    finally:
        db.close()


def test_bootstrap_crea_admin_si_no_hay_ninguno(monkeypatch):
    email = f"boot-{uuid.uuid4().hex}@chakrashop.pe"
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", email)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", "un-password-cualquiera")
    _borrar_todos_los_admins()

    main.sembrar_admin_inicial()

    db = main.SessionLocal()
    try:
        creado = db.query(main.models.Usuario).filter(main.models.Usuario.email == email).first()
        assert creado is not None
        assert creado.rol == "admin"
    finally:
        db.close()


def test_bootstrap_no_duplica_si_ya_existe_admin(monkeypatch):
    _crear_usuario("admin")  # ya hay al menos un admin (con email random, no el del bootstrap)
    email_que_no_deberia_crearse = f"boot-{uuid.uuid4().hex}@chakrashop.pe"
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", email_que_no_deberia_crearse)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", "otro-password")

    main.sembrar_admin_inicial()

    db = main.SessionLocal()
    try:
        no_se_creo = db.query(main.models.Usuario).filter(
            main.models.Usuario.email == email_que_no_deberia_crearse
        ).first()
        assert no_se_creo is None
    finally:
        db.close()


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/usuarios/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
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


# ============ GET /usuarios/{id}: solo el propio usuario o un admin ============

def test_un_usuario_ve_su_propio_perfil():
    usuario_id = _crear_usuario("comprador")
    _auth(usuario_id, "comprador")
    resp = client.get(f"/usuarios/{usuario_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == usuario_id


def test_un_usuario_no_puede_ver_el_perfil_de_otro():
    ajeno = _crear_usuario("productor")
    propio = _crear_usuario("comprador")
    _auth(propio, "comprador")
    resp = client.get(f"/usuarios/{ajeno}")
    assert resp.status_code == 403
    assert "@" not in resp.text  # ni rastro del correo del otro usuario


def test_un_admin_puede_ver_cualquier_perfil():
    ajeno = _crear_usuario("productor")
    _admin_autenticado()
    resp = client.get(f"/usuarios/{ajeno}")
    assert resp.status_code == 200, resp.text
    assert "@" in resp.json()["email"]


def test_un_id_de_usuario_mal_formado_falla_422_no_500():
    _auth(str(uuid.uuid4()), "comprador")
    assert client.get("/usuarios/no-es-un-uuid").status_code == 422


# ============ Contraseña mínima al registrarse y bloqueo del login ============

import acceso


def _usuario_con_password(password, email=None, rol="comprador", password_hash="calcular"):
    email = email or _email()
    hash_ = seguridad.hashear_password(password) if password_hash == "calcular" else password_hash
    db = main.SessionLocal()
    try:
        db.add(main.models.Usuario(nombre="QA Login", email=email, password_hash=hash_, rol=rol))
        db.commit()
    finally:
        db.close()
    return email


def _login(email, password):
    return client.post("/usuarios/login", json={"email": email, "password": password})


def _fila_de_intentos(email):
    db = main.SessionLocal()
    try:
        fila = db.get(main.models.IntentoLogin, acceso.normalizar_email(email))
        if fila is not None:
            db.expunge(fila)
        return fila
    finally:
        db.close()


def _editar_intentos(email, **cambios):
    db = main.SessionLocal()
    try:
        fila = db.get(main.models.IntentoLogin, acceso.normalizar_email(email))
        for campo, valor in cambios.items():
            setattr(fila, campo, valor)
        db.commit()
    finally:
        db.close()


def _registrar(email, password):
    return client.post("/usuarios/registro", json={"nombre": "QA Registro", "email": email, "password": password, "rol": "comprador"})


def _existe_usuario(email):
    db = main.SessionLocal()
    try:
        return db.query(main.models.Usuario).filter(main.models.Usuario.email == email).first() is not None
    finally:
        db.close()


def test_el_registro_rechaza_contrasenas_cortas_largas_comunes_o_iguales_al_correo():
    email = _email()
    casos = [
        ("abc1234", "al menos 8 caracteres"),
        ("        ", "solo espacios"),
        ("x" * 73, "demasiado larga"),
        ("ñ" * 40, "demasiado larga"),  # 40 caracteres pero 80 bytes: bcrypt solo mira los 72 primeros
        ("12345678", "demasiado común"),
        ("PassWord", "demasiado común"),
        (email, "igual a tu correo"),
    ]
    for password, motivo in casos:
        resp = _registrar(email, password)
        assert resp.status_code == 422, (password, resp.text)
        assert motivo in resp.text, (password, resp.text)
        assert not _existe_usuario(email)

    for password in ("clave-larga-y-buena", "a" * 72, "abcd1234x"):
        correo = _email()
        assert _registrar(correo, password).status_code == 200, password
        assert _login(correo, password).status_code == 200


def test_una_cuenta_anterior_con_contrasena_corta_sigue_entrando():
    """La regla de la contraseña se aplica solo al registrarse: no deja fuera a quien ya tenía cuenta."""
    email = _usuario_con_password("abc")
    assert _login(email, "abc").status_code == 200


def test_el_login_rechaza_una_contrasena_enorme_con_422():
    assert _login(_email(), "x" * 201).status_code == 422


def test_el_primer_fallo_no_avisa_del_bloqueo_y_los_ultimos_si(monkeypatch):
    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 8)
    email = _usuario_con_password("la-buena-123")

    primero = _login(email, "mala")
    assert primero.status_code == 401
    assert primero.json()["detail"] == "Email o contraseña incorrectos"

    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 4)  # ahora faltan 4 - 1 = 3 intentos
    segundo = _login(email, "mala")
    assert segundo.status_code == 401
    assert "Te quedan 2 intentos antes de un bloqueo temporal" in segundo.json()["detail"]


def test_tras_los_fallos_seguidos_el_correo_se_bloquea_incluso_con_la_contrasena_correcta(monkeypatch):
    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 4)
    email = _usuario_con_password("la-buena-123")

    detalles = [_login(email, "mala").json()["detail"] for _ in range(3)]
    assert "Te quedan 3 intentos" in detalles[0] and "Te quedan 1 intento antes" in detalles[2]

    ultimo = _login(email, "mala")  # el que agota el tope ya responde 429
    assert ultimo.status_code == 429, ultimo.text
    assert f"se bloqueó {acceso.BLOQUEO_LOGIN_MINUTOS} minutos" in ultimo.json()["detail"]
    assert ultimo.headers["Retry-After"] == str(acceso.BLOQUEO_LOGIN_MINUTOS * 60)

    correcta = _login(email, "la-buena-123")
    assert correcta.status_code == 429, correcta.text
    assert "access_token" not in correcta.text
    assert "minuto(s)" in correcta.json()["detail"]


def test_pasado_el_bloqueo_se_puede_entrar_y_se_reinicia_la_cuenta(monkeypatch):
    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 3)
    email = _usuario_con_password("la-buena-123")
    for _ in range(3):
        _login(email, "mala")
    assert _login(email, "la-buena-123").status_code == 429

    _editar_intentos(email, bloqueado_hasta=datetime.utcnow() - timedelta(seconds=1))  # pasó el tiempo

    ok = _login(email, "la-buena-123")
    assert ok.status_code == 200, ok.text
    assert _fila_de_intentos(email) is None


def test_un_correo_que_no_existe_se_bloquea_igual_y_con_los_mismos_mensajes(monkeypatch):
    """Si solo se bloquearan las cuentas que existen, el bloqueo delataría qué correos están registrados."""
    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 3)
    real = _usuario_con_password("la-buena-123")
    fantasma = _email()

    respuestas_reales = [_login(real, "mala") for _ in range(3)]
    respuestas_fantasma = [_login(fantasma, "mala") for _ in range(3)]

    assert [r.status_code for r in respuestas_reales] == [r.status_code for r in respuestas_fantasma] == [401, 401, 429]
    assert [r.json()["detail"] for r in respuestas_reales] == [r.json()["detail"] for r in respuestas_fantasma]


def test_un_inicio_de_sesion_correcto_reinicia_la_cuenta(monkeypatch):
    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 4)
    email = _usuario_con_password("la-buena-123")

    for _ in range(2):
        assert _login(email, "mala").status_code == 401
    assert _login(email, "la-buena-123").status_code == 200
    for _ in range(2):
        assert _login(email, "mala").status_code == 401  # 2 + 2 fallos, pero separados por un acierto: sin bloqueo
    assert _fila_de_intentos(email).fallos == 2


def test_los_fallos_de_hace_mas_de_una_hora_ya_no_cuentan(monkeypatch):
    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 4)
    email = _usuario_con_password("la-buena-123")
    for _ in range(3):
        _login(email, "mala")
    _editar_intentos(email, ultimo_fallo=datetime.utcnow() - timedelta(minutes=acceso.OLVIDO_DE_FALLOS_MINUTOS + 5))

    resp = _login(email, "mala")  # sin el olvido sería el 4º y bloquearía

    assert resp.status_code == 401, resp.text
    assert _fila_de_intentos(email).fallos == 1


def test_el_correo_cuenta_igual_con_mayusculas_o_espacios_y_no_afecta_a_otras_cuentas(monkeypatch):
    monkeypatch.setattr(acceso, "MAX_INTENTOS_LOGIN", 3)
    email = _usuario_con_password("la-buena-123")
    otra = _usuario_con_password("otra-buena-123")

    assert _login(email.upper(), "mala").status_code == 401
    assert _login(email, "mala").status_code == 401
    assert _login(email.upper(), "mala").status_code == 429  # los tres intentos contaron para la misma cuenta

    assert _login(otra, "otra-buena-123").status_code == 200  # otra cuenta no se enteró


def test_los_correos_que_no_existen_no_llenan_la_tabla_para_siempre(monkeypatch):
    viejo = _email()
    db = main.SessionLocal()
    try:
        db.add(main.models.IntentoLogin(email=viejo, fallos=2, ultimo_fallo=datetime.utcnow() - timedelta(hours=3)))
        db.commit()
    finally:
        db.close()

    _login(_email(), "mala")  # un fallo cualquiera limpia las filas viejas sin bloqueo vigente

    assert _fila_de_intentos(viejo) is None


def test_siempre_se_compara_una_contrasena_exista_o_no_la_cuenta(monkeypatch):
    """Sin esto, responder más rápido a un correo desconocido delataba qué correos están registrados."""
    comparaciones = []
    original = main.verificar_password

    def espia(password, hash_):
        comparaciones.append(hash_)
        return original(password, hash_)

    monkeypatch.setattr(main, "verificar_password", espia)
    solo_google = _usuario_con_password("no-importa", password_hash=None)

    _login(_email(), "mala")  # el correo no existe
    _login(solo_google, "mala")  # cuenta sin contraseña propia

    assert comparaciones == [seguridad.HASH_DE_RELLENO, seguridad.HASH_DE_RELLENO]





# ============ Tipo de comprador: persona o negocio (observación de la presentación del 2026-10-01) ============

def _registrar_comprador(**extra):
    return client.post("/usuarios/registro", json={
        "nombre": "Comprador", "email": _email(), "password": "test1234", "rol": "comprador", **extra})


def _perfil_comprador_de(usuario_id, rol="comprador"):
    _auth(usuario_id, rol)
    return client.get(f"/usuarios/{usuario_id}").json()


def test_el_digito_verificador_del_ruc():
    for valido in ("20131312955", "20100047218"):  # SUNAT y BCP
        assert main.ruc_valido(valido), valido
    for invalido in ("20131312954", "2013131295", "201313129551", "30131312955", "2013131295a", ""):
        assert not main.ruc_valido(invalido), invalido


def test_un_comprador_se_registra_como_persona_por_defecto():
    resp = _registrar_comprador()
    assert resp.status_code == 200, resp.text
    assert resp.json()["tipo_comprador"] == "persona" and resp.json()["ruc"] is None


def test_un_negocio_se_registra_con_ruc_y_nombre_comercial():
    resp = _registrar_comprador(tipo_comprador="negocio", ruc=" 20131312955 ", nombre_comercial="  Bodega   Doña Rosa ")
    assert resp.status_code == 200, resp.text
    perfil = _perfil_comprador_de(resp.json()["id"])
    assert perfil["tipo_comprador"] == "negocio"
    assert perfil["ruc"] == "20131312955" and perfil["nombre_comercial"] == "Bodega Doña Rosa"


def test_un_negocio_puede_registrarse_sin_ruc():
    resp = _registrar_comprador(tipo_comprador="negocio")
    assert resp.status_code == 200, resp.text
    assert resp.json()["tipo_comprador"] == "negocio" and resp.json()["ruc"] is None


def test_un_ruc_mal_escrito_o_un_tipo_inventado_fallan_422_y_no_crean_la_cuenta():
    email = _email()
    for extra in ({"tipo_comprador": "negocio", "ruc": "20131312954"}, {"tipo_comprador": "mayorista"},
                  {"tipo_comprador": "negocio", "nombre_comercial": "x" * 121}):
        resp = client.post("/usuarios/registro", json={
            "nombre": "Comprador", "email": email, "password": "test1234", "rol": "comprador", **extra})
        assert resp.status_code == 422, (extra, resp.text)
    assert client.post("/usuarios/login", json={"email": email, "password": "test1234"}).status_code == 401


def test_una_persona_no_guarda_ruc_aunque_lo_mande():
    resp = _registrar_comprador(tipo_comprador="persona", ruc="20131312955", nombre_comercial="Algo")
    assert resp.json()["ruc"] is None and resp.json()["nombre_comercial"] is None


def test_los_otros_roles_no_tienen_tipo_de_comprador():
    resp = client.post("/usuarios/registro", json={
        "nombre": "Productor", "email": _email(), "password": "test1234", "rol": "productor",
        "tipo_comprador": "negocio", "ruc": "20131312955"})
    assert resp.status_code == 200, resp.text
    assert "tipo_comprador" not in resp.json()
    assert "tipo_comprador" not in _perfil_comprador_de(resp.json()["id"], "productor")


def test_el_comprador_cambia_su_tipo_desde_el_perfil():
    usuario_id = _registrar_comprador().json()["id"]
    _auth(usuario_id, "comprador")

    resp = client.patch("/usuarios/me/comprador", json={"tipo_comprador": "negocio", "ruc": "20100047218",
                                                        "nombre_comercial": "Mercado Central"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tipo_comprador"] == "negocio" and resp.json()["ruc"] == "20100047218"

    resp = client.patch("/usuarios/me/comprador", json={"tipo_comprador": "persona"})
    assert resp.json()["tipo_comprador"] == "persona" and resp.json()["ruc"] is None
    assert _perfil_comprador_de(usuario_id)["nombre_comercial"] is None


def test_cambiar_el_tipo_valida_igual_que_el_registro_y_es_solo_para_compradores():
    usuario_id = _registrar_comprador().json()["id"]
    _auth(usuario_id, "comprador")
    assert client.patch("/usuarios/me/comprador", json={"tipo_comprador": "negocio", "ruc": "123"}).status_code == 422
    assert client.patch("/usuarios/me/comprador", json={}).status_code == 422

    _auth(_crear_usuario("productor"), "productor")
    assert client.patch("/usuarios/me/comprador", json={"tipo_comprador": "negocio"}).status_code == 403


def test_un_comprador_de_antes_sin_tipo_se_ve_como_persona():
    usuario_id = _registrar_comprador().json()["id"]
    db = main.SessionLocal()
    try:
        db.execute(text("UPDATE usuarios SET tipo_comprador = NULL WHERE id = :id"), {"id": usuario_id})
        db.commit()
    finally:
        db.close()
    assert _perfil_comprador_de(usuario_id)["tipo_comprador"] == "persona"


def test_un_comprador_nuevo_con_google_queda_como_persona(monkeypatch):
    _mockear_google(monkeypatch, sub=f"google-{uuid.uuid4().hex}", email=_email())
    resp = client.post("/usuarios/oauth/google", json={"id_token": "x", "rol": "comprador"})
    assert resp.status_code == 200, resp.text
    usuario_id = _decodificar_jwt(resp.json()["access_token"])["sub"]
    assert _perfil_comprador_de(usuario_id)["tipo_comprador"] == "persona"
