import area from '@turf/area';
import type { Polygon } from 'geojson';
import L from 'leaflet';
import '@geoman-io/leaflet-geoman-free';
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css';
import { useEffect, useRef, useState } from 'react';
import { formatearHectareas } from '../lib/formato';
import { agregarCapasSateliteYCalles, COLOR_GUIA_DIBUJO, ESTILO_CONTORNO, iconoMarcador } from './Mapa';

interface Props {
  id: string;
  // El punto registrado de la chacra: se muestra como alfiler de referencia para dibujar el contorno alrededor de él.
  punto: { lat: number; lng: number };
  inicial?: Polygon | null;
  // Al crear, editar o quitar la figura. El área (de @turf/area) es solo para mostrarla: la que vale la calcula el servidor.
  onChange: (contorno: Polygon | null, areaM2: number) => void;
}

const ZOOM_SIN_FIGURA = 17;
const MARGEN_ENCUADRE: L.FitBoundsOptions = { padding: [28, 28] };

// Editor del contorno de UNA chacra sobre la imagen satelital, con Geoman (dibujar polígono, editar vértices, quitar).
// Sigue el patrón de Mapa.tsx: Leaflet puro dentro de un useEffect que lo crea y lo destruye. Una sola figura: dibujar
// otra reemplaza a la anterior. Se carga con lazy() desde la pantalla, para no descargar Geoman en el resto de la app.
export function EditorContorno({ id, punto, inicial, onChange }: Props) {
  const contenedor = useRef<HTMLDivElement>(null);
  // onChange vive en un ref: cambiarlo no reinicializa el mapa ni Geoman.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const [areaM2, setAreaM2] = useState(() => (inicial ? area(inicial) : 0));
  const [hayFigura, setHayFigura] = useState(Boolean(inicial));

  useEffect(() => {
    if (!contenedor.current) return undefined;
    const mapa = L.map(contenedor.current);
    agregarCapasSateliteYCalles(mapa);
    // El alfiler de referencia no se edita ni se borra con las herramientas (pmIgnore).
    L.marker([punto.lat, punto.lng], { icon: iconoMarcador('map-pin', '#2D3B2A'), interactive: false, keyboard: false, pmIgnore: true }).addTo(mapa);

    let figura: L.Polygon | null = null;

    const avisar = () => {
      const contorno = figura ? (figura.toGeoJSON().geometry as Polygon) : null;
      const m2 = contorno ? area(contorno) : 0;
      setAreaM2(m2);
      setHayFigura(Boolean(contorno));
      // Con una figura ya dibujada, se corrige o se quita: el botón de dibujar queda apagado.
      mapa.pm.Toolbar.setButtonDisabled('drawPolygon', Boolean(contorno));
      onChangeRef.current(contorno, m2);
    };
    const adoptar = (capa: L.Polygon) => {
      if (figura && figura !== capa) figura.remove();
      figura = capa;
      capa.setStyle(ESTILO_CONTORNO);
      capa.on('pm:edit', avisar);
    };

    mapa.pm.setLang('es');
    mapa.pm.setGlobalOptions({
      allowSelfIntersection: false,
      snappable: true,
      snapDistance: 20,
      templineStyle: { color: COLOR_GUIA_DIBUJO, weight: 2 },
      hintlineStyle: { color: COLOR_GUIA_DIBUJO, weight: 2, dashArray: '6 6' },
      pathOptions: ESTILO_CONTORNO,
    });
    mapa.pm.addControls({
      position: 'topleft',
      drawPolygon: true,
      editMode: true,
      removalMode: true,
      drawMarker: false,
      drawCircleMarker: false,
      drawPolyline: false,
      drawRectangle: false,
      drawCircle: false,
      drawText: false,
      cutPolygon: false,
      dragMode: false,
      rotateMode: false,
    });

    if (inicial) {
      // Leaflet no necesita el vértice que cierra el anillo (lo quita solo); toGeoJSON() lo vuelve a poner al guardar.
      const anillo = (inicial.coordinates[0] ?? []).map(([lng, lat]) => [lat, lng] as L.LatLngTuple);
      adoptar(L.polygon(anillo, ESTILO_CONTORNO).addTo(mapa));
      mapa.fitBounds(figura!.getBounds(), MARGEN_ENCUADRE);
      mapa.pm.Toolbar.setButtonDisabled('drawPolygon', true);
    } else {
      mapa.setView([punto.lat, punto.lng], ZOOM_SIN_FIGURA);
    }

    mapa.on('pm:create', (e) => {
      if (e.shape !== 'Polygon') return;
      adoptar(e.layer as L.Polygon);
      avisar();
    });
    mapa.on('pm:remove', (e) => {
      if (e.layer !== figura) return;
      figura = null;
      avisar();
    });

    // El diálogo que lo contiene termina de abrirse después del montaje: se recalcula el tamaño y se vuelve a encuadrar.
    const temporizador = setTimeout(() => {
      mapa.invalidateSize();
      if (figura) mapa.fitBounds(figura.getBounds(), MARGEN_ENCUADRE);
    }, 150);
    return () => {
      clearTimeout(temporizador);
      mapa.pm.disableDraw();
      mapa.pm.disableGlobalEditMode();
      mapa.pm.disableGlobalRemovalMode();
      mapa.pm.removeControls();
      mapa.off();
      mapa.remove();
    };
    // El punto y la figura inicial no cambian durante la vida del editor: la pantalla lo monta de nuevo para otra chacra.
  }, []);

  return (
    <div className="contorno-editor">
      <div ref={contenedor} id={id} className="contorno-editor-mapa" role="application" aria-label="Mapa satelital para dibujar el contorno de la chacra" />
      <div className="contorno-editor-area" aria-live="polite">
        {hayFigura
          ? <><strong>{formatearHectareas(areaM2)} ha</strong><span>{Math.round(areaM2).toLocaleString('es-PE')} m² (área aproximada; la exacta la calcula el servidor al guardar)</span></>
          : <span className="muted">Todavía no hay contorno: toca el polígono de la barra del mapa y marca las esquinas de tu chacra.</span>}
      </div>
    </div>
  );
}
