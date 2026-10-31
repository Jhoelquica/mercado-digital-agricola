import type { ReactNode } from 'react';
import { Link, NavLink, useSearchParams } from 'react-router';
import { Logo } from '../../components/Logo';

// Si el servidor no dio un texto útil (por ejemplo, un JSON crudo) se muestra el mensaje de respaldo.
export function mensajeDeError(err: unknown, respaldo: string): string {
  const mensaje = err instanceof Error ? err.message.trim() : '';
  return mensaje && !/^[[{]/.test(mensaje) ? mensaje : respaldo;
}

interface Props {
  tituloLado: string;
  textoLado: string;
  children: ReactNode;
}

// Los dos paneles del acceso: la marca a la izquierda y el formulario a la derecha (en móvil, una franja de marca arriba).
export function AuthLayout({ tituloLado, textoLado, children }: Props) {
  const [params] = useSearchParams();
  const sufijo = params.get('next') ? `?next=${encodeURIComponent(params.get('next') as string)}` : '';
  return (
    <main className="login-pagina">
      <aside className="auth-lado">
        <Link className="auth-lado-marca" to="/" aria-label="ChakraShop, ir al inicio">
          <span className="auth-lado-logo"><Logo variante="claro" /></span>
          <span className="auth-lado-nombre">Chakra<strong>Shop</strong></span>
        </Link>
        <div className="auth-lado-cuerpo">
          <div className="auth-lado-texto">
            <h2>{tituloLado}</h2>
            <p>{textoLado}</p>
          </div>
          <ul className="auth-lado-lista">
            <li><i className="ti ti-shield-check" aria-hidden="true" /><span>Origen verificado con blockchain</span></li>
            <li><i className="ti ti-credit-card" aria-hidden="true" /><span>Pago seguro con tarjeta o Yape</span></li>
            <li><i className="ti ti-truck-delivery" aria-hidden="true" /><span>Seguimiento en vivo de tu envío</span></li>
          </ul>
        </div>
      </aside>

      <div className="auth-panel">
        <div className="login-contenido">
          <Link className="login-volver" to="/"><i className="ti ti-arrow-left" aria-hidden="true" /> Volver a la tienda</Link>
          <nav className="auth-tabs" aria-label="Acceso a tu cuenta">
            <NavLink className="auth-tab" to={`/login${sufijo}`}>Iniciar sesión</NavLink>
            <NavLink className="auth-tab" to={`/registro${sufijo}`}>Registrarse</NavLink>
          </nav>
          {children}
        </div>
      </div>
    </main>
  );
}
