import { request } from './client';
import type { components } from './esquemas/transporte';

type Esquemas = components['schemas'];

export interface Recojo {
  id: string;
  producto_id: string;
  producto_nombre: string | null;
  productor_id: string;
  modalidad: string;
  estado: string;
  latitud: number | null;
  longitud: number | null;
  referencia: string | null;
  telefono: string | null;
  fecha_estimada: string | null;
  peso_kg: number | null;
  distancia_km: number | null;
  costo: number | null;
  repartidor: { id: string; nombre: string; tipo_vehiculo: string | null } | null;
  motivo_cancelacion: string | null;
  responder_antes_de: string | null;
  fecha_creacion: string | null;
  fecha_recogido: string | null;
  fecha_en_almacen: string | null;
  fecha_completado: string | null;
  sacos: number | null;
  transporte_contratado: {
    nombre: string | null;
    telefono: string | null;
    placa: string | null;
    capacidad_kg: number | null;
    capacidad_sacos: number | null;
    fecha_programada: string | null;
  } | null;
  sin_vehiculo_registrado: boolean;
}

export interface CotizacionRecojo {
  distancia_km: number;
  peso_kg: number;
  tarifa_distancia: number;
  recargo_peso: number;
  total: number;
  hay_vehiculo: boolean;
  sacos: number | null;
}

// Tramo chacra -> almacén de una cosecha publicada (ver transporte/logica_recojos.py).
export const recojos = {
  // Agricultor: cuánto costaría, pedirlo (o avisar que la lleva él), ver los suyos y anular.
  cotizar: (productoId: string, latitud: number, longitud: number) => request<CotizacionRecojo>('transporte',
    `/recojos/cotizacion?producto_id=${encodeURIComponent(productoId)}&latitud=${encodeURIComponent(latitud)}&longitud=${encodeURIComponent(longitud)}`, { auth: true }),
  pedir: (datos: Esquemas['RecojoCrear']) => request<Recojo>('transporte', '/recojos', { method: 'POST', body: datos, auth: true }),
  mios: () => request<Recojo[]>('transporte', '/recojos/mios', { auth: true }),
  cancelar: (id: string) => request<Recojo>('transporte', `/recojos/${id}/cancelar`, { method: 'POST', auth: true }),
  // Repartidor: los que le ofrecieron y los que tiene a cargo, y cada paso.
  propuestas: () => request<Recojo[]>('transporte', '/recojos/propuestas', { auth: true }),
  asignados: () => request<Recojo[]>('transporte', '/recojos/asignados', { auth: true }),
  aceptar: (id: string) => request<Recojo>('transporte', `/recojos/${id}/aceptar`, { method: 'POST', auth: true }),
  rechazar: (id: string) => request<{ mensaje: string }>('transporte', `/recojos/${id}/rechazar`, { method: 'POST', auth: true }),
  recogido: (id: string) => request<Recojo>('transporte', `/recojos/${id}/recogido`, { method: 'POST', auth: true }),
  enAlmacen: (id: string) => request<Recojo>('transporte', `/recojos/${id}/en-almacen`, { method: 'POST', auth: true }),
  // Admin: cómo llega cada cosecha que se espera en el almacén.
  enCurso: () => request<Recojo[]>('transporte', '/recojos', { auth: true }),
  // Admin (H8): el camión que contrató cuando ningún vehículo registrado carga la cosecha, y cuándo lo recogió.
  contratarTransporte: (id: string, datos: Esquemas['TransporteContratado']) => request<Recojo>('transporte', `/recojos/${id}/transporte-contratado`, { method: 'POST', body: datos, auth: true }),
  recogidoContratado: (id: string) => request<Recojo>('transporte', `/recojos/${id}/recogido-contratado`, { method: 'POST', auth: true }),
};
