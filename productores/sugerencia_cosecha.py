"""Sugerencia de la fecha de cosecha (observación de la presentación del 2026-10-01: "IA para sugerir la fecha de
cosecha").

Gemini (Google, plan gratuito de la API) estima cuántos días pasan de la siembra a la cosecha de ese cultivo en la zona
de la chacra, y el servidor calcula las fechas con esos días (la fecha no la inventa el modelo). Si no hay clave de API,
se acabó la cuota gratis, Gemini no responde a tiempo, bloquea la respuesta o devuelve algo fuera de lo razonable, la
sugerencia sale de una tabla de ciclos típicos de los cultivos de la sierra. La respuesta siempre dice de dónde salió:
fuente "ia" o "tabla".

En el plan gratuito Google puede usar lo que se le manda para mejorar sus productos: por eso solo va el cultivo, la
fecha de siembra y la zona aproximada de la chacra (coordenadas redondeadas a 0,1°, unos 11 km), nada del agricultor.
"""
import json
import logging
import os
import threading
import time
import unicodedata
from datetime import datetime, timedelta

import httpx
from google import genai
from google.genai import errors as errores_gemini
from google.genai import types

logger = logging.getLogger(__name__)

# Modelos del plan gratuito (2026-10-03, ai.google.dev/gemini-api/docs/pricing). Medido con la clave real ese día:
# Flash-Lite responde en 1-2 s con rangos razonables y más cuota gratis por día; 3.8 Flash tarda 9-17 s (cerca del
# límite de 20 s) y a ratos responde 503 "alta demanda". Por eso el principal es Flash-Lite, y si está saturado o se le
# acabó la cuota se le pregunta a 3.8 Flash (su cuota es aparte) antes de caer a la tabla. Los dos se cambian sin tocar
# el código: GEMINI_MODELO y GEMINI_MODELO_RESPALDO (vacío = sin respaldo).
MODELO_POR_DEFECTO = "gemini-3.5-flash-lite"
MODELO_RESPALDO_POR_DEFECTO = "gemini-3.8-flash"
CODIGOS_PARA_EL_RESPALDO = {429, 500, 503}  # sin cuota, error interno o saturado
TIEMPO_MAXIMO_SEGUNDOS = 20  # el agricultor está esperando en el asistente: pasado esto, la tabla
DECIMALES_ZONA = 1           # 0,1° ≈ 11 km: la zona (altura, clima), no la chacra
DIAS_MINIMOS, DIAS_MAXIMOS = 20, 730
LARGO_MAXIMO_EXPLICACION = 300
# La cuota gratis de Gemini es por día y para todo el proyecto: por productor, a lo más estas consultas por hora (las
# repetidas salen del caché y no cuentan), para que nadie la gaste sola.
CONSULTAS_IA_POR_HORA = 30
TAMANO_CACHE = 500

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre",
         "noviembre", "diciembre")

# Días de la siembra a la cosecha en la sierra (unos 2500 a 3800 m), para lo que normalmente se vende. Valores de
# referencia, redondeados: la variedad, la altura y las lluvias los mueven.
CICLOS_TIPICOS = {
    "papa": ("Papa", 120, 150, 180, ("papa", "patata"),
             "Las papas mejoradas se cosechan antes; las nativas tardan más."),
    "maiz": ("Maíz", 150, 180, 240, ("maiz", "choclo"),
             "Para choclo se cosecha antes; para grano seco, después."),
    "quinua": ("Quinua", 140, 165, 200, ("quinua", "quinoa"),
               "Las variedades precoces se cosechan antes."),
    "habas": ("Habas", 120, 150, 200, ("haba", "habas"),
              "En verde se cosechan antes; en grano seco, después."),
    "arveja": ("Arveja", 90, 120, 150, ("arveja", "alverja"),
               "En verde se cosecha antes; en grano seco, después."),
    "trigo": ("Trigo", 140, 165, 190, ("trigo",), "Depende de la variedad y de las lluvias."),
    "cebada": ("Cebada", 120, 140, 165, ("cebada",), "Depende de la variedad y de las lluvias."),
    "oca": ("Oca", 180, 210, 240, ("oca",), "Es de ciclo largo: depende de la altura."),
    "olluco": ("Olluco", 150, 180, 210, ("olluco", "ulluco", "lisa"), "Depende de la altura y de las lluvias."),
    "mashua": ("Mashua", 180, 210, 240, ("mashua", "isano"), "Es de ciclo largo: depende de la altura."),
    "kiwicha": ("Kiwicha", 120, 150, 180, ("kiwicha", "amaranto"), "Depende de la variedad y de las lluvias."),
    "tarwi": ("Tarwi", 180, 210, 270, ("tarwi", "chocho", "lupino"), "Es de ciclo largo: depende de la altura."),
    "frijol": ("Frijol", 90, 120, 150, ("frijol", "frejol", "poroto"),
               "En verde se cosecha antes; en grano seco, después."),
    "cebolla": ("Cebolla", 120, 140, 170, ("cebolla",), "Contando desde que se siembra o trasplanta."),
    "ajo": ("Ajo", 150, 180, 210, ("ajo",), "Depende de la variedad y de la altura."),
    "zanahoria": ("Zanahoria", 90, 110, 130, ("zanahoria",), "Depende de la variedad y del clima."),
    "zapallo": ("Zapallo", 120, 150, 180, ("zapallo", "calabaza"), "Depende de la variedad y del clima."),
}

SISTEMA = (
    "Eres un agrónomo que conoce bien la sierra del Perú. Un agricultor acaba de registrar una siembra y quiere saber "
    "cuándo podrá cosechar. Con el cultivo, la fecha de siembra y la zona aproximada de su chacra, estima cuántos días "
    "pasan desde la siembra hasta la cosecha que normalmente se vende, en esa zona: ten en cuenta la altura, el clima "
    "y la temporada de lluvias de ese lugar y de esa fecha.\n"
    "- dias_min, dias_probable y dias_max: el rango razonable y el valor más probable, en días.\n"
    "- explicacion: una o dos frases cortas en español sencillo, para una persona mayor del campo: de qué depende "
    "(la variedad, las lluvias, la altura). Sin palabras técnicas, sin coordenadas y sin repetir los días.\n"
    "- es_cultivo: false si el texto no es algo que se siembra y se cosecha (en ese caso los días van en 0)."
)

ESQUEMA = {
    "type": "object",
    "properties": {
        "es_cultivo": {"type": "boolean"},
        "dias_min": {"type": "integer"},
        "dias_probable": {"type": "integer"},
        "dias_max": {"type": "integer"},
        "explicacion": {"type": "string"},
    },
    "required": ["es_cultivo", "dias_min", "dias_probable", "dias_max", "explicacion"],
}

_cliente = None
_cache = {}
_consultas_por_productor = {}
_candado = threading.Lock()


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def ciclo_de_la_tabla(cultivo: str):
    """(nombre, mín, probable, máx, nota) del primer cultivo conocido que aparezca en el texto ("Papa amarilla",
    "maíz morado", "HABAS"...), o None."""
    for palabra in _normalizar(cultivo).replace(",", " ").split():
        for nombre, dias_min, probable, dias_max, alias, nota in CICLOS_TIPICOS.values():
            if palabra in alias or (palabra.endswith("s") and palabra[:-1] in alias):
                return nombre, dias_min, probable, dias_max, nota
    return None


def _cliente_ia():
    """El cliente se crea la primera vez que se usa y solo si hay clave (si no, no hay IA y punto). Sin reintentos: el
    agricultor está esperando, y si falla está la tabla."""
    global _cliente
    clave = os.getenv("GEMINI_API_KEY")
    if not clave:
        return None
    if _cliente is None:
        _cliente = genai.Client(api_key=clave, http_options=types.HttpOptions(
            timeout=TIEMPO_MAXIMO_SEGUNDOS * 1000,  # en milisegundos
            retry_options=types.HttpRetryOptions(attempts=1),
        ))
    return _cliente


def _modelos():
    principal = os.getenv("GEMINI_MODELO") or MODELO_POR_DEFECTO
    respaldo = os.getenv("GEMINI_MODELO_RESPALDO", MODELO_RESPALDO_POR_DEFECTO)
    return [principal] + ([respaldo] if respaldo and respaldo != principal else [])


def _generar(cliente, pregunta: str):
    """La respuesta del modelo principal o, si está saturado o sin cuota, la del de respaldo. Un tiempo agotado no se
    reintenta (el agricultor ya esperó): sube como error y se usa la tabla."""
    config = types.GenerateContentConfig(
        system_instruction=SISTEMA,
        response_mime_type="application/json",
        response_json_schema=ESQUEMA,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        max_output_tokens=4000,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # no usa herramientas
    )
    modelos = _modelos()
    for i, modelo in enumerate(modelos):
        try:
            return cliente.models.generate_content(model=modelo, contents=pregunta, config=config)
        except errores_gemini.APIError as error:
            if error.code not in CODIGOS_PARA_EL_RESPALDO or i == len(modelos) - 1:
                raise
            logger.warning("Sugerencia de cosecha: %s respondió %s; se pregunta a %s", modelo, error.code, modelos[i + 1])


def _puede_consultar(clave_productor: str) -> bool:
    ahora = time.monotonic()
    with _candado:
        recientes = [t for t in _consultas_por_productor.get(clave_productor, []) if ahora - t < 3600]
        if len(recientes) >= CONSULTAS_IA_POR_HORA:
            _consultas_por_productor[clave_productor] = recientes
            return False
        _consultas_por_productor[clave_productor] = recientes + [ahora]
        return True


def _guardar_en_cache(clave, valor):
    with _candado:
        if len(_cache) >= TAMANO_CACHE:
            _cache.pop(next(iter(_cache)))
        _cache[clave] = valor


def _preguntar_a_la_ia(cliente, cultivo: str, fecha_siembra: datetime, latitud: float, longitud: float):
    """(mín, probable, máx, explicación); ("no_es_cultivo",) si la IA dice que eso no se cosecha; None si no sirvió."""
    pregunta = (
        f'Cultivo: "{cultivo}"\n'
        f"Fecha de siembra: {fecha_siembra.day} de {MESES[fecha_siembra.month - 1]} de {fecha_siembra.year}\n"
        f"Zona de la chacra (aproximada): latitud {latitud:.{DECIMALES_ZONA}f}, longitud {longitud:.{DECIMALES_ZONA}f}"
    )
    try:
        respuesta = _generar(cliente, pregunta)
        candidato = (respuesta.candidates or [None])[0]
        motivo = candidato.finish_reason if candidato is not None else None
        if motivo != types.FinishReason.STOP:  # bloqueada (SAFETY...), sin espacio (MAX_TOKENS) o sin respuesta
            bloqueo = getattr(respuesta.prompt_feedback, "block_reason", None)
            logger.warning("Sugerencia de cosecha: Gemini terminó con %s (%s); se usa la tabla", motivo, bloqueo)
            return None
        datos = json.loads(respuesta.text)
        if not datos["es_cultivo"]:
            return ("no_es_cultivo",)
        dias_min, probable, dias_max = (int(datos[k]) for k in ("dias_min", "dias_probable", "dias_max"))
        explicacion = " ".join(str(datos["explicacion"]).split())[:LARGO_MAXIMO_EXPLICACION]
    except (errores_gemini.APIError, httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        # APIError incluye el 429 de cuota gratis agotada; httpx.HTTPError, la falta de red y el tiempo agotado.
        logger.warning("Sugerencia de cosecha: Gemini no respondió bien (%s); se usa la tabla", type(error).__name__)
        return None

    if not (DIAS_MINIMOS <= dias_min <= probable <= dias_max <= DIAS_MAXIMOS) or not explicacion:
        logger.warning("Sugerencia de cosecha: días fuera de rango (%s, %s, %s); se usa la tabla",
                       dias_min, probable, dias_max)
        return None
    return dias_min, probable, dias_max, explicacion


def _resultado(fecha_siembra: datetime, dias_min, probable, dias_max, explicacion, fuente, cultivo_tabla=None):
    def fecha(dias):
        return (fecha_siembra + timedelta(days=dias)).date().isoformat()

    return {
        "fecha_sugerida": fecha(probable),
        "fecha_desde": fecha(dias_min),
        "fecha_hasta": fecha(dias_max),
        "dias_min": dias_min,
        "dias_probable": probable,
        "dias_max": dias_max,
        "explicacion": explicacion,
        "fuente": fuente,
        "cultivo_tabla": cultivo_tabla,
    }


def sugerir(cultivo: str, fecha_siembra: datetime, latitud: float, longitud: float, clave_productor: str):
    """La sugerencia (ver _resultado), o None si no se sabe nada de ese cultivo."""
    zona = (round(float(latitud), DECIMALES_ZONA), round(float(longitud), DECIMALES_ZONA))
    clave = (_normalizar(cultivo), fecha_siembra.month, zona)

    ia = _cache.get(clave)
    if ia is None:
        cliente = _cliente_ia()
        if cliente is not None and _puede_consultar(clave_productor):
            ia = _preguntar_a_la_ia(cliente, cultivo, fecha_siembra, *zona)
            if ia is not None:
                _guardar_en_cache(clave, ia)
    if ia is not None and ia[0] != "no_es_cultivo":
        return _resultado(fecha_siembra, *ia, fuente="ia")

    tabla = ciclo_de_la_tabla(cultivo)
    if tabla is None:
        return None
    nombre, dias_min, probable, dias_max, nota = tabla
    return _resultado(fecha_siembra, dias_min, probable, dias_max, nota, fuente="tabla", cultivo_tabla=nombre)
