import { useEffect, useState, useSyncExternalStore } from 'react';
import { Link, NavLink, useLocation } from 'react-router';
import { useAuth } from '../auth/AuthContext';
import { rutaInicialPara } from '../auth/rutasPorRol';
import { Logo } from '../components/Logo';
import { leerCarrito, suscribirCarrito } from '../features/checkout/carrito';
import type { EstadoCheckout } from '../features/checkout/CheckoutPage';
import { abrirMenu, abrirPanelBusqueda, cerrarMenuConRetraso, cerrarMenus, leerMenu, suscribirMenus } from '../features/catalogo/menus';
import { NavCategorias } from '../features/catalogo/NavCategorias';

const PANEL_POR_ROL: Partial<Record<string, string>> = {
  productor: 'Panel Productor',
  repartidor: 'Gestionar Envíos',
  admin: 'Panel Admin',
};

const ACUMULADO_PARA_OCULTAR = 8; // px de scroll seguido hacia abajo antes de reaccionar (ignora micro-scrolls)
const SCROLL_MINIMO_PARA_OCULTAR = 80; // cerca del tope siempre visible, sin importar la dirección

// Dos efectos independientes: transparente sobre el hero y sólido al pasarlo (según la POSICIÓN), y se oculta al bajar y
// reaparece al subir (según la DIRECCIÓN). Como mucho una actualización por cuadro.
function useNavbarDinamico(pathname: string) {
  const [solido, setSolido] = useState(false);
  const [oculto, setOculto] = useState(false);

  useEffect(() => {
    let ultimoY = window.scrollY;
    let acumulado = 0;
    let pendiente = false;

    const actualizar = () => {
      pendiente = false;
      const y = window.scrollY;
      // El umbral es la altura real del hero (si la página no lo tiene, un margen chico).
      const altura = document.querySelector<HTMLElement>('.landing-hero')?.offsetHeight ?? 0;
      setSolido(y > (altura > 100 ? altura - 80 : 20));

      const delta = y - ultimoY;
      if (y <= SCROLL_MINIMO_PARA_OCULTAR) {
        setOculto(false);
        acumulado = 0;
      } else if (delta > 0) {
        acumulado += delta;
        if (acumulado > ACUMULADO_PARA_OCULTAR) setOculto(true);
      } else if (delta < 0) {
        setOculto(false);
        acumulado = 0;
      }
      ultimoY = y;
    };
    const alScroll = () => {
      if (pendiente) return;
      pendiente = true;
      requestAnimationFrame(actualizar);
    };

    // Tras navegar, el navbar siempre vuelve a mostrarse y el seguimiento de la dirección arranca de cero.
    setOculto(false);
    actualizar();
    window.addEventListener('scroll', alScroll, { passive: true });
    return () => window.removeEventListener('scroll', alScroll);
  }, [pathname]);

  return { solido, oculto };
}

export function Navbar() {
  const { sesion, autenticado, cerrarSesion } = useAuth();
  const { pathname } = useLocation();
  const carrito = useSyncExternalStore(suscribirCarrito, leerCarrito);
  const menu = useSyncExternalStore(suscribirMenus, leerMenu);
  const [menuMovilAbierto, setMenuMovilAbierto] = useState(false);
  const { solido, oculto } = useNavbarDinamico(pathname);
  const cuentaAbierta = menu?.tipo === 'cuenta';

  // Los controles de compra solo tienen sentido para un comprador o un visitante (comprador potencial).
  const esCompradorOInvitado = !autenticado || sesion.rol === 'comprador';
  const nombrePanel = sesion.rol ? PANEL_POR_ROL[sesion.rol] : undefined;
  const unidades = carrito.reduce((total, linea) => total + linea.cantidad, 0);

  useEffect(() => {
    cerrarMenus();
    setMenuMovilAbierto(false);
  }, [pathname]);

  useEffect(() => {
    const alClic = (e: MouseEvent) => {
      if (!(e.target as Element).closest?.('.nav-cat, .nav-account')) cerrarMenus();
    };
    const alEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cerrarMenus();
    };
    document.addEventListener('click', alClic);
    document.addEventListener('keydown', alEscape);
    return () => {
      document.removeEventListener('click', alClic);
      document.removeEventListener('keydown', alEscape);
    };
  }, []);

  const esTactil = () => !window.matchMedia('(hover: hover)').matches;

  return (
    <header className={`navbar ${solido ? 'navbar-scrolled' : ''} ${oculto ? 'navbar-oculto' : ''}`}>
      <div className="navbar-inner">
        <Link className="brand" to="/" aria-label="ChakraShop, ir al inicio">
          <span className="brand-icon"><Logo /></span>
          <span className="brand-text">Chakra<strong>Shop</strong></span>
        </Link>

        <nav className={`nav-links ${menuMovilAbierto ? 'open' : ''}`} id="nav-links" aria-label="Principal">
          {esCompradorOInvitado && <NavCategorias alElegir={() => setMenuMovilAbierto(false)} />}
          {nombrePanel && <NavLink className="nav-link" to={rutaInicialPara(sesion.rol)}>{nombrePanel}</NavLink>}
        </nav>

        <div className="nav-actions">
          {esCompradorOInvitado && (
            <>
              <button className="btn btn-ghost" aria-label="Buscar productos" onClick={() => { setMenuMovilAbierto(false); abrirPanelBusqueda(); }}><i className="ti ti-search" /></button>
              <Link className="btn btn-ghost cart-btn" to="/comprador/checkout" state={{ conFondo: true } satisfies EstadoCheckout} preventScrollReset aria-label={`Carrito, ${unidades} ${unidades === 1 ? 'producto' : 'productos'}`}>
                <i className="ti ti-shopping-cart" aria-hidden="true" />
                {unidades > 0 && <span className="cart-badge">{unidades}</span>}
              </Link>
            </>
          )}

          <div
            className={`nav-account ${cuentaAbierta ? 'open' : ''}`}
            onMouseEnter={() => { if (!esTactil()) abrirMenu({ tipo: 'cuenta' }); }}
            onMouseLeave={() => { if (!esTactil()) cerrarMenuConRetraso(); }}
          >
            <button
              type="button"
              className="nav-account-trigger"
              aria-haspopup="true"
              aria-expanded={cuentaAbierta}
              aria-label="Mi cuenta"
              onClick={() => { if (cuentaAbierta && esTactil()) cerrarMenus(); else abrirMenu({ tipo: 'cuenta' }); }}
            >
              <i className="ti ti-user" aria-hidden="true" />
            </button>
            <div className="nav-account-panel">
              <div className="nav-account-panel-inner">
                {autenticado ? (
                  <div>
                    <div className="nav-account-saludo">Hola, <strong>{sesion.nombre || 'Usuario'}</strong></div>
                    {sesion.rol === 'comprador' && <Link className="nav-account-item" to="/comprador/pedidos"><i className="ti ti-package" aria-hidden="true" /> Mis Pedidos</Link>}
                    <Link className="nav-account-item" to="/notificaciones"><i className="ti ti-bell" aria-hidden="true" /> Notificaciones</Link>
                    <Link className="nav-account-item" to="/perfil"><i className="ti ti-user" aria-hidden="true" /> Mi Perfil</Link>
                    <button type="button" className="nav-account-item nav-account-logout" onClick={() => { cerrarMenus(); cerrarSesion(); }}><i className="ti ti-logout" aria-hidden="true" /> Cerrar sesión</button>
                  </div>
                ) : (
                  <div>
                    <Link className="nav-account-item" to="/login"><i className="ti ti-key" aria-hidden="true" /> Iniciar sesión</Link>
                    <Link className="nav-account-item" to="/registro"><i className="ti ti-user-plus" aria-hidden="true" /> Registrarse</Link>
                  </div>
                )}
              </div>
            </div>
          </div>

          <button type="button" className="hamburger" aria-label="Menú" aria-expanded={menuMovilAbierto} aria-controls="nav-links" onClick={() => setMenuMovilAbierto((v) => !v)}>
            <i className="ti ti-menu-2" aria-hidden="true" />
          </button>
        </div>
      </div>
    </header>
  );
}
