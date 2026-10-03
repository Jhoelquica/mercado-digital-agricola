"""
Tests de la sugerencia de la fecha de cosecha (POST /productores/produccion/sugerir-cosecha, sugerencia_cosecha.py).

Cubre:
- sin clave de API la sugerencia sale de la tabla de ciclos típicos, y reconoce el cultivo dentro del texto
  ("Papa amarilla", "maíz morado", "HABAS"...)
- con IA (Gemini), las fechas las calcula el servidor con los días que estima el modelo, y al modelo solo le llega el
  cultivo, la fecha y la zona aproximada de la chacra (nada del agricultor ni las coordenadas exactas)
- si el modelo principal está saturado o sin cuota se pregunta al de respaldo; si tampoco sirve, Gemini bloquea la
  respuesta, falla, no responde a tiempo o devuelve algo absurdo, se usa la tabla
- un cultivo que nadie conoce responde 404 con un mensaje para el agricultor
- una consulta repetida sale del caché, y por productor hay un límite de consultas a la IA por hora
- la chacra tiene que ser del productor, y solo el productor puede pedir la sugerencia

Gemini NUNCA se llama de verdad: un fixture reemplaza el cliente por uno falso. Requiere el mismo Postgres que
test_main.py (create_all() al importar main).
"""
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors as errores_gemini
from google.genai import types

import main
import sugerencia_cosecha
from auth import verificar_token

client = TestClient(main.app)
SIEMBRA = datetime(2026, 10, 15)


@pytest.fixture(autouse=True)
def _sin_ia_ni_estado(monkeypatch):
    """Por defecto no hay clave (como un despliegue sin IA) y cada test empieza con el caché y los límites vacíos."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODELO", raising=False)
    monkeypatch.delenv("GEMINI_MODELO_RESPALDO", raising=False)
    monkeypatch.setattr(sugerencia_cosecha, "_cliente", None)
    monkeypatch.setattr(sugerencia_cosecha, "_cache", {})
    monkeypatch.setattr(sugerencia_cosecha, "_consultas_por_productor", {})


class _GeminiFalso:
    """Responde `respuesta` o lanza `error` en cada llamada; con `por_modelo` ({modelo: respuesta o excepción}), según el
    modelo que se pida."""
    def __init__(self, respuesta=None, error=None, por_modelo=None):
        self.llamadas = []
        self.respuesta = respuesta
        self.error = error
        self.por_modelo = por_modelo
        self.models = SimpleNamespace(generate_content=self._generar)

    def _generar(self, **kwargs):
        self.llamadas.append(kwargs)
        resultado = self.por_modelo[kwargs["model"]] if self.por_modelo else (self.error or self.respuesta)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


def _error_gemini(codigo, estado="UNAVAILABLE"):
    clase = errores_gemini.ServerError if codigo >= 500 else errores_gemini.ClientError
    return clase(codigo, {"error": {"code": codigo, "message": estado, "status": estado}})


def _respuesta(datos, motivo=types.FinishReason.STOP):
    texto = datos if isinstance(datos, str) else json.dumps(datos)
    return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=motivo)], text=texto, prompt_feedback=None)


def _bloqueada_en_la_pregunta():
    return SimpleNamespace(candidates=None, text=None,
                           prompt_feedback=SimpleNamespace(block_reason="PROHIBITED_CONTENT"))


def _ia(dias_min=100, probable=115, dias_max=130, explicacion="Depende de las lluvias.", es_cultivo=True):
    return {"es_cultivo": es_cultivo, "dias_min": dias_min, "dias_probable": probable, "dias_max": dias_max,
            "explicacion": explicacion}


def _con_ia(monkeypatch, falso):
    monkeypatch.setattr(sugerencia_cosecha, "_cliente_ia", lambda: falso)
    return falso


def _auth(sub, rol="productor"):
    main.app.dependency_overrides[verificar_token] = lambda: {"sub": sub, "rol": rol}


def _productor_con_chacra():
    """Crea un productor con una chacra en (-13.16, -74.22) y lo deja autenticado. Devuelve (sub, chacra_id)."""
    sub = str(uuid.uuid4())
    _auth(sub)
    assert client.post("/productores", json={"nombre": f"Productor {sub[:8]}"}).status_code == 200
    chacra = client.post("/chacras", json={"codigo": "CH-IA", "nombre": "Chacra del cerro",
                                           "ubicacion_latitud": -13.16, "ubicacion_longitud": -74.22})
    assert chacra.status_code == 200, chacra.text
    return sub, chacra.json()["id"]


def _sugerir(chacra_id, cultivo="Papa amarilla", siembra=SIEMBRA):
    return client.post("/productores/produccion/sugerir-cosecha",
                       json={"chacra_id": chacra_id, "cultivo": cultivo, "fecha_siembra": siembra.isoformat()})


def _dia(dias, siembra=SIEMBRA):
    return (siembra + timedelta(days=dias)).date().isoformat()


def test_sin_clave_de_api_la_sugerencia_sale_de_la_tabla():
    _, chacra_id = _productor_con_chacra()
    resp = _sugerir(chacra_id, "Papa amarilla")

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["fuente"] == "tabla"
    assert cuerpo["cultivo_tabla"] == "Papa"
    assert (cuerpo["dias_min"], cuerpo["dias_probable"], cuerpo["dias_max"]) == (120, 150, 180)
    assert cuerpo["fecha_sugerida"] == "2027-03-14" == _dia(150)
    assert cuerpo["fecha_desde"] == _dia(120) and cuerpo["fecha_hasta"] == _dia(180)
    assert cuerpo["explicacion"]


@pytest.mark.parametrize("texto, nombre", [
    ("Papa amarilla", "Papa"), ("papas", "Papa"), ("maíz morado", "Maíz"), ("Choclo", "Maíz"), ("HABAS", "Habas"),
    ("Alverja verde", "Arveja"), ("Ollucos", "Olluco"), ("quinoa blanca", "Quinua"), ("Palta", None), ("", None),
])
def test_la_tabla_reconoce_el_cultivo_dentro_del_texto(texto, nombre):
    ciclo = sugerencia_cosecha.ciclo_de_la_tabla(texto)
    assert (ciclo[0] if ciclo else None) == nombre


def test_con_ia_las_fechas_salen_de_los_dias_de_gemini_y_solo_viaja_la_zona(monkeypatch):
    falso = _con_ia(monkeypatch, _GeminiFalso(_respuesta(_ia(100, 115, 130, "  Depende   de las lluvias. "))))
    sub, chacra_id = _productor_con_chacra()

    resp = _sugerir(chacra_id, "Papa amarilla")

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["fuente"] == "ia" and cuerpo["cultivo_tabla"] is None
    assert cuerpo["fecha_sugerida"] == _dia(115)
    assert (cuerpo["fecha_desde"], cuerpo["fecha_hasta"]) == (_dia(100), _dia(130))
    assert cuerpo["explicacion"] == "Depende de las lluvias."

    [llamada] = falso.llamadas
    assert llamada["model"] == "gemini-3.5-flash-lite"
    config = llamada["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == sugerencia_cosecha.ESQUEMA
    assert config.system_instruction == sugerencia_cosecha.SISTEMA
    pregunta = llamada["contents"]
    assert "Papa amarilla" in pregunta and "15 de octubre de 2026" in pregunta
    assert "-13.2" in pregunta and "-74.2" in pregunta
    assert "-13.16" not in pregunta and "-74.22" not in pregunta
    assert sub not in pregunta and sub[:8] not in pregunta and "Chacra del cerro" not in pregunta


@pytest.mark.parametrize("falso, llamadas", [
    (_GeminiFalso(_respuesta(_ia(), motivo=types.FinishReason.SAFETY)), 1),
    (_GeminiFalso(_respuesta(_ia(), motivo=types.FinishReason.MAX_TOKENS)), 1),
    (_GeminiFalso(_bloqueada_en_la_pregunta()), 1),
    (_GeminiFalso(error=_error_gemini(429, "RESOURCE_EXHAUSTED")), 2),  # los dos modelos sin cuota
    (_GeminiFalso(error=_error_gemini(503)), 2),                        # los dos saturados
    (_GeminiFalso(error=_error_gemini(400, "INVALID_ARGUMENT")), 1),     # un error de la consulta no se reintenta
    (_GeminiFalso(error=httpx.ReadTimeout("tardó demasiado")), 1),      # el agricultor ya esperó: no se reintenta
    (_GeminiFalso(error=httpx.ConnectError("sin red")), 1),
    (_GeminiFalso(_respuesta(_ia(5, 6, 7))), 1),             # días absurdos
    (_GeminiFalso(_respuesta(_ia(150, 120, 180))), 1),       # el probable fuera del rango
    (_GeminiFalso(_respuesta(_ia(explicacion="   "))), 1),   # sin explicación
    (_GeminiFalso(_respuesta("esto no es JSON")), 1),
    (_GeminiFalso(_respuesta({"es_cultivo": True})), 1),     # faltan campos
], ids=["bloqueada", "sin-espacio", "pregunta-bloqueada", "cuota-agotada", "sobrecargado", "consulta-invalida", "tarda",
        "sin-red", "absurdo", "desordenado", "sin-explicacion", "json-roto", "incompleto"])
def test_si_gemini_no_sirve_se_usa_la_tabla(monkeypatch, falso, llamadas):
    _con_ia(monkeypatch, falso)
    _, chacra_id = _productor_con_chacra()

    resp = _sugerir(chacra_id, "Quinua")

    assert resp.status_code == 200, resp.text
    assert resp.json()["fuente"] == "tabla"
    assert resp.json()["cultivo_tabla"] == "Quinua"
    assert len(falso.llamadas) == llamadas


@pytest.mark.parametrize("codigo", [503, 429, 500])
def test_si_el_modelo_principal_esta_saturado_responde_el_de_respaldo(monkeypatch, codigo):
    falso = _con_ia(monkeypatch, _GeminiFalso(por_modelo={
        "gemini-3.5-flash-lite": _error_gemini(codigo), "gemini-3.8-flash": _respuesta(_ia(100, 115, 130)),
    }))
    _, chacra_id = _productor_con_chacra()

    resp = _sugerir(chacra_id, "Papa")

    assert resp.json()["fuente"] == "ia" and resp.json()["fecha_sugerida"] == _dia(115)
    assert [llamada["model"] for llamada in falso.llamadas] == ["gemini-3.5-flash-lite", "gemini-3.8-flash"]


def test_sin_modelo_de_respaldo_un_503_va_directo_a_la_tabla(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELO_RESPALDO", "")
    falso = _con_ia(monkeypatch, _GeminiFalso(error=_error_gemini(503)))
    _, chacra_id = _productor_con_chacra()

    assert _sugerir(chacra_id, "Papa").json()["fuente"] == "tabla"
    assert len(falso.llamadas) == 1


def test_un_cultivo_que_nadie_conoce_responde_404():
    _, chacra_id = _productor_con_chacra()
    resp = _sugerir(chacra_id, "Palta fuerte")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No conocemos cuánto tarda ese cultivo: elige tú la fecha."


def test_si_la_ia_dice_que_no_es_un_cultivo_y_la_tabla_tampoco_lo_conoce_responde_404(monkeypatch):
    _con_ia(monkeypatch, _GeminiFalso(_respuesta(_ia(0, 0, 0, "", es_cultivo=False))))
    _, chacra_id = _productor_con_chacra()
    assert _sugerir(chacra_id, "Piedra").status_code == 404


def test_una_consulta_repetida_sale_del_cache(monkeypatch):
    falso = _con_ia(monkeypatch, _GeminiFalso(_respuesta(_ia())))
    _, chacra_id = _productor_con_chacra()

    primera = _sugerir(chacra_id, "Papa amarilla")
    otro_dia_del_mismo_mes = _sugerir(chacra_id, "papa  AMARILLA", siembra=datetime(2026, 10, 20))
    assert len(falso.llamadas) == 1
    assert primera.json()["fuente"] == otro_dia_del_mismo_mes.json()["fuente"] == "ia"
    assert otro_dia_del_mismo_mes.json()["fecha_sugerida"] == _dia(115, datetime(2026, 10, 20))

    _sugerir(chacra_id, "Papa amarilla", siembra=datetime(2026, 12, 1))  # otro mes: otra temporada, otra consulta
    assert len(falso.llamadas) == 2


def test_cada_productor_tiene_un_limite_de_consultas_a_la_ia_por_hora(monkeypatch):
    monkeypatch.setattr(sugerencia_cosecha, "CONSULTAS_IA_POR_HORA", 2)
    falso = _con_ia(monkeypatch, _GeminiFalso(_respuesta(_ia())))
    _, chacra_id = _productor_con_chacra()

    fuentes = [_sugerir(chacra_id, cultivo).json()["fuente"] for cultivo in ("Papa", "Maíz", "Quinua")]
    assert fuentes == ["ia", "ia", "tabla"]
    assert len(falso.llamadas) == 2

    _, otra_chacra = _productor_con_chacra()  # otro productor no comparte el límite
    assert _sugerir(otra_chacra, "Oca").json()["fuente"] == "ia"


def test_el_modelo_se_cambia_con_gemini_modelo(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELO", "gemini-3.8-flash")  # el mismo que el de respaldo: no se repite
    falso = _con_ia(monkeypatch, _GeminiFalso(_respuesta(_ia())))
    _, chacra_id = _productor_con_chacra()

    assert _sugerir(chacra_id).json()["fuente"] == "ia"
    assert falso.llamadas[0]["model"] == "gemini-3.8-flash"


def test_el_cliente_real_solo_se_crea_con_clave_y_sin_reintentos(monkeypatch):
    assert sugerencia_cosecha._cliente_ia() is None  # sin GEMINI_API_KEY: no hay IA

    creados = []
    monkeypatch.setattr(sugerencia_cosecha.genai, "Client", lambda **kw: creados.append(kw) or object())
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba")
    cliente = sugerencia_cosecha._cliente_ia()
    assert sugerencia_cosecha._cliente_ia() is cliente  # se crea una sola vez

    [argumentos] = creados
    assert argumentos["api_key"] == "clave-de-prueba"
    assert argumentos["http_options"].timeout == 20_000
    assert argumentos["http_options"].retry_options.attempts == 1


def test_la_chacra_tiene_que_ser_del_productor():
    _, chacra_ajena = _productor_con_chacra()
    _productor_con_chacra()

    assert _sugerir(chacra_ajena).status_code == 403
    assert _sugerir(str(uuid.uuid4())).status_code == 404


def test_solo_el_productor_pide_la_sugerencia_y_con_una_fecha_razonable():
    sub, chacra_id = _productor_con_chacra()
    assert _sugerir(chacra_id, siembra=datetime(1990, 1, 1)).status_code == 422
    assert _sugerir(chacra_id, siembra=datetime.utcnow() + timedelta(days=800)).status_code == 422
    assert client.post("/productores/produccion/sugerir-cosecha",
                       json={"chacra_id": chacra_id, "cultivo": "", "fecha_siembra": SIEMBRA.isoformat()}).status_code == 422

    _auth(sub, "comprador")
    assert _sugerir(chacra_id).status_code == 403
