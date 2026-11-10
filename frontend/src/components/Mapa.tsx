import type { Polygon } from 'geojson';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useEffect, useImperativeHandle, useRef, type Ref } from 'react';
import { formatearHectareas } from '../lib/formato';
import { hayCoordenadas } from '../lib/productos';

// Teselas del servidor comunitario de OpenStreetMap. Su política exige identificar la aplicación con la cabecera Referer;
// a quien no la cumple le devuelve una tesela con el texto "403 Access blocked". Otros proveedores gratuitos no sirven:
// CARTO ya exige llave de API y pinta "API KEY REQUIRED" sobre el mapa.
const MAPA_TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAPA_ATRIBUCION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
const MAPA_CENTRO_PERU: L.LatLngTuple = [-9.19, -75.015];

// Imagen satelital (Esri World Imagery): sobre ella el agricultor reconoce su chacra y dibuja su contorno. La CSP del
// frontend (security-headers.conf) permite estas teselas en img-src.
const SATELITE_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const SATELITE_ATRIBUCION = 'Tiles &copy; Esri';

function capaCalles(): L.TileLayer {
  // referrerPolicy 'origin': cada tesela lleva "Referer: <origen de la página>" aunque la política de la página sea más estricta.
  return L.tileLayer(MAPA_TILE_URL, { attribution: MAPA_ATRIBUCION, maxZoom: 19, referrerPolicy: 'origin' });
}

function agregarCapaBase(mapa: L.Map): void {
  capaCalles().addTo(mapa);
}

// Satélite por defecto y Calles como alternativa, con el selector de capas de Leaflet (arriba a la derecha).
export function agregarCapasSateliteYCalles(mapa: L.Map): L.Control.Layers {
  const satelite = L.tileLayer(SATELITE_TILE_URL, { attribution: SATELITE_ATRIBUCION, maxZoom: 19 }).addTo(mapa);
  return L.control.layers({ 'Satélite': satelite, 'Calles': capaCalles() }, undefined, { position: 'topright' }).addTo(mapa);
}

// El contorno de la chacra con la paleta oficial (tokens.css): borde --color-primario y relleno --color-borde al 35 %.
export const ESTILO_CONTORNO: L.PathOptions = { color: '#2D3B2A', weight: 2.5, fillColor: '#82907A', fillOpacity: 0.35 };
// Línea guía mientras se dibuja (ámbar, para distinguirla del contorno ya cerrado).
export const COLOR_GUIA_DIBUJO = '#f59e0b';

// El polígono guardado, de solo lectura, con la etiqueta "nombre · X.XX ha" que sigue al puntero. La etiqueta se arma
// como elemento, no como HTML: el nombre lo escribe el productor.
function capaContorno(contorno: Polygon, nombre: string, areaM2: number | null | undefined): L.GeoJSON {
  const etiqueta = document.createElement('span');
  etiqueta.textContent = areaM2 != null ? `${nombre} · ${formatearHectareas(areaM2)} ha` : nombre;
  return L.geoJSON(contorno, { style: () => ESTILO_CONTORNO }).bindTooltip(etiqueta, { sticky: true, direction: 'top', className: 'contorno-tooltip' });
}

// El marcador se arma como elemento (no como HTML con style="..."): el color va por CSSOM, que la CSP permite sin
// 'unsafe-inline' en los estilos.
export function iconoMarcador(icono: string, color: string): L.DivIcon {
  const marca = document.createElement('span');
  marca.className = 'marcador-mapa';
  marca.style.background = color;
  const simbolo = document.createElement('i');
  simbolo.className = `ti ti-${icono}`;
  marca.append(simbolo);
  return L.divIcon({
    className: 'marcador-mapa-wrap',
    html: marca,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -18],
  });
}

interface PropsSoloLectura {
  latitud: number | string;
  longitud: number | string;
  popup: string;
  etiqueta: string;
  icono?: string;
  color?: string;
  clase?: string;
  // Con el contorno de la chacra, el mapa lo encuadra sobre la imagen satelital (con "Calles" como alternativa).
  contorno?: Polygon | null;
  areaM2?: number | null;
}

export function MapaSoloLectura({ latitud, longitud, popup, etiqueta, icono = 'map-pin', color = '#2d6a4f', clase = 'mapa-mini', contorno = null, areaM2 = null }: PropsSoloLectura) {
  const contenedor = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!contenedor.current) return undefined;
    const punto: L.LatLngExpression = [Number(latitud), Number(longitud)];
    const mapa = L.map(contenedor.current, { scrollWheelZoom: false });
    if (contorno) {
      agregarCapasSateliteYCalles(mapa);
      mapa.fitBounds(capaContorno(contorno, popup, areaM2).addTo(mapa).getBounds(), { padding: [18, 18] });
    } else {
      mapa.setView(punto, 14);
      agregarCapaBase(mapa);
    }
    const texto = document.createElement('span');
    texto.textContent = popup;
    L.marker(punto, { icon: iconoMarcador(icono, color) }).addTo(mapa).bindPopup(texto);
    const temporizador = setTimeout(() => mapa.invalidateSize(), 80);
    return () => {
      clearTimeout(temporizador);
      mapa.remove();
    };
  }, [latitud, longitud, popup, icono, color, contorno, areaM2]);

  return <div ref={contenedor} className={clase} role="img" aria-label={etiqueta} />;
}

export interface MapaSeleccionableApi {
  moverMarcador: (lat: number, lng: number) => void;
  verPunto: (lat: number, lng: number, zoom: number) => void;
  cambiarZoom: (zoom: number) => void;
  // Un círculo (p. ej. el radio de entrega de una ciudad) centrado en el punto; con `encuadrar`, el mapa lo muestra entero.
  dibujarCirculo: (lat: number, lng: number, radioMetros: number, encuadrar?: boolean) => void;
}

interface PropsSeleccionable {
  id: string;
  clase?: string;
  color?: string;
  icono?: string;
  alSeleccionar: (lat: number, lng: number) => void;
  // Punto con que arranca (zoom 14 y su marcador); sin él, todo el Perú.
  inicial?: { lat: number; lng: number } | null;
  // Con el punto inicial: el radio (en metros) del círculo con que arranca, encuadrado entero.
  radioInicialMetros?: number | null;
  zoomInicial?: number;
  ref?: Ref<MapaSeleccionableApi>;
}

// Se toca el mapa para marcar un punto. Arranca mostrando todo el Perú y sin marcador.
export function MapaSeleccionable({ id, clase, color = '#e76f51', icono = 'map-pin', alSeleccionar, inicial, radioInicialMetros, zoomInicial = 14, ref }: PropsSeleccionable) {
  const contenedor = useRef<HTMLDivElement>(null);
  const mapaRef = useRef<L.Map | null>(null);
  const marcador = useRef<L.Marker | null>(null);
  const circulo = useRef<L.Circle | null>(null);
  const alSeleccionarRef = useRef(alSeleccionar);
  alSeleccionarRef.current = alSeleccionar;

  function ponerMarcador(lat: number, lng: number) {
    if (!mapaRef.current) return;
    if (marcador.current) marcador.current.setLatLng([lat, lng]);
    else marcador.current = L.marker([lat, lng], { icon: iconoMarcador(icono, color) }).addTo(mapaRef.current);
  }

  useImperativeHandle(ref, () => ({
    moverMarcador(lat, lng) {
      mapaRef.current?.setView([lat, lng], 15);
      ponerMarcador(lat, lng);
    },
    verPunto: (lat, lng, zoom) => { mapaRef.current?.setView([lat, lng], zoom); },
    cambiarZoom: (zoom) => { mapaRef.current?.setZoom(zoom); },
    dibujarCirculo(lat, lng, radioMetros, encuadrar = false) {
      if (!mapaRef.current) return;
      if (circulo.current) circulo.current.setLatLng([lat, lng]).setRadius(radioMetros);
      else circulo.current = L.circle([lat, lng], { radius: radioMetros, color, weight: 1.5, fillOpacity: 0.08 }).addTo(mapaRef.current);
      if (encuadrar) mapaRef.current.fitBounds(circulo.current.getBounds(), { padding: [12, 12] });
    },
  }));

  useEffect(() => {
    if (!contenedor.current) return undefined;
    const punto = inicial && hayCoordenadas(inicial.lat, inicial.lng) ? [Number(inicial.lat), Number(inicial.lng)] as L.LatLngTuple : null;
    const mapa = L.map(contenedor.current).setView(punto ?? MAPA_CENTRO_PERU, punto ? zoomInicial : 5);
    mapaRef.current = mapa;
    agregarCapaBase(mapa);
    if (punto) ponerMarcador(punto[0], punto[1]);
    if (punto && radioInicialMetros) {
      circulo.current = L.circle(punto, { radius: radioInicialMetros, color, weight: 1.5, fillOpacity: 0.08 }).addTo(mapa);
      mapa.fitBounds(circulo.current.getBounds(), { padding: [12, 12] });
    }
    mapa.on('click', (e: L.LeafletMouseEvent) => {
      ponerMarcador(e.latlng.lat, e.latlng.lng);
      alSeleccionarRef.current(e.latlng.lat, e.latlng.lng);
    });
    const temporizador = setTimeout(() => mapa.invalidateSize(), 150);
    return () => {
      clearTimeout(temporizador);
      mapa.remove();
      mapaRef.current = null;
      marcador.current = null;
      circulo.current = null;
    };
    // El color y el punto inicial no cambian durante la vida del mapa.
  }, []);

  return <div ref={contenedor} id={id} className={clase} />;
}

export interface PuntoDeRuta {
  latitud: number | string | null;
  longitud: number | string | null;
}

interface PropsRuta {
  id: string;
  origen: PuntoDeRuta;
  destino: PuntoDeRuta;
}

// Repartidor y destino unidos por una línea. Si cambia el origen se mueve el marcador, sin volver a crear el mapa.
export function MapaRuta({ id, origen, destino }: PropsRuta) {
  const contenedor = useRef<HTMLDivElement>(null);
  const capas = useRef<{ marcadorOrigen: L.Marker; linea: L.Polyline } | null>(null);
  const hayPuntos = hayCoordenadas(origen.latitud, origen.longitud) && hayCoordenadas(destino.latitud, destino.longitud);

  useEffect(() => {
    if (!contenedor.current || !hayPuntos) return undefined;
    const puntoOrigen: L.LatLngTuple = [Number(origen.latitud), Number(origen.longitud)];
    const puntoDestino: L.LatLngTuple = [Number(destino.latitud), Number(destino.longitud)];
    const mapa = L.map(contenedor.current, { scrollWheelZoom: false });
    mapa.fitBounds([puntoOrigen, puntoDestino], { padding: [28, 28], maxZoom: 15 });
    agregarCapaBase(mapa);
    const marcadorOrigen = L.marker(puntoOrigen, { icon: iconoMarcador('moped', '#2d6a4f') }).addTo(mapa).bindPopup('Repartidor');
    L.marker(puntoDestino, { icon: iconoMarcador('map-pin', '#e76f51') }).addTo(mapa).bindPopup('Destino');
    const linea = L.polyline([puntoOrigen, puntoDestino], { color: '#40916c', weight: 3, dashArray: '6 8' }).addTo(mapa);
    capas.current = { marcadorOrigen, linea };
    const temporizador = setTimeout(() => mapa.invalidateSize(), 80);
    return () => {
      clearTimeout(temporizador);
      mapa.remove();
      capas.current = null;
    };
    // Solo al aparecer los puntos: los cambios de después los aplica el efecto de abajo.
  }, [hayPuntos]);

  useEffect(() => {
    if (!capas.current || !hayPuntos) return;
    const puntoOrigen: L.LatLngTuple = [Number(origen.latitud), Number(origen.longitud)];
    capas.current.marcadorOrigen.setLatLng(puntoOrigen);
    capas.current.linea.setLatLngs([puntoOrigen, [Number(destino.latitud), Number(destino.longitud)]]);
  }, [origen.latitud, origen.longitud, destino.latitud, destino.longitud, hayPuntos]);

  return <div ref={contenedor} id={id} className="mapa-mini" />;
}
