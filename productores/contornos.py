"""Contorno poligonal de la chacra (validación y área), con shapely y pyproj.

El agricultor dibuja el borde de su chacra sobre el mapa satelital (EditorContorno en el frontend) y llega como un
GeoJSON Polygon con las coordenadas en el orden [longitud, latitud]. Acá se comprueba que sea un polígono sano (sin
cruces), dentro del Perú, de un tamaño creíble (50 m² a 200 ha) y que rodee, o casi, el punto que la chacra ya tiene
registrado: ese punto sigue siendo su ubicación oficial (quedó en la cadena de certificación) y no cambia por dibujar
el contorno. El área se calcula SIEMPRE acá, sobre el elipsoide WGS84: lo que mande el cliente se ignora.
"""
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
# Hasta dónde puede quedar el punto registrado de la chacra fuera del borde dibujado (el GPS falla seguido en el campo).
TOLERANCIA_PUNTO_M = 50.0
MENSAJE_PUNTO_FUERA = "El punto registrado de la chacra queda fuera del contorno dibujado"

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


def validar_contorno(geo, punto_lat, punto_lng, distancia_km):
    """Devuelve (contorno normalizado, área en m²) o lanza ContornoInvalido.

    `distancia_km(lat1, lng1, lat2, lng2)` es la haversine del servicio (main.calcular_distancia_km): mide cuánto le
    falta al punto registrado de la chacra para tocar el borde cuando queda fuera del polígono.
    """
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
    punto = Point(float(punto_lng), float(punto_lat))
    if not poligono.covers(punto):
        _, en_el_borde = nearest_points(punto, poligono.exterior)
        if distancia_km(punto_lat, punto_lng, en_el_borde.y, en_el_borde.x) * 1000 > TOLERANCIA_PUNTO_M:
            raise ContornoInvalido(MENSAJE_PUNTO_FUERA)
    return {"type": "Polygon", "coordinates": [vertices]}, round(area_m2, 2)
