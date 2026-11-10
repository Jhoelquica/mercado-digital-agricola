import type { Polygon } from 'geojson';
import { request } from './client';
import type { components } from './esquemas/productores';

type Esquemas = components['schemas'];

// El contorno llega como GeoJSON Polygon ([lng, lat]); el esquema generado solo sabe que es un objeto.
export type Chacra = Omit<Esquemas['ChacraSalida'], 'contorno'> & { contorno?: Polygon | null };

export const chacras = {
  misChacras: () => request<Chacra[]>('productores', '/chacras/me', { auth: true }),
  crear: (datos: Esquemas['ChacraCrear']) => request<Chacra>('productores', '/chacras', { method: 'POST', body: datos, auth: true }),
  actualizar: (id: string, datos: Esquemas['ChacraCrear']) => request<Chacra>('productores', `/chacras/${id}`, { method: 'PUT', body: datos, auth: true }),
  eliminar: (id: string) => request<{ mensaje: string }>('productores', `/chacras/${id}`, { method: 'DELETE', auth: true }),
  // El contorno dibujado sobre el satélite: el servidor lo valida y calcula el área (la chacra vuelve con area_m2).
  dibujarContorno: (id: string, contorno: Polygon) => request<Chacra>('productores', `/chacras/${id}/contorno`, { method: 'PUT', body: { contorno }, auth: true }),
  borrarContorno: (id: string) => request<Chacra>('productores', `/chacras/${id}/contorno`, { method: 'DELETE', auth: true }),
};
