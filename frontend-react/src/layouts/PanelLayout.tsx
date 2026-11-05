import type { ReactNode } from 'react';
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
  children?: ReactNode;
}

// Encabezado y pestañas de un panel. Las pestañas son rutas: se pueden abrir, recargar y volver a ellas con Atrás.
export function PanelLayout({ titulo, icono, base, secciones, estilo, acciones, insignias, sinPestanas, children }: Props) {
  // "/productor/" también es la pestaña de inicio (NavLink con `end` solo marca "/productor").
  const enInicio = useLocation().pathname.replace(/\/+$/, '') === base;
  return (
    <section>
      <div className="section-header panel-header-hero">
        <h2><i className={`ti ti-${icono}`} aria-hidden="true" /> {titulo}</h2>
        {acciones}
      </div>
      {children}
      {sinPestanas ?? (
      <div className={`${estilo}-shell`}>
        <nav className={`${estilo}-tabs`} aria-label="Secciones del panel">
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
