// El carrito vive solo en memoria, como en el frontend actual: no tiene datos personales y se conserva si la sesión
// vence (el usuario suele volver a entrar), pero se vacía al cerrar sesión a propósito.
export interface LineaCarrito {
  producto_id: string;
  nombre: string;
  precio: number;
  cantidad: number;
  stockDisponible: number;
  unidad?: string | null;
}

let lineas: LineaCarrito[] = [];
const oyentes = new Set<() => void>();

export const leerCarrito = (): LineaCarrito[] => lineas;

export function suscribirCarrito(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => { oyentes.delete(oyente); };
}

export function guardarCarrito(nuevas: LineaCarrito[]): void {
  lineas = nuevas;
  oyentes.forEach((o) => o());
}

export function vaciarCarrito(): void {
  guardarCarrito([]);
}
