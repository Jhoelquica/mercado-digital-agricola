import { request } from './client';
import type { components } from './esquemas/productores';

type Esquemas = components['schemas'];

export type Chacra = Esquemas['ChacraSalida'];

export const chacras = {
  misChacras: () => request<Chacra[]>('productores', '/chacras/me', { auth: true }),
  crear: (datos: Esquemas['ChacraCrear']) => request<Chacra>('productores', '/chacras', { method: 'POST', body: datos, auth: true }),
  actualizar: (id: string, datos: Esquemas['ChacraCrear']) => request<Chacra>('productores', `/chacras/${id}`, { method: 'PUT', body: datos, auth: true }),
  eliminar: (id: string) => request<{ mensaje: string }>('productores', `/chacras/${id}`, { method: 'DELETE', auth: true }),
};
