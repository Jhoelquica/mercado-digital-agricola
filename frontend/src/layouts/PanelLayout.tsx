import { useEffect, useRef, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { Texto } from '../idioma/IdiomaContext';
import type { SeccionDePanel } from '../router/secciones';

interface Props {
  titulo: ReactNode;
  icono: string;
  base: string;
  secciones: SeccionDePanel[];
  estilo: 'productor' | 'admin';
  acciones?: ReactNode;
  // Contador junto a una pestaña (por su ruta); con 0 no se muestra.
  insignias?: Record<string, { valor: number; titulo: string }>;
  // En lugar de las pestañas (p. ej. el formulario para crear el perfil antes de usar el panel).
  sinPestanas?: ReactNode;
  // El id de la vista en el frontend anterior (p. ej. "view-panel-admin"): algunas reglas de legacy.css dependen de él.
  id?: string;
  children?: ReactNode;
}

// Encabezado y pestañas de un panel. Las pestañas son rutas: se pueden abrir, recargar y volver a ellas con Atrás.
export function PanelLayout({ titulo, icono, base, secciones, estilo, acciones, insignias, sinPestanas, id, children }: Props) {
  // "/productor/" también es la pestaña de inicio (NavLink con `end` solo marca "/productor").
  const ruta = useLocation().pathname;
  const enInicio = ruta.replace(/\/+$/, '') === base;
  const fila = useRef<HTMLElement>(null);

  // En el celular las pestañas van en una fila que se desliza: un degradado en el borde avisa que hay más a ese lado (en
  // computadora van en varias filas y no hace falta). Ver "Pestañas de los paneles" en legacy.css.
  useEffect(() => {
    const nav = fila.current;
    if (!nav) return undefined;
    const actualizar = () => {
      const desborda = nav.scrollWidth > nav.clientWidth + 1;
      nav.classList.toggle('hay-mas-izq', desborda && nav.scrollLeft > 4);
      nav.classList.toggle('hay-mas-der', desborda && nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 4);
    };
    nav.addEventListener('scroll', actualizar, { passive: true });
    window.addEventListener('resize', actualizar);
    const observador = new ResizeObserver(actualizar);
    observador.observe(nav);
    actualizar();
    return () => {
      nav.removeEventListener('scroll', actualizar);
      window.removeEventListener('resize', actualizar);
      observador.disconnect();
    };
  }, [sinPestanas]);

  // La pestaña activa se trae al centro de la fila, sin mover la página.
  useEffect(() => {
    const nav = fila.current;
    const activa = nav?.querySelector<HTMLElement>('.active');
    if (nav && activa && nav.scrollWidth > nav.clientWidth) {
      nav.scrollTo({ left: activa.offsetLeft - (nav.clientWidth - activa.offsetWidth) / 2, behavior: 'smooth' });
    }
  }, [ruta, sinPestanas]);
  return (
    <section id={id}>
      <div className="section-header panel-header-hero">
        <h2><i className={`ti ti-${icono}`} aria-hidden="true" /> {titulo}</h2>
        {acciones}
      </div>
      {children}
      {sinPestanas ?? (
      <div className={`${estilo}-shell`}>
        <nav ref={fila} className={`${estilo}-tabs`} aria-label="Secciones del panel">
          {secciones.map((s) => (
            <NavLink key={s.ruta} className={({ isActive }) => `${estilo}-tab-btn${isActive || (!s.ruta && enInicio) ? ' active' : ''}`} to={s.ruta ? `${base}/${s.ruta}` : base} end>
              <i className={`ti ti-${s.icono}`} aria-hidden="true" />{' '}
              {s.claveIdioma ? <Texto clave={s.claveIdioma}>{s.etiqueta}</Texto> : s.etiqueta}
              {insignias?.[s.ruta]?.valor ? <> <span className="badge-contador" title={insignias[s.ruta]?.titulo}>{insignias[s.ruta]?.valor}</span></> : null}
            </NavLink>
          ))}
        </nav>
        <Outlet />
      </div>
      )}
    </section>
  );
}
