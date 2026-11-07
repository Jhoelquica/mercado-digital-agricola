import { useEffect, useState, useSyncExternalStore } from 'react';
import { certificacion, type Bloque } from '../../api/certificacion';
import { productos } from '../../api/productos';
import { LineaTiempoCertificacion, QrCertificacion } from './LineaTiempoCertificacion';

let productoAbierto: string | null = null;
const oyentes = new Set<() => void>();

function fijar(id: string | null): void {
  productoAbierto = id;
  oyentes.forEach((o) => o());
}

// En el frontend actual lo abre el botón de cada producto en "Mis Productos" del panel del productor.
export function abrirModalCertificacion(productoId: string): void {
  fijar(productoId);
}

type Contenido =
  | { tipo: 'cargando' }
  | { tipo: 'error'; mensaje: string }
  | { tipo: 'listo'; nombre: string; historial: Bloque[] };

function CuerpoModal({ productoId }: { productoId: string }) {
  const [contenido, setContenido] = useState<Contenido>({ tipo: 'cargando' });
  const [falloQr, setFalloQr] = useState(false);

  useEffect(() => {
    let vigente = true;
    Promise.all([certificacion.historial(productoId), productos.obtener(productoId)])
      .then(([historial, producto]) => { if (vigente) setContenido({ tipo: 'listo', nombre: producto.nombre, historial }); })
      .catch((err: Error) => { if (vigente) setContenido({ tipo: 'error', mensaje: err.message }); });
    return () => { vigente = false; };
  }, [productoId]);

  if (contenido.tipo === 'cargando') {
    return <div className="verificar-cargando"><div className="spinner" /><p>Cargando certificación...</p></div>;
  }
  if (contenido.tipo === 'error') {
    return (
      <div className="verificar-banner banner-error">
        <span className="banner-icon"><i className="ti ti-alert-triangle" /></span>
        <div>
          <strong>No se pudo cargar la certificación</strong>
          <p>{contenido.mensaje}</p>
        </div>
      </div>
    );
  }
  return (
    <>
      <h3><i className="ti ti-link" /> Certificación de trazabilidad</h3>
      <p className="muted">{contenido.nombre}</p>
      <div className="certificacion-qr-bloque">
        {falloQr
          ? <p className="muted">No se pudo cargar el código QR.</p>
          : <QrCertificacion url={certificacion.qrUrl(productoId)} alFallar={() => setFalloQr(true)} />}
        <p className="muted certificacion-qr-nota">Comparte este QR con tus compradores para que verifiquen el origen del producto.</p>
      </div>
      <h4 className="timeline-titulo">Historial de la cadena</h4>
      <LineaTiempoCertificacion historial={contenido.historial} />
    </>
  );
}

export function ModalCertificacion() {
  const productoId = useSyncExternalStore((o) => { oyentes.add(o); return () => { oyentes.delete(o); }; }, () => productoAbierto);

  useEffect(() => {
    if (!productoId) return undefined;
    const alEscape = (e: KeyboardEvent) => { if (e.key === 'Escape') fijar(null); };
    document.addEventListener('keydown', alEscape);
    return () => document.removeEventListener('keydown', alEscape);
  }, [productoId]);

  if (!productoId) return null;
  return (
    <div className="modal-overlay" id="modal-certificacion" onClick={(e) => { if (e.target === e.currentTarget) fijar(null); }}>
      <div className="modal modal-certificacion" role="dialog" aria-modal="true" aria-label="Certificación de trazabilidad">
        <button className="modal-close" aria-label="Cerrar" onClick={() => fijar(null)}><i className="ti ti-x" /></button>
        <div id="certificacion-content"><CuerpoModal key={productoId} productoId={productoId} /></div>
      </div>
    </div>
  );
}
