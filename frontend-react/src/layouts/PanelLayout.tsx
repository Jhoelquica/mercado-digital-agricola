import type { ReactNode } from 'react';
import { NavLink, Outlet } from 'react-router';
import { Texto } from '../idioma/IdiomaContext';
import type { SeccionDePanel } from '../router/secciones';

interface Props {
  titulo: ReactNode;
  icono: string;
  base: string;
  secciones: SeccionDePanel[];
  estilo: 'productor' | 'admin';
  acciones?: ReactNode;
  children?: ReactNode;
}

// Encabezado y pestañas de un panel. Las pestañas son rutas: se pueden abrir, recargar y volver a ellas con Atrás.
export function PanelLayout({ titulo, icono, base, secciones, estilo, acciones, children }: Props) {
  return (
    <section>
      <div className="section-header panel-header-hero">
        <h2><i className={`ti ti-${icono}`} aria-hidden="true" /> {titulo}</h2>
        {acciones}
      </div>
      {children}
      <div className={`${estilo}-shell`}>
        <nav className={`${estilo}-tabs`} aria-label="Secciones del panel">
          {secciones.map((s) => (
            <NavLink key={s.ruta} className={`${estilo}-tab-btn`} to={s.ruta ? `${base}/${s.ruta}` : base} end>
              <i className={`ti ti-${s.icono}`} aria-hidden="true" />{' '}
              {s.claveIdioma ? <Texto clave={s.claveIdioma}>{s.etiqueta}</Texto> : s.etiqueta}
            </NavLink>
          ))}
        </nav>
        <Outlet />
      </div>
    </section>
  );
}
