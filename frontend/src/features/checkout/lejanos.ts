import type { DestinoPublico } from '../../api/pedidos';

// Misma caja que Pedidos: dentro de ella el repartidor llega a la puerta; fuera, solo a las ciudades habilitadas.
const AYACUCHO_CAJA = { latMin: -15.2, latMax: -12.85, lngMin: -75.1, lngMax: -73.0 };

export const HUAMANGA_CENTRO = { latitud: -13.1588, longitud: -74.2232 };

export function estaEnAyacucho(lat: number, lng: number): boolean {
  const c = AYACUCHO_CAJA;
  return lat >= c.latMin && lat <= c.latMax && lng >= c.lngMin && lng <= c.lngMax;
}

export function distanciaKmAprox(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const r = (g: number) => (g * Math.PI) / 180;
  const a = Math.sin(r(lat2 - lat1) / 2) ** 2 + Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lng2 - lng1) / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function ciudadDelPunto(destinos: DestinoPublico[], lat: number, lng: number): string | undefined {
  if (estaEnAyacucho(lat, lng)) return 'Ayacucho';
  return destinos.find((d) => distanciaKmAprox(d.latitud, d.longitud, lat, lng) <= d.radio_km)?.ciudad;
}

// Mismo redondeo que Pagos (al céntimo, mitad hacia arriba).
export function netoDelRepartidor(costoEnvio: number, porcentaje: number): number {
  const bruto = Number(costoEnvio) || 0;
  const comision = Math.round((bruto * porcentaje / 100 + Number.EPSILON) * 100) / 100;
  return Math.round((bruto - comision) * 100) / 100;
}

export function formatearKg(kg: number | string | null | undefined): string {
  return `${Number(kg).toLocaleString('es-PE', { maximumFractionDigits: 1 })} kg`;
}
