import { request, requestFormData } from './client';
import type { components } from './esquemas/productos';
import { exigirImagenLiviana } from './errors';
import { normalizarImagenesDeProducto, normalizarUrlImagen } from './imagenes';

type Esquemas = components['schemas'];

export interface UnidadAlternativa {
  id: string;
  unidad: string;
  precio: number;
  factor_a_base: number;
}

export interface UnidadAlternativaGuardada extends UnidadAlternativa {
  producto_id: string;
}

export interface ImagenProducto {
  id: string;
  url: string;
  orden: number | null;
}

// Decimal llega como número, también desde la caché (ver productos/cache.py).
interface ProductoBase {
  id: string;
  productor_id: string;
  productor_nombre: string;
  nombre: string;
  categoria: string | null;
  precio: number | null;
  stock: number | null;
  unidad_medida: string | null;
  imagen_url: string | null;
  fecha_publicacion: string | null;
  estado: string;
  unidades_permitidas: string[] | null;
}

export interface ProductoCatalogo extends ProductoBase {
  equivalencia_kg: number | null;
  imagen_principal: string | null;
  calificacion_promedio: number | null;
  total_resenas: number;
  unidades_alternativas: UnidadAlternativa[];
}

export interface ProductoMio extends ProductoBase {
  imagen_principal: string | null;
  registro_produccion_id: string | null;
  descripcion: string | null;
}

export interface ProductoDetalle extends ProductoBase {
  descripcion: string | null;
  equivalencia_kg: number | null;
  imagenes: ImagenProducto[];
  calificacion_promedio: number | null;
  total_resenas: number;
  unidades_alternativas: UnidadAlternativa[];
}

// Las acciones sobre un producto devuelven la fila de la base. unidades_permitidas viene como texto separado por comas.
export interface ProductoFila {
  id: string;
  productor_id: string;
  productor_nombre: string;
  nombre: string;
  descripcion: string | null;
  unidades_permitidas: string | null;
  imagen_url: string | null;
  categoria: string | null;
  precio: number | null;
  stock: number | null;
  unidad_medida: string | null;
  fecha_publicacion: string | null;
  estado: string;
  registro_produccion_id: string | null;
  equivalencia_kg: number | null;
}

export interface Resena {
  id: string;
  producto_id: string;
  usuario_id: string;
  usuario_nombre: string;
  calificacion: number;
  comentario: string | null;
  fecha_creacion: string | null;
}

export interface Reserva {
  id: string;
  producto_id: string;
  comprador_id: string;
  fecha_creacion: string | null;
  cantidad: number | null;
  unidad: string | null;
}

export interface MisReservas {
  productos: string[];
  reservas: { producto_id: string; cantidad: number | null; unidad: string | null }[];
}

export interface PrecioReferencia {
  nombre_producto: string;
  precio_promedio: number | null;
  mensaje?: string;
}

export const productos = {
  listar: () => request<ProductoCatalogo[]>('productos', '/productos').then((lista) => lista.map(normalizarImagenesDeProducto)),
  mios: () => request<ProductoMio[]>('productos', '/productos/mios', { auth: true }).then((lista) => lista.map(normalizarImagenesDeProducto)),
  // Con sesión si la hay: el detalle de un borrador solo lo devuelve a su productor (y al admin).
  obtener: (id: string) => request<ProductoDetalle>('productos', `/productos/${id}`, { auth: 'opcional' }).then(normalizarImagenesDeProducto),
  precioReferencia: (nombre: string) => request<PrecioReferencia>('productos', `/productos/precio-referencia/${encodeURIComponent(nombre)}`),
  actualizar: (id: string, datos: Esquemas['ProductoActualizar']) => request<ProductoFila>('productos', `/productos/${id}`, { method: 'PATCH', body: datos, auth: true }),
  publicar: (id: string) => request<ProductoFila>('productos', `/productos/${id}/publicar`, { method: 'PATCH', auth: true }),
  // El Admin dice cuánto llegó de verdad (entero, no más de lo declarado) y, si quiere, una observación:
  // el servidor deja ambos escritos en la cadena de certificación del producto.
  confirmarLlegadaAlmacen: (id: string, cantidadRecibida: number, observaciones = '') => request<ProductoFila>('productos', `/productos/${id}/confirmar-llegada-almacen`, { method: 'PATCH', body: { cantidad_recibida: cantidadRecibida, observaciones: observaciones || null } satisfies Esquemas['LlegadaAlmacen'], auth: true }),
  reservas: (id: string) => request<Reserva[]>('productos', `/productos/${id}/reservas`, { auth: true }),
  // Comprador: reservar lo que todavía viene en camino (sin pagar; se le avisa cuando llega), ver qué reservó y
  // quitar su reserva ("ya no me avises").
  // datos (opcional, H7): { cantidad, unidad } — cuánto le interesa; sin datos es solo el aviso.
  reservar: (id: string, datos?: Esquemas['ReservaCrear']) => request<Reserva>('productos', `/productos/${id}/reservar`, { method: 'POST', body: datos || undefined, auth: true }),
  misReservas: () => request<MisReservas>('productos', '/productos/mis-reservas', { auth: true }),
  quitarReserva: (id: string) => request<{ mensaje: string }>('productos', `/productos/${id}/reservar`, { method: 'DELETE', auth: true }),
  listarResenas: (id: string) => request<Resena[]>('productos', `/productos/${id}/resenas`),
  crearResena: (id: string, datos: Esquemas['ResenaCrear']) => request<Resena>('productos', `/productos/${id}/resenas`, { method: 'POST', body: datos, auth: true }),
  subirImagen: (id: string, archivo: File, orden = 0) => {
    exigirImagenLiviana(archivo);
    const formData = new FormData();
    formData.append('archivo', archivo);
    return requestFormData<ImagenProducto & { producto_id: string }>('productos', `/productos/${id}/imagenes/subir?orden=${orden}`, formData).then((img) => {
      if (img.url) img.url = normalizarUrlImagen(img.url);
      return img;
    });
  },
  eliminarImagen: (imagenId: string) => request<{ mensaje: string }>('productos', `/productos/imagenes/${imagenId}`, { method: 'DELETE', auth: true }),
  actualizarOrdenImagen: (imagenId: string, orden: number) => request<ImagenProducto & { producto_id: string }>('productos', `/productos/imagenes/${imagenId}/orden`, { method: 'PATCH', body: { orden } satisfies Esquemas['OrdenImagen'], auth: true }),
  unidades: {
    listar: (id: string) => request<UnidadAlternativaGuardada[]>('productos', `/productos/${id}/unidades`, { auth: 'opcional' }),
    crear: (id: string, datos: Esquemas['UnidadAlternativaCrear']) => request<UnidadAlternativaGuardada>('productos', `/productos/${id}/unidades`, { method: 'POST', body: datos, auth: true }),
    editar: (unidadId: string, datos: Esquemas['UnidadAlternativaActualizar']) => request<UnidadAlternativaGuardada>('productos', `/productos/unidades/${unidadId}`, { method: 'PATCH', body: datos, auth: true }),
    eliminar: (unidadId: string) => request<{ mensaje: string }>('productos', `/productos/unidades/${unidadId}`, { method: 'DELETE', auth: true }),
  },
  registrarBusqueda: (termino: string) => request<{ registrado: boolean }>('productos', '/productos/busquedas/registrar', { method: 'POST', body: { termino } satisfies Esquemas['BusquedaRegistrar'] }),
  busquedasPopulares: () => request<{ termino: string; total: number }[]>('productos', '/productos/busquedas/populares'),
};
