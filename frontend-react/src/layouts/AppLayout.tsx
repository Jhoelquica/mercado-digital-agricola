import { useEffect, useSyncExternalStore } from 'react';
import { Link, Outlet, ScrollRestoration, useLocation, useMatches } from 'react-router';
import { MENSAJE_SESION_EXPIRADA } from '../api/errors';
import { useAuth } from '../auth/AuthContext';
import { rutaDeLogin } from '../auth/rutasPorRol';
import { haySesionRecienExpirada, leerSesion, olvidarSesionRecienExpirada, suscribirSesion } from '../auth/sesion';
import { PanelBusqueda } from '../features/catalogo/PanelBusqueda';
import { ModalCertificacion } from '../features/producto/ModalCertificacion';
import { sincronizarAvisosCelular } from '../lib/avisos';
import { Navbar } from './Navbar';

export interface DatosDeRuta {
  titulo?: string;
}

export function useTituloDeRuta(): string | undefined {
  const matches = useMatches();
  return (matches.at(-1)?.handle as DatosDeRuta | undefined)?.titulo;
}

// La sesión expiró estando en una página pública: se queda donde está y puede volver a entrar cuando quiera.
function AvisoSesionExpirada() {
  const expirada = useSyncExternalStore(suscribirSesion, haySesionRecienExpirada);
  const { pathname, search } = useLocation();
  if (!expirada) return null;
  return (
    <div className="aviso-sesion" role="alert">
      <i className="ti ti-clock-exclamation" aria-hidden="true" />
      <span>{MENSAJE_SESION_EXPIRADA}</span>
      <Link className="btn btn-primary btn-sm" to={rutaDeLogin(`${pathname}${search}`)}>Iniciar sesión</Link>
      <button type="button" className="aviso-sesion-cerrar" aria-label="Cerrar aviso" onClick={olvidarSesionRecienExpirada}><i className="ti ti-x" /></button>
    </div>
  );
}

export function AppLayout() {
  const titulo = useTituloDeRuta();
  const { autenticado } = useAuth();

  useEffect(() => {
    document.title = titulo ? `${titulo} — ChakraShop` : 'ChakraShop — Mercado Digital Agrícola';
  }, [titulo]);

  // Aquí y no al cargar la sesión: /verificar queda fuera de este layout y no debe mandar ningún token.
  useEffect(() => {
    if (leerSesion().token) void sincronizarAvisosCelular();
  }, []);

  return (
    <>
      <a className="enlace-saltar" href="#app-main">Saltar al contenido</a>
      <Navbar />
      <PanelBusqueda />
      <AvisoSesionExpirada />
      <main id="app-main" tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="footer">
        {!autenticado && (
          <div className="footer-ctas">
            <p className="footer-ctas-title">¿Listo para empezar?</p>
            <p className="footer-ctas-sub">Únete gratis y sé parte de la cadena que lleva el campo de Ayacucho hasta tu mesa.</p>
            <div className="footer-ctas-buttons">
              <Link className="btn btn-primary btn-lg" to="/registro?rol=comprador"><i className="ti ti-shopping-cart" /> Soy comprador</Link>
              <Link className="btn btn-primary btn-lg" to="/registro?rol=productor"><i className="ti ti-tractor" /> Soy productor</Link>
              <Link className="btn btn-primary btn-lg" to="/registro?rol=repartidor"><i className="ti ti-truck" /> Soy repartidor</Link>
            </div>
          </div>
        )}
        <p>🌾 Chakra Shop — Conectando el campo peruano con tu mesa. Proyecto de microservicios distribuidos.</p>
      </footer>
      <ModalCertificacion />
      <ScrollRestoration />
    </>
  );
}
