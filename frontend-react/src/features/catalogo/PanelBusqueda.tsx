import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent } from 'react';
import { atraparFoco } from '../../lib/foco';
import { productos as apiProductos, type ProductoCatalogo } from '../../api/productos';
import { CATEGORIAS_PRODUCTO, ICONOS_CATEGORIA, NOMBRE_CATEGORIA, normalizarCategoria, ordenarPorDestacado, type Categoria } from '../../lib/productos';
import { cerrarPanelBusqueda, leerBusqueda, suscribirMenus } from './menus';
import { ProductoMega } from './TarjetasProducto';
import { useAbrirProducto, useCatalogo } from './useCatalogo';

// Menor = más relevante: primero los que empiezan con lo escrito, luego los que lo contienen en el nombre y al final los
// que solo coinciden por categoría o productor.
function puntuarCoincidencia(p: ProductoCatalogo, texto: string): number {
  const nombre = String(p.nombre || '').toLowerCase();
  if (nombre.startsWith(texto)) return 0;
  if (nombre.includes(texto)) return 1;
  return 2;
}

const ORDENES_BUSQUEDA: Record<string, (a: ProductoCatalogo, b: ProductoCatalogo) => number> = {
  'precio-asc': (a, b) => Number(a.precio) - Number(b.precio),
  'precio-desc': (a, b) => Number(b.precio) - Number(a.precio),
  calificacion: (a, b) => (b.calificacion_promedio || 0) - (a.calificacion_promedio || 0) || (b.total_resenas || 0) - (a.total_resenas || 0),
};

export function PanelBusqueda() {
  const panel = useSyncExternalStore(suscribirMenus, leerBusqueda);
  const { data: catalogo } = useCatalogo(panel.abierto);
  const abrirProducto = useAbrirProducto();
  const [texto, setTexto] = useState('');
  const [categoria, setCategoria] = useState('');
  const [explorando, setExplorando] = useState(false);
  const [orden, setOrden] = useState('relevancia');
  const [populares, setPopulares] = useState<{ termino: string; total: number }[]>([]);
  const entrada = useRef<HTMLInputElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const chips = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const focoPrevio = useRef<HTMLElement | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (!panel.abierto) return undefined;
    focoPrevio.current = document.activeElement as HTMLElement | null;
    setCategoria(panel.categoria);
    setExplorando(panel.explorar);
    setOrden('relevancia');
    setTexto('');
    if (overlay.current) overlay.current.scrollTop = 0;
    entrada.current?.focus();
    // Término real más buscado en los últimos 7 días. Se carga una vez al abrir y queda fija aunque el usuario escriba.
    let vigente = true;
    apiProductos.busquedasPopulares().then((lista) => { if (vigente) setPopulares(lista); }).catch(() => { if (vigente) setPopulares([]); });
    return () => { vigente = false; };
    // La apertura es lo que reinicia el panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel.apertura, panel.abierto]);

  function cerrar() {
    cerrarPanelBusqueda();
    if (focoPrevio.current?.isConnected) focoPrevio.current.focus?.();
    focoPrevio.current = null;
  }

  useEffect(() => {
    if (!panel.abierto) return undefined;
    const alEscape = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') cerrar(); };
    document.addEventListener('keydown', alEscape);
    return () => document.removeEventListener('keydown', alEscape);
  }, [panel.abierto]);

  // En móvil las categorías van en una fila desplazable: centra la activa. Se mueve solo esa fila.
  useLayoutEffect(() => {
    const cont = chips.current;
    const activo = cont?.querySelector<HTMLElement>('.search-chip.active');
    if (cont && activo && cont.scrollWidth > cont.clientWidth) {
      const izquierda = activo.getBoundingClientRect().left - cont.getBoundingClientRect().left + cont.scrollLeft;
      cont.scrollLeft = izquierda - (cont.clientWidth - activo.offsetWidth) / 2;
    }
  }, [categoria, panel.abierto]);

  // Sin bloquear ni avisar: si falla no afecta la búsqueda. Con retraso para no registrar cada tecla suelta.
  function registrarBusqueda(termino: string) {
    clearTimeout(debounce.current);
    if (termino.trim().length < 2) return;
    debounce.current = setTimeout(() => { apiProductos.registrarBusqueda(termino.trim()).catch(() => {}); }, 600);
  }

  function cambiarTexto(valor: string) {
    setTexto(valor);
    if (valor.trim()) registrarBusqueda(valor.trim().toLowerCase());
    else clearTimeout(debounce.current);
  }

  const { resultados, titulo, icono } = useMemo(() => {
    const original = texto.trim();
    const busqueda = original.toLowerCase();
    let lista = catalogo || [];
    if (categoria) lista = lista.filter((p) => normalizarCategoria(p.categoria) === categoria);
    if (busqueda) lista = lista.filter((p) => `${p.nombre} ${p.categoria || ''} ${p.productor_nombre || ''}`.toLowerCase().includes(busqueda));

    let tituloNuevo: string;
    let iconoNuevo: string;
    if (busqueda) {
      tituloNuevo = `Resultados para "${original}"`;
      iconoNuevo = 'search';
      lista = [...lista].sort((a, b) => puntuarCoincidencia(a, busqueda) - puntuarCoincidencia(b, busqueda));
    } else if (categoria) {
      tituloNuevo = `Categoría: ${NOMBRE_CATEGORIA[categoria as Categoria]}`;
      iconoNuevo = 'category';
      lista = ordenarPorDestacado(lista);
    } else if (explorando) {
      tituloNuevo = 'Todo el catálogo';
      iconoNuevo = 'building-store';
      lista = ordenarPorDestacado(lista);
    } else {
      tituloNuevo = 'Recomendaciones';
      iconoNuevo = 'sparkles';
      lista = ordenarPorDestacado(lista).slice(0, 8);
    }
    const comparar = ORDENES_BUSQUEDA[orden];
    if (comparar) lista = [...lista].sort(comparar);
    return { resultados: lista, titulo: tituloNuevo, icono: iconoNuevo };
  }, [catalogo, categoria, explorando, orden, texto]);

  function alTeclaEntrada(e: KeyboardEvent<HTMLInputElement>) {
    const primero = grid.current?.querySelector<HTMLElement>('.nav-mega-producto');
    if (e.key === 'Enter' && primero) { e.preventDefault(); primero.click(); }
    if (e.key === 'ArrowDown' && primero) { e.preventDefault(); primero.focus(); }
  }

  const chip = (valor: string, etiqueta: string, emoji?: string) => (
    <button
      key={valor}
      type="button"
      className={`search-chip ${categoria === valor ? 'active' : ''}`}
      aria-pressed={categoria === valor}
      onClick={() => { setCategoria(valor); setExplorando(true); }} // elegir una categoría es explorar, no ver recomendaciones
    >
      {emoji && <><span aria-hidden="true">{emoji}</span>{' '}</>}{etiqueta}
    </button>
  );

  const termino = texto.trim();

  return (
    <div
      ref={overlay}
      className={`search-overlay ${panel.abierto ? '' : 'hidden'}`}
      id="panel-busqueda"
      onClick={(e) => { if (e.target === e.currentTarget) cerrar(); }}
      onKeyDown={atraparFoco}
    >
      <div className="search-panel" role="dialog" aria-modal="true" aria-label="Buscar productos">
        <button type="button" className="modal-close" aria-label="Cerrar búsqueda" onClick={cerrar}><i className="ti ti-x" /></button>
        <div className="search-panel-input-wrap">
          <i className="ti ti-search search-panel-icon" aria-hidden="true" />
          <input
            ref={entrada}
            type="text"
            id="input-busqueda-panel"
            className="search-panel-input"
            placeholder="Busca papa, quinua, queso o un productor…"
            aria-label="Buscar por producto, categoría o productor"
            autoComplete="off"
            enterKeyHint="search"
            value={texto}
            onChange={(e) => cambiarTexto(e.target.value)}
            onKeyDown={alTeclaEntrada}
          />
          <button type="button" className={`search-panel-limpiar ${texto ? '' : 'hidden'}`} aria-label="Borrar búsqueda" onClick={() => { cambiarTexto(''); entrada.current?.focus(); }}><i className="ti ti-x" /></button>
        </div>
        <div className="search-panel-chips" ref={chips} role="group" aria-label="Filtrar por categoría">
          {chip('', 'Todo')}
          {CATEGORIAS_PRODUCTO.map((c) => chip(c, NOMBRE_CATEGORIA[c], ICONOS_CATEGORIA[c]))}
        </div>
        <div className="search-panel-body">
          <div className={`search-panel-populares ${populares.length ? '' : 'hidden'}`}>
            <span className="search-panel-populares-titulo">Búsquedas populares</span>
            <div>
              {populares.map((p) => (
                <button key={p.termino} type="button" className="search-panel-popular-item" onClick={() => { cambiarTexto(p.termino); entrada.current?.focus(); }}>{p.termino}</button>
              ))}
            </div>
          </div>
          <div className="search-panel-resultados">
            <div className="search-panel-barra">
              <h3 className="search-panel-heading"><i className={`ti ti-${icono}`} /> {titulo}</h3>
              <div className="search-panel-controles">
                <span className="search-panel-conteo" role="status" aria-live="polite">{resultados.length ? `${resultados.length} producto${resultados.length === 1 ? '' : 's'}` : ''}</span>
                <label className="search-panel-orden">
                  <span className="mp-solo-lectores">Ordenar por</span>
                  <select value={orden} onChange={(e) => setOrden(e.target.value)}>
                    <option value="relevancia">Más relevantes</option>
                    <option value="precio-asc">Precio: menor a mayor</option>
                    <option value="precio-desc">Precio: mayor a menor</option>
                    <option value="calificacion">Mejor calificados</option>
                  </select>
                </label>
              </div>
            </div>
            <div className="search-panel-grid" ref={grid}>
              {resultados.map((p) => <ProductoMega key={p.id} producto={p} onClick={() => { cerrar(); abrirProducto(p.id); }} />)}
            </div>
            {catalogo && !resultados.length && (
              <div className="search-panel-vacio">
                <span className="search-vacio-icono"><i className="ti ti-search-off" aria-hidden="true" /></span>
                <strong>{termino ? `No encontramos productos para "${termino}"` : 'No hay productos en esta categoría por ahora'}</strong>
                <p>Revisa la ortografía o prueba con otra categoría.</p>
                <button type="button" className="btn btn-outline" onClick={() => { cambiarTexto(''); setCategoria(''); setExplorando(true); }}>Ver todo el catálogo</button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
