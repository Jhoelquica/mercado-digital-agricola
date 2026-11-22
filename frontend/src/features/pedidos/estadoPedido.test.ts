// "Pago en revisión" en Mis pedidos (src/features/pedidos/estadoPedido.ts): lo dice el pago de Pagos, también en un
// re-pago por transferencia sobre un pedido cancelado; si el pago no se pudo leer, lo dice el pedido.   npm test
import { expect, test } from 'vitest';
import type { Pago } from '../../api/pagos';
import type { Pedido } from '../../api/pedidos';
import { estadoGeneralPedido, pasosPedido, textoEstadoPago } from './estadoPedido';

const pedido = (estado: string) => ({ id: 'a1b2c3d4-0000-4000-8000-000000000001', estado, items: [], costo_envio: 5 }) as unknown as Pedido;
const pago = (estado: string, metodo = 'transferencia') => ({ estado, metodo }) as unknown as Pago;
const pasoDelPago = (d: Parameters<typeof pasosPedido>[0]) => {
  const paso = pasosPedido(d).find((p) => p.titulo === 'Pago');
  if (!paso) throw new Error('falta el paso del pago');
  return paso;
};

test('con la constancia enviada el pedido dice "Pago en revisión", no "Pago pendiente"', () => {
  const d = { pedido: pedido('en_revision'), envio: null, pago: pago('en_revision') };
  expect(estadoGeneralPedido(d).etiqueta).toBe('Pago en revisión');
  expect([pasoDelPago(d).detalle, pasoDelPago(d).estado]).toEqual(['En revisión', 'actual']);
  expect(textoEstadoPago(d.pago)).toBe('En revisión · transferencia');
});

test('un re-pago por transferencia sobre un pedido cancelado sin motivo también se ve en revisión, no como fallo', () => {
  const d = { pedido: pedido('cancelado'), envio: null, pago: pago('en_revision') };
  expect(estadoGeneralPedido(d).etiqueta).toBe('Pago en revisión');
  expect(pasoDelPago(d).estado).toBe('actual');
});

test('si el pago no se pudo leer, lo dice el pedido de Pedidos', () => {
  expect(estadoGeneralPedido({ pedido: pedido('en_revision'), envio: null, pago: null }).etiqueta).toBe('Pago en revisión');
});

test('aprobada la transferencia, ya no está en revisión', () => {
  const d = { pedido: pedido('confirmado'), envio: null, pago: pago('aprobado') };
  expect(estadoGeneralPedido(d).etiqueta).not.toBe('Pago en revisión');
  expect(pasoDelPago(d).estado).toBe('hecho');
});
