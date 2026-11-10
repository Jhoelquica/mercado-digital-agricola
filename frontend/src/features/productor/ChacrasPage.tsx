import { useQueryClient } from '@tanstack/react-query';
import type { Polygon } from 'geojson';
import { lazy, Suspense, useRef, useState, type FormEvent } from 'react';
import { chacras as apiChacras, type Chacra } from '../../api/chacras';
import type { MapaSeleccionableApi } from '../../components/Mapa';
import { manejarError, toast } from '../../components/Toast';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearHectareas } from '../../lib/formato';
import { hayCoordenadas } from '../../lib/productos';
import { BotonMiUbicacion, TEXTO_UN_SOLO_PIN } from './AltaPerfil';
import { usePerfilProductor, useChacras, useRegistros } from './comun';
import { DialogoContorno } from './ContornoChacra';

const MapaSeleccionable = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaSeleccionable })));
const MapaSoloLectura = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaSoloLectura })));

// Con contorno, el mapa chico lo muestra sobre el satélite, con su área al pasar el cursor.
function MapaMini({ lat, lng, nombre, id, contorno, areaM2 }: { lat: number | string; lng: number | string; nombre: string; id: string; contorno?: Polygon | null; areaM2?: number | null }) {
  return (
    <div id={id} className="chacra-card-mapa-mini">
      <Suspense fallback={null}><MapaSoloLectura latitud={lat} longitud={lng} popup={nombre} etiqueta={`Mapa de ${nombre}`} contorno={contorno} areaM2={areaM2} /></Suspense>
    </div>
  );
}

// Primer toque: pregunta en la misma tarjeta (Sí / No); segundo: elimina. El servidor no deja eliminar una chacra con
// siembras y dice por qué.
function TarjetaChacra({ c, indice, cultivos, siembras, alEditar, alEliminar, alDibujar }: { c: Chacra; indice: number; cultivos: string[]; siembras: number; alEditar: () => void; alEliminar: () => Promise<boolean>; alDibujar: () => void }) {
  const [confirmando, setConfirmando] = useState(false);
  const [borrando, setBorrando] = useState(false);
  const no = useRef<HTMLButtonElement>(null);
  return (
    <div className="chacra-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }}>
      <div className="chacra-card-banner">
        <span className="chacra-codigo-badge">{c.codigo}</span>
        <span className="chacra-card-banner-chips">
          {c.area_m2 != null && <span className="chacra-area-chip" title="Área del contorno dibujado"><i className="ti ti-ruler-measure" aria-hidden="true" /> {formatearHectareas(c.area_m2)} ha</span>}
          <span className="chacra-siembras">{siembras} {siembras === 1 ? 'siembra' : 'siembras'}</span>
        </span>
      </div>
      <div className="chacra-card-body">
        <h4>{c.nombre || c.codigo}</h4>
        <div className="chacra-card-cultivos">
          {cultivos.length ? cultivos.map((n) => <span key={n} className="chacra-cultivo-chip">{n}</span>) : <span className="muted">Sin siembras todavía</span>}
        </div>
        {hayCoordenadas(c.ubicacion_latitud, c.ubicacion_longitud) && <MapaMini id={`mapa-chacra-mini-${c.id}`} lat={c.ubicacion_latitud} lng={c.ubicacion_longitud} nombre={c.nombre || c.codigo} contorno={c.contorno} areaM2={c.area_m2} />}
        <div className="chacra-card-ubicacion"><i className="ti ti-map-pin" /> {Number(c.ubicacion_latitud).toFixed(5)}, {Number(c.ubicacion_longitud).toFixed(5)}</div>
        <div className={`chacra-card-acciones ${confirmando ? 'confirmando' : ''}`}>
          <button type="button" className="btn btn-outline btn-sm btn-contorno-chacra" onClick={alDibujar}><i className="ti ti-polygon" aria-hidden="true" /> {c.contorno ? 'Editar contorno' : 'Dibujar contorno'}</button>
          <button type="button" className="btn btn-outline btn-sm btn-editar-chacra" onClick={alEditar}><i className="ti ti-pencil" aria-hidden="true" /> Editar</button>
          <button type="button" className="btn btn-ghost btn-sm btn-borrar-chacra" onClick={() => { setConfirmando(true); requestAnimationFrame(() => no.current?.focus()); }}><i className="ti ti-trash" aria-hidden="true" /> Eliminar</button>
          <span className="chacra-confirmar" role="group" aria-label="Confirmar eliminación">
            <span>¿Eliminar esta chacra?</span>
            <button type="button" className="btn btn-danger btn-sm btn-si-borrar-chacra" disabled={borrando}
              onClick={() => { setBorrando(true); void alEliminar().then((ok) => { if (!ok) { setBorrando(false); setConfirmando(false); } }); }}>Sí, eliminar</button>
            <button ref={no} type="button" className="btn btn-ghost btn-sm btn-no-borrar-chacra" onClick={() => setConfirmando(false)}>No</button>
          </span>
        </div>
      </div>
    </div>
  );
}

// H2: quien marcó su parcela en el perfil y todavía no tiene chacras puede guardarla con un toque, sin volver a marcarla.
function ParcelaDelPerfil({ lat, lng, alGuardar }: { lat: number; lng: number; alGuardar: () => Promise<void> }) {
  const [guardando, setGuardando] = useState(false);
  return (
    <section className="chacra-del-perfil" aria-labelledby="chacra-del-perfil-titulo">
      <div className="chacra-del-perfil-texto">
        <span className="chacra-del-perfil-icono" aria-hidden="true"><i className="ti ti-map-pin-check" /></span>
        <div>
          <h4 id="chacra-del-perfil-titulo">Tu parcela ya está marcada en tu perfil</h4>
          <p>Guárdala como tu primera chacra y ya puedes registrar tus siembras. No tienes que volver a marcarla.</p>
        </div>
      </div>
      <MapaMini id="mapa-chacra-del-perfil" lat={lat} lng={lng} nombre="Tu parcela" />
      <button type="button" className="btn btn-primary btn-chacra-del-perfil" disabled={guardando} onClick={() => { setGuardando(true); void alGuardar().finally(() => setGuardando(false)); }}>
        <i className="ti ti-device-floppy" aria-hidden="true" /> Guardarla como mi chacra
      </button>
      <small className="muted">Si tu chacra está en otro lugar, regístrala con el formulario.</small>
    </section>
  );
}

export default function ChacrasPage() {
  const queryClient = useQueryClient();
  const chacras = useChacras();
  const registros = useRegistros();
  const perfil = usePerfilProductor();
  const [codigo, setCodigo] = useState('');
  const [nombre, setNombre] = useState('');
  const [ubicacion, setUbicacion] = useState<{ lat: number; lng: number } | null>(null);
  const [enEdicion, setEnEdicion] = useState<Chacra | null>(null);
  const [guardando, setGuardando] = useState(false);
  // El mapa del formulario se vuelve a crear (sin alfiler) al terminar de registrar o editar.
  const [versionMapa, setVersionMapa] = useState(0);
  // La chacra cuyo contorno se está dibujando en el diálogo satelital.
  const [contornoDe, setContornoDe] = useState<Chacra | null>(null);
  const mapa = useRef<MapaSeleccionableApi>(null);
  const formulario = useRef<HTMLFormElement>(null);
  const campoNombre = useRef<HTMLInputElement>(null);
  const lista = chacras.data ?? [];
  const deRegistros = registros.data ?? [];
  const recargar = () => queryClient.invalidateQueries({ queryKey: ['productor', 'chacras'] });

  function limpiar() {
    setEnEdicion(null);
    setCodigo('');
    setNombre('');
    setUbicacion(null);
    setVersionMapa((v) => v + 1);
  }

  function editar(c: Chacra) {
    setEnEdicion(c);
    setCodigo(c.codigo);
    setNombre(c.nombre || '');
    if (hayCoordenadas(c.ubicacion_latitud, c.ubicacion_longitud)) {
      const punto = { lat: Number(c.ubicacion_latitud), lng: Number(c.ubicacion_longitud) };
      setUbicacion(punto);
      mapa.current?.moverMarcador(punto.lat, punto.lng);
    }
    formulario.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    campoNombre.current?.focus({ preventScroll: true });
  }

  async function guardar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!ubicacion) {
      toast('Marca la ubicación de la chacra en el mapa', 'error');
      return;
    }
    setGuardando(true);
    const datos = { codigo: codigo.trim(), nombre: nombre.trim() || null, ubicacion_latitud: ubicacion.lat, ubicacion_longitud: ubicacion.lng };
    try {
      if (enEdicion) {
        await apiChacras.actualizar(enEdicion.id, datos);
        toast('Chacra actualizada.');
      } else {
        await apiChacras.crear(datos);
        toast('Chacra registrada 🌱');
      }
      limpiar();
      await recargar();
    } catch (err) {
      manejarError(err, enEdicion ? 'guardar los cambios de la chacra' : 'registrar la chacra');
    } finally {
      setGuardando(false);
    }
  }

  async function eliminar(c: Chacra): Promise<boolean> {
    try {
      await apiChacras.eliminar(c.id);
      toast('Chacra eliminada.');
      if (enEdicion?.id === c.id) limpiar();
      await recargar();
      return true;
    } catch (err) {
      manejarError(err, 'eliminar la chacra');
      return false;
    }
  }

  async function guardarParcelaDelPerfil(lat: number, lng: number) {
    try {
      await apiChacras.crear({ codigo: 'CH-01', nombre: 'Mi chacra', ubicacion_latitud: lat, ubicacion_longitud: lng });
      toast('Listo: tu parcela ya es tu chacra CH-01 🌱');
      await recargar();
    } catch (err) {
      manejarError(err, 'guardar tu chacra');
    }
  }

  const p = perfil.data;
  const parcelaDelPerfil = !chacras.isLoading && !lista.length && p && hayCoordenadas(p.latitud, p.longitud) ? { lat: Number(p.latitud), lng: Number(p.longitud) } : null;

  return (
    <div className="productor-tab-panel active" data-tab-panel="chacras">
      <div className="section-header inner"><h3><i className="ti ti-map-pin" /> <Texto clave="seccion.chacras">Mis chacras</Texto></h3></div>
      <div className="chacras-layout">
        <div className="card-panel">
          <h4 id="form-chacra-titulo">{enEdicion ? `Editar chacra ${enEdicion.codigo}` : 'Registrar nueva chacra'}</h4>
          <p className="muted">Cada siembra que registres en "Mis siembras" se asocia a una de tus chacras. Después puedes dibujar su contorno sobre la imagen satelital desde su tarjeta.</p>
          <form ref={formulario} id="form-chacra" className="form-grid" onSubmit={(e) => { void guardar(e); }}>
            <label>Código
              <input type="text" id="chacra-codigo" required maxLength={30} placeholder="Ej. CH-01" value={codigo} onChange={(e) => setCodigo(e.target.value)} />
            </label>
            <label>Nombre (opcional)
              <input ref={campoNombre} type="text" id="chacra-nombre" maxLength={80} placeholder="Ej. Parcela norte" value={nombre} onChange={(e) => setNombre(e.target.value)} />
            </label>
            <div className="mapa-selector-bloque">
              <div className="mapa-selector-label"><span><i className="ti ti-map-pin" /> Ubicación de la chacra</span></div>
              <BotonMiUbicacion id="btn-ubicacion-chacra" alObtener={(lat, lng) => { mapa.current?.moverMarcador(lat, lng); setUbicacion({ lat, lng }); }} />
              <Suspense fallback={<div className="mapa-seleccionable" />}>
                <MapaSeleccionable key={versionMapa} ref={mapa} id="mapa-ubicacion-chacra" clase="mapa-seleccionable" color="#2d6a4f" alSeleccionar={(lat, lng) => setUbicacion({ lat, lng })} />
              </Suspense>
              {ubicacion
                ? <p className="mapa-ayuda confirmado" id="mapa-chacra-ayuda"><i className="ti ti-map-pin" /> Ubicación marcada ({ubicacion.lat.toFixed(5)}, {ubicacion.lng.toFixed(5)}). {TEXTO_UN_SOLO_PIN}</p>
                : <p className="mapa-ayuda" id="mapa-chacra-ayuda">Si estás en la chacra, toca «Usar mi ubicación». Si no, toca en el mapa dónde está.</p>}
            </div>
            <div className="form-chacra-acciones">
              <button type="submit" className="btn btn-primary" id="btn-guardar-chacra" disabled={guardando}>{enEdicion ? 'Guardar cambios' : 'Registrar chacra'}</button>
              {enEdicion && <button type="button" className="btn btn-ghost" id="btn-cancelar-edicion-chacra" onClick={limpiar}>Cancelar</button>}
            </div>
          </form>
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div id="chacras-lista" className="grid-chacras">
            {chacras.isLoading && [0, 1].map((i) => <div className="skeleton-row-card" key={i}><div className="skeleton skeleton-line w-40" /><div className="skeleton skeleton-line tall w-70" /><div className="skeleton skeleton-line w-60" /></div>)}
            {lista.map((c, i) => {
              const propias = deRegistros.filter((r) => r.chacra_id === c.id);
              return <TarjetaChacra key={c.id} c={c} indice={i} siembras={propias.length} cultivos={[...new Set(propias.map((r) => r.cultivo))]} alEditar={() => editar(c)} alEliminar={() => eliminar(c)} alDibujar={() => setContornoDe(c)} />;
            })}
            {parcelaDelPerfil && <ParcelaDelPerfil lat={parcelaDelPerfil.lat} lng={parcelaDelPerfil.lng} alGuardar={() => guardarParcelaDelPerfil(parcelaDelPerfil.lat, parcelaDelPerfil.lng)} />}
          </div>
          {!chacras.isLoading && !lista.length && !parcelaDelPerfil && (
            <div id="chacras-empty" className="empty-state">
              <span className="empty-state-icon"><i className="ti ti-map-pin" /></span>
              <p><strong>Aún no registraste ninguna chacra</strong></p>
              <p className="muted">Registra tu primera chacra para poder asociarle siembras y cosechas desde "Mis siembras".</p>
            </div>
          )}
        </div>
      </div>
      {contornoDe && <DialogoContorno key={contornoDe.id} chacra={contornoDe} alCerrar={() => setContornoDe(null)} />}
    </div>
  );
}
