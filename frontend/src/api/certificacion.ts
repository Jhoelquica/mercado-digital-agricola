import { apiBase, request } from './client';

export interface VerificacionCadena {
  valido: boolean;
  total_bloques: number;
  mensaje: string;
}

export interface Bloque {
  id: string;
  producto_id: string;
  indice: number;
  evento: string;
  datos: string | null;
  fecha: string | null;
  hash_anterior: string;
  hash_actual: string;
  verificador_id: string | null;
  verificador_nombre: string | null;
  verificador_credencial: string | null;
  foto_url: string | null;
}

export interface CertificadoPedido {
  pedido_id: string;
  merkle_root: string;
  detalle: { producto_id: string; hash: string }[];
  fecha_generado: string | null;
}

export const certificacion = {
  verificar: (productoId: string) => request<VerificacionCadena>('certificacion', `/certificacion/${productoId}/verificar`),
  historial: (productoId: string) => request<Bloque[]>('certificacion', `/certificacion/${productoId}/historial`),
  qrUrl: (productoId: string) => `${apiBase('certificacion')}/certificacion/${productoId}/qr`,
  certificadoPedido: (pedidoId: string) => request<CertificadoPedido>('certificacion', `/certificacion/pedido/${pedidoId}/certificado`),
};
