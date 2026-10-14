"""
Tests de la entidad Chacra y su vínculo con RegistroProduccion, y de completar la cosecha con evidencia en origen
(el rol Verificador se retiró en la presentación del 2026-10-01).

Cubre:
- creación válida de chacra
- código duplicado para el MISMO productor -> 400
- mismo código para dos productores DISTINTOS -> ambos OK (el UniqueConstraint es compuesto)
- crear RegistroProduccion con una chacra que no es del productor autenticado -> 403
- completar la cosecha exige la foto (subida antes, con su huella SHA-256), crea el borrador en Productos y deja el
  registro en "cosechado", sin pasar por nadie
- la ubicación del agricultor al sembrar y al cosechar: dentro o fuera del radio de la chacra se permite igual (queda la
  distancia, y la advertencia en la cadena); sin GPS también
- la evidencia llega a Certificación (texto con distancias y huella, y la foto); si Certificación no responde, la
  cosecha se completa igual
- si Productos falla, la cosecha no se completa (502) y se puede reintentar; su 409 es un reintento idempotente
- las cosechas que quedaron pendientes o rechazadas con el Verificador se completan con foto
- datos personales: las rutas públicas de productores no devuelven teléfono, usuario_id ni las
  coordenadas exactas (van redondeadas); el dueño lo ve todo en /me y el admin en /admin/todos
- el servidor exige lo que el formulario solo sugiere: números finitos y acotados (sin costos
  negativos, NaN ni Infinity), textos con largo máximo, coordenadas válidas y fechas coherentes
  (cosecha no anterior a la siembra ni en el futuro)

Requiere una base de datos Postgres real accesible según database.py (tipos UUID de
postgresql + create_all() al importar main) — no se puede sustituir por SQLite.
Este archivo no levanta Productos, Certificación ni MinIO: un fixture reemplaza las funciones puntuales que los llaman
(no el circuit breaker ni httpx) para no depender de una red real ni de sus timeouts.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import main
from auth import verificar_token

client = TestClient(main.app)

# Referencia a la función real ANTES de que el fixture de abajo la reemplace en cada test — los
# tests del propio helper la necesitan intacta.
_registrar_certificacion_real = main._registrar_certificacion_cosecha


@pytest.fixture(autouse=True)
def _sin_certificacion_real(monkeypatch):
    """completar_cosecha avisa a Certificación (best-effort) — ningún test de acá levanta ese
    servicio, así que por defecto se reemplaza por un stub que responde "registrado"."""
    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", MagicMock(return_value=True))


class _RespuestaFalsaProductos:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


JPEG_FALSO = b"\xff\xd8\xff\xe0" + b"foto de la cosecha" * 10
FOTO_URL_FALSA = "http://localhost:9000/cosechas-evidencias/foto-de-prueba.jpg"


@pytest.fixture(autouse=True)
def _catalogo_y_almacen_simulados(monkeypatch):
    """Productos crea el borrador (200, con un id nuevo), MinIO "guarda" la foto con una URL fija y el precio de
    referencia es S/ 10 el kg. Los tests que necesitan otra respuesta la reemplazan. Se fuerza el breaker a cerrado para
    que un fallo inducido por otro test no deje el circuito abierto y contamine este."""
    main.breaker_crear_producto.close()
    monkeypatch.setattr(
        main, "_pedir_crear_producto_desde_cosecha",
        lambda payload: _RespuestaFalsaProductos(200, {"id": str(uuid.uuid4())}),
    )
    monkeypatch.setattr(main, "subir_foto_cosecha", MagicMock(return_value=FOTO_URL_FALSA))
    monkeypatch.setattr(main, "_consultar_precio_referencia", lambda cultivo: (10.0, None))


def _auth(sub: str, rol: str = "productor"):
    """Simula al usuario autenticado. Se sobreescribe `verificar_token` (dependencia estable),
    no `requiere_rol(...)` que genera un closure nuevo por llamada."""
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}


def _nuevo_productor() -> str:
    """Crea un productor con un usuario nuevo y deja ese usuario como el autenticado.
    Devuelve el `sub` (usuario_id) usado."""
    sub = str(uuid.uuid4())
    _auth(sub)
    resp = client.post("/productores", json={"nombre": f"Productor {sub[:8]}"})
    assert resp.status_code == 200, resp.text
    return sub


def _chacra_payload(codigo="CH-01", nombre="Chacra del cerro"):
    return {
        "codigo": codigo,
        "nombre": nombre,
        "ubicacion_latitud": -13.16,
        "ubicacion_longitud": -74.22,
    }


def _registro_payload(chacra_id):
    siembra = datetime(2026, 1, 1)
    return {
        "chacra_id": str(chacra_id),
        "cultivo": "papa",
        "numero_parcelas": 2,
        "costo_semillas": 100.0,
        "costo_insumos": 50.0,
        "unidad_medida": "kg",
        "fecha_siembra": siembra.isoformat(),
        "fecha_cosecha_estimada": (siembra + timedelta(days=120)).isoformat(),
    }


def test_crear_chacra_valida():
    _nuevo_productor()
    resp = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["codigo"] == "CH-01"
    assert cuerpo["nombre"] == "Chacra del cerro"
    assert cuerpo["ubicacion_latitud"] == -13.16
    assert "id" in cuerpo and "productor_id" in cuerpo


def test_codigo_duplicado_mismo_productor_falla_400():
    _nuevo_productor()
    primera = client.post("/chacras", json=_chacra_payload(codigo="CH-07"))
    assert primera.status_code == 200, primera.text

    repetida = client.post("/chacras", json=_chacra_payload(codigo="CH-07"))
    assert repetida.status_code == 400
    assert repetida.json()["detail"] == "Ya tienes una chacra registrada con el código 'CH-07'."


def test_mismo_codigo_dos_productores_distintos_ok():
    _nuevo_productor()
    r1 = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))
    assert r1.status_code == 200, r1.text

    _nuevo_productor()  # segundo productor, usuario distinto
    r2 = client.post("/chacras", json=_chacra_payload(codigo="CH-01"))
    assert r2.status_code == 200, r2.text

    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["productor_id"] != r2.json()["productor_id"]


def test_registro_produccion_con_chacra_ajena_falla_403():
    # Productor A crea una chacra.
    _nuevo_productor()
    chacra_a = client.post("/chacras", json=_chacra_payload(codigo="CH-A"))
    assert chacra_a.status_code == 200, chacra_a.text
    chacra_a_id = chacra_a.json()["id"]

    # Productor B (usuario distinto) intenta usar la chacra de A.
    _nuevo_productor()
    resp = client.post("/productores/produccion", json=_registro_payload(chacra_a_id))

    assert resp.status_code == 403
    assert resp.json()["detail"] == "La chacra especificada no pertenece a tu cuenta."


def test_registro_produccion_con_chacra_propia_ok():
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-OK"))
    assert chacra.status_code == 200, chacra.text
    chacra_id = chacra.json()["id"]

    resp = client.post("/productores/produccion", json=_registro_payload(chacra_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["chacra_id"] == chacra_id


def test_crear_registro_produccion_con_unidad_chica_valida_funciona():
    """kg, unidad y litro son las únicas unidades chicas/granulares permitidas como base de una
    cosecha — las de empaque (saco, arroba) se agregan después en Productos, como unidad
    alternativa del producto ya publicado."""
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-UNI"))
    assert chacra.status_code == 200, chacra.text
    chacra_id = chacra.json()["id"]

    for unidad in ["kg", "unidad", "litro"]:
        payload = _registro_payload(chacra_id)
        payload["cultivo"] = "Producto sin plantilla"  # un alimento reconocido solo admite sus unidades (ver H1)
        payload["unidad_medida"] = unidad
        if unidad != "kg":
            payload["equivalencia_kg"] = 1.5
        resp = client.post("/productores/produccion", json=payload)
        assert resp.status_code == 200, resp.text
        assert resp.json()["unidad_medida"] == unidad


def test_crear_registro_produccion_con_unidad_de_empaque_falla_422():
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-EMP"))
    assert chacra.status_code == 200, chacra.text
    chacra_id = chacra.json()["id"]

    for unidad in ["saco", "arroba"]:
        payload = _registro_payload(chacra_id)
        payload["unidad_medida"] = unidad
        payload["equivalencia_kg"] = 10.0  # aunque venga, la unidad en sí ya no es válida como base
        resp = client.post("/productores/produccion", json=payload)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == (
            "La unidad de la cosecha debe ser kg, unidad o litro. Las unidades de empaque "
            "(saco, arroba) se agregan después, como unidad de venta alternativa del producto."
        )


# ============ Completar la cosecha: evidencia en origen, sin Verificador ============

# La chacra de _chacra_payload está en (-13.16, -74.22).
CERCA = {"latitud": -13.165, "longitud": -74.22}   # ~0.56 km
LEJOS = {"latitud": -13.20, "longitud": -74.22}    # ~4.45 km


def _subir_foto(registro_id, datos=JPEG_FALSO, tipo="image/jpeg"):
    return client.post(
        f"/productores/produccion/{registro_id}/foto-cosecha", files={"archivo": ("cosecha.jpg", datos, tipo)},
    )


def _datos_cosecha(cantidad=50.0, **extra):
    return {
        "cantidad_cosechada": cantidad,
        "fecha_cosecha_real": datetime(2026, 5, 1).isoformat(),
        "costo_mano_obra": 20.0,
        **extra,
    }


def _completar_cosecha(registro_id, cantidad=50.0, **extra):
    return client.patch(
        f"/productores/produccion/{registro_id}/completar-cosecha", json=_datos_cosecha(cantidad, **extra),
    )


def _crear_registro_planificado(unidad_medida="kg", equivalencia_kg=None, **ubicacion) -> str:
    """Productor nuevo (queda como el autenticado) + chacra + siembra. Devuelve el id del registro."""
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}"))
    assert chacra.status_code == 200, chacra.text
    payload = {**_registro_payload(chacra.json()["id"]), "unidad_medida": unidad_medida, **ubicacion}
    if equivalencia_kg is not None:
        payload["equivalencia_kg"] = equivalencia_kg
    registro = client.post("/productores/produccion", json=payload)
    assert registro.status_code == 200, registro.text
    assert registro.json()["estado"] == "planificado"
    return registro.json()["id"]


def _crear_registro_con_foto(**kwargs) -> str:
    registro_id = _crear_registro_planificado(**kwargs)
    foto = _subir_foto(registro_id)
    assert foto.status_code == 200, foto.text
    return registro_id


def _fila(registro_id):
    db = main.SessionLocal()
    try:
        return db.query(main.models.RegistroProduccion).filter(main.models.RegistroProduccion.id == registro_id).one()
    finally:
        db.close()


def _poner_estado(registro_id, estado):
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.RegistroProduccion).filter(main.models.RegistroProduccion.id == registro_id).one()
        fila.estado = estado
        db.commit()
    finally:
        db.close()


def _mi_registro(registro_id):
    return next(r for r in client.get("/productores/produccion/me").json() if r["id"] == registro_id)


def test_completar_la_cosecha_publica_el_borrador_sin_pasar_por_un_verificador():
    registro_id = _crear_registro_con_foto()

    resp = _completar_cosecha(registro_id)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["estado"] == "cosechado"
    assert cuerpo["producto_id"] is not None
    assert cuerpo["certificacion_registrada"] is True
    assert cuerpo["foto_cosecha_url"] == FOTO_URL_FALSA
    actual = _mi_registro(registro_id)
    assert actual["estado"] == "cosechado" and actual["cantidad_cosechada"] == 50.0


def test_completar_sin_foto_falla_422_y_no_crea_el_producto(monkeypatch):
    registro_id = _crear_registro_planificado()
    crear = MagicMock()
    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", crear)

    resp = _completar_cosecha(registro_id)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "Falta la foto de la cosecha: tómala desde tu chacra antes de confirmar."
    crear.assert_not_called()
    assert _fila(registro_id).estado == "planificado"


def test_la_foto_guarda_su_huella_sha256_y_se_puede_reemplazar():
    registro_id = _crear_registro_planificado()

    primera = _subir_foto(registro_id)

    assert primera.status_code == 200, primera.text
    huella = hashlib.sha256(JPEG_FALSO).hexdigest()
    assert primera.json() == {"foto_cosecha_url": FOTO_URL_FALSA, "foto_cosecha_sha256": huella}
    fila = _fila(registro_id)
    assert fila.foto_cosecha_sha256 == huella and fila.foto_cosecha_fecha is not None
    # vale la última: otra foto, otra huella
    otra = JPEG_FALSO + b"otra"
    assert _subir_foto(registro_id, datos=otra).status_code == 200
    assert _fila(registro_id).foto_cosecha_sha256 == hashlib.sha256(otra).hexdigest()


def test_la_foto_rechaza_lo_que_no_es_una_imagen():
    registro_id = _crear_registro_planificado()

    assert _subir_foto(registro_id, datos=b"hola", tipo="text/plain").status_code == 422
    disfrazado = _subir_foto(registro_id, datos=b"<html><script>alert(1)</script></html>", tipo="image/jpeg")
    assert disfrazado.status_code == 422
    assert disfrazado.json()["detail"] == "El archivo no es una imagen JPEG, PNG o WEBP válida."
    main.subir_foto_cosecha.assert_not_called()
    assert _fila(registro_id).foto_cosecha_url is None


def test_una_foto_de_mas_de_5_mb_falla_413():
    registro_id = _crear_registro_planificado()
    enorme = b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)

    resp = _subir_foto(registro_id, datos=enorme)

    assert resp.status_code == 413, resp.text
    assert _fila(registro_id).foto_cosecha_url is None


def test_si_no_se_puede_guardar_la_foto_responde_502_y_no_guarda_nada(monkeypatch):
    registro_id = _crear_registro_planificado()
    monkeypatch.setattr(main, "subir_foto_cosecha", MagicMock(side_effect=Exception("MinIO caído")))

    resp = _subir_foto(registro_id)

    assert resp.status_code == 502, resp.text
    fila = _fila(registro_id)
    assert fila.foto_cosecha_url is None and fila.foto_cosecha_sha256 is None


def test_la_foto_y_la_cosecha_solo_las_completa_su_dueno():
    registro_id = _crear_registro_planificado()

    _nuevo_productor()  # otro productor
    assert _subir_foto(registro_id).status_code == 403
    assert _completar_cosecha(registro_id).status_code == 403
    _auth(str(uuid.uuid4()), rol="comprador")
    assert _subir_foto(registro_id).status_code == 403
    assert _completar_cosecha(registro_id).status_code == 403

    _nuevo_productor()
    assert _subir_foto(uuid.uuid4()).status_code == 404
    assert _subir_foto("no-es-un-uuid").status_code == 422
    assert _completar_cosecha("no-es-un-uuid").status_code == 422
    assert _fila(registro_id).foto_cosecha_url is None


def test_una_cosecha_completada_no_se_completa_ni_cambia_su_foto_otra_vez():
    registro_id = _crear_registro_con_foto()
    assert _completar_cosecha(registro_id).status_code == 200

    otra_vez = _completar_cosecha(registro_id, cantidad=999.0)
    otra_foto = _subir_foto(registro_id, datos=JPEG_FALSO + b"otra")

    assert otra_vez.status_code == 409 and otra_vez.json()["detail"] == "Esta cosecha ya fue completada"
    assert otra_foto.status_code == 409
    fila = _fila(registro_id)
    assert float(fila.cantidad_cosechada) == 50.0
    assert fila.foto_cosecha_sha256 == hashlib.sha256(JPEG_FALSO).hexdigest()


def test_dentro_del_radio_de_la_chacra_queda_la_distancia_sin_las_coordenadas():
    registro_id = _crear_registro_con_foto(**CERCA)

    resp = _completar_cosecha(registro_id, **CERCA)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["radio_chacra_km"] == 1.5
    assert 0.5 < cuerpo["siembra_distancia_km"] < 0.6 and 0.5 < cuerpo["cosecha_distancia_km"] < 0.6
    assert "latitud" not in json.dumps(cuerpo)  # al agricultor se le dice la distancia, no se le devuelven coordenadas
    fila = _fila(registro_id)
    assert (fila.siembra_latitud, fila.siembra_longitud) == (-13.165, -74.22)
    assert (fila.cosecha_latitud, fila.cosecha_longitud) == (-13.165, -74.22)


def test_lejos_de_la_chacra_se_permite_igual_y_queda_la_distancia():
    registro_id = _crear_registro_con_foto(**LEJOS)

    resp = _completar_cosecha(registro_id, **LEJOS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "cosechado"
    assert resp.json()["siembra_distancia_km"] > 4 and resp.json()["cosecha_distancia_km"] > 4


def test_sin_gps_se_permite_igual_y_la_distancia_queda_vacia():
    registro_id = _crear_registro_con_foto()

    resp = _completar_cosecha(registro_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["siembra_distancia_km"] is None and resp.json()["cosecha_distancia_km"] is None


def test_la_ubicacion_necesita_latitud_y_longitud_validas():
    chacra_id = _nueva_chacra()
    for ubicacion in ({"latitud": -13.16}, {"longitud": -74.22}, {"latitud": 95, "longitud": -74.22},
                      {"latitud": -13.16, "longitud": 181}):
        resp = client.post("/productores/produccion", json={**_registro_payload(chacra_id), **ubicacion})
        assert resp.status_code == 422, (ubicacion, resp.text)
    assert client.get("/productores/produccion/me").json() == []

    registro_id = _crear_registro_con_foto()
    url = f"/productores/produccion/{registro_id}/completar-cosecha"
    assert _completar_cosecha(registro_id, longitud=-74.22).status_code == 422
    crudo = json.dumps(_datos_cosecha(latitud=0, longitud=-74.22)).replace('"latitud": 0', '"latitud": NaN')
    assert _con_json_crudo("PATCH", url, crudo).status_code == 422
    assert _fila(registro_id).estado == "planificado"


def test_la_evidencia_llega_a_la_cadena_de_certificacion(monkeypatch):
    registro_id = _crear_registro_con_foto(**CERCA)
    monkeypatch.setattr(
        main, "_pedir_crear_producto_desde_cosecha",
        lambda payload: _RespuestaFalsaProductos(200, {"id": "producto-cert"}),
    )
    llamadas = []

    def _registrar(registro, productor, chacra, producto_id):
        # Se lee acá, con la sesión de la petición abierta (como lo hace la función real).
        llamadas.append({
            "registro_id": str(registro.id), "estado": registro.estado, "sha256": registro.foto_cosecha_sha256,
            "siembra_km": registro.siembra_distancia_km, "cosecha_km": registro.cosecha_distancia_km,
            "chacra": chacra.codigo, "productor": productor.nombre, "producto_id": producto_id,
        })
        return True

    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", _registrar)

    resp = _completar_cosecha(registro_id, **LEJOS)

    assert resp.status_code == 200, resp.text
    assert len(llamadas) == 1
    llamada = llamadas[0]
    assert llamada["producto_id"] == "producto-cert"
    assert llamada["registro_id"] == registro_id and llamada["estado"] == "cosechado"
    assert llamada["sha256"] == hashlib.sha256(JPEG_FALSO).hexdigest()
    assert llamada["siembra_km"] < 1.5 < llamada["cosecha_km"]
    assert llamada["chacra"].startswith("CH-") and llamada["productor"].startswith("Productor ")


def _capturar_post_a_certificacion(monkeypatch, status_code=200):
    capturado = {}

    def _post_falso(url, json=None, headers=None, timeout=None):
        capturado.update(url=url, json=json, headers=headers)
        return SimpleNamespace(status_code=status_code)

    monkeypatch.setattr(main.httpx, "post", _post_falso)
    monkeypatch.setattr(main, "PRODUCTORES_A_CERTIFICACION_SECRETO", "secreto-de-prueba")
    return capturado


def _registro_para_la_cadena(**cambios):
    return SimpleNamespace(**{
        "cantidad_cosechada": 45.5, "unidad_medida": "kg", "cultivo": "Papa Nativa",
        "fecha_siembra": datetime(2026, 1, 1), "fecha_cosecha_real": datetime(2026, 8, 20),
        "siembra_distancia_km": 0.42, "cosecha_distancia_km": 3.1,
        "foto_cosecha_sha256": "ab" * 32, "foto_cosecha_url": FOTO_URL_FALSA,
        **cambios,
    })


def test_registrar_certificacion_cosecha_manda_la_evidencia_y_el_secreto(monkeypatch):
    capturado = _capturar_post_a_certificacion(monkeypatch)
    productor = SimpleNamespace(nombre="Sol Naciente")
    chacra = SimpleNamespace(codigo="CH-01", nombre="Parcela norte")

    ok = _registrar_certificacion_real(_registro_para_la_cadena(), productor, chacra, "producto-1")

    assert ok is True
    assert capturado["url"].endswith("/certificacion/producto-1/cosecha-aprobada")
    assert capturado["headers"]["X-Servicio-Secreto"] == "secreto-de-prueba"
    assert capturado["json"]["foto_url"] == FOTO_URL_FALSA
    assert "verificador_usuario_id" not in capturado["json"] and "datos_certificado" not in capturado["json"]
    texto = capturado["json"]["datos_cosecha"]
    assert "45.5 kg de Papa Nativa" in texto and "CH-01 (Parcela norte)" in texto and "Sol Naciente" in texto
    assert "Al registrar la siembra: a 0,42 km de la chacra (dentro del radio de 1,5 km)." in texto
    assert "Al completar la cosecha: a 3,10 km de la chacra, fuera del radio de 1,5 km (advertencia)." in texto
    assert f"Foto de la cosecha (SHA-256 {'ab' * 32})." in texto


def test_sin_gps_la_cadena_lo_dice_como_advertencia(monkeypatch):
    capturado = _capturar_post_a_certificacion(monkeypatch)
    registro = _registro_para_la_cadena(siembra_distancia_km=None, cosecha_distancia_km=None)

    _registrar_certificacion_real(registro, SimpleNamespace(nombre="X"), None, "producto-1")

    texto = capturado["json"]["datos_cosecha"]
    assert "Al registrar la siembra: sin ubicación GPS (advertencia)." in texto
    assert "Al completar la cosecha: sin ubicación GPS (advertencia)." in texto
    assert "chacra no especificada" in texto


def test_registrar_certificacion_cosecha_devuelve_false_si_falla(monkeypatch):
    _capturar_post_a_certificacion(monkeypatch, status_code=500)
    assert _registrar_certificacion_real(_registro_para_la_cadena(), SimpleNamespace(nombre="X"), None, "p") is False

    def _post_roto(*a, **kw):
        raise main.httpx.RequestError("fallo simulado")

    monkeypatch.setattr(main.httpx, "post", _post_roto)
    assert _registrar_certificacion_real(_registro_para_la_cadena(), SimpleNamespace(nombre="X"), None, "p") is False


def test_si_certificacion_no_responde_la_cosecha_se_completa_igual(monkeypatch):
    registro_id = _crear_registro_con_foto()
    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", MagicMock(return_value=False))

    resp = _completar_cosecha(registro_id)

    # El producto ya existe: el registro en la cadena es reintentable (endpoint idempotente), no motivo para deshacerlo.
    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "cosechado"
    assert resp.json()["certificacion_registrada"] is False


def _capturar_payload_a_productos(monkeypatch):
    capturado = {}

    def _capturar(payload):
        capturado.update(payload)
        return _RespuestaFalsaProductos(200, {"id": "producto-capturado"})

    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", _capturar)
    return capturado


def test_completar_envia_el_payload_correcto_a_productos(monkeypatch):
    registro_id = _crear_registro_con_foto()
    capturado = _capturar_payload_a_productos(monkeypatch)

    resp = _completar_cosecha(registro_id, cantidad=45.6)

    assert resp.status_code == 200, resp.text
    fila = _fila(registro_id)
    assert capturado["registro_produccion_id"] == registro_id
    assert capturado["productor_id"] == str(fila.productor_id)
    assert capturado["productor_nombre"].startswith("Productor ")
    assert capturado["nombre"] == "papa" and capturado["unidad_medida"] == "kg"
    assert capturado["stock"] == 46  # se redondea, no se trunca
    assert capturado["equivalencia_kg"] is None  # cosecha en "kg" — no hace falta factor


def test_completar_envia_equivalencia_kg_cuando_la_cosecha_no_es_en_kg(monkeypatch):
    registro_id = _crear_registro_con_foto(unidad_medida="litro", equivalencia_kg=1.03, cultivo="Producto sin plantilla")
    capturado = _capturar_payload_a_productos(monkeypatch)

    assert _completar_cosecha(registro_id).status_code == 200
    assert capturado["unidad_medida"] == "litro" and capturado["equivalencia_kg"] == 1.03


def test_si_productos_falla_la_cosecha_no_se_completa_y_se_puede_reintentar(monkeypatch):
    registro_id = _crear_registro_con_foto()

    def _falla(payload):
        raise main.httpx.RequestError("fallo simulado de red")

    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", _falla)
    resp = _completar_cosecha(registro_id, **LEJOS)

    assert resp.status_code == 502
    assert resp.json()["detail"] == (
        "No se pudo crear el producto en el catálogo. Intenta completar la cosecha de nuevo en unos momentos."
    )
    fila = _fila(registro_id)  # nada de lo que se mandó quedó guardado
    assert fila.estado == "planificado" and fila.cantidad_cosechada is None and fila.cosecha_distancia_km is None

    for status_code in (500, 403):  # cualquier otra respuesta de Productos tampoco completa la cosecha
        monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", lambda p, s=status_code: _RespuestaFalsaProductos(s))
        assert _completar_cosecha(registro_id).status_code == 502
    assert _fila(registro_id).estado == "planificado"

    monkeypatch.setattr(
        main, "_pedir_crear_producto_desde_cosecha",
        lambda payload: _RespuestaFalsaProductos(200, {"id": "producto-tras-reintento"}),
    )
    reintento = _completar_cosecha(registro_id)
    assert reintento.status_code == 200, reintento.text
    assert reintento.json()["estado"] == "cosechado" and reintento.json()["producto_id"] == "producto-tras-reintento"


def test_un_409_de_productos_es_un_reintento_idempotente_y_no_se_certifica_sin_id(monkeypatch):
    # Productos ya había creado el producto en un intento anterior (UniqueConstraint sobre registro_produccion_id en
    # productos/models.py), pero la respuesta nunca llegó a confirmarse acá.
    registro_id = _crear_registro_con_foto()
    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", lambda payload: _RespuestaFalsaProductos(409))
    mock_cert = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_registrar_certificacion_cosecha", mock_cert)

    resp = _completar_cosecha(registro_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "cosechado"
    assert resp.json()["producto_id"] is None  # el 409 no devuelve el id del producto existente
    assert resp.json()["certificacion_registrada"] is False
    mock_cert.assert_not_called()


def test_una_cosecha_que_quedo_con_el_verificador_se_completa_con_su_foto():
    for estado in ("pendiente_verificacion", "rechazado"):
        registro_id = _crear_registro_planificado()
        _poner_estado(registro_id, estado)

        assert _completar_cosecha(registro_id).status_code == 422, estado  # también necesita la foto
        assert _subir_foto(registro_id).status_code == 200, estado
        resp = _completar_cosecha(registro_id)

        assert resp.status_code == 200, (estado, resp.text)
        assert resp.json()["estado"] == "cosechado"
        assert resp.json()["motivo_rechazo"] is None

    aprobado_id = _crear_registro_planificado()
    _poner_estado(aprobado_id, "aprobado")  # ya tiene su producto
    assert _subir_foto(aprobado_id).status_code == 409
    assert _completar_cosecha(aprobado_id).status_code == 409


def test_las_rutas_del_verificador_ya_no_existen():
    registro_id = _crear_registro_con_foto()
    _auth(str(uuid.uuid4()), rol="verificador")

    for metodo, ruta, cuerpo in (
        ("GET", "/productores/produccion/pendientes-verificacion", None),
        ("GET", "/productores/produccion/historial-verificacion", None),
        ("POST", f"/productores/produccion/{registro_id}/aprobar", None),
        ("POST", f"/productores/produccion/{registro_id}/rechazar", {"motivo": "no"}),
    ):
        assert client.request(metodo, ruta, json=cuerpo).status_code in (404, 405), ruta
    assert _fila(registro_id).estado == "planificado"


# ============ Validación de los formularios: el servidor no confía en el frontend ============

def _con_json_crudo(metodo, ruta, texto_json):
    """Manda el cuerpo tal cual: JSON.stringify no puede producir NaN ni Infinity, pero cualquier
    otro cliente de la API sí."""
    return client.request(metodo, ruta, content=texto_json, headers={"Content-Type": "application/json"})


def _nueva_chacra() -> str:
    _nuevo_productor()
    resp = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}"))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_chacra_con_datos_invalidos_falla_422_y_no_guarda_nada():
    _nuevo_productor()
    invalidas = [
        {**_chacra_payload(), "ubicacion_latitud": 95},
        {**_chacra_payload(), "ubicacion_longitud": -181},
        {**_chacra_payload(), "codigo": "   "},
        {**_chacra_payload(), "codigo": "X" * 31},
        {**_chacra_payload(), "nombre": "N" * 81},
        {**_chacra_payload(), "codigo": "A\u0000B"},
    ]
    for cuerpo in invalidas:
        resp = client.post("/chacras", json=cuerpo)
        assert resp.status_code == 422, (cuerpo, resp.text)
    for coordenada in ("NaN", "Infinity", "-Infinity"):
        crudo = '{"codigo": "CH-NAN", "ubicacion_latitud": %s, "ubicacion_longitud": -74.2}' % coordenada
        assert _con_json_crudo("POST", "/chacras", crudo).status_code == 422, coordenada
    assert client.get("/chacras/me").json() == []


def test_codigo_de_chacra_se_recorta():
    _nuevo_productor()
    resp = client.post("/chacras", json=_chacra_payload(codigo="  CH-99  "))
    assert resp.status_code == 200, resp.text
    assert resp.json()["codigo"] == "CH-99"


def test_perfil_de_productor_con_datos_invalidos_falla_422():
    _auth(str(uuid.uuid4()))
    for cuerpo in (
        {"nombre": ""}, {"nombre": "   "}, {"nombre": "N" * 121}, {"nombre": "ok", "contacto": "9" * 61},
        {"nombre": "ok", "latitud": "abc"}, {"nombre": "ok", "latitud": "95", "longitud": "-74"},
        {"nombre": "ok", "longitud": "NaN"}, {"nombre": "ok", "comunidad": "C" * 121},
    ):
        resp = client.post("/productores", json=cuerpo)
        assert resp.status_code == 422, (cuerpo, resp.text)
    ok = client.post("/productores", json={"nombre": "Con ubicación", "latitud": "-13.16", "longitud": "-74.22"})
    assert ok.status_code == 200, ok.text


def test_siembra_con_numeros_o_textos_invalidos_falla_422_y_no_guarda_nada():
    chacra_id = _nueva_chacra()
    cambios = [
        {"costo_semillas": -1}, {"costo_insumos": -0.01}, {"costo_semillas": 10_000_000}, {"numero_parcelas": 1001},
        {"numero_parcelas": 10**11}, {"unidad_medida": "litro", "equivalencia_kg": -2},
        {"unidad_medida": "litro", "equivalencia_kg": 5001}, {"cultivo": "   "}, {"cultivo": "P" * 81},
        {"cultivo": "pa\u0000pa"}, {"ubicacion_cosecha": "U" * 201},
    ]
    for cambio in cambios:
        resp = client.post("/productores/produccion", json={**_registro_payload(chacra_id), **cambio})
        assert resp.status_code == 422, (cambio, resp.text)
    for campo in ("costo_semillas", "costo_insumos"):
        for valor in ("NaN", "Infinity", "-Infinity"):
            crudo = json.dumps({**_registro_payload(chacra_id), campo: 0}).replace(f'"{campo}": 0', f'"{campo}": {valor}')
            assert _con_json_crudo("POST", "/productores/produccion", crudo).status_code == 422, (campo, valor)
    assert client.get("/productores/produccion/me").json() == []


def test_siembra_con_fechas_absurdas_falla_422_pero_planificar_a_futuro_cercano_si():
    chacra_id = _nueva_chacra()
    hoy = datetime.utcnow()
    absurdas = [
        (datetime(1999, 12, 31), datetime(2000, 6, 1)),
        (hoy + timedelta(days=731), hoy + timedelta(days=900)),
        (datetime(2026, 1, 1), datetime(2026, 1, 1) + timedelta(days=1826)),
        (datetime(2026, 3, 1), datetime(2026, 2, 1)),
    ]
    for siembra, estimada in absurdas:
        payload = {**_registro_payload(chacra_id), "fecha_siembra": siembra.isoformat(), "fecha_cosecha_estimada": estimada.isoformat()}
        resp = client.post("/productores/produccion", json=payload)
        assert resp.status_code == 422, (siembra, estimada, resp.text)

    planificada = {
        **_registro_payload(chacra_id),
        "fecha_siembra": (hoy + timedelta(days=30)).isoformat(),
        "fecha_cosecha_estimada": (hoy + timedelta(days=150)).isoformat(),
    }
    assert client.post("/productores/produccion", json=planificada).status_code == 200


def test_fechas_con_zona_horaria_no_rompen_la_comparacion():
    """Una fecha con zona ("...Z") junto a una sin zona lanzaba un error 500 al compararlas."""
    chacra_id = _nueva_chacra()
    payload = {**_registro_payload(chacra_id), "fecha_siembra": "2026-01-01T00:00:00Z", "fecha_cosecha_estimada": "2026-05-01T00:00:00"}
    resp = client.post("/productores/produccion", json=payload)
    assert resp.status_code == 200, resp.text
    payload["fecha_cosecha_estimada"] = "2026-01-01T04:00:00+00:00"
    assert client.post("/productores/produccion", json=payload).status_code == 200


def test_la_ganancia_ya_no_incluye_un_costo_de_envio(monkeypatch):
    """El "costo de envío final" se quitó del formulario de completar cosecha (presentación del 2026-10-01): si un
    cliente viejo lo manda se ignora, y un registro antiguo que lo tenga guardado no cambia su ganancia."""
    registro_id = _crear_registro_con_foto()

    resp = client.patch(f"/productores/produccion/{registro_id}/completar-cosecha", json={
        "cantidad_cosechada": 50.0, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat(),
        "costo_mano_obra": 20.0, "costo_envio": 999.0,  # campo retirado: se ignora
    })
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert "costo_envio" not in cuerpo
    # semillas 100 + insumos 50 + mano de obra 20; 50 kg x S/ 10 = 500
    assert cuerpo["costo_total"] == 170.0 and cuerpo["ganancia_estimada"] == 330.0

    db = main.SessionLocal()
    try:
        fila = db.query(main.models.RegistroProduccion).filter(main.models.RegistroProduccion.id == registro_id).first()
        assert float(fila.costo_envio) == 0  # no se guardó el 999
        fila.costo_envio = 40  # como un registro de antes del cambio
        db.commit()
    finally:
        db.close()
    actual = next(r for r in client.get("/productores/produccion/me").json() if r["id"] == registro_id)
    assert actual["costo_total"] == 170.0 and actual["ganancia_estimada"] == 330.0


def test_completar_cosecha_con_datos_invalidos_falla_422_y_no_cambia_nada(monkeypatch):
    registro_id = _crear_registro_con_foto()

    url = f"/productores/produccion/{registro_id}/completar-cosecha"
    base = {"cantidad_cosechada": 50.0, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat(), "costo_mano_obra": 20.0}
    hoy = datetime.utcnow()
    invalidos = [
        {"cantidad_cosechada": 1e12}, {"cantidad_cosechada": 1_000_001}, {"cantidad_cosechada": 0}, {"cantidad_cosechada": -5},
        {"costo_mano_obra": -1}, {"costo_mano_obra": 10_000_000},
        {"fecha_cosecha_real": datetime(2025, 12, 31).isoformat()},      # antes de la siembra (2026-01-01)
        {"fecha_cosecha_real": (hoy + timedelta(days=5)).isoformat()},   # en el futuro
    ]
    for cambio in invalidos:
        resp = client.patch(url, json={**base, **cambio})
        assert resp.status_code == 422, (cambio, resp.text)
    for campo in ("cantidad_cosechada", "costo_mano_obra"):
        for valor in ("NaN", "Infinity"):
            crudo = json.dumps(base).replace(f'"{campo}": {json.dumps(base[campo])}', f'"{campo}": {valor}')
            assert _con_json_crudo("PATCH", url, crudo).status_code == 422, (campo, valor)

    actual = next(r for r in client.get("/productores/produccion/me").json() if r["id"] == registro_id)
    assert actual["estado"] == "planificado" and actual["cantidad_cosechada"] is None

    sano = client.patch(url, json=base)
    assert sano.status_code == 200, sano.text
    assert sano.json()["estado"] == "cosechado"


# ============ Los secretos de servicio fallan cerrados ============

def test_los_endpoints_protegidos_por_secreto_rechazan_a_todos_si_el_secreto_no_esta_configurado(monkeypatch):
    """Antes cada guarda hacía `if cabecera != SECRETO`: con la variable de entorno sin definir
    (SECRETO = None) una petición sin cabecera (None) resultaba igual y PASABA. Ahora, sin secreto
    configurado, no pasa nadie; y con uno configurado solo pasa el correcto."""
    import uuid as _uuid
    protegidos = [
        ("DELETE", f"/productores/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
        ("DELETE", f"/productores/produccion/{_uuid.uuid4()}", None, "QA_LIMPIEZA_SECRETO"),
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


# ============ Datos personales: lo público, lo del dueño y lo del admin ============

CAMPOS_PUBLICOS = {"id", "nombre", "comunidad", "ubicacion", "fecha_registro", "latitud", "longitud"}


def _productor_con_datos_personales():
    """Crea un productor con teléfono y coordenadas exactas y deja a su usuario como el autenticado.
    Devuelve (usuario_id, productor_id)."""
    sub = str(uuid.uuid4())
    _auth(sub)
    resp = client.post("/productores", json={
        "nombre": "Finca de prueba", "comunidad": "Quinua", "contacto": "987654321",
        "latitud": "-13.158812", "longitud": "-74.223940",
    })
    assert resp.status_code == 200, resp.text
    return sub, resp.json()["id"]


def test_las_rutas_publicas_de_productores_no_exponen_telefono_usuario_ni_coordenadas_exactas():
    sub, productor_id = _productor_con_datos_personales()

    lista = client.get("/productores")
    assert lista.status_code == 200
    en_lista = next(p for p in lista.json() if p["id"] == productor_id)
    detalle = client.get(f"/productores/{productor_id}")
    assert detalle.status_code == 200

    for vista in (en_lista, detalle.json()):
        assert set(vista) == CAMPOS_PUBLICOS
        assert vista["nombre"] == "Finca de prueba" and vista["comunidad"] == "Quinua"
        # una zona aproximada (2 decimales ~ 1 km), no la finca
        assert (vista["latitud"], vista["longitud"]) == ("-13.16", "-74.22")
        texto = json.dumps(vista)
        assert "987654321" not in texto and sub not in texto and "-13.158812" not in texto


def test_un_productor_sin_coordenadas_validas_se_muestra_sin_mapa():
    db = main.SessionLocal()
    try:
        sin_datos = main.models.Productor(nombre="Sin mapa", latitud=None, longitud=None)
        sucio = main.models.Productor(nombre="Coordenadas sucias", latitud="abc", longitud="nan")
        db.add_all([sin_datos, sucio])
        db.commit()
        ids = [str(sin_datos.id), str(sucio.id)]
    finally:
        db.close()

    for productor_id in ids:
        vista = client.get(f"/productores/{productor_id}").json()
        assert vista["latitud"] is None and vista["longitud"] is None


def test_el_dueno_sigue_viendo_su_perfil_completo():
    sub, _ = _productor_con_datos_personales()
    resp = client.get("/productores/me")
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["contacto"] == "987654321"
    assert (cuerpo["latitud"], cuerpo["longitud"]) == ("-13.158812", "-74.223940")
    assert cuerpo["usuario_id"] == sub


def test_el_admin_ve_la_lista_completa_y_los_demas_roles_no():
    sub, productor_id = _productor_con_datos_personales()

    _auth(str(uuid.uuid4()), rol="admin")
    resp = client.get("/productores/admin/todos")
    assert resp.status_code == 200, resp.text
    item = next(p for p in resp.json() if p["id"] == productor_id)
    assert item["contacto"] == "987654321"
    assert (item["latitud"], item["longitud"]) == ("-13.158812", "-74.223940")
    assert item["usuario_id"] == sub

    for rol in ("productor", "comprador", "verificador"):
        _auth(str(uuid.uuid4()), rol=rol)
        assert client.get("/productores/admin/todos").status_code == 403, rol


def test_un_id_de_productor_mal_formado_falla_422_no_500():
    assert client.get("/productores/no-es-un-uuid").status_code == 422
    assert client.get(f"/productores/{uuid.uuid4()}").status_code == 404




# ============ Corregir o anular una siembra; la chacra con cosechas no se mueve ============

def _datos_corregidos(**cambios):
    siembra = datetime(2026, 2, 1)
    return {"cultivo": "papa amarilla", "numero_parcelas": 3, "costo_semillas": 120.0, "costo_insumos": 40.0,
            "unidad_medida": "kg", "fecha_siembra": siembra.isoformat(),
            "fecha_cosecha_estimada": (siembra + timedelta(days=150)).isoformat(), **cambios}


def test_el_productor_corrige_una_siembra_planificada_sin_tocar_su_evidencia():
    registro_id = _crear_registro_planificado(latitud=-13.16, longitud=-74.22)
    antes = _fila(registro_id)

    resp = client.put(f"/productores/produccion/{registro_id}", json=_datos_corregidos())

    assert resp.status_code == 200, resp.text
    fila = _fila(registro_id)
    # Con etapas, los costos son la suma de sus gastos: se corrigen en "Gastos por etapa", no aquí (siguen en 100).
    assert (fila.cultivo, fila.numero_parcelas, float(fila.costo_semillas)) == ("papa amarilla", 3, 100.0)
    assert fila.fecha_siembra == datetime(2026, 2, 1)
    assert (fila.chacra_id, fila.siembra_latitud, fila.siembra_longitud) == (antes.chacra_id, antes.siembra_latitud, antes.siembra_longitud)
    assert client.put(f"/productores/produccion/{registro_id}",
                      json=_datos_corregidos(fecha_cosecha_estimada=datetime(2025, 1, 1).isoformat())).status_code == 422


def test_una_siembra_anulada_desaparece_de_la_lista_y_no_se_puede_cosechar():
    registro_id = _crear_registro_planificado()

    resp = client.post(f"/productores/produccion/{registro_id}/anular")

    assert resp.status_code == 200 and resp.json()["estado"] == "anulado"
    assert registro_id not in [r["id"] for r in client.get("/productores/produccion/me").json()]
    assert _completar_cosecha(registro_id).status_code == 409
    assert client.put(f"/productores/produccion/{registro_id}", json=_datos_corregidos()).status_code == 404


def test_una_siembra_cosechada_no_se_corrige_ni_se_anula():
    registro_id = _crear_registro_planificado()
    _poner_estado(registro_id, "cosechado")

    assert client.put(f"/productores/produccion/{registro_id}", json=_datos_corregidos()).status_code == 409
    assert client.post(f"/productores/produccion/{registro_id}/anular").status_code == 409


def test_otro_productor_no_corrige_ni_anula_una_siembra_ajena():
    registro_id = _crear_registro_planificado()
    _nuevo_productor()

    assert client.put(f"/productores/produccion/{registro_id}", json=_datos_corregidos()).status_code == 403
    assert client.post(f"/productores/produccion/{registro_id}/anular").status_code == 403
    assert _fila(registro_id).estado == "planificado"


def test_una_chacra_con_cosechas_no_cambia_de_lugar_ni_de_codigo_pero_si_de_nombre():
    registro_id = _crear_registro_planificado()
    chacra_id = _fila(registro_id).chacra_id
    actual = client.get(f"/chacras/{chacra_id}").json()
    movida = {**actual, "ubicacion_latitud": -13.30}
    del movida["id"], movida["productor_id"]

    # todavía sin cosechas: se puede corregir el lugar
    assert client.put(f"/chacras/{chacra_id}", json=movida).status_code == 200
    _poner_estado(registro_id, "cosechado")

    otra_vez = {**movida, "ubicacion_latitud": -13.10}
    resp = client.put(f"/chacras/{chacra_id}", json=otra_vez)
    assert resp.status_code == 409 and "cadena de certificación" in resp.json()["detail"]
    assert client.put(f"/chacras/{chacra_id}", json={**movida, "codigo": "OTRO-1"}).status_code == 409
    renombrada = client.put(f"/chacras/{chacra_id}", json={**movida, "nombre": "Chacra de abajo"})
    assert renombrada.status_code == 200 and renombrada.json()["nombre"] == "Chacra de abajo"
    assert client.get(f"/chacras/{chacra_id}").json()["ubicacion_latitud"] == -13.30



# ============ Etapas y gastos de cada alimento ============

def _crear_con_etapas(cultivo="Papa amarilla", gastos=None, **extra):
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo=f"CH-{uuid.uuid4().hex[:6]}")).json()
    payload = {**_registro_payload(chacra["id"]), "cultivo": cultivo, "costo_semillas": 0, "costo_insumos": 0,
               "gastos": gastos or [], **extra}
    resp = client.post("/productores/produccion", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_la_siembra_guarda_sus_gastos_por_etapa_y_suma_las_columnas_de_siempre():
    registro = _crear_con_etapas(gastos=[
        {"etapa": "preparacion", "rubro": "maquinaria", "monto": 300},
        {"etapa": "siembra", "rubro": "semilla", "monto": 600},
        {"etapa": "siembra", "rubro": "mano_obra", "cantidad": 4, "precio_unitario": 50, "unidad": "jornal"},
    ])
    assert (registro["plantilla"], registro["tipo_produccion"], registro["alimento"]) == ("papa", "temporada", "Papa")
    assert (float(registro["costo_semillas"]), float(registro["costo_insumos"]), float(registro["costo_mano_obra"])) == (600, 300, 200)
    etapas = {e["clave"]: e["total"] for e in registro["gastos_por_etapa"]}
    assert etapas == {"preparacion": 300, "siembra": 800, "deshierbo": 0, "aporque": 0, "fumigacion": 0, "cosecha": 0}
    detalle = client.get(f"/productores/produccion/{registro['id']}/gastos").json()
    jornales = next(g for g in detalle["gastos"] if g["rubro"] == "mano_obra")
    assert (jornales["cantidad"], jornales["precio_unitario"], jornales["monto"], detalle["total"]) == (4, 50, 200, 1100)


def test_los_gastos_se_anotan_cuando_ocurren_y_se_corrigen_o_borran():
    registro = _crear_con_etapas()
    url = f"/productores/produccion/{registro['id']}/gastos"
    aporque = client.post(url, json={"etapa": "aporque", "rubro": "abono", "descripcion": "2 sacos de urea", "monto": 240})
    assert aporque.status_code == 200 and aporque.json()["total"] == 240
    assert "no es de Papa" in client.post(url, json={"etapa": "trilla", "rubro": "abono", "monto": 10}).json()["detail"]
    assert client.post(url, json={"etapa": "aporque", "rubro": "cohete", "monto": 10}).status_code == 422
    assert client.post(url, json={"etapa": "aporque", "rubro": "abono"}).status_code == 422  # sin monto
    gasto = aporque.json()["gastos"][0]
    corregido = client.put(f"{url}/{gasto['id']}", json={"etapa": "aporque", "rubro": "abono", "monto": 260}).json()
    assert corregido["total"] == 260 and corregido["costo_insumos"] == 260
    assert client.delete(f"{url}/{gasto['id']}").json()["total"] == 0
    _nuevo_productor()  # otra cuenta
    assert client.post(url, json={"etapa": "aporque", "rubro": "abono", "monto": 1}).status_code == 403


def test_al_cosechar_se_anotan_los_gastos_de_la_cosecha_y_despues_se_cierran():
    registro = _crear_con_etapas(gastos=[{"etapa": "siembra", "rubro": "semilla", "monto": 500}])
    assert _subir_foto(registro["id"]).status_code == 200
    resp = client.patch(f"/productores/produccion/{registro['id']}/completar-cosecha", json={
        "cantidad_cosechada": 800, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat(),
        "gastos": [{"etapa": "cosecha", "rubro": "mano_obra", "cantidad": 6, "precio_unitario": 50},
                   {"etapa": "cosecha", "rubro": "envases", "monto": 40}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["costo_total"] == 840
    url = f"/productores/produccion/{registro['id']}/gastos"
    assert client.post(url, json={"etapa": "cosecha", "rubro": "otros", "monto": 5}).status_code == 409


def test_huevos_y_leche_no_se_siembran_tienen_sus_propias_etapas():
    huevos = _crear_con_etapas(cultivo="Huevo de campo", gastos=[{"etapa": "alimentacion", "rubro": "alimento", "monto": 900}],
                               unidad_medida="unidad", equivalencia_kg=0.06)
    assert huevos["tipo_produccion"] == "crianza" and [e["clave"] for e in huevos["gastos_por_etapa"]][0] == "alimentacion"
    assert float(huevos["costo_insumos"]) == 900
    leche = _crear_con_etapas(cultivo="Leche fresca", unidad_medida="litro", equivalencia_kg=1.03)
    assert leche["plantilla"] == "leche" and "ordeno" in [e["clave"] for e in leche["gastos_por_etapa"]]


def test_una_pantalla_de_antes_sigue_funcionando_y_sus_montos_van_a_las_etapas():
    registro_id = _crear_registro_planificado()  # manda costo_semillas 100 y costo_insumos 50, sin gastos
    detalle = client.get(f"/productores/produccion/{registro_id}/gastos").json()
    assert {(g["etapa"], g["rubro"], g["monto"]) for g in detalle["gastos"]} == {("siembra", "semilla", 100), ("siembra", "abono", 50)}
    assert _subir_foto(registro_id).status_code == 200
    assert _completar_cosecha(registro_id).status_code == 200  # con costo_mano_obra 20, sin gastos
    assert float(_mi_registro(registro_id)["costo_mano_obra"]) == 20


def test_un_registro_de_antes_sin_plantilla_conserva_sus_tres_costos():
    registro_id = _crear_registro_planificado()
    db = main.SessionLocal()
    try:
        fila = db.query(main.models.RegistroProduccion).filter(main.models.RegistroProduccion.id == registro_id).one()
        db.query(main.models.GastoProduccion).filter(main.models.GastoProduccion.registro_id == fila.id).delete()
        fila.plantilla, fila.tipo_produccion = None, None
        db.commit()
    finally:
        db.close()
    assert _mi_registro(registro_id)["gastos_por_etapa"] is None
    assert client.get(f"/productores/produccion/{registro_id}/gastos").status_code == 409
    assert _subir_foto(registro_id).status_code == 200
    sin_mano = client.patch(f"/productores/produccion/{registro_id}/completar-cosecha",
                            json={"cantidad_cosechada": 10, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat()})
    assert sin_mano.status_code == 422
    assert _completar_cosecha(registro_id).status_code == 200


def test_las_plantillas_son_publicas_y_reconocen_el_nombre():
    catalogo = client.get("/plantillas").json()
    assert {"tipos", "rubros", "familias", "alimentos"} <= set(catalogo)
    assert client.get("/plantillas/para", params={"nombre": "Queso andino"}).json()["tipo"] == "elaboracion"
    desconocido = client.get("/plantillas/para", params={"nombre": "Chuño"}).json()
    assert desconocido["reconocido"] is False and desconocido["tipo"] == "temporada"



def test_la_cadena_dice_recogidos_en_la_granja_para_los_huevos(monkeypatch):
    capturado = _capturar_post_a_certificacion(monkeypatch)
    registro = _registro_para_la_cadena(cultivo="Huevo de campo", unidad_medida="unidad", cantidad_cosechada=360,
                                        tipo_produccion="crianza")
    _registrar_certificacion_real(registro, SimpleNamespace(nombre="Granja Sol"), SimpleNamespace(codigo="GR-1", nombre=None), "p-1")
    texto = capturado["json"]["datos_cosecha"]
    assert "360 unidad de Huevo de campo recogidos en granja GR-1 por Granja Sol (periodo desde el 01/01/2026, hasta el 20/08/2026)." in texto
    assert "Al registrar lo recogido: a 3,10 km de la granja" in texto and "Foto de lo recogido" in texto



# ============ ¿Qué me conviene sembrar? ============

def test_que_sembrar_junta_ventas_catalogo_y_siembras_de_otros(monkeypatch):
    class Respuesta:
        def __init__(self, datos):
            self._datos = datos
        def raise_for_status(self):
            pass
        def json(self):
            return self._datos

    def get_falso(url, **kwargs):
        if url.endswith("/ventas/mercado"):
            assert kwargs["headers"]["Authorization"].startswith("Bearer ")
            return Respuesta({"meses": ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"],
                              "productos": [{"nombre": "Papa amarilla", "kg": 500, "soles": 1750, "pedidos": 9, "kg_por_mes": [50, 60, 80, 90, 100, 120]}]})
        return Respuesta([{"nombre": "Quinua roja", "unidad_medida": "kg", "precio": 9.5, "stock": 40}])

    monkeypatch.setattr(main.httpx, "get", get_falso)
    monkeypatch.setattr(main.recomendacion_siembra, "consejo", lambda ranking, clave: "Siembra papa.")
    otro = _crear_con_etapas(cultivo="Quinua blanca", fecha_siembra=datetime.utcnow().isoformat(),
                             fecha_cosecha_estimada=(datetime.utcnow() + timedelta(days=165)).isoformat())
    assert otro["estado"] == "planificado"
    _nuevo_productor()  # quien pregunta es otro agricultor
    resp = client.get("/que-sembrar", headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    papa = next(f for f in datos["recomendaciones"] if f["clave"] == "papa")
    quinua = next(f for f in datos["recomendaciones"] if f["clave"] == "quinua")
    assert (papa["precio_kg"], papa["demanda_kg"], papa["tendencia"]) == (3.5, 500, "sube")
    assert quinua["fuente_precio"] == "precio del catálogo" and quinua["competencia"] >= 1
    assert datos["consejo"] == "Siembra papa." and datos["ventas_disponibles"] is True and datos["referencias"]
    _auth(str(uuid.uuid4()), rol="comprador")
    assert client.get("/que-sembrar", headers={"Authorization": "Bearer token"}).status_code == 403


def test_el_borrador_de_huevos_nace_con_su_categoria(monkeypatch):
    payloads = []

    def crear(payload):
        payloads.append(payload)
        return _RespuestaFalsaProductos(200, {"id": "producto-huevos"})

    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha", crear)
    registro = _crear_con_etapas(cultivo="Huevo de campo", unidad_medida="unidad", equivalencia_kg=0.06)
    assert _subir_foto(registro["id"]).status_code == 200
    resp = client.patch(f"/productores/produccion/{registro['id']}/completar-cosecha",
                        json={"cantidad_cosechada": 300, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat()})
    assert resp.status_code == 200, resp.text
    assert payloads[0]["categoria"] == "huevo"


# ============ H1 (validación con usuarios del 2026-10-13): cada alimento en sus unidades ============

def test_la_papa_se_siembra_por_kilos_y_no_por_unidades():
    _nuevo_productor()
    chacra = client.post("/chacras", json=_chacra_payload(codigo="CH-H1"))
    payload = {**_registro_payload(chacra.json()["id"]), "cultivo": "Papa amarilla", "unidad_medida": "unidad", "equivalencia_kg": 0.5}
    resp = client.post("/productores/produccion", json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "La cosecha de papa se mide en kilos (se vende por kilos o sacos o arrobas)."
    payload.update(unidad_medida="kg", equivalencia_kg=None)
    assert client.post("/productores/produccion", json=payload).status_code == 200


def test_los_huevos_se_registran_por_unidad_y_no_en_kilos():
    respuesta = client.post("/productores/produccion", json={**_registro_payload(_crear_chacra_para_h1()), "cultivo": "Huevo de campo"})
    assert respuesta.status_code == 422 and "unidades" in respuesta.json()["detail"]


def _crear_chacra_para_h1():
    _nuevo_productor()
    return client.post("/chacras", json=_chacra_payload(codigo="CH-H1B")).json()["id"]


def test_corregir_una_siembra_tampoco_acepta_otra_unidad():
    registro = _crear_con_etapas(cultivo="Papa amarilla")
    datos = {**_registro_payload(registro["chacra_id"]), "cultivo": "Papa amarilla", "unidad_medida": "litro", "equivalencia_kg": 1}
    resp = client.put(f"/productores/produccion/{registro['id']}", json=datos)
    assert resp.status_code == 422, resp.text


def test_el_borrador_lleva_las_unidades_en_que_se_vende_su_alimento(monkeypatch):
    payloads = []
    monkeypatch.setattr(main, "_pedir_crear_producto_desde_cosecha",
                        lambda payload: payloads.append(payload) or _RespuestaFalsaProductos(200, {"id": "p"}))
    for cultivo, unidad, equivalencia in (("Papa amarilla", "kg", None), ("Producto sin plantilla", "kg", None)):
        registro = _crear_con_etapas(cultivo=cultivo, unidad_medida=unidad, equivalencia_kg=equivalencia)
        assert _subir_foto(registro["id"]).status_code == 200
        resp = client.patch(f"/productores/produccion/{registro['id']}/completar-cosecha",
                            json={"cantidad_cosechada": 100, "fecha_cosecha_real": datetime(2026, 5, 1).isoformat()})
        assert resp.status_code == 200, resp.text
    assert payloads[0]["unidades_permitidas"] == ["kg", "saco", "arroba"]
    assert payloads[1]["unidades_permitidas"] is None


# ============ H2 (validación con usuarios del 2026-10-13): la parcela del perfil ya es su chacra ============

def test_el_perfil_con_ubicacion_ya_trae_su_primera_chacra():
    _auth(str(uuid.uuid4()))
    resp = client.post("/productores", json={"nombre": "Con parcela", "latitud": "-13.158812", "longitud": "-74.22394"})
    assert resp.status_code == 200, resp.text
    chacras = client.get("/chacras/me").json()
    assert [(c["codigo"], c["nombre"], c["ubicacion_latitud"], c["ubicacion_longitud"]) for c in chacras] == [
        ("CH-01", "Mi chacra", -13.158812, -74.22394)]
    # y con ella ya puede sembrar, sin registrar otra
    siembra = client.post("/productores/produccion", json=_registro_payload(chacras[0]["id"]))
    assert siembra.status_code == 200, siembra.text


def test_el_perfil_sin_ubicacion_no_inventa_una_chacra():
    _nuevo_productor()
    assert client.get("/chacras/me").json() == []
