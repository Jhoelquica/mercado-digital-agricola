import { useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useRef, useState, type FormEvent } from 'react';
import { productores } from '../../api/productores';
import type { MapaSeleccionableApi } from '../../components/Mapa';
import { manejarError, toast } from '../../components/Toast';
import { obtenerUbicacionActual } from '../../lib/ubicacion';

const MapaSeleccionable = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaSeleccionable })));

// H5 (validación con usuarios del 2026-10-13): el mapa tiene un solo alfiler (tocar otro punto lo mueve) y el botón
// "Usar mi ubicación" va grande, arriba del mapa.
export const TEXTO_UN_SOLO_PIN = 'Si no es ahí, toca otro punto: el alfiler se mueve.';

export function BotonMiUbicacion({ id, alObtener }: { id: string; alObtener: (lat: number, lng: number) => void }) {
  const [buscando, setBuscando] = useState(false);
  async function usar() {
    setBuscando(true);
    try {
      const { lat, lng } = await obtenerUbicacionActual();
      alObtener(lat, lng);
    } catch (err) {
      toast((err as Error).message, 'error');
    } finally {
      setBuscando(false);
    }
  }
  return (
    <button type="button" className="btn btn-outline btn-mi-ubicacion" id={id} disabled={buscando} onClick={() => { void usar(); }}>
      {buscando ? 'Obteniendo ubicación...' : <><i className="ti ti-current-location" aria-hidden="true" /> Usar mi ubicación</>}
    </button>
  );
}

// Antes de publicar productos, el agricultor cuenta quién es; la parcela que marque será su primera chacra.
export function AltaPerfil() {
  const queryClient = useQueryClient();
  const [nombre, setNombre] = useState('');
  const [comunidad, setComunidad] = useState('');
  const [contacto, setContacto] = useState('');
  const [ubicacion, setUbicacion] = useState<{ lat: number; lng: number } | null>(null);
  const [enviando, setEnviando] = useState(false);
  const mapa = useRef<MapaSeleccionableApi>(null);

  async function crear(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setEnviando(true);
    try {
      await productores.crear({
        nombre: nombre.trim(),
        comunidad: comunidad.trim() || null,
        contacto: contacto.trim() || null,
        latitud: ubicacion ? String(ubicacion.lat) : null,
        longitud: ubicacion ? String(ubicacion.lng) : null,
      });
      toast('¡Perfil de productor creado! Ya puedes publicar productos 🌱');
      await queryClient.invalidateQueries({ queryKey: ['productor'] });
    } catch (err) {
      manejarError(err, 'crear el perfil de productor');
      setEnviando(false);
    }
  }

  return (
    <div id="productor-setup" className="card-panel">
      <h3>Completa tu perfil de productor</h3>
      <p className="muted">Antes de publicar productos, cuéntanos un poco sobre tu emprendimiento.</p>
      <form id="form-productor" className="form-grid" onSubmit={(e) => { void crear(e); }}>
        <label>Nombre / Razón social
          <input type="text" id="productor-nombre" required placeholder="Ej. Asociación Agrícola Sol Naciente" value={nombre} onChange={(e) => setNombre(e.target.value)} />
        </label>
        <label>Comunidad / Región
          <input type="text" id="productor-comunidad" placeholder="Ej. Valle del Mantaro, Junín" value={comunidad} onChange={(e) => setComunidad(e.target.value)} />
        </label>
        <label>Contacto
          <input type="text" id="productor-contacto" placeholder="Ej. 987654321" value={contacto} onChange={(e) => setContacto(e.target.value)} />
        </label>
        <div className="mapa-selector-bloque">
          <div className="mapa-selector-label">
            <span><i className="ti ti-map-pin" /> Ubicación de tu chakra (opcional)</span>
          </div>
          <BotonMiUbicacion id="btn-ubicacion-productor" alObtener={(lat, lng) => { mapa.current?.moverMarcador(lat, lng); setUbicacion({ lat, lng }); }} />
          <Suspense fallback={<div className="mapa-seleccionable" />}>
            <MapaSeleccionable ref={mapa} id="mapa-ubicacion-productor" clase="mapa-seleccionable" color="#2d6a4f" alSeleccionar={(lat, lng) => setUbicacion({ lat, lng })} />
          </Suspense>
          {ubicacion
            ? <p className="mapa-ayuda confirmado" id="mapa-productor-ayuda"><i className="ti ti-map-pin" /> Ubicación marcada ({ubicacion.lat.toFixed(5)}, {ubicacion.lng.toFixed(5)}). {TEXTO_UN_SOLO_PIN}</p>
            : <p className="mapa-ayuda" id="mapa-productor-ayuda">Si estás en tu parcela, toca «Usar mi ubicación». Si no, toca en el mapa dónde está: será tu primera chacra.</p>}
        </div>
        <button type="submit" className="btn btn-primary" disabled={enviando}>Crear perfil de productor</button>
      </form>
    </div>
  );
}
