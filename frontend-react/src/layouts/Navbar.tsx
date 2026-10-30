import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Link, NavLink, useLocation } from 'react-router';
import { useAuth } from '../auth/AuthContext';
import { rutaInicialPara } from '../auth/rutasPorRol';
import { Logo } from '../components/Logo';
import { leerCarrito, suscribirCarrito } from '../features/checkout/carrito';

const PANEL_POR_ROL: Partial<Record<string, string>> = {
  productor: 'Panel Productor',
  repartidor: 'Gestionar Envíos',
  admin: 'Panel Admin',
};

export function Navbar() {
  const { sesion, autenticado, cerrarSesion } = useAuth();
  const { pathname } = useLocation();
  const carrito = useSyncExternalStore(suscribirCarrito, leerCarrito);
  const [cuentaAbierta, setCuentaAbierta] = useState(false);
  const [menuAbierto, setMenuAbierto] = useState(false);
  const cuenta = useRef<HTMLDivElement>(null);

  // Los controles de compra solo tienen sentido para un comprador o un visitante (comprador potencial).
  const esCompradorOInvitado = !autenticado || sesion.rol === 'comprador';
  const nombrePanel = sesion.rol ? PANEL_POR_ROL[sesion.rol] : undefined;
  const unidades = carrito.reduce((total, linea) => total + linea.cantidad, 0);

  useEffect(() => {
    setCuentaAbierta(false);
    setMenuAbierto(false);
  }, [pathname]);

  useEffect(() => {
    if (!cuentaAbierta) return undefined;
    const alClicFuera = (e: MouseEvent) => {
      if (!cuenta.current?.contains(e.target as Node)) setCuentaAbierta(false);
    };
    const alEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setCuentaAbierta(false);
    };
    document.addEventListener('mousedown', alClicFuera);
    document.addEventListener('keydown', alEscape);
    return () => {
      document.removeEventListener('mousedown', alClicFuera);
      document.removeEventListener('keydown', alEscape);
    };
  }, [cuentaAbierta]);

  return (
    <header className="navbar navbar-scrolled">
      <div className="navbar-inner">
        <Link className="brand" to="/" aria-label="ChakraShop, ir al inicio">
          <span className="brand-icon"><Logo /></span>
          <span className="brand-text">Chakra<strong>Shop</strong></span>
        </Link>

        <nav className={`nav-links ${menuAbierto ? 'open' : ''}`} id="nav-links" aria-label="Principal">
          {nombrePanel && <NavLink className="nav-link" to={rutaInicialPara(sesion.rol)}>{nombrePanel}</NavLink>}
        </nav>

        <div className="nav-actions">
          {esCompradorOInvitado && (
            <Link className="btn btn-ghost cart-btn" to="/comprador/checkout" aria-label={`Carrito, ${unidades} ${unidades === 1 ? 'producto' : 'productos'}`}>
              <i className="ti ti-shopping-cart" aria-hidden="true" />
              {unidades > 0 && <span className="cart-badge">{unidades}</span>}
            </Link>
          )}

          <div className={`nav-account ${cuentaAbierta ? 'open' : ''}`} ref={cuenta}>
            <button type="button" className="nav-account-trigger" aria-haspopup="true" aria-expanded={cuentaAbierta} aria-label="Mi cuenta" onClick={() => setCuentaAbierta((v) => !v)}>
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
                    <button type="button" className="nav-account-item nav-account-logout" onClick={cerrarSesion}><i className="ti ti-logout" aria-hidden="true" /> Cerrar sesión</button>
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

          {nombrePanel && (
            <button type="button" className="hamburger" aria-label="Menú" aria-expanded={menuAbierto} aria-controls="nav-links" onClick={() => setMenuAbierto((v) => !v)}>
              <i className="ti ti-menu-2" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
