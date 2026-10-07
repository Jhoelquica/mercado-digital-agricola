"""¿Qué me conviene sembrar? (observación del usuario del 2026-10-06: "generar sugerencias de qué productos es rentable
sembrar"). Un ranking de los cultivos de temporada que combina, con lo que haya:

- demanda: los kilos que se vendieron en ChakraShop en los últimos meses y si van subiendo (Pedidos /ventas/mercado);
- precio por kg: el de esas ventas o, si no hubo, el del catálogo;
- costo por kg: el de las cosechas del propio agricultor, si no el de otros agricultores de la plataforma, si no una
  referencia investigada (costo y rendimiento por hectárea);
- cuánto tarda (sugerencia_cosecha.CICLOS_TIPICOS) y, con eso, cuándo cosecharía si siembra hoy;
- riesgo de que sobre: siembras ya planificadas por OTROS agricultores que se cosechan cerca de esa fecha y el stock del
  catálogo frente a lo que se vende.

Los números salen solo de los datos; la IA (opcional) solo los cuenta en palabras sencillas.
"""
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta

import plantillas_produccion as plantillas
import sugerencia_cosecha

logger = logging.getLogger("productores.que_sembrar")

# Referencias investigadas (2026-10-07). Rendimiento en kg por hectárea; costo en soles por hectárea (None = sin dato).
REFERENCIAS = {
    "papa": {"rendimiento_kg_ha": 23500, "costo_ha": 15500,
             "fuente": "Dirección Regional Agraria de Ayacucho (Andina): S/ 15–16 mil por hectárea y 23–24 t/ha"},
    "quinua": {"rendimiento_kg_ha": 1250, "costo_ha": 2800,
               "fuente": "Congreso de la República: en la sierra 1–1,5 t/ha, a S/ 2–2,50 por kg"},
    "haba": {"rendimiento_kg_ha": 5000, "costo_ha": None, "fuente": "INIA: 5 t/ha en vaina verde (1 t/ha en grano seco)"},
    "maiz": {"rendimiento_kg_ha": 15000, "costo_ha": None, "fuente": "agraria.pe: choclo unas 15 t/ha (grano seco 2–3 t/ha)"},
    "trigo": {"rendimiento_kg_ha": 1800, "costo_ha": None, "fuente": "agraria.pe: promedio nacional 1,8 t/ha"},
    "kiwicha": {"rendimiento_kg_ha": 1885, "costo_ha": None, "fuente": "UNASAM: promedio nacional 1 885 kg/ha"},
}
DIAS_COMPETENCIA = 30  # otra cosecha a ±30 días de la tuya compite por los mismos compradores
RIESGOS = ("bajo", "medio", "alto")


def candidatos() -> list[str]:
    """Los alimentos que se siembran (cultivos de temporada)."""
    return [clave for clave, (_n, familia, _a, _u) in plantillas.ALIMENTOS.items()
            if plantillas.FAMILIAS[familia]["tipo"] == "temporada"]


def _tendencia(kg_por_mes: list[float]) -> str | None:
    if len(kg_por_mes) < 6:
        return None
    antes, ahora = sum(kg_por_mes[-6:-3]), sum(kg_por_mes[-3:])
    if not antes and not ahora:
        return None
    if not antes:
        return "sube"
    razon = ahora / antes
    return "sube" if razon > 1.15 else "baja" if razon < 0.85 else "estable"


def _miles(n) -> str:
    """1234.6 → "1 235" (el espacio de miles que se usa en el resto de la aplicación)."""
    return f"{round(n):,}".replace(",", " ")


def _mes_legible(fecha: datetime) -> str:
    meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre", "noviembre",
             "diciembre")
    return f"{meses[fecha.month - 1]} de {fecha.year}"


def recomendar(mercado: dict, catalogo: dict, planificadas: dict, costos_propios: dict, costos_plataforma: dict,
               hoy: datetime) -> dict:
    """mercado[clave] = {"kg", "soles", "kg_por_mes"}; catalogo[clave] = {"precio_kg", "stock_kg"};
    planificadas[clave] = [fechas de cosecha estimadas de OTROS]; costos_*[clave] = S/ por kg. Devuelve el ranking
    (los que tienen datos para comparar) y los que no tienen datos todavía."""
    maximo_kg = max((m["kg"] for m in mercado.values()), default=0) or 1
    filas, sin_datos = [], []
    for clave in candidatos():
        nombre = plantillas.ALIMENTOS[clave][0]
        m, c, ref = mercado.get(clave), catalogo.get(clave), REFERENCIAS.get(clave)
        ciclo = sugerencia_cosecha.ciclo_de_la_tabla(nombre)
        dias = ciclo[2] if ciclo else None
        cosecha = hoy + timedelta(days=dias) if dias else None

        if m and m["kg"] > 0:
            precio, fuente_precio = m["soles"] / m["kg"], "ventas en ChakraShop"
        elif c and c.get("precio_kg"):
            precio, fuente_precio = c["precio_kg"], "precio del catálogo"
        else:
            precio, fuente_precio = None, None

        if clave in costos_propios:
            costo, fuente_costo = costos_propios[clave], "tus cosechas"
        elif clave in costos_plataforma:
            costo, fuente_costo = costos_plataforma[clave], "cosechas de otros agricultores"
        elif ref and ref["costo_ha"]:
            costo, fuente_costo = ref["costo_ha"] / ref["rendimiento_kg_ha"], "referencia"
        else:
            costo, fuente_costo = None, None

        demanda_kg = m["kg"] if m else 0.0
        tendencia = _tendencia(m["kg_por_mes"]) if m else None
        otras = planificadas.get(clave, [])
        compiten = [f for f in otras if cosecha and abs((f - cosecha).days) <= DIAS_COMPETENCIA] if cosecha else otras
        nivel = 0 if not compiten else 1 if len(compiten) <= 2 else 2
        por_mes = demanda_kg / max(1, len(m["kg_por_mes"])) if m else 0
        if c and c.get("stock_kg") and por_mes and c["stock_kg"] > 3 * por_mes:
            nivel = min(2, nivel + 1)  # ya hay en el catálogo más de 3 meses de lo que se vende

        if precio is None and not demanda_kg and not otras:
            if ref or clave in costos_plataforma:
                sin_datos.append(nombre)
            continue
        margen = precio - costo if precio is not None and costo is not None else None
        margen_pct = margen / precio if margen is not None and precio else None
        demanda = demanda_kg / maximo_kg + {"sube": 0.1, "baja": -0.1}.get(tendencia, 0)
        riesgo = 1 - nivel / 2
        if margen_pct is not None:
            puntaje = 0.45 * (max(-1, min(1, margen_pct)) + 1) / 2 + 0.35 * max(0, min(1, demanda)) + 0.2 * riesgo
        else:
            puntaje = 0.8 * (0.6 * max(0, min(1, demanda)) + 0.4 * riesgo)  # sin costo: menos seguro

        frases = []
        if demanda_kg:
            frases.append(f"Se vendieron {_miles(demanda_kg)} kg en ChakraShop en los últimos {len(m['kg_por_mes'])} meses"
                          + {"sube": " y va subiendo", "baja": " y va bajando", "estable": " y se mantiene"}.get(tendencia, "") + ".")
        else:
            frases.append("Todavía no se vende en ChakraShop: no hay demanda comprobada.")
        if precio is not None:
            frases.append(f"Precio promedio: S/ {precio:.2f} el kg ({fuente_precio}).")
        if margen is not None:
            frases.append(f"Te costaría unos S/ {costo:.2f} el kg ({fuente_costo}): ganarías unos S/ {margen:.2f} por kg "
                          f"({margen_pct:.0%}).".replace("%", " %"))
        elif precio is not None:
            frases.append("Falta saber cuánto cuesta producirlo: anota tus gastos por etapa al sembrar y lo calcularemos.")
        if cosecha:
            frases.append(f"Tarda unos {round(dias / 30)} meses: si siembras hoy, cosecharías en {_mes_legible(cosecha)}.")
        if compiten:
            frases.append(f"Ojo: {len(compiten)} siembra{'s' if len(compiten) > 1 else ''} de otros agricultores se "
                          f"cosecha{'n' if len(compiten) > 1 else ''} cerca de esa fecha: puede sobrar y bajar el precio.")
        elif cosecha:
            frases.append("Nadie más en la plataforma cosecha cerca de esa fecha.")
        if ref:
            frases.append(f"Rinde unos {_miles(ref['rendimiento_kg_ha'])} kg por hectárea ({ref['fuente']}).")

        filas.append({
            "clave": clave, "nombre": nombre, "puntaje": round(100 * max(0, min(1, puntaje))),
            "precio_kg": round(precio, 2) if precio is not None else None, "fuente_precio": fuente_precio,
            "costo_kg": round(costo, 2) if costo is not None else None, "fuente_costo": fuente_costo,
            "margen_kg": round(margen, 2) if margen is not None else None,
            "margen_pct": round(100 * margen_pct) if margen_pct is not None else None,
            "demanda_kg": round(demanda_kg, 1), "tendencia": tendencia, "ciclo_dias": dias,
            "cosecha_estimada": cosecha.date().isoformat() if cosecha else None,
            "competencia": len(compiten), "riesgo": RIESGOS[nivel],
            "stock_catalogo_kg": round(c["stock_kg"], 1) if c and c.get("stock_kg") else 0,
            "rendimiento_kg_ha": ref["rendimiento_kg_ha"] if ref else None, "frases": frases,
            "completo": precio is not None and margen is not None and demanda_kg > 0,
        })
    filas.sort(key=lambda f: (-f["completo"], -f["puntaje"]))
    return {"recomendaciones": filas, "sin_datos": sorted(sin_datos)}


# ---- Consejo en palabras sencillas (opcional, con la misma IA de la fecha de cosecha) ----
SISTEMA_CONSEJO = (
    "Eres un asesor agrario que habla con agricultores de la sierra de Ayacucho, Perú, muchos de edad avanzada. Con el "
    "ranking que te paso (ya calculado con datos reales de la plataforma), escribe un consejo corto: 2 o 3 oraciones en "
    "español sencillo, recomendando 1 o 2 cultivos y diciendo por qué con los datos que ves. No inventes números ni "
    "datos que no estén en el ranking; si falta información, dilo. Nada de listas ni de saludos."
)
ESQUEMA_CONSEJO = {"type": "object", "properties": {"consejo": {"type": "string"}}, "required": ["consejo"]}
_cache_consejos = {}
_candado = threading.Lock()


def consejo(ranking: list[dict], clave_productor: str) -> str | None:
    """2–3 oraciones con el resumen del ranking, o None (sin clave de IA, sin cuota, error): la pantalla muestra igual
    el ranking con sus frases."""
    cliente = sugerencia_cosecha._cliente_ia()
    if not cliente or not ranking:
        return None
    resumen = [{k: f[k] for k in ("nombre", "puntaje", "precio_kg", "costo_kg", "margen_kg", "demanda_kg", "tendencia",
                                  "cosecha_estimada", "competencia", "riesgo")} for f in ranking[:5]]
    clave = hashlib.sha256(json.dumps(resumen, sort_keys=True).encode()).hexdigest()
    with _candado:
        guardado = _cache_consejos.get(clave)
        if guardado and time.monotonic() - guardado[0] < 6 * 3600:
            return guardado[1]
    if not sugerencia_cosecha._puede_consultar(f"que-sembrar:{clave_productor}"):
        return None
    from google.genai import errors as errores_gemini
    from google.genai import types
    config = types.GenerateContentConfig(
        system_instruction=SISTEMA_CONSEJO, response_mime_type="application/json", response_json_schema=ESQUEMA_CONSEJO,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW), max_output_tokens=800,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    pregunta = f"Ranking (de más a menos conveniente): {json.dumps(resumen, ensure_ascii=False)}"
    modelos = sugerencia_cosecha._modelos()
    for i, modelo in enumerate(modelos):
        try:
            texto = json.loads(cliente.models.generate_content(model=modelo, contents=pregunta, config=config).text)["consejo"].strip()
            with _candado:
                _cache_consejos[clave] = (time.monotonic(), texto)
            return texto
        except errores_gemini.APIError as error:
            if error.code not in sugerencia_cosecha.CODIGOS_PARA_EL_RESPALDO or i == len(modelos) - 1:
                logger.warning("Qué sembrar: la IA respondió %s", error.code)
                return None
        except Exception:
            logger.warning("Qué sembrar: no se pudo obtener el consejo de la IA", exc_info=True)
            return None
    return None
