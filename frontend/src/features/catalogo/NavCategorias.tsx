import { useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { useNavigate } from 'react-router';
import { CATEGORIAS_PRODUCTO, ICONOS_CATEGORIA, NOMBRE_CATEGORIA, normalizarCategoria, ordenarPorDestacado, type Categoria } from '../../lib/productos';
import { abrirMenu, abrirPanelBusqueda, cerrarMenuConRetraso, cerrarMenus, leerMenu, suscribirMenus } from './menus';
import { ProductoMega } from './TarjetasProducto';
import { useAbrirProducto, useCatalogo } from './useCatalogo';

const esTactil = () => !window.matchMedia('(hover: hover)').matches;

function SkeletonMega() {
  return (
    <>
      {[0, 1, 2, 3].map((i) => (
        <div className="nav-mega-producto" key={i}>
          <div className="skeleton" style={{ aspectRatio: '1/1', borderRadius: 10 }} />
          <div className="skeleton skeleton-line w-70" />
          <div className="skeleton skeleton-line w-40" />
        </div>
      ))}
    </>
  );
}

function ContenidoCategoria({ categoria, alCerrarMenu }: { categoria: Categoria; alCerrarMenu: () => void }) {
  const { data: productos, isPending } = useCatalogo();
  const abrirProducto = useAbrirProducto();
  const navigate = useNavigate();
  const todos = (productos || []).filter((p) => normalizarCategoria(p.categoria) === categoria);
  // Los mejor calificados primero (mismo criterio de "destacados" de la landing), no el orden del catálogo.
  const destacados = ordenarPorDestacado(todos).slice(0, 4);

  return (
    <div className="nav-cat-panel-inner">
      <div className="nav-cat-panel-productos">
        <div className="nav-cat-cabecera">
          <span className="nav-cat-cabecera-icono" aria-hidden="true">{ICONOS_CATEGORIA[categoria]}</span>
          <div className="nav-cat-cabecera-texto">
            <strong>{NOMBRE_CATEGORIA[categoria]}</strong>
            <span className="nav-cat-conteo">{productos ? `${todos.length} producto${todos.length === 1 ? '' : 's'}` : ''}</span>
          </div>
          <button type="button" className="nav-cat-vertodo" onClick={() => { alCerrarMenu(); abrirPanelBusqueda(categoria); }}>Ver todos <i className="ti ti-arrow-right" aria-hidden="true" /></button>
        </div>
        <div className="nav-mega-productos">
          {isPending && <SkeletonMega />}
          {!isPending && (destacados.length
            ? destacados.map((p) => <ProductoMega key={p.id} producto={p} onClick={() => { alCerrarMenu(); abrirProducto(p.id); }} />)
            : <p className="muted" style={{ gridColumn: '1/-1', margin: 0 }}>Sin productos en esta categoría por ahora.</p>)}
        </div>
      </div>
      <div className="nav-cat-panel-descubre">
        <span className="nav-cat-descubre-titulo">Descubre</span>
        <button type="button" className="nav-cat-descubre-link" onClick={() => { alCerrarMenu(); abrirPanelBusqueda('', { explorar: true }); }}>Ver todo el catálogo</button>
        {/* No existe una vista dedicada a "cómo funciona": se ancla a la tarjeta real de la landing. */}
        <button type="button" className="nav-cat-descubre-link" onClick={() => { alCerrarMenu(); void navigate('/', { state: { anclar: 'landing-confianza-blockchain' } }); }}>Cómo funciona la trazabilidad blockchain</button>
      </div>
    </div>
  );
}

function NavCategoria({ categoria, alCerrarMenu }: { categoria: Categoria; alCerrarMenu: () => void }) {
  const menu = useSyncExternalStore(suscribirMenus, leerMenu);
  const abierta = menu?.tipo === 'categoria' && menu.categoria === categoria;
  const raiz = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  // El panel es position:fixed (ver .nav-cat-panel en legacy.css): se posiciona a mano contra el botón real.
  useLayoutEffect(() => {
    if (!abierta || !raiz.current || !panel.current) return;
    const rect = raiz.current.getBoundingClientRect();
    const anchoPanel = panel.current.offsetWidth || 560;
    const left = Math.min(rect.left, window.innerWidth - anchoPanel - 16);
    panel.current.style.top = `${rect.bottom}px`;
    panel.current.style.left = `${Math.max(16, left)}px`;
  }, [abierta]);

  const abrir = () => abrirMenu({ tipo: 'categoria', categoria });

  return (
    <div
      ref={raiz}
      className={`nav-cat ${abierta ? 'open' : ''}`}
      onMouseEnter={() => { if (!esTactil()) abrir(); }}
      onMouseLeave={() => { if (!esTactil()) cerrarMenuConRetraso(); }}
    >
      <button
        type="button"
        className="nav-link nav-cat-trigger"
        aria-haspopup="true"
        aria-expanded={abierta}
        onClick={() => { if (abierta && esTactil()) cerrarMenus(); else abrir(); }}
      >
        {NOMBRE_CATEGORIA[categoria]}
      </button>
      <div className="nav-cat-panel" ref={panel}>
        {abierta && <ContenidoCategoria categoria={categoria} alCerrarMenu={alCerrarMenu} />}
      </div>
    </div>
  );
}

export function NavCategorias({ alElegir }: { alElegir: () => void }) {
  const alCerrarMenu = () => { cerrarMenus(); alElegir(); };
  return (
    <div className="nav-categorias" id="nav-categorias">
      {CATEGORIAS_PRODUCTO.map((c) => <NavCategoria key={c} categoria={c} alCerrarMenu={alCerrarMenu} />)}
    </div>
  );
}
