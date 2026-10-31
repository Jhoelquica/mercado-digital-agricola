import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useEffect, useRef } from 'react';

// Teselas del servidor comunitario de OpenStreetMap. Su política exige identificar la aplicación con la cabecera Referer;
// a quien no la cumple le devuelve una tesela con el texto "403 Access blocked". Otros proveedores gratuitos no sirven:
// CARTO ya exige llave de API y pinta "API KEY REQUIRED" sobre el mapa.
const MAPA_TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAPA_ATRIBUCION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';

interface Props {
  latitud: number | string;
  longitud: number | string;
  popup: string;
  etiqueta: string;
}

export function MapaSoloLectura({ latitud, longitud, popup, etiqueta }: Props) {
  const contenedor = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!contenedor.current) return undefined;
    const punto: L.LatLngExpression = [Number(latitud), Number(longitud)];
    const mapa = L.map(contenedor.current, { scrollWheelZoom: false }).setView(punto, 14);
    // referrerPolicy 'origin': cada tesela lleva "Referer: <origen de la página>" aunque la política de la página sea más estricta.
    L.tileLayer(MAPA_TILE_URL, { attribution: MAPA_ATRIBUCION, maxZoom: 19, referrerPolicy: 'origin' }).addTo(mapa);
    const icono = L.divIcon({
      className: 'marcador-mapa-wrap',
      html: '<span class="marcador-mapa" style="background:#2d6a4f"><i class="ti ti-map-pin"></i></span>',
      iconSize: [30, 30],
      iconAnchor: [15, 15],
      popupAnchor: [0, -18],
    });
    const texto = document.createElement('span');
    texto.textContent = popup;
    L.marker(punto, { icon: icono }).addTo(mapa).bindPopup(texto);
    const temporizador = setTimeout(() => mapa.invalidateSize(), 80);
    return () => {
      clearTimeout(temporizador);
      mapa.remove();
    };
  }, [latitud, longitud, popup]);

  return <div ref={contenedor} className="mapa-mini" role="img" aria-label={etiqueta} />;
}
