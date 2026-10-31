import { normalizarUrlImagen } from '../../api/imagenes';
import type { Bloque } from '../../api/certificacion';
import { formatearFechaServidor, hashCorto } from '../../lib/formato';

const EVENTOS_CERTIFICACION: Record<string, { icono: string; texto: string }> = {
  cosecha_registrada: { icono: 'wheat', texto: 'Cosecha registrada' },
  certificado_productor: { icono: 'circle-check', texto: 'Certificado por el productor' },
  llegada_almacen: { icono: 'building-warehouse', texto: 'Llegada al almacén' },
  verificado_punto_venta: { icono: 'building-store', texto: 'Verificado en punto de venta' },
};

function labelEvento(evento: string) {
  return EVENTOS_CERTIFICACION[evento] || { icono: 'package', texto: evento };
}

// El texto de un bloque de cosecha trae varias oraciones (cantidad y chacra; dónde estaba el agricultor al sembrar y al
// cosechar; la huella de la foto): se muestran una por línea y las advertencias con su ícono, no solo con color.
export function DatosDeBloque({ datos }: { datos: string }) {
  const frases = String(datos).split(/(?<=\.)\s+(?=[A-ZÁÉÍÓÚÑ])/);
  if (frases.length < 2) return <p className="timeline-datos">{datos}</p>;
  return (
    <ul className="timeline-datos timeline-datos-lista">
      {frases.map((frase) => {
        const advertencia = frase.includes('(advertencia)');
        const icono = advertencia ? 'alert-triangle'
          : /^Al (registrar|completar)/.test(frase) ? 'map-pin'
            : /^Foto de la cosecha/.test(frase) ? 'fingerprint' : 'point';
        return <li key={frase} className={advertencia ? 'es-advertencia' : undefined}><i className={`ti ti-${icono}`} aria-hidden="true" /><span>{frase}</span></li>;
      })}
    </ul>
  );
}

// La foto de la cosecha (solo en cosecha_registrada). Su huella está en el texto del bloque, que sí entra en el hash.
export function FotoDeBloque({ url }: { url: string }) {
  return (
    <a className="timeline-foto" href={url} target="_blank" rel="noopener">
      <img src={url} alt="Foto de la cosecha tomada en la chacra" loading="lazy" />
      <span><i className="ti ti-camera" aria-hidden="true" /> Foto tomada en la chacra · ver en grande</span>
    </a>
  );
}

// Solo aplica al evento certificado_productor; el backend ya resuelve el nombre en /historial (puede venir null si el
// verificador fue borrado, ahí caemos al id truncado).
function LineaVerificador({ bloque }: { bloque: Bloque }) {
  if (bloque.evento !== 'certificado_productor' || !bloque.verificador_id) return null;
  const nombre = bloque.verificador_nombre || `verificador ${String(bloque.verificador_id).slice(0, 8)}`;
  return <p className="timeline-verificador"><i className="ti ti-user" /> Certificado por: {nombre}</p>;
}

export function LineaTiempoCertificacion({ historial }: { historial: Bloque[] }) {
  if (!historial.length) {
    return <p className="muted" style={{ textAlign: 'center', padding: '12px 0 28px' }}>Aún no hay eventos registrados para este producto.</p>;
  }
  return (
    <div className="timeline">
      {historial.map((b) => {
        const info = labelEvento(b.evento);
        return (
          <div className="timeline-item" key={b.id}>
            <div className="timeline-marker"><i className={`ti ti-${info.icono}`} /></div>
            <div className="timeline-content">
              <div className="timeline-header">
                <strong>{info.texto}</strong>
                <span className="timeline-indice">Bloque #{b.indice}</span>
              </div>
              <div className="timeline-fecha">{formatearFechaServidor(b.fecha)}</div>
              <LineaVerificador bloque={b} />
              {b.datos && <DatosDeBloque datos={b.datos} />}
              {b.foto_url && <FotoDeBloque url={normalizarUrlImagen(b.foto_url)} />}
              <div className="timeline-hash">Hash: <code>{hashCorto(b.hash_actual)}</code></div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// El QR de certificación; si no carga, un aviso en su lugar.
export function QrCertificacion({ url, alFallar }: { url: string; alFallar: () => void }) {
  return <img src={url} alt="Código QR de certificación" className="certificacion-qr" onError={alFallar} />;
}
