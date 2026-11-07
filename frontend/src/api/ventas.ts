import { request } from './client';

export interface CifrasDeVenta {
  soles: number;
  kg: number;
  pedidos: number;
  compradores: number;
  ticket_promedio: number;
}

export interface VentaDeProducto {
  producto_id: string;
  nombre: string | null;
  productor_id: string | null;
  soles: number;
  kg: number;
  pedidos: number;
  precio_promedio_kg: number | null;
  soles_por_mes: number[];
  kg_por_mes: number[];
}

export interface ResumenVentas<PorMes extends CifrasDeVenta = CifrasDeVenta> {
  meses: string[];
  por_mes: (PorMes & { mes: string })[];
  productos: VentaDeProducto[];
  totales: CifrasDeVenta & { compradores_recurrentes: number };
  comparativa: {
    actual: CifrasDeVenta & { mes: string };
    anterior: CifrasDeVenta & { mes: string };
    anio_pasado: CifrasDeVenta & { mes: string };
  };
  por_destino: { nombre: string; soles: number }[];
  por_tipo: { nombre: string; soles: number }[];
}

export interface CifrasDeVentaAdmin extends CifrasDeVenta {
  compradores_nuevos: number;
  cancelados_devueltos: number;
  sin_pagar: number;
  soles_envio: number;
}

export interface VentasPlataforma extends ResumenVentas<CifrasDeVentaAdmin> {
  productores: { productor_id: string; soles: number; kg: number; pedidos: number }[];
}

// Ventas pagadas por mes y por producto: las del agricultor con sesión, o las de toda la plataforma (admin).
export const ventas = {
  mias: (meses: number) => request<ResumenVentas>('pedidos', `/ventas/mias?meses=${meses}`, { auth: true }),
  admin: (meses: number) => request<VentasPlataforma>('pedidos', `/ventas/admin?meses=${meses}`, { auth: true }),
};
