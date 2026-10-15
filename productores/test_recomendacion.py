"""Ranking de "¿qué me conviene sembrar?" (recomendacion_siembra.recomendar): sin base de datos ni red."""
from datetime import datetime, timedelta

import recomendacion_siembra as r

HOY = datetime(2026, 10, 7)


def test_gana_lo_que_se_vende_deja_margen_y_nadie_mas_cosecha():
    mercado = {"papa": {"kg": 600, "soles": 600 * 3.5, "kg_por_mes": [50, 50, 80, 120, 150, 150]},
               "quinua": {"kg": 60, "soles": 60 * 9, "kg_por_mes": [10] * 6}}
    planificadas = {"quinua": [HOY + timedelta(days=165 + d) for d in (-10, 0, 12)]}  # 3 cosechan cerca: riesgo alto
    datos = r.recomendar(mercado, {}, planificadas, {"papa": 1.2}, {}, HOY)
    papa, quinua = datos["recomendaciones"][0], datos["recomendaciones"][1]
    assert (papa["clave"], papa["fuente_costo"], papa["tendencia"], papa["riesgo"]) == ("papa", "tus cosechas", "sube", "bajo")
    assert papa["margen_kg"] == 2.3 and papa["margen_pct"] == 66 and papa["completo"]
    assert (quinua["clave"], quinua["competencia"], quinua["riesgo"]) == ("quinua", 3, "alto")
    assert quinua["fuente_costo"] == "referencia" and quinua["costo_kg"] == round(2800 / 1250, 2)
    assert any("3 siembras de otros agricultores" in f for f in quinua["frases"])
    # El rango de la tabla (120 a 180 días desde el 7/10/2026) y de qué depende, no un plazo solo.
    assert any(f.startswith("Tarda de 4 a 6 meses: si siembras hoy, cosecharías entre febrero y abril de 2027. Las papas")
               for f in papa["frases"])


def test_sin_costo_queda_detras_y_lo_que_no_tiene_datos_no_entra():
    mercado = {"oca": {"kg": 300, "soles": 900, "kg_por_mes": [50] * 6}, "papa": {"kg": 100, "soles": 350, "kg_por_mes": [0, 0, 0, 0, 50, 50]}}
    datos = r.recomendar(mercado, {}, {}, {}, {"papa": 1.0}, HOY)
    claves = [f["clave"] for f in datos["recomendaciones"]]
    assert claves[0] == "papa" and "oca" in claves  # la oca no tiene costo: va después aunque se venda más
    oca = next(f for f in datos["recomendaciones"] if f["clave"] == "oca")
    assert oca["margen_kg"] is None and not oca["completo"] and any("anota tus gastos" in f for f in oca["frases"])
    assert "Trigo" in datos["sin_datos"] and "trigo" not in claves


def test_el_precio_sale_del_catalogo_si_no_hubo_ventas_y_el_stock_sube_el_riesgo():
    datos = r.recomendar({"haba": {"kg": 30, "soles": 120, "kg_por_mes": [5] * 6}}, {"haba": {"precio_kg": 4.0, "stock_kg": 200}},
                         {}, {}, {"haba": 1.5}, HOY)
    haba = datos["recomendaciones"][0]
    # 200 kg en el catálogo y se venden 5 kg al mes: 40 meses de stock, mucho riesgo y menos puntaje
    assert haba["fuente_precio"] == "ventas en ChakraShop" and haba["riesgo"] == "alto"
    assert any("alcanza para más de un año" in f for f in haba["frases"])
    poco_stock = r.recomendar({"haba": {"kg": 30, "soles": 120, "kg_por_mes": [5] * 6}}, {"haba": {"precio_kg": 4.0, "stock_kg": 20}},
                              {}, {}, {"haba": 1.5}, HOY)["recomendaciones"][0]
    assert poco_stock["riesgo"] == "medio" and "unos 4 meses de lo que se vende" in " ".join(poco_stock["frases"])
    assert poco_stock["puntaje"] > haba["puntaje"]
    solo_catalogo = r.recomendar({}, {"arveja": {"precio_kg": 5.0, "stock_kg": 0}}, {}, {}, {}, HOY)["recomendaciones"][0]
    assert (solo_catalogo["clave"], solo_catalogo["fuente_precio"], solo_catalogo["demanda_kg"]) == ("arveja", "precio del catálogo", 0)
    sin_vender = r.recomendar({}, {"arveja": {"precio_kg": 5.0, "stock_kg": 80}}, {}, {}, {}, HOY)["recomendaciones"][0]
    assert sin_vender["riesgo"] == "medio" and "Hay 80 kg en el catálogo y todavía no se venden." in sin_vender["frases"]


def test_sin_clave_de_ia_no_hay_consejo_y_el_ranking_igual_sirve(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(r.sugerencia_cosecha, "_cliente", None, raising=False)
    assert r.consejo([{"nombre": "Papa"}], "p-1") is None


def test_la_kiwicha_dice_su_rango_y_de_que_depende():
    """Validación con usuarios del 2026-10-14 (A05): "no es útil que me sugiera cosechar en 3 o 4 meses la Kiwicha"."""
    datos = r.recomendar({"kiwicha": {"kg": 50, "soles": 50 * 12, "kg_por_mes": [5, 5, 10, 10, 10, 10]}}, {}, {}, {}, {}, HOY)
    kiwicha = next(f for f in datos["recomendaciones"] if f["clave"] == "kiwicha")
    [cuando] = [f for f in kiwicha["frases"] if f.startswith("Tarda")]
    assert cuando.startswith("Tarda de 5 a 6 meses: si siembras hoy, cosecharías entre febrero y abril de 2027.")
    assert "155 a 160 días" in cuando


def test_cuando_cosecharia_en_un_mes_o_entre_dos_anios():
    assert r._cuando_cosecharia(datetime(2027, 4, 2), datetime(2027, 4, 28)) == "en abril de 2027"
    assert r._cuando_cosecharia(datetime(2026, 12, 20), datetime(2027, 1, 30)) == "entre diciembre de 2026 y enero de 2027"
    assert r._cuanto_tarda(150, 160) == "unos 5 meses"
