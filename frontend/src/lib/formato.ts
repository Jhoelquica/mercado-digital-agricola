export function formatearFecha(fechaStr: string | null | undefined): string {
  if (!fechaStr) return '—';
  const d = new Date(fechaStr);
  if (isNaN(d.getTime())) return fechaStr;
  return d.toLocaleString('es-PE', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Para timestamps generados por el servidor (datetime.utcnow, que el JSON entrega sin zona): sin la Z
// el navegador los lee como hora local y los muestra corridos (en Perú, +5 h, a veces el día siguiente).
// NO usar con fechas que escribe el usuario (siembra, cosecha): esas son fechas de calendario, no instantes en UTC.
export function parsearFechaServidor(fechaStr: string | null | undefined): Date {
  if (!fechaStr) return new Date(NaN);
  return new Date(/(Z|[+-]\d{2}:?\d{2})$/i.test(fechaStr) ? fechaStr : `${fechaStr}Z`);
}

export function formatearFechaServidor(fechaStr: string | null | undefined): string {
  if (!fechaStr) return '—';
  const d = parsearFechaServidor(fechaStr);
  return isNaN(d.getTime()) ? fechaStr : formatearFecha(d.toISOString());
}

// Fecha corta (02 nov. 2026) de un timestamp del servidor (sin zona: es UTC).
export function formatearFechaCortaServidor(fechaStr: string | null | undefined): string {
  const d = parsearFechaServidor(fechaStr);
  return isNaN(d.getTime()) ? '—' : d.toLocaleDateString('es-PE', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function formatearMoneda(valor: number | string | null | undefined): string {
  const n = Number(valor) || 0;
  const monto = Math.abs(n).toFixed(2);
  return `${n < 0 && Number(monto) !== 0 ? '-' : ''}S/ ${monto}`; // "-S/ 199.46", no "S/ -199.46"
}

export function hashCorto(hash: string | null | undefined): string {
  if (!hash) return '—';
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

// Área de una chacra en hectáreas con dos decimales ("1.25"); el servidor la manda en m².
export function formatearHectareas(areaM2: number): string {
  return (areaM2 / 10_000).toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
