"""Las búsquedas de la conciliación en Culqi (culqi_client.buscar_cargo y buscar_devolucion), con un httpx.Client falso
que se porta como la API real según lo que se comprobó contra el sandbox el 2026-11-19: la lista no viene en orden de
fecha, filtra por creation_date_from/creation_date_to, ignora los parámetros que no conoce, no guarda la metadata de las
devoluciones y el cargo dice cuánto se le devolvió (amount_refunded)."""
from datetime import datetime, timedelta

import httpx
import pytest

import culqi_client

INICIO = datetime(2026, 11, 19, 21, 0, 0)  # la hora del intento (UTC, sin zona, como en la base)
DESDE, HASTA = INICIO - timedelta(minutes=5), INICIO + timedelta(minutes=5)


def _ms(momento: datetime) -> int:
    return culqi_client._milisegundos(momento)


def _cargo(ident, minutos_despues=0, referencia=None, **extra):
    return {"object": "charge", "id": ident, "creation_date": _ms(INICIO + timedelta(minutes=minutos_despues)),
            "metadata": {"referencia": referencia} if referencia else {}, "outcome": {"type": "venta_exitosa"}, **extra}


class _Culqi:
    """httpx.Client falso: responde lo que se le dio por ruta ("charges", "refunds", "charges/<id>") y anota lo pedido.
    Con aplicar_filtro=False hace como si Culqi no conociera los parámetros de fecha (los ignora)."""

    def __init__(self, respuestas: dict, aplicar_filtro=True):
        self.respuestas, self.aplicar_filtro, self.pedidos = respuestas, aplicar_filtro, []

    def __call__(self, *args, **kwargs):  # se usa como httpx.Client()
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None, headers=None, timeout=None):
        ruta = url.removeprefix(culqi_client.CULQI_API_BASE + "/")
        self.pedidos.append((ruta, dict(params or {})))
        codigo, cuerpo = self.respuestas[ruta]
        if codigo == 200 and ruta in ("charges", "refunds") and self.aplicar_filtro and params:
            desde, hasta = params.get("creation_date_from"), params.get("creation_date_to")
            cuerpo = {**cuerpo, "data": [o for o in cuerpo["data"] if desde <= o["creation_date"] <= hasta]}
        return httpx.Response(codigo, json=cuerpo, request=httpx.Request("GET", url))


@pytest.fixture
def culqi(monkeypatch):
    def poner(respuestas, aplicar_filtro=True):
        falso = _Culqi(respuestas, aplicar_filtro)
        monkeypatch.setattr(culqi_client.httpx, "Client", falso)
        return falso
    return poner


def test_buscar_cargo_pide_una_sola_ventana_y_lo_encuentra_aunque_la_lista_no_venga_en_orden(culqi):
    lista = [_cargo("chr_B", 3), _cargo("chr_a", -2, referencia="ref-1"), _cargo("chr_C", -40), _cargo("chr_d", 1)]
    falso = culqi({"charges": (200, {"data": lista})})
    hallado = culqi_client.buscar_cargo("ref-1", DESDE, HASTA)
    assert hallado == {"encontrado": lista[1], "completo": True}
    [(ruta, parametros)] = falso.pedidos  # una sola consulta: la paginación de Culqi no es confiable
    assert ruta == "charges" and parametros == {"limit": 100, "creation_date_from": _ms(DESDE), "creation_date_to": _ms(HASTA)}


def test_buscar_cargo_sin_rastro_en_una_ventana_completa_concluye_que_no_se_cobro(culqi):
    culqi({"charges": (200, {"data": [_cargo("chr_x", 1, referencia="otra"), _cargo("chr_y", -1)]})})
    assert culqi_client.buscar_cargo("ref-1", DESDE, HASTA) == {"encontrado": None, "completo": True}


def test_buscar_cargo_no_concluye_si_culqi_no_aplico_el_filtro_de_fechas(culqi):
    # Culqi ignora en silencio un parámetro que no conoce: devolvería todo, y lo de fuera de la ventana lo delata.
    culqi({"charges": (200, {"data": [_cargo("chr_x", 1), _cargo("chr_viejo", -600)]})}, aplicar_filtro=False)
    assert culqi_client.buscar_cargo("ref-1", DESDE, HASTA) == {"encontrado": None, "completo": False}


def test_buscar_cargo_no_concluye_si_la_ventana_trae_la_pagina_llena(culqi):
    culqi({"charges": (200, {"data": [_cargo(f"chr_{n}", 0) for n in range(culqi_client.POR_PAGINA)]})})
    assert culqi_client.buscar_cargo("ref-1", DESDE, HASTA) == {"encontrado": None, "completo": False}


def test_buscar_cargo_lanza_si_culqi_responde_con_error(culqi):
    culqi({"charges": (500, {"object": "error"})})
    with pytest.raises(httpx.HTTPStatusError):
        culqi_client.buscar_cargo("ref-1", DESDE, HASTA)


def test_buscar_devolucion_hecha_la_reconoce_por_amount_refunded_y_busca_su_id_en_la_ventana(culqi):
    devolucion = {"object": "refund", "id": "ref_1", "charge_id": "chr_a", "creation_date": _ms(INICIO), "status": "completa"}
    otra = {"object": "refund", "id": "ref_2", "charge_id": "chr_b", "creation_date": _ms(INICIO), "status": "completa"}
    falso = culqi({"charges/chr_a": (200, _cargo("chr_a", amount=4500, amount_refunded=4500)),
                   "refunds": (200, {"data": [otra, devolucion]})})
    assert culqi_client.buscar_devolucion("chr_a", 45.0, DESDE, HASTA) == {"encontrado": devolucion, "completo": True}
    assert [ruta for ruta, _ in falso.pedidos] == ["charges/chr_a", "refunds"]


def test_buscar_devolucion_hecha_sin_hallar_su_id_igual_cuenta_como_hecha(culqi):
    culqi({"charges/chr_a": (200, _cargo("chr_a", amount=4500, amount_refunded=4500)), "refunds": (503, {})})
    assert culqi_client.buscar_devolucion("chr_a", 45.0, DESDE, HASTA) == {
        "encontrado": {"id": None, "charge_id": "chr_a"}, "completo": True}


@pytest.mark.parametrize("devuelto, esperado", [
    (0, {"encontrado": None, "completo": True}),       # no se devolvió
    (1500, {"encontrado": None, "completo": False}),   # una parte: no se puede concluir
    (None, {"encontrado": None, "completo": False}),   # sin el campo
])
def test_buscar_devolucion_no_hecha_o_dudosa(culqi, devuelto, esperado):
    culqi({"charges/chr_a": (200, _cargo("chr_a", amount=4500, amount_refunded=devuelto))})
    assert culqi_client.buscar_devolucion("chr_a", 45.0, DESDE, HASTA) == esperado


def test_buscar_devolucion_lanza_si_culqi_no_da_el_cargo(culqi):
    culqi({"charges/chr_a": (502, {})})
    with pytest.raises(ValueError):
        culqi_client.buscar_devolucion("chr_a", 45.0, DESDE, HASTA)


@pytest.mark.parametrize("devuelto, esperado", [
    (4500, {"encontrado": None, "completo": True}),    # lo mismo que antes: esta devolución no se hizo
    (9000, "hecha"),                                   # lo de antes + esta
    (6000, {"encontrado": None, "completo": False}),   # entre medio: no se puede concluir
])
def test_buscar_devolucion_compara_lo_acumulado(culqi, devuelto, esperado):
    # El cargo ya tenía S/ 45 devueltos al empezar: que amount_refunded diga 4500 no prueba esta devolución.
    culqi({"charges/chr_a": (200, _cargo("chr_a", amount=9000, amount_refunded=devuelto)), "refunds": (200, {"data": []})})
    hallado = culqi_client.buscar_devolucion("chr_a", 45.0, DESDE, HASTA, devuelto_antes=4500)
    if esperado == "hecha":
        assert hallado == {"encontrado": {"id": None, "charge_id": "chr_a"}, "completo": True}
    else:
        assert hallado == esperado


def test_devuelto_del_cargo(culqi):
    culqi({"charges/chr_a": (200, _cargo("chr_a", amount_refunded=1500)), "charges/chr_b": (200, _cargo("chr_b")),
           "charges/chr_c": (404, {"object": "error"})})
    assert culqi_client.devuelto_del_cargo("chr_a") == 1500
    for sin_dato in ("chr_b", "chr_c"):  # sin el campo, o Culqi no lo tiene: no se sigue sin ese dato
        with pytest.raises(ValueError):
            culqi_client.devuelto_del_cargo(sin_dato)


def test_dos_cargos_iguales_en_correo_monto_y_hora_se_distinguen_por_la_referencia(culqi):
    gemelos = [_cargo(f"chr_{n}", 1, referencia=f"ref-{n}", email="cliente@test.com", amount=4000) for n in (1, 2)]
    culqi({"charges": (200, {"data": gemelos})})
    assert culqi_client.buscar_cargo("ref-1", DESDE, HASTA)["encontrado"]["id"] == "chr_1"
    assert culqi_client.buscar_cargo("ref-2", DESDE, HASTA)["encontrado"]["id"] == "chr_2"
    assert culqi_client.buscar_cargo("ref-3", DESDE, HASTA) == {"encontrado": None, "completo": True}
