"""Contorno poligonal de la chacra: validación, área, huella y geocerca (shapely y pyproj).

El agricultor dibuja el borde de su chacra sobre el mapa satelital (EditorContorno en el frontend) y llega como un
GeoJSON Polygon con las coordenadas en el orden [longitud, latitud]. Acá se comprueba que sea un polígono sano (sin
cruces), dentro del Perú, de un tamaño creíble (50 m² a 200 ha) y que rodee, o casi, el punto que la chacra ya tiene
registrado: ese punto sigue siendo su ubicación oficial y no cambia por dibujar el contorno. El área se calcula SIEMPRE
acá, sobre el elipsoide WGS84: lo que mande el cliente se ignora.

Con el contorno dibujado, la geocerca de cada siembra y de cada cosecha se mide contra él (evaluar_geocerca): dentro,
cerca del borde, fuera... Sin contorno se sigue usando el punto y el radio de siempre (main.RADIO_CHACRA_KM). Lo que
queda sellado en la cadena de certificación es la frase de frase_de_geocerca, con la huella SHA-256 del contorno
(hash_del_contorno): nunca coordenadas ni el polígono.
"""
import hashlib
import json
import math

from pyproj import Geod
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points
from shapely.validation import explain_validity

COORDENADAS_MINIMAS = 4        # un triángulo cerrado: tres vértices y el primero repetido al final
COORDENADAS_MAXIMAS = 500
# Caja que contiene al Perú (longitud, latitud).
PERU_LNG = (-81.4, -68.6)
PERU_LAT = (-18.4, 0.1)
AREA_MINIMA_M2 = 50.0
AREA_MAXIMA_M2 = 2_000_000.0   # 200 ha
# Hasta dónde puede quedar un punto fuera del borde dibujado y seguir contando como "en la chacra": el punto registrado
# de la chacra al dibujar el contorno, y el agricultor al sembrar o cosechar (el GPS falla seguido en el campo).
TOLERANCIA_PUNTO_M = 50.0
MENSAJE_PUNTO_FUERA = "El punto registrado de la chacra queda fuera del contorno dibujado"
# Con una lectura del GPS menos precisa que esto no se puede decir si el agricultor está dentro del contorno.
PRECISION_MAXIMA_M = 100.0
# La huella del contorno se calcula sobre sus coordenadas con 7 decimales (~1 cm).
DECIMALES_CANONICOS = 7

METODO_POLIGONO = "poligono"
METODO_RADIO = "radio"
# Lo que puede resultar de una geocerca (ver evaluar_geocerca). "radio": la chacra no tiene contorno y vale lo de
# siempre, la distancia a su punto.
RESULTADOS_CONFIRMADOS = ("dentro", "cerca")

_geod = Geod(ellps="WGS84")


class ContornoInvalido(ValueError):
    """Un contorno que no se acepta: el mensaje va tal cual en el 422."""


def _anillo_exterior(geo) -> list[list[float]]:
    """El anillo exterior como lista de [lng, lat] (números finitos), cerrado. Solo se acepta un Polygon sin huecos."""
    if not isinstance(geo, dict) or geo.get("type") != "Polygon":
        raise ContornoInvalido("El contorno debe ser un polígono (GeoJSON Polygon)")
    anillos = geo.get("coordinates")
    if not isinstance(anillos, list) or not anillos or not isinstance(anillos[0], list):
        raise ContornoInvalido("El contorno no tiene coordenadas")
    if len(anillos) > 1:
        raise ContornoInvalido("El contorno no puede tener huecos: dibuja solo el borde de la chacra")
    vertices = []
    for posicion in anillos[0]:
        if not isinstance(posicion, (list, tuple)) or len(posicion) < 2:
            raise ContornoInvalido("Cada vértice del contorno debe ser [longitud, latitud]")
        try:
            lng, lat = float(posicion[0]), float(posicion[1])
        except (TypeError, ValueError):
            raise ContornoInvalido("Cada vértice del contorno debe ser [longitud, latitud]") from None
        if not (math.isfinite(lng) and math.isfinite(lat)):
            raise ContornoInvalido("Cada vértice del contorno debe ser [longitud, latitud]")
        vertices.append([lng, lat])
    # GeoJSON exige el anillo cerrado (el último vértice repite al primero); si el cliente lo manda abierto, se cierra.
    if vertices and vertices[0] != vertices[-1]:
        vertices.append(list(vertices[0]))
    if not COORDENADAS_MINIMAS <= len(vertices) <= COORDENADAS_MAXIMAS:
        raise ContornoInvalido(
            f"El contorno debe tener entre {COORDENADAS_MINIMAS} y {COORDENADAS_MAXIMAS} coordenadas "
            "(contando la que cierra el anillo)")
    return vertices


def formatear_area(area_m2: float) -> str:
    return f"{area_m2 / 10_000:.2f} ha" if area_m2 >= 10_000 else f"{area_m2:.0f} m²"


def distancia_al_borde_m(contorno, latitud, longitud, distancia_km) -> float:
    """Cuántos metros le faltan a un punto para tocar el contorno: 0 si cae dentro (o sobre el borde).

    `contorno` es el GeoJSON guardado o un Polygon de shapely ya armado. `distancia_km(lat1, lng1, lat2, lng2)` es la
    haversine del servicio (main.calcular_distancia_km): con ella se mide del punto al lugar más cercano del borde.
    """
    poligono = contorno if isinstance(contorno, Polygon) else Polygon(contorno["coordinates"][0])
    punto = Point(float(longitud), float(latitud))
    if poligono.covers(punto):
        return 0.0
    _, en_el_borde = nearest_points(punto, poligono.exterior)
    return round(distancia_km(latitud, longitud, en_el_borde.y, en_el_borde.x) * 1000, 1)


def validar_contorno(geo, punto_lat, punto_lng, distancia_km):
    """Devuelve (contorno normalizado, área en m²) o lanza ContornoInvalido."""
    vertices = _anillo_exterior(geo)
    poligono = Polygon(vertices)
    if not poligono.is_valid:
        raise ContornoInvalido(f"El contorno no es válido (se cruza a sí mismo o repite vértices): {explain_validity(poligono)}")
    lng_min, lat_min, lng_max, lat_max = poligono.bounds
    if not (PERU_LNG[0] <= lng_min and lng_max <= PERU_LNG[1] and PERU_LAT[0] <= lat_min and lat_max <= PERU_LAT[1]):
        raise ContornoInvalido("El contorno debe estar dentro del Perú")
    # Área geodésica (m²): positiva o negativa según el sentido en que se dibujó; importa el tamaño.
    area_m2 = abs(_geod.geometry_area_perimeter(poligono)[0])
    if not AREA_MINIMA_M2 <= area_m2 <= AREA_MAXIMA_M2:
        raise ContornoInvalido(
            f"El área del contorno debe estar entre {AREA_MINIMA_M2:.0f} m² y {AREA_MAXIMA_M2 / 10_000:.0f} ha "
            f"(el dibujado tiene {formatear_area(area_m2)})")
    if distancia_al_borde_m(poligono, punto_lat, punto_lng, distancia_km) > TOLERANCIA_PUNTO_M:
        raise ContornoInvalido(MENSAJE_PUNTO_FUERA)
    return {"type": "Polygon", "coordinates": [vertices]}, round(area_m2, 2)


# ---------------------------------------------------------------- huella del contorno

def contorno_canonico(contorno) -> str:
    """El contorno como texto canónico: claves ordenadas, sin espacios y coordenadas redondeadas a 7 decimales. Dos
    contornos iguales dan siempre el mismo texto, lo mande quien lo mande y con los decimales que traiga."""
    anillos = [
        [[round(float(posicion[0]), DECIMALES_CANONICOS), round(float(posicion[1]), DECIMALES_CANONICOS)] for posicion in anillo]
        for anillo in contorno["coordinates"]
    ]
    return json.dumps({"type": "Polygon", "coordinates": anillos}, sort_keys=True, separators=(",", ":"))


def hash_del_contorno(contorno) -> str:
    """Huella SHA-256 del contorno canónico. Es lo que se sella en la cadena de certificación en vez del polígono: quien
    tenga la versión guardada (chacra_contorno_versiones) puede recalcularla y comprobar que es la misma."""
    return hashlib.sha256(contorno_canonico(contorno).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- geocerca de una siembra o una cosecha

def evaluar_geocerca(contorno, latitud, longitud, precision_m, distancia_km) -> dict:
    """Dónde estaba el agricultor respecto de su chacra. Devuelve metodo_geocerca, resultado_geocerca,
    distancia_borde_m y precision_m (las columnas del registro, sin el prefijo de siembra o cosecha).

    Nunca rechaza: el registro se guarda igual y lo que cambia es el resultado. Se evalúa en este orden:
      sin_gps         no llegó la ubicación
      radio           la chacra no tiene contorno: vale lo de siempre (distancia a su punto, radio de 1,5 km)
      precision_baja  la lectura del GPS es menos precisa que 100 m: no alcanza para decir si está dentro
      dentro          dentro del contorno
      cerca           fuera, pero a 50 m o menos del borde
      fuera           a más de 50 m del borde
    `contorno` es el GeoJSON de la versión vigente, o None si la chacra no lo tiene.
    """
    metodo = METODO_POLIGONO if contorno else METODO_RADIO
    if latitud is None or longitud is None:
        return {"metodo_geocerca": metodo, "resultado_geocerca": "sin_gps", "distancia_borde_m": None, "precision_m": None}
    if not contorno:
        return {"metodo_geocerca": METODO_RADIO, "resultado_geocerca": "radio", "distancia_borde_m": None, "precision_m": precision_m}
    distancia = distancia_al_borde_m(contorno, latitud, longitud, distancia_km)
    if precision_m is not None and precision_m > PRECISION_MAXIMA_M:
        resultado = "precision_baja"
    elif distancia == 0:
        resultado = "dentro"
    elif distancia <= TOLERANCIA_PUNTO_M:
        resultado = "cerca"
    else:
        resultado = "fuera"
    return {"metodo_geocerca": METODO_POLIGONO, "resultado_geocerca": resultado, "distancia_borde_m": distancia, "precision_m": precision_m}


def frase_de_geocerca(momento: str, metodo: str, resultado: str, distancia_borde_m=None, precision_m=None,
                      contorno_hash: str | None = None) -> str:
    """La frase de formato fijo que queda sellada en la cadena de certificación, una por momento ("siembra" o "cosecha"):

        Geocerca siembra: metodo=poligono; resultado=dentro; distancia_borde_m=0; precision_m=12; contorno_sha256=<hash>.

    Con el método del radio no lleva distancia_borde_m ni contorno_sha256 (la distancia en km va en su frase de siempre),
    y un dato que no existe (sin GPS no hay distancia ni precisión) se omite. Nunca lleva coordenadas. La página del QR
    la lee con este mismo formato (leerFraseDeGeocerca en el frontend): si cambia acá, cambia allá.
    """
    partes = [f"metodo={metodo}", f"resultado={resultado}"]
    if metodo == METODO_POLIGONO and distancia_borde_m is not None:
        partes.append(f"distancia_borde_m={round(distancia_borde_m)}")
    if precision_m is not None:
        partes.append(f"precision_m={round(precision_m)}")
    if metodo == METODO_POLIGONO and contorno_hash:
        partes.append(f"contorno_sha256={contorno_hash}")
    return f"Geocerca {momento}: " + "; ".join(partes) + "."
