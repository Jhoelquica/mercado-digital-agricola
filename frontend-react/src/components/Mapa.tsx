import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useEffect, useImperativeHandle, useRef, type Ref } from 'react';
import { hayCoordenadas } from '../lib/productos';

// Teselas del servidor comunitario de OpenStreetMap. Su política exige identificar la aplicación con la cabecera Referer;
// a quien no la cumple le devuelve una tesela con el texto "403 Access blocked". Otros proveedores gratuitos no sirven:
// CARTO ya exige llave de API y pinta "API KEY REQUIRED" sobre el mapa.
const MAPA_TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAPA_ATRIBUCION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
const MAPA_CENTRO_PERU: L.LatLngTuple = [-9.19, -75.015];

function agregarCapaBase(mapa: L.Map): void {
  // referrerPolicy 'origin': cada tesela lleva "Referer: <origen de la página>" aunque la política de la página sea más estricta.
  L.tileLayer(MAPA_TILE_URL, { attribution: MAPA_ATRIBUCION, maxZoom: 19, referrerPolicy: 'origin' }).addTo(mapa);
}

function iconoMarcador(icono: string, color: string): L.DivIcon {
  return L.divIcon({
    className: 'marcador-mapa-wrap',
    html: `<span class="marcador-mapa" style="background:${color}"><i class="ti ti-${icono}"></i></span>`,
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
}

export function MapaSoloLectura({ latitud, longitud, popup, etiqueta, icono = 'map-pin', color = '#2d6a4f', clase = 'mapa-mini' }: PropsSoloLectura) {
  const contenedor = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!contenedor.current) return undefined;
    const punto: L.LatLngExpression = [Number(latitud), Number(longitud)];
    const mapa = L.map(contenedor.current, { scrollWheelZoom: false }).setView(punto, 14);
    agregarCapaBase(mapa);
    const texto = document.createElement('span');
    texto.textContent = popup;
    L.marker(punto, { icon: iconoMarcador(icono, color) }).addTo(mapa).bindPopup(texto);
    const temporizador = setTimeout(() => mapa.invalidateSize(), 80);
    return () => {
      clearTimeout(temporizador);
      mapa.remove();
    };
  }, [latitud, longitud, popup, icono, color]);

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
