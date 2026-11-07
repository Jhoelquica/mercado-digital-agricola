import { parsearFechaServidor } from './formato';

// El estado de un envío dicho para personas (avisos, pedidos sin entregar del admin).
export const ETIQUETAS_ESTADO_ENVIO: Record<string, string> = {
  pendiente: 'Pendiente', pendiente_asignacion: 'Buscando repartidor', propuesto: 'Propuesto a un repartidor', asignado: 'Repartidor asignado',
  en_camino: 'En camino', esperando_camion: 'Esperando que se llene el camión', en_destino: 'El camión llegó a tu ciudad',
  en_agencia: 'En la agencia de tu ciudad', recogido: 'Recogida en la chacra', en_almacen: 'En el almacén', completado: 'Pesada y a la venta',
  entregado: 'Entregado', cancelado: 'Cancelado',
};

// "hace 5 min", "ayer", "hace 3 días" o la fecha corta si pasó una semana.
export function tiempoRelativo(fechaStr: string | null | undefined): string {
  const d = parsearFechaServidor(fechaStr);
  if (isNaN(d.getTime())) return '—';
  const min = Math.floor(Math.max(0, Date.now() - d.getTime()) / 60000);
  if (min < 1) return 'hace un momento';
  if (min < 60) return `hace ${min} min`;
  const horas = Math.floor(min / 60);
  if (horas < 24) return `hace ${horas} h`;
  const dias = Math.floor(horas / 24);
  if (dias === 1) return 'ayer';
  if (dias < 7) return `hace ${dias} días`;
  return d.toLocaleDateString('es-PE', { day: '2-digit', month: 'short', year: 'numeric' });
}
