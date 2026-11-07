import { useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router';
import { productos, type ProductoDetalle, type Resena } from '../../api/productos';
import { useAuth } from '../../auth/AuthContext';
import { rutaDeLogin } from '../../auth/rutasPorRol';
import { toast } from '../../components/Toast';
import { formatearFechaServidor } from '../../lib/formato';

// Un solo ícono: "llena" o "vacía" se distingue por color (ver .estrella-llena en legacy.css).
export function Estrellas({ promedio }: { promedio: number | null | undefined }) {
  const llenas = Math.round(promedio || 0);
  return <>{[1, 2, 3, 4, 5].map((i) => <i key={i} className={`ti ti-star${i <= llenas ? ' estrella-llena' : ''}`} />)}</>;
}

function AvisoResena({ icono, titulo, texto }: { icono: string; titulo: string; texto: string }) {
  return (
    <div className="resena-login resena-aviso" role="status">
      <i className={`ti ti-${icono}`} aria-hidden="true" />
      <strong>{titulo}</strong>
      <p>{texto}</p>
    </div>
  );
}

function FormularioResena({ productoId, alPublicar }: { productoId: string; alPublicar: () => Promise<void> }) {
  const [calificacion, setCalificacion] = useState(0);
  const [comentario, setComentario] = useState('');
  const [publicando, setPublicando] = useState(false);

  async function alEnviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!calificacion) {
      toast('Selecciona una calificación de 1 a 5 estrellas.', 'error');
      return;
    }
    setPublicando(true);
    try {
      await productos.crearResena(productoId, { calificacion, comentario: comentario.trim() || null });
      toast('¡Gracias por tu reseña! 🌟');
      await alPublicar();
    } catch (err) {
      console.error('publicar tu reseña', err);
      toast((err as Error).message || 'Ocurrió un error en publicar tu reseña', 'error');
      setPublicando(false);
    }
  }

  return (
    <form id="form-resena" className="resena-form" onSubmit={alEnviar}>
      <h4>Deja tu reseña</h4>
      <p className="resena-form-nota"><i className="ti ti-circle-check" aria-hidden="true" /> Ya recibiste este producto: cuéntales a otros qué tal llegó.</p>
      <div className="estrellas-selector" role="group" aria-label="Tu calificación">
        {[1, 2, 3, 4, 5].map((v) => (
          <button key={v} type="button" className={v <= calificacion ? 'activa' : undefined} aria-label={`${v} estrella${v === 1 ? '' : 's'}`} aria-pressed={v === calificacion} onClick={() => setCalificacion(v)}>
            <i className="ti ti-star" aria-hidden="true" />
          </button>
        ))}
      </div>
      <textarea placeholder="Cuéntanos qué te pareció (opcional)" aria-label="Tu comentario" maxLength={1000} value={comentario} onChange={(e) => setComentario(e.target.value)} />
      <button type="submit" className="btn btn-primary" disabled={publicando}>{publicando ? 'Publicando...' : 'Publicar reseña'}</button>
    </form>
  );
}

interface Props {
  resenas: Resena[];
  producto: ProductoDetalle;
  // Solo califica un comprador que ya RECIBIÓ el producto, una vez (el servidor lo exige igual). null = no se pudo consultar.
  recibidos: Set<string> | null;
  alPublicar: () => Promise<void>;
}

function AccionResena({ resenas, producto, recibidos, alPublicar }: Props) {
  const { sesion, autenticado } = useAuth();
  const { pathname } = useLocation();
  if (!autenticado) {
    return (
      <div className="resena-login">
        <i className="ti ti-lock" aria-hidden="true" />
        <p>Inicia sesión para calificar un producto que compraste y recibiste.</p>
        <Link className="btn btn-outline" to={rutaDeLogin(pathname)}>Iniciar sesión</Link>
      </div>
    );
  }
  if (sesion.rol !== 'comprador') return null;
  if (resenas.some((r) => String(r.usuario_id) === String(sesion.usuarioId))) {
    return <AvisoResena icono="circle-check" titulo="Ya calificaste este producto" texto="Gracias: tu opinión ayuda a otros compradores a elegir." />;
  }
  if (!recibidos) {
    return <AvisoResena icono="alert-circle" titulo="No pudimos revisar tus compras" texto="Vuelve a abrir el producto en un momento para calificarlo." />;
  }
  if (!recibidos.has(String(producto.id))) {
    return <AvisoResena icono="package" titulo="Califícalo cuando lo recibas" texto="Solo califican quienes compraron este producto y ya lo recibieron." />;
  }
  return <FormularioResena productoId={producto.id} alPublicar={alPublicar} />;
}

// A la izquierda el resumen (promedio, barras por estrellas, calculadas con las reseñas reales) y el formulario; a la
// derecha las opiniones.
export function ResenasDetalle(props: Props) {
  const { resenas, producto: p } = props;
  const total = resenas.length;
  const promedio = total ? (p.calificacion_promedio ?? resenas.reduce((suma, r) => suma + r.calificacion, 0) / total) : null;

  return (
    <div className="detalle-resenas-grid">
      <div className="detalle-resenas-lateral">
        {total > 0 && (
          <div className="resenas-resumen">
            <div className="resenas-promedio">
              <strong>{Number(promedio).toFixed(1)}</strong>
              <span className="mp-solo-lectores">de 5 estrellas</span>
              <span className="rating-stars" aria-hidden="true"><Estrellas promedio={promedio} /></span>
              <span className="rating-count">{total} reseña{total === 1 ? '' : 's'}</span>
            </div>
            <ul className="resenas-barras" aria-label="Reseñas según su cantidad de estrellas">
              {[5, 4, 3, 2, 1].map((n) => {
                const cantidad = resenas.filter((r) => Math.round(r.calificacion) === n).length;
                return <li key={n}><span>{n}</span><i className="ti ti-star" aria-hidden="true" /><span className="resenas-barra"><span style={{ width: `${(cantidad / total) * 100}%` }} /></span><span className="resenas-barra-n">{cantidad}</span></li>;
              })}
            </ul>
          </div>
        )}
        <AccionResena {...props} />
      </div>
      <div className="detalle-resenas-lista">
        {total ? resenas.map((r) => (
          <article className="resena-card" key={r.id}>
            <header className="resena-header">
              <span className="resena-avatar" aria-hidden="true">{(r.usuario_nombre || '?').trim().charAt(0).toUpperCase()}</span>
              <div className="resena-quien">
                <span className="resena-usuario">{r.usuario_nombre}</span>
                <span className="resena-fecha">{formatearFechaServidor(r.fecha_creacion)}</span>
              </div>
              <span className="rating-stars" role="img" aria-label={`${r.calificacion} de 5 estrellas`}><Estrellas promedio={r.calificacion} /></span>
            </header>
            {r.comentario && <p className="resena-comentario">{r.comentario}</p>}
          </article>
        )) : (
          <div className="resenas-vacio">
            <i className="ti ti-message-circle" aria-hidden="true" />
            <strong>Todavía no hay reseñas</strong>
            <p>Cuando alguien pruebe este producto, su opinión aparecerá aquí.</p>
          </div>
        )}
      </div>
    </div>
  );
}
