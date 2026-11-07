import type { Pago } from '../../api/pagos';
import type { Pedido, PedidoItem } from '../../api/pedidos';
import type { EnvioDetalle, RutaEnvio } from '../../api/transporte';

export interface DatosPedido {
  pedido: Pedido;
  envio: EnvioDetalle | null;
  pago: Pago | null;
  ruta: RutaEnvio | null;
}

type DatosDeEstado = Pick<DatosPedido, 'pedido' | 'envio' | 'pago'>;

export function esPedidoEntregado(d: DatosDeEstado): boolean {
  return d.envio ? d.envio.estado === 'entregado' : d.pedido.estado === 'entregado';
}

// Suma de las líneas del pedido, sin envío.
export function subtotalPedido(pedido: Pedido): number {
  return (pedido.items || []).reduce((total, i: PedidoItem) => total + Number(i.precio_unitario) * i.cantidad, 0);
}

// Lo que se cobra por el pedido: líneas + envío. Es el mismo monto que calcula Pagos al crear el pedido, así que también
// es el que se reintenta si el pago se rechaza.
export function totalPedido(pedido: Pedido): number {
  return subtotalPedido(pedido) + (Number(pedido.costo_envio) || 0);
}

// El pago cuenta como confirmado si Pagos lo aprobó o si el pedido ya avanzó por eso: Pagos y Pedidos se sincronizan por
// eventos, así que uno puede ir un instante detrás.
export function pagoConfirmadoPedido(d: DatosDeEstado): boolean {
  return d.pago?.estado === 'aprobado' || ['confirmado', 'entregado'].includes(d.pedido.estado || '') || esPedidoEntregado(d);
}

export function pagoRechazadoPedido(d: DatosDeEstado): boolean {
  return d.pago?.estado === 'rechazado' && !pagoConfirmadoPedido(d);
}

const ETIQUETAS_ESTADO_PAGO: Record<string, string> = { pendiente: 'Pendiente', en_revision: 'En revisión', aprobado: 'Aprobado', rechazado: 'Rechazado', adelanto_pagado: 'Adelanto pagado (70%)', reembolsado: 'Devuelto' };
const ETIQUETAS_METODO_PAGO: Record<string, string> = { tarjeta: 'tarjeta', yape: 'Yape', transferencia: 'transferencia' };

export function textoEstadoPago(pago: Pago | null): string {
  if (!pago) return '—';
  if (pago.estado === 'rechazado' && pago.vencido_en) return 'Plazo para pagar vencido';
  const estado = ETIQUETAS_ESTADO_PAGO[pago.estado || ''] || pago.estado || '';
  // El método solo se sabe cuando hubo un intento (un pago pendiente todavía no tiene uno elegido).
  return pago.estado === 'pendiente' || !pago.metodo ? estado : `${estado} · ${ETIQUETAS_METODO_PAGO[pago.metodo] || pago.metodo}`;
}

export interface EstadoGeneral {
  clave: string;
  etiqueta: string;
  icono: string;
  pill: string;
}

// Estado global del pedido (chip + color de acento de la tarjeta). Sale de los tres estados reales: pedido, pago y envío.
export function estadoGeneralPedido(d: DatosDeEstado): EstadoGeneral {
  const { pedido, envio } = d;
  if (esPedidoEntregado(d)) return { clave: 'entregado', etiqueta: 'Entregado', icono: 'circle-check', pill: 'entregado' };
  // Una transferencia en revisión puede ser el reintento de un pago rechazado (pedido "cancelado" hasta que se apruebe).
  if (d.pago?.estado === 'en_revision') return { clave: 'pendiente', etiqueta: 'Transferencia en revisión', icono: 'building-bank', pill: 'pendiente' };
  // Pedidos solo cancela un pedido cuando su pago se rechaza, y ese pago se puede reintentar: "Pago rechazado" lo dice mejor.
  if (pagoRechazadoPedido(d)) {
    return d.pago?.vencido_en
      ? { clave: 'pago_rechazado', etiqueta: 'Plazo vencido', icono: 'clock-x', pill: 'rechazado' }
      : { clave: 'pago_rechazado', etiqueta: 'Pago rechazado', icono: 'alert-triangle', pill: 'rechazado' };
  }
  if (d.pago?.estado === 'reembolsado') return { clave: 'pago_rechazado', etiqueta: 'Cancelado · dinero devuelto', icono: 'receipt-refund', pill: 'cancelado' };
  if (pedido.estado === 'cancelado') return { clave: 'pago_rechazado', etiqueta: 'Cancelado', icono: 'circle-x', pill: 'cancelado' };
  if (envio?.estado === 'en_agencia') return { clave: 'en_curso', etiqueta: 'En la agencia de tu ciudad', icono: 'building-warehouse', pill: 'en_camino' };
  if (envio?.estado === 'en_destino') return { clave: 'en_curso', etiqueta: 'El camión llegó a tu ciudad', icono: 'truck', pill: 'en_camino' };
  if (envio?.estado === 'esperando_camion') return { clave: 'en_curso', etiqueta: 'Esperando que se llene el camión', icono: 'truck-loading', pill: 'confirmado' };
  if (envio?.estado === 'en_camino') return { clave: 'en_curso', etiqueta: 'En camino', icono: 'truck', pill: 'en_camino' };
  if (envio?.estado === 'asignado') return { clave: 'en_curso', etiqueta: 'Repartidor asignado', icono: 'user-check', pill: 'asignado' };
  if (envio?.estado === 'propuesto') return { clave: 'en_curso', etiqueta: 'Esperando repartidor', icono: 'hourglass', pill: 'propuesto' };
  if (pagoConfirmadoPedido(d)) return { clave: 'en_curso', etiqueta: 'Preparando tu pedido', icono: 'package', pill: 'confirmado' };
  return { clave: 'pendiente', etiqueta: 'Pago pendiente', icono: 'hourglass', pill: 'pendiente' };
}

export interface PasoPedido {
  icono: string;
  titulo: string;
  detalle: string;
  estado: 'hecho' | 'actual' | 'error' | 'pendiente';
}

// Cuatro hitos: Pedido → Pago → En camino → Entregado. Un hito está "hecho" cuando se alcanzó; el primero sin alcanzar es
// el "actual" (o "error" si el pago falló) y los siguientes quedan "pendientes".
export function pasosPedido(d: DatosDeEstado): PasoPedido[] {
  const { pedido, envio } = d;
  const entregado = esPedidoEntregado(d);
  const pagoOk = pagoConfirmadoPedido(d);
  // Una transferencia en revisión no es un fallo, aunque sea el reintento de un pedido que quedó cancelado.
  const enRevision = d.pago?.estado === 'en_revision';
  const pagoMal = !pagoOk && !enRevision && (pagoRechazadoPedido(d) || pedido.estado === 'cancelado');
  const enCamino = ['en_camino', 'en_agencia', 'en_destino'].includes(envio?.estado || '') || entregado;
  // Pedidos a otra ciudad: el paso "En camino" dice cómo va (agencia o camión) en vez de "Buscando repartidor".
  const detalleLejano = ({ esperando_camion: 'Completando el camión', en_agencia: 'En la agencia de tu ciudad', en_destino: 'Llegó a tu ciudad' } as Record<string, string>)[envio?.estado || ''];

  const pasos = [
    { icono: 'receipt', titulo: 'Pedido', hecho: true, error: false, detalle: 'Realizado' },
    {
      icono: 'credit-card',
      titulo: 'Pago',
      hecho: pagoOk,
      error: pagoMal,
      detalle: pagoOk ? 'Confirmado' : enRevision ? 'En revisión'
        : pagoMal ? (d.pago?.estado === 'reembolsado' ? 'Devuelto' : pedido.estado === 'cancelado' ? 'Cancelado' : 'Rechazado') : 'Pendiente',
    },
    {
      icono: 'truck',
      titulo: 'En camino',
      hecho: enCamino,
      error: false,
      detalle: enCamino ? (entregado ? 'Completado' : detalleLejano || 'En ruta hacia ti')
        : !pagoOk ? 'Por asignar'
          : detalleLejano ? detalleLejano
            : envio?.estado === 'asignado' ? 'Repartidor asignado'
              : envio?.estado === 'propuesto' ? 'Esperando al repartidor' // ya se le ofreció el envío; falta que acepte
                : envio ? 'Buscando repartidor' : 'Preparando el envío',
    },
    { icono: 'circle-check', titulo: 'Entregado', hecho: entregado, error: false, detalle: entregado ? 'Recibido' : 'Pendiente' },
  ];
  const primeroSinCumplir = pasos.findIndex((p) => !p.hecho);
  return pasos.map(({ icono, titulo, detalle, hecho, error }, i) => ({
    icono,
    titulo,
    detalle,
    estado: hecho ? 'hecho' : i === primeroSinCumplir ? (error ? 'error' : 'actual') : 'pendiente',
  }));
}

export function formatearTelefono(telefono: string | null | undefined): string {
  const digitos = String(telefono || '').replace(/\D/g, '');
  return digitos.length === 9 ? `${digitos.slice(0, 3)} ${digitos.slice(3, 6)} ${digitos.slice(6)}` : String(telefono || '');
}
