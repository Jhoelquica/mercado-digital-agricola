// Reglas puras del pedido sin pagar (sin React ni red): qué carrito es "el mismo pedido" y hasta qué hora se guarda.
// Pedidos admite un solo pedido sin pagar por comprador: el mismo carrito lo retoma y otro recibe 409 con ese pedido.
import { parsearFechaServidor } from './formato';

export interface LineaDePedido {
  producto_id: string;
  // null o ausente: la unidad base del producto.
  unidad?: string | null;
  cantidad: number;
}

const clave = (linea: LineaDePedido) => `${linea.producto_id}|${linea.unidad || ''}|${Number(linea.cantidad)}`;

// El mismo criterio que Pedidos (_lineas en pedidos/main.py): producto, unidad y cantidad, en cualquier orden.
export function mismasLineas(a: readonly LineaDePedido[], b: readonly LineaDePedido[]): boolean {
  if (a.length !== b.length) return false;
  const x = a.map(clave).sort();
  const y = b.map(clave).sort();
  return x.every((valor, i) => valor === y[i]);
}

// "18:30": la hora en Lima, sea cual sea la zona del navegador.
export function horaDeLima(momento: Date): string {
  return momento.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'America/Lima' });
}

// Hasta cuándo se guarda un pedido sin pagar: el plazo GUARDADO en su pago (vence_en), que el servidor alarga al elegir
// transferencia o al abrir Culqi. Nunca se calcula aquí desde la creación. null si todavía no se sabe.
export function venceDe(venceEn: string | null | undefined): Date | null {
  const vence = parsearFechaServidor(venceEn);
  return isNaN(vence.getTime()) ? null : vence;
}

// Un pago que se está verificando (cobro en curso o transferencia en revisión): ese pedido no se paga ni se cancela
// todavía (Pagos responde 409).
export const pagoSeEstaVerificando = (estado: string | null | undefined): boolean => estado === 'procesando' || estado === 'en_revision';
