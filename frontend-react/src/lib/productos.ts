import type { UnidadAlternativa } from '../api/productos';

// Las 8 categorías fijas del catálogo, cada una con su ícono propio.
export const CATEGORIAS_PRODUCTO = ['fruta', 'verdura', 'tuberculo', 'grano', 'legumbre', 'lacteo', 'huevo', 'hierba'] as const;
export type Categoria = (typeof CATEGORIAS_PRODUCTO)[number];

export const ICONOS_CATEGORIA: Record<Categoria, string> = {
  fruta: '🍎',
  verdura: '🥬',
  tuberculo: '🥔',
  grano: '🌾',
  legumbre: '🫘',
  lacteo: '🥛',
  huevo: '🥚',
  hierba: '🌿',
};

export const NOMBRE_CATEGORIA: Record<Categoria, string> = {
  fruta: 'Fruta',
  verdura: 'Verdura',
  tuberculo: 'Tubérculo',
  grano: 'Grano',
  legumbre: 'Legumbre',
  lacteo: 'Lácteo',
  huevo: 'Huevo',
  hierba: 'Hierba',
};

// Normaliza variantes/typos de datos existentes (p.ej. "tuberculos", "cereales") a la categoría canónica.
export function normalizarCategoria(categoria: string | null | undefined): Categoria | null {
  const c = String(categoria || '').toLowerCase().trim();
  if (c.startsWith('tuber')) return 'tuberculo';
  if (c.startsWith('cerea') || c.startsWith('gran')) return 'grano';
  if (c.startsWith('verdur')) return 'verdura';
  if (c.startsWith('frut')) return 'fruta';
  if (c.startsWith('legum')) return 'legumbre';
  if (c.startsWith('lact')) return 'lacteo';
  if (c.startsWith('huev')) return 'huevo';
  if (c.startsWith('hierb')) return 'hierba';
  return (CATEGORIAS_PRODUCTO as readonly string[]).includes(c) ? (c as Categoria) : null;
}

export function iconoCategoria(categoria: string | null | undefined): string {
  const cat = normalizarCategoria(categoria);
  return cat ? ICONOS_CATEGORIA[cat] : '🧺';
}

export function nombreCategoria(categoria: string | null | undefined): string {
  const cat = normalizarCategoria(categoria);
  return (cat && NOMBRE_CATEGORIA[cat]) || categoria || 'General';
}

// Lo que comparten las tres formas en que la API entrega un producto.
export interface ProductoConPrecio {
  precio: number | null;
  stock: number | null;
  unidad_medida: string | null;
  unidades_alternativas?: UnidadAlternativa[];
}

// Única fuente de las unidades de medida válidas. "esBase": unidad chica/granular válida como unidad_medida de una
// cosecha (kg, unidad, litro); las de empaque grande (saco, arroba) son compra por mayor.
const UNIDADES_MEDIDA: Record<string, { singular: string; plural: string; etiqueta: string; label: string; esBase: boolean }> = {
  kg: { singular: 'kg', plural: 'kg', etiqueta: 'kg', label: 'Kilogramo (kg)', esBase: true },
  unidad: { singular: 'unidad', plural: 'unidades', etiqueta: 'unidades', label: 'Unidad', esBase: true },
  saco: { singular: 'saco', plural: 'sacos', etiqueta: 'sacos', label: 'Saco', esBase: false },
  arroba: { singular: 'arroba', plural: 'arrobas', etiqueta: 'arrobas', label: 'Arroba', esBase: false },
  litro: { singular: 'litro', plural: 'litros', etiqueta: 'litros', label: 'Litro', esBase: true },
};

export function infoUnidad(codigo: string | null | undefined) {
  return (codigo && UNIDADES_MEDIDA[codigo]) || UNIDADES_MEDIDA.unidad!;
}

export function unidadPlural(codigo: string | null | undefined, cantidad: number): string {
  const info = infoUnidad(codigo);
  return cantidad === 1 ? info.singular : info.plural;
}

export function unidadCorta(codigo: string | null | undefined): string {
  return infoUnidad(codigo).singular;
}

export function unidadEtiqueta(codigo: string | null | undefined): string {
  return infoUnidad(codigo).etiqueta;
}

// Precio/stock según la unidad de compra elegida: null significa "unidad base" (precio/stock del producto tal cual).
function unidadAlternativaDe(producto: ProductoConPrecio, unidad: string | null): UnidadAlternativa | null {
  if (!unidad) return null;
  return (producto.unidades_alternativas || []).find((u) => u.unidad === unidad) || null;
}

export function precioEnUnidad(producto: ProductoConPrecio, unidad: string | null): number {
  const alt = unidadAlternativaDe(producto, unidad);
  return Number(alt ? alt.precio : producto.precio);
}

// Stock disponible EXPRESADO en la unidad elegida. Math.floor a propósito: no tiene sentido ofrecer "2.7 sacos" como
// tope si sobran kg pero no alcanzan para un saco completo más.
export function stockEnUnidad(producto: ProductoConPrecio, unidad: string | null): number {
  const alt = unidadAlternativaDe(producto, unidad);
  if (!alt) return producto.stock ?? 0;
  return Math.floor((producto.stock ?? 0) / Number(alt.factor_a_base));
}

// El productor pone el precio del saco o la arroba; aquí se muestra con lo que cuesta cada kg (o la unidad base) y cuánto
// se ahorra frente a comprar suelto. Si el saco no sale más barato, igual es compra por mayor, pero sin "ahorras".
export function precioPorMayor(producto: ProductoConPrecio, alt: UnidadAlternativa) {
  const factor = Number(alt.factor_a_base) || 1;
  const porBase = Number(alt.precio) / factor;
  const base = Number(producto.precio) || 0;
  const ahorro = base > 0 ? Math.round((1 - porBase / base) * 100) : 0;
  return { porBase, ahorro: ahorro >= 1 ? ahorro : 0, factor };
}

// Solo las unidades de empaque (saco, arroba) son compra por mayor.
export function esUnidadPorMayor(alt: UnidadAlternativa): boolean {
  return !infoUnidad(alt.unidad).esBase;
}

// La unidad por mayor más conveniente que tenga stock (la de menor precio por kg), o null.
export function mejorUnidadPorMayor(producto: ProductoConPrecio) {
  return (producto.unidades_alternativas || [])
    .filter((alt) => esUnidadPorMayor(alt) && stockEnUnidad(producto, alt.unidad) > 0)
    .map((alt) => ({ alt, ...precioPorMayor(producto, alt) }))
    .sort((a, b) => a.porBase - b.porBase)[0] || null;
}

export function nombreUnidadPorMayor(producto: ProductoConPrecio, alt: UnidadAlternativa): string {
  const factor = Number(alt.factor_a_base) || 1;
  const cantidad = Number.isInteger(factor) ? factor : String(factor).replace('.', ',');
  return `${infoUnidad(alt.unidad).label} de ${cantidad} ${unidadPlural(producto.unidad_medida, factor)}`;
}

// Estado del producto para la insignia de su tarjeta: en camino al almacén, agotado o pocas unidades.
export function insigniaProducto(p: { estado?: string; stock: number | null }) {
  if (p.estado === 'en_transito') return { clase: 'transito', texto: 'En tránsito' };
  if ((p.stock ?? 0) <= 0) return { clase: 'agotado', texto: 'Agotado' };
  if ((p.stock ?? 0) <= 5) return { clase: 'pocas', texto: 'Últimas unidades' };
  return null;
}

interface ProductoOrdenable {
  nombre: string;
  categoria: string | null;
  calificacion_promedio?: number | null;
  total_resenas?: number;
}

// Un solo criterio de "esto es dato de QA/pruebas" en toda la app.
function esProductoDePrueba(p: ProductoOrdenable): boolean {
  return /\btest\b|\bqa\b/i.test(`${p.nombre} ${p.categoria}`);
}

export function sinProductosDePrueba<T extends ProductoOrdenable>(productos: T[]): T[] {
  return productos.filter((p) => !esProductoDePrueba(p));
}

// "Lo destacado": QA fuera, mejor calificado primero (sin calificaciones aún, el sort es estable y conserva el orden del
// catálogo).
export function ordenarPorDestacado<T extends ProductoOrdenable>(productos: T[]): T[] {
  const candidatos = sinProductosDePrueba(productos);
  const fuente = candidatos.length >= 3 ? candidatos : productos;
  return [...fuente]
    .sort((a, b) => (b.calificacion_promedio || 0) - (a.calificacion_promedio || 0) || (b.total_resenas || 0) - (a.total_resenas || 0));
}

export function normalizarTexto(s: string | null | undefined): string {
  return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
}

// Cada casillero de un banner puede traer varios nombres separados por coma como alternativas para el mismo lugar: se
// usa el primero que exista en el catálogo real.
export function buscarProductoPorNombre<T extends { nombre: string }>(productos: T[], nombresCsv: string): T | null {
  const alternativas = nombresCsv.split(',').map((s) => s.trim()).filter(Boolean);
  for (const alt of alternativas) {
    const objetivo = normalizarTexto(alt);
    const match = productos.find((p) => {
      const n = normalizarTexto(p.nombre);
      return n === objetivo || n.includes(objetivo) || objetivo.includes(n);
    });
    if (match) return match;
  }
  return null;
}

export function slugProducto(nombre: string): string {
  return normalizarTexto(nombre).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

export function hayCoordenadas(lat: unknown, lng: unknown): boolean {
  return lat != null && lng != null && lat !== '' && lng !== '' && !isNaN(Number(lat)) && !isNaN(Number(lng));
}
