// Pruebas de las reglas del pedido sin pagar (src/lib/pedidoPendiente.ts) y del 409 que lo trae.   npm test
import { expect, test } from 'vitest';
import { ApiError, mensajeDeDetalle } from '../api/errors';
import { pedidoPendienteDe, type PedidoPendiente } from '../api/pedidos';
import { horaDeLima, mismasLineas, pagoSeEstaVerificando, venceDe } from './pedidoPendiente';

const PAPA = '3f1c9a52-0000-4000-8000-000000000001';
const MAIZ = '3f1c9a52-0000-4000-8000-000000000002';

test('el mismo carrito es el mismo pedido aunque cambie el orden de las líneas', () => {
  const carrito = [{ producto_id: PAPA, unidad: null, cantidad: 5 }, { producto_id: MAIZ, unidad: 'saco', cantidad: 2 }];
  const pedido = [{ producto_id: MAIZ, unidad: 'saco', cantidad: 2 }, { producto_id: PAPA, cantidad: 5 }];
  expect(mismasLineas(carrito, pedido)).toBe(true);
});

test('otra cantidad, otra unidad, otra línea o una de menos ya es otro pedido', () => {
  const base = [{ producto_id: PAPA, unidad: null, cantidad: 5 }];
  expect(mismasLineas(base, [{ producto_id: PAPA, unidad: null, cantidad: 6 }])).toBe(false);
  expect(mismasLineas(base, [{ producto_id: PAPA, unidad: 'saco', cantidad: 5 }])).toBe(false);
  expect(mismasLineas(base, [...base, { producto_id: MAIZ, cantidad: 1 }])).toBe(false);
  expect(mismasLineas(base, [])).toBe(false);
  expect(mismasLineas([], [])).toBe(true);
});

test('la hora del aviso es la de Lima, venga como venga la fecha del servidor', () => {
  expect(horaDeLima(new Date('2026-11-20T23:30:00Z'))).toBe('18:30');
  expect(horaDeLima(new Date('2026-11-21T04:05:00Z'))).toBe('23:05');
});

test('el pedido se guarda hasta el plazo guardado en su pago; sin ese dato no se inventa uno', () => {
  expect(venceDe('2026-11-20T23:30:00')?.toISOString()).toBe('2026-11-20T23:30:00.000Z');
  expect(venceDe('2026-11-20T23:30:00.123456')?.toISOString()).toBe('2026-11-20T23:30:00.123Z');
  expect(venceDe(null)).toBeNull();
  expect(venceDe(undefined)).toBeNull();
  expect(venceDe('no es una fecha')).toBeNull();
});

test('un cobro en curso o una transferencia en revisión se están verificando; un pago pendiente no', () => {
  expect(['procesando', 'en_revision'].map(pagoSeEstaVerificando)).toEqual([true, true]);
  expect(['pendiente', 'rechazado', 'aprobado', null, undefined].map(pagoSeEstaVerificando)).toEqual([false, false, false, false, false]);
});

test('el 409 de otro carrito trae el pedido pendiente y su mensaje se lee bien', () => {
  const pendiente: PedidoPendiente = {
    id: 'a1b2c3d4-0000-4000-8000-000000000009', fecha_creacion: '2026-11-20T22:00:00', costo_envio: 6, total: 56,
    monto_adelanto: null, modalidad_envio: 'local', items: [{ producto_id: PAPA, producto_nombre: 'Papa', cantidad: 5, unidad: null }],
  };
  const detalle = { codigo: 'pedido_pendiente', mensaje: 'Tienes un pedido sin pagar.', pedido_pendiente: pendiente };
  expect(pedidoPendienteDe(new ApiError(mensajeDeDetalle(detalle), 409, detalle))).toEqual(pendiente);
  expect(mensajeDeDetalle(detalle)).toBe('Tienes un pedido sin pagar.');
  expect(pedidoPendienteDe(new ApiError('Stock insuficiente', 409, 'Stock insuficiente'))).toBeNull();
  expect(pedidoPendienteDe(new ApiError('otro', 503, detalle))).toBeNull();
  expect(pedidoPendienteDe(new Error('cualquiera'))).toBeNull();
});
