// Qué está abierto en el navbar y en el panel de búsqueda. Es un estado compartido porque lo abren botones de otras partes
// de la página (los "Ver categoría" de la landing, "Ver todos los productos"…).
export type MenuAbierto = { tipo: 'categoria'; categoria: string } | { tipo: 'cuenta' } | null;

export interface PanelBusqueda {
  abierto: boolean;
  categoria: string;
  explorar: boolean;
  apertura: number; // cambia en cada apertura, para volver a empezar aunque se pida lo mismo
}

let menu: MenuAbierto = null;
let busqueda: PanelBusqueda = { abierto: false, categoria: '', explorar: false, apertura: 0 };
let temporizadorCierre: ReturnType<typeof setTimeout> | undefined;
const oyentes = new Set<() => void>();

function avisar(): void {
  oyentes.forEach((o) => o());
}

export function suscribirMenus(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => { oyentes.delete(oyente); };
}

export const leerMenu = (): MenuAbierto => menu;
export const leerBusqueda = (): PanelBusqueda => busqueda;

export function abrirMenu(nuevo: MenuAbierto): void {
  clearTimeout(temporizadorCierre);
  menu = nuevo;
  avisar();
}

export function cerrarMenus(): void {
  abrirMenu(null);
}

// Con un pequeño retraso: mover el puntero del botón a su panel no debe cerrarlo (evita el parpadeo).
export function cerrarMenuConRetraso(): void {
  clearTimeout(temporizadorCierre);
  temporizadorCierre = setTimeout(cerrarMenus, 180);
}

// categoria: abre ya filtrado ("Ver todos" del menú de categorías); explorar: abre con el catálogo completo ("Ver todo el
// catálogo"). Sin argumentos: recomendaciones.
export function abrirPanelBusqueda(categoria = '', { explorar = false } = {}): void {
  clearTimeout(temporizadorCierre);
  menu = null;
  busqueda = { abierto: true, categoria, explorar: explorar || Boolean(categoria), apertura: busqueda.apertura + 1 };
  avisar();
}

export function cerrarPanelBusqueda(): void {
  busqueda = { ...busqueda, abierto: false };
  avisar();
}
