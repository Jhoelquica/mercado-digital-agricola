import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { certificacion, type Bloque, type VerificacionCadena } from '../../api/certificacion';
import { ApiError } from '../../api/errors';
import { normalizarUrlImagen } from '../../api/imagenes';
import { productos, type ProductoDetalle } from '../../api/productos';
import { Logo } from '../../components/Logo';
import { formatearFechaServidor, hashCorto } from '../../lib/formato';
import { DatosDeBloque, FotoDeBloque } from '../producto/LineaTiempoCertificacion';

const EVENTOS_LABEL: Record<string, { icono: string; texto: string }> = {
  // Vale para todo lo que se obtiene: una cosecha, los huevos de un periodo o un queso (el texto del bloque lo detalla).
  cosecha_registrada: { icono: 'ti-seedling', texto: 'Registrado en el origen' },
  certificado_productor: { icono: 'ti-certificate', texto: 'Certificado por el productor' },
  llegada_almacen: { icono: 'ti-building-warehouse', texto: 'Llegada al almacén' },
  verificado_punto_venta: { icono: 'ti-building-store', texto: 'Verificado en punto de venta' },
};

function labelEvento(evento: string) {
  return EVENTOS_LABEL[evento] || { icono: 'ti-package', texto: evento };
}

// Solo aplica al evento certificado_productor; el backend ya resuelve el nombre en /historial (verificador_nombre puede
// venir null si el verificador fue borrado, ahí caemos al id truncado).
function LineaVerificador({ bloque }: { bloque: Bloque }) {
  if (bloque.evento !== 'certificado_productor' || !bloque.verificador_id) return null;
  const nombre = bloque.verificador_nombre || `verificador ${String(bloque.verificador_id).slice(0, 8)}`;
  return (
    <p className="timeline-verificador">
      <i className="ti ti-user-check" /> Certificado por: {nombre}{bloque.verificador_credencial ? ` · Credencial: ${bloque.verificador_credencial}` : ''}
    </p>
  );
}

function ErrorGeneral({ mensaje }: { mensaje: string }) {
  return (
    <div className="verificar-sello sello-error">
      <span className="verificar-sello-icono"><i className="ti ti-alert-triangle" /></span>
      <strong>No se pudo verificar el producto</strong>
      <p>{mensaje}</p>
    </div>
  );
}

// El "sello" es el elemento de mayor peso visual de toda la página a propósito: es la respuesta a la única pregunta que
// trae a alguien acá: ¿este producto es de fiar?
function Sello({ verificacion }: { verificacion: VerificacionCadena }) {
  if (verificacion.total_bloques === 0) {
    return (
      <div className="verificar-sello sello-neutro">
        <span className="verificar-sello-icono"><i className="ti ti-info-circle" /></span>
        <strong>Sin historial de certificación aún</strong>
        <p>{verificacion.mensaje}</p>
      </div>
    );
  }
  if (verificacion.valido) {
    return (
      <div className="verificar-sello sello-ok">
        <span className="verificar-sello-icono"><i className="ti ti-rosette-discount-check" /></span>
        <strong>Origen verificado</strong>
        <p>{verificacion.mensaje}</p>
        <span className="verificar-sello-meta"><i className="ti ti-link" /> {verificacion.total_bloques} evento{verificacion.total_bloques === 1 ? '' : 's'} enlazado{verificacion.total_bloques === 1 ? '' : 's'} criptográficamente</span>
      </div>
    );
  }
  return (
    <div className="verificar-sello sello-error">
      <span className="verificar-sello-icono"><i className="ti ti-shield-x" /></span>
      <strong>Alteración detectada</strong>
      <p>{verificacion.mensaje}</p>
    </div>
  );
}

function LineaTiempo({ historial }: { historial: Bloque[] }) {
  if (!historial.length) {
    return <p className="muted" style={{ textAlign: 'center', padding: '12px 0 28px' }}>Aún no hay eventos registrados para este producto.</p>;
  }
  return (
    <div className="timeline">
      {historial.map((b) => {
        const info = labelEvento(b.evento);
        return (
          <div className="timeline-item" key={b.id}>
            <div className="timeline-marker"><i className={`ti ${info.icono}`} /></div>
            <div className="timeline-content">
              <div className="timeline-header">
                <strong>{info.texto}</strong>
                <span className="timeline-indice">Bloque #{b.indice}</span>
              </div>
              <div className="timeline-fecha"><i className="ti ti-calendar-event" /> {formatearFechaServidor(b.fecha)}</div>
              <LineaVerificador bloque={b} />
              {b.datos && <DatosDeBloque datos={b.datos} />}
              {b.foto_url && <FotoDeBloque url={normalizarUrlImagen(b.foto_url)} />}
              <div className="timeline-hash"><i className="ti ti-fingerprint" /> <code>{hashCorto(b.hash_actual)}</code></div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FotoProducto({ producto }: { producto: ProductoDetalle }) {
  const url = producto.imagenes?.[0]?.url || producto.imagen_url;
  if (url) return <img src={url} alt={producto.nombre} />;
  return <span className="verificar-producto-foto-vacia"><i className="ti ti-photo" /></span>;
}

type Estado =
  | { tipo: 'cargando' }
  | { tipo: 'error'; mensaje: string }
  | { tipo: 'listo'; producto: ProductoDetalle; verificacion: VerificacionCadena; historial: Bloque[] };

// La página pública del QR: siempre como visitante, sin token, aunque haya sesión (igual que verificar.html del frontend anterior).
export default function VerificarPage() {
  const [params] = useSearchParams();
  const productoId = (params.get('producto_id') || '').trim();
  const [estado, setEstado] = useState<Estado>({ tipo: 'cargando' });

  useEffect(() => {
    document.title = 'Verificar producto — Chakra Shop';
    let vigente = true;
    const fijar = (nuevo: Estado) => { if (vigente) setEstado(nuevo); };

    async function cargar() {
      fijar({ tipo: 'cargando' });
      if (!productoId) {
        fijar({ tipo: 'error', mensaje: 'Falta el identificador del producto en el enlace. Escanea nuevamente el código QR del producto.' });
        return;
      }
      let producto: ProductoDetalle;
      try {
        producto = await productos.obtenerComoVisitante(productoId);
      } catch (err) {
        const noExiste = err instanceof ApiError && err.status === 404;
        fijar({ tipo: 'error', mensaje: noExiste ? 'Este producto no existe o ya no está disponible.' : 'No se pudo conectar con el servidor de productos. Verifica tu conexión e intenta de nuevo.' });
        return;
      }
      try {
        const [verificacion, historial] = await Promise.all([certificacion.verificar(productoId), certificacion.historial(productoId)]);
        fijar({ tipo: 'listo', producto, verificacion, historial });
      } catch {
        fijar({ tipo: 'error', mensaje: 'No se pudo conectar con el servicio de certificación. Intenta de nuevo más tarde.' });
      }
    }
    void cargar();
    return () => { vigente = false; };
  }, [productoId]);

  return (
    <>
      <meta name="robots" content="noindex" />
      <div className="verificar-topbar">
        <div className="verificar-topbar-inner">
          <span className="brand-icon"><Logo variante="texto" /></span>
          <span>Chakra<strong>Shop</strong></span>
          <span className="verificar-topbar-tag"><i className="ti ti-shield-check" /> Verificación de origen</span>
        </div>
      </div>

      <div className="verificar-page">
        <div id="contenido">
          {estado.tipo === 'cargando' && (
            <div className="verificar-cargando">
              <div className="spinner" />
              <p>Verificando el producto...</p>
            </div>
          )}
          {estado.tipo === 'error' && <ErrorGeneral mensaje={estado.mensaje} />}
          {estado.tipo === 'listo' && (
            <>
              <div className="verificar-producto-hero">
                <div className="verificar-producto-foto"><FotoProducto producto={estado.producto} /></div>
                <h1>{estado.producto.nombre}</h1>
                <p className="verificar-producto-productor"><i className="ti ti-tractor" /> {estado.producto.productor_nombre || 'Productor no especificado'}</p>
              </div>
              <Sello verificacion={estado.verificacion} />
              <h2 className="timeline-titulo"><i className="ti ti-timeline-event" /> Historial de la cadena</h2>
              <LineaTiempo historial={estado.historial} />
              <div className="verificar-nota">
                <i className="ti ti-lock" />
                <div><strong>¿Qué significa esto?</strong> Cada evento queda enlazado matemáticamente al anterior mediante un hash — si alguien intentara alterar un registro pasado, la cadena completa dejaría de coincidir y la alteración quedaría expuesta automáticamente.</div>
              </div>
              <div className="verificar-footer">
                <i className="ti ti-shield-check" /> Verificado por Chakra Shop
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
