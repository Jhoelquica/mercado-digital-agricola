"""
Tests de la sugerencia de la fecha de cosecha (POST /productores/produccion/sugerir-cosecha, sugerencia_cosecha.py).

Cubre:
- sin clave de API la sugerencia sale de la tabla de ciclos típicos, y reconoce el cultivo dentro del texto
  ("Papa amarilla", "maíz morado", "HABAS"...)
- con IA, las fechas las calcula el servidor con los días que estima Claude, y a Claude solo le llega el cultivo, la
  fecha y la zona aproximada de la chacra (nada del agricultor ni las coordenadas exactas)
- si Claude se niega, falla, no responde a tiempo o devuelve algo absurdo, se usa la tabla
- un cultivo que nadie conoce responde 404 con un mensaje para el agricultor
- una consulta repetida sale del caché, y por productor hay un límite de consultas a Claude por hora
- la chacra tiene que ser del productor, y solo el productor puede pedir la sugerencia

Claude NUNCA se llama de verdad: un fixture reemplaza el cliente por uno falso. Requiere el mismo Postgres que
test_main.py (create_all() al importar main).
"""
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

import main
import sugerencia_cosecha
from auth import verificar_token

client = TestClient(main.app)
SIEMBRA = datetime(2026, 10, 15)


@pytest.fixture(autouse=True)
def _sin_claude_ni_estado(monkeypatch):
    """Por defecto no hay clave (como un despliegue sin IA) y cada test empieza con el caché y los límites vacíos."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sugerencia_cosecha, "_cliente", None)
    monkeypatch.setattr(sugerencia_cosecha, "_cache", {})
    monkeypatch.setattr(sugerencia_cosecha, "_consultas_por_productor", {})


class _ClaudeFalso:
    def __init__(self, respuesta=None, error=None):
        self.llamadas = []
        self.respuesta = respuesta
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._crear))

    def _crear(self, **kwargs):
        self.llamadas.append(kwargs)
        if self.error:
            raise self.error
        return self.respuesta


def _respuesta(datos, stop_reason="end_turn"):
    texto = datos if isinstance(datos, str) else json.dumps(datos)
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=texto)])


def _ia(dias_min=100, probable=115, dias_max=130, explicacion="Depende de las lluvias.", es_cultivo=True):
    return {"es_cultivo": es_cultivo, "dias_min": dias_min, "dias_probable": probable, "dias_max": dias_max,
            "explicacion": explicacion}


def _con_claude(monkeypatch, falso):
    monkeypatch.setattr(sugerencia_cosecha, "_cliente_claude", lambda: falso)
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


def test_con_ia_las_fechas_salen_de_los_dias_de_claude_y_solo_viaja_la_zona(monkeypatch):
    falso = _con_claude(monkeypatch, _ClaudeFalso(_respuesta(_ia(100, 115, 130, "  Depende   de las lluvias. "))))
    sub, chacra_id = _productor_con_chacra()

    resp = _sugerir(chacra_id, "Papa amarilla")

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["fuente"] == "ia" and cuerpo["cultivo_tabla"] is None
    assert cuerpo["fecha_sugerida"] == _dia(115)
    assert (cuerpo["fecha_desde"], cuerpo["fecha_hasta"]) == (_dia(100), _dia(130))
    assert cuerpo["explicacion"] == "Depende de las lluvias."

    [llamada] = falso.llamadas
    assert llamada["model"] == "claude-sonnet-5-5"
    assert llamada["fallbacks"] == "default" and llamada["betas"] == ["server-side-fallback-2026-07-01"]
    assert llamada["output_config"]["format"]["type"] == "json_schema"
    pregunta = llamada["messages"][0]["content"]
    assert "Papa amarilla" in pregunta and "15 de octubre de 2026" in pregunta
    assert "-13.2" in pregunta and "-74.2" in pregunta
    assert "-13.16" not in pregunta and "-74.22" not in pregunta
    assert sub not in pregunta and sub[:8] not in pregunta and "Chacra del cerro" not in pregunta


@pytest.mark.parametrize("falso", [
    _ClaudeFalso(_respuesta(_ia(), stop_reason="refusal")),
    _ClaudeFalso(_respuesta(_ia(), stop_reason="max_tokens")),
    _ClaudeFalso(error=anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com"))),
    _ClaudeFalso(error=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))),
    _ClaudeFalso(_respuesta(_ia(5, 6, 7))),             # días absurdos
    _ClaudeFalso(_respuesta(_ia(150, 120, 180))),       # el probable fuera del rango
    _ClaudeFalso(_respuesta(_ia(explicacion="   "))),   # sin explicación
    _ClaudeFalso(_respuesta("esto no es JSON")),
    _ClaudeFalso(_respuesta({"es_cultivo": True})),     # faltan campos
], ids=["se-niega", "sin-espacio", "tarda", "sin-red", "absurdo", "desordenado", "sin-explicacion", "json-roto",
        "incompleto"])
def test_si_claude_no_sirve_se_usa_la_tabla(monkeypatch, falso):
    _con_claude(monkeypatch, falso)
    _, chacra_id = _productor_con_chacra()

    resp = _sugerir(chacra_id, "Quinua")

    assert resp.status_code == 200, resp.text
    assert resp.json()["fuente"] == "tabla"
    assert resp.json()["cultivo_tabla"] == "Quinua"
    assert len(falso.llamadas) == 1


def test_un_cultivo_que_nadie_conoce_responde_404():
    _, chacra_id = _productor_con_chacra()
    resp = _sugerir(chacra_id, "Palta fuerte")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No conocemos cuánto tarda ese cultivo: elige tú la fecha."


def test_si_claude_dice_que_no_es_un_cultivo_y_la_tabla_tampoco_lo_conoce_responde_404(monkeypatch):
    _con_claude(monkeypatch, _ClaudeFalso(_respuesta(_ia(0, 0, 0, "", es_cultivo=False))))
    _, chacra_id = _productor_con_chacra()
    assert _sugerir(chacra_id, "Piedra").status_code == 404


def test_una_consulta_repetida_sale_del_cache(monkeypatch):
    falso = _con_claude(monkeypatch, _ClaudeFalso(_respuesta(_ia())))
    _, chacra_id = _productor_con_chacra()

    primera = _sugerir(chacra_id, "Papa amarilla")
    otro_dia_del_mismo_mes = _sugerir(chacra_id, "papa  AMARILLA", siembra=datetime(2026, 10, 20))
    assert len(falso.llamadas) == 1
    assert primera.json()["fuente"] == otro_dia_del_mismo_mes.json()["fuente"] == "ia"
    assert otro_dia_del_mismo_mes.json()["fecha_sugerida"] == _dia(115, datetime(2026, 10, 20))

    _sugerir(chacra_id, "Papa amarilla", siembra=datetime(2026, 12, 1))  # otro mes: otra temporada, otra consulta
    assert len(falso.llamadas) == 2


def test_cada_productor_tiene_un_limite_de_consultas_a_claude_por_hora(monkeypatch):
    monkeypatch.setattr(sugerencia_cosecha, "CONSULTAS_IA_POR_HORA", 2)
    falso = _con_claude(monkeypatch, _ClaudeFalso(_respuesta(_ia())))
    _, chacra_id = _productor_con_chacra()

    fuentes = [_sugerir(chacra_id, cultivo).json()["fuente"] for cultivo in ("Papa", "Maíz", "Quinua")]
    assert fuentes == ["ia", "ia", "tabla"]
    assert len(falso.llamadas) == 2

    _, otra_chacra = _productor_con_chacra()  # otro productor no comparte el límite
    assert _sugerir(otra_chacra, "Oca").json()["fuente"] == "ia"


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
