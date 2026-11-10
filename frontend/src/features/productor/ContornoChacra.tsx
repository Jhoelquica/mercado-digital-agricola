import { useQueryClient } from '@tanstack/react-query';
import type { Polygon } from 'geojson';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { chacras as apiChacras, type Chacra } from '../../api/chacras';
import { manejarError, toast } from '../../components/Toast';
import { formatearHectareas } from '../../lib/formato';

// Geoman y Leaflet solo se descargan al abrir el diálogo.
const EditorContorno = lazy(() => import('../../components/EditorContorno').then((m) => ({ default: m.EditorContorno })));

interface Props {
  chacra: Chacra;
  alCerrar: () => void;
}

// Diálogo para dibujar o corregir el contorno de UNA chacra sobre la imagen satelital. <dialog> nativo: atrapa el foco,
// se cierra con Escape y oscurece el resto de la página. Guardar manda el polígono al servidor, que lo valida y calcula
// el área; quitar borra el contorno guardado (pide "Sí / No" en el mismo pie, como eliminar una chacra).
export function DialogoContorno({ chacra, alCerrar }: Props) {
  const queryClient = useQueryClient();
  const dialogo = useRef<HTMLDialogElement>(null);
  const alCerrarRef = useRef(alCerrar);
  alCerrarRef.current = alCerrar;
  const [contorno, setContorno] = useState<Polygon | null>(chacra.contorno ?? null);
  const [guardando, setGuardando] = useState(false);
  const [confirmandoQuitar, setConfirmandoQuitar] = useState(false);
  const nombre = chacra.nombre || chacra.codigo;

  useEffect(() => {
    const d = dialogo.current;
    if (!d) return undefined;
    const alCerrarse = () => alCerrarRef.current();
    d.addEventListener('close', alCerrarse);
    if (!d.open) d.showModal();
    return () => {
      d.removeEventListener('close', alCerrarse);
      if (d.open) d.close();
    };
  }, []);

  const recargar = () => queryClient.invalidateQueries({ queryKey: ['productor', 'chacras'] });

  async function guardar() {
    if (!contorno) {
      toast('Dibuja el contorno de la chacra en el mapa', 'error');
      return;
    }
    setGuardando(true);
    try {
      const guardada = await apiChacras.dibujarContorno(chacra.id, contorno);
      toast(`Contorno guardado: ${formatearHectareas(guardada.area_m2 ?? 0)} ha 🌱`);
      await recargar();
      dialogo.current?.close();
    } catch (err) {
      manejarError(err, 'guardar el contorno de la chacra');
    } finally {
      setGuardando(false);
    }
  }

  async function quitar() {
    setGuardando(true);
    try {
      await apiChacras.borrarContorno(chacra.id);
      toast('Contorno quitado. La chacra sigue con su ubicación marcada.');
      await recargar();
      dialogo.current?.close();
    } catch (err) {
      manejarError(err, 'quitar el contorno de la chacra');
    } finally {
      setGuardando(false);
      setConfirmandoQuitar(false);
    }
  }

  return (
    <dialog ref={dialogo} className="contorno-dialogo" aria-labelledby="contorno-dialogo-titulo">
      <div className="contorno-dialogo-cuerpo">
        <div className="contorno-dialogo-cabecera">
          <div>
            <h3 id="contorno-dialogo-titulo"><i className="ti ti-polygon" aria-hidden="true" /> Contorno de {nombre}</h3>
            <p>Dibuja el borde de tu chacra sobre la imagen satelital, alrededor del alfiler. Su ubicación marcada no cambia.</p>
          </div>
          <button type="button" className="btn-cerrar-contorno" aria-label="Cerrar" onClick={() => dialogo.current?.close()}><i className="ti ti-x" aria-hidden="true" /></button>
        </div>
        <ol className="contorno-pasos" aria-label="Cómo dibujar el contorno">
          <li><b>1</b> Toca el polígono en la barra del mapa</li>
          <li><b>2</b> Marca cada esquina de tu chacra</li>
          <li><b>3</b> Cierra tocando la primera esquina</li>
        </ol>
        <Suspense fallback={<div className="contorno-editor"><div className="contorno-editor-mapa skeleton" /><div className="contorno-editor-area"><span className="muted">Cargando el mapa satelital…</span></div></div>}>
          <EditorContorno id={`editor-contorno-${chacra.id}`} punto={{ lat: Number(chacra.ubicacion_latitud), lng: Number(chacra.ubicacion_longitud) }} inicial={chacra.contorno ?? null} onChange={(nuevo) => setContorno(nuevo)} />
        </Suspense>
        <div className="contorno-dialogo-pie">
          {chacra.contorno && (
            <span className="contorno-quitar">
              {confirmandoQuitar
                ? <>
                  <span className="contorno-confirmar">¿Quitar el contorno guardado?</span>
                  <button type="button" className="btn btn-danger btn-sm" id="btn-si-quitar-contorno" disabled={guardando} onClick={() => { void quitar(); }}>Sí, quitar</button>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirmandoQuitar(false)}>No</button>
                </>
                : <button type="button" className="btn btn-ghost" id="btn-quitar-contorno" disabled={guardando} onClick={() => setConfirmandoQuitar(true)}><i className="ti ti-trash" aria-hidden="true" /> Quitar contorno</button>}
            </span>
          )}
          <button type="button" className="btn btn-outline" id="btn-cancelar-contorno" disabled={guardando} onClick={() => dialogo.current?.close()}>Cancelar</button>
          <button type="button" className="btn btn-primary" id="btn-guardar-contorno" disabled={guardando || !contorno} aria-busy={guardando} onClick={() => { void guardar(); }}>
            <i className="ti ti-device-floppy" aria-hidden="true" /> {guardando ? 'Guardando…' : 'Guardar contorno'}
          </button>
        </div>
      </div>
    </dialog>
  );
}
