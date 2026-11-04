import { toast } from '../../components/Toast';
import { precioEnUnidad, stockEnUnidad, type ProductoConPrecio } from '../../lib/productos';

// El carrito vive solo en memoria, como en el frontend actual: no tiene datos personales y se conserva si la sesión
// vence (el usuario suele volver a entrar), pero se vacía al cerrar sesión a propósito.
export interface LineaCarrito {
  producto_id: string;
  nombre: string;
  // Solo para mostrar: Pedidos vuelve a resolver el precio real contra Productos al confirmar.
  precio: number;
  cantidad: number;
  stockDisponible: number;
  // null = unidad base del producto. Dos líneas del mismo producto en unidades distintas no se combinan.
  unidad: string | null;
  // Unidad efectiva de la línea (la base o la alternativa).
  unidad_medida: string | null;
}

interface ProductoParaCarrito extends ProductoConPrecio {
  id: string;
  nombre: string;
  estado?: string | null;
}

let lineas: LineaCarrito[] = [];
// Sobrevive a cerrar y volver a abrir el checkout, igual que el campo del formulario en el frontend actual.
let telefonoDeEntrega = '';
const oyentes = new Set<() => void>();

export const leerCarrito = (): LineaCarrito[] => lineas;
export const leerTelefonoDeEntrega = (): string => telefonoDeEntrega;

export function guardarTelefonoDeEntrega(telefono: string): void {
  telefonoDeEntrega = telefono;
}

export function suscribirCarrito(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => { oyentes.delete(oyente); };
}

export function guardarCarrito(nuevas: LineaCarrito[]): void {
  lineas = nuevas;
  oyentes.forEach((o) => o());
}

export function vaciarCarrito(): void {
  telefonoDeEntrega = '';
  guardarCarrito([]);
}

const esLaLinea = (linea: LineaCarrito, productoId: string, unidad: string | null) => linea.producto_id === productoId && linea.unidad === unidad;

export function agregarLinea(producto: ProductoParaCarrito, cantidad = 1, unidad: string | null = null): void {
  if (producto.estado && producto.estado !== 'disponible') {
    toast('Este producto todavía viene en camino: resérvalo y te avisamos cuando llegue.', 'error');
    return;
  }
  const stockMaximo = stockEnUnidad(producto, unidad);
  const existente = lineas.find((l) => esLaLinea(l, producto.id, unidad));
  if (existente) {
    const nuevaCantidad = Math.min(existente.cantidad + cantidad, stockMaximo);
    if (nuevaCantidad === existente.cantidad) toast('No hay más stock disponible de este producto.', 'error');
    guardarCarrito(lineas.map((l) => (l === existente ? { ...l, cantidad: nuevaCantidad } : l)));
  } else {
    guardarCarrito([...lineas, {
      producto_id: producto.id,
      nombre: producto.nombre,
      precio: precioEnUnidad(producto, unidad),
      cantidad: Math.min(cantidad, stockMaximo),
      stockDisponible: stockMaximo,
      unidad,
      unidad_medida: unidad || producto.unidad_medida || null,
    }]);
  }
  toast(`${producto.nombre} agregado al pedido 🧺`);
}

export function cambiarCantidad(productoId: string, delta: number, unidad: string | null): void {
  const linea = lineas.find((l) => esLaLinea(l, productoId, unidad));
  if (!linea) return;
  const cantidad = linea.cantidad + delta;
  if (cantidad <= 0) {
    quitarLinea(productoId, unidad);
  } else if (cantidad > linea.stockDisponible) {
    guardarCarrito(lineas.map((l) => (l === linea ? { ...l, cantidad: linea.stockDisponible } : l)));
    toast('Alcanzaste el stock máximo disponible.', 'error');
  } else {
    guardarCarrito(lineas.map((l) => (l === linea ? { ...l, cantidad } : l)));
  }
}

export function quitarLinea(productoId: string, unidad: string | null): void {
  guardarCarrito(lineas.filter((l) => !esLaLinea(l, productoId, unidad)));
}

export const subtotalCarrito = (carrito: LineaCarrito[]): number => carrito.reduce((total, l) => total + l.precio * l.cantidad, 0);

// Lo que se manda a Pedidos: sin precio (lo resuelve el servidor) y con la unidad solo si no es la base.
export const itemsParaPedido = (carrito: LineaCarrito[]) => carrito.map((l) => (l.unidad
  ? { producto_id: l.producto_id, cantidad: l.cantidad, unidad: l.unidad }
  : { producto_id: l.producto_id, cantidad: l.cantidad }));
