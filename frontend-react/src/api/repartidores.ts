import { request, requestFormData } from './client';
import type { components } from './esquemas/transporte';

type Esquemas = components['schemas'];

// La fila de la base, tal como la devuelven estas rutas.
export interface Repartidor {
  id: string;
  usuario_id: string;
  nombre: string;
  dni: string;
  estado_disponibilidad: string | null;
  fecha_registro: string | null;
  tipo_vehiculo: string | null;
  capacidad_maxima_kg: number | null;
  transportista_carga: boolean | null;
  placa: string | null;
  licencia_numero: string | null;
  verificacion_estado: string | null;
  verificacion_motivo: string | null;
  fecha_documentos: string | null;
  fecha_verificacion: string | null;
}

export interface DocumentosDeCarga {
  placa: string;
  licenciaNumero: string;
  licencia: File;
  tarjetaPropiedad: File;
  soat: File;
}

export const repartidores = {
  miPerfil: () => request<Repartidor>('transporte', '/repartidores/me', { auth: true }),
  crear: (datos: Esquemas['RepartidorCrear']) => request<Repartidor>('transporte', '/repartidores', { method: 'POST', body: datos, auth: true }),
  // Cambiar de vehículo o corregir su capacidad: le llega lo que ahora puede cargar.
  actualizarVehiculo: (datos: Esquemas['VehiculoActualizar']) => request<Repartidor>('transporte', '/repartidores/me/vehiculo', { method: 'PATCH', body: datos, auth: true }),
  // Transportista de carga: placa, licencia y los documentos (licencia, tarjeta de propiedad, SOAT) que revisa el admin.
  enviarDocumentosCarga: ({ placa, licenciaNumero, licencia, tarjetaPropiedad, soat }: DocumentosDeCarga) => {
    const formData = new FormData();
    formData.append('placa', placa);
    formData.append('licencia_numero', licenciaNumero);
    formData.append('licencia', licencia);
    formData.append('tarjeta_propiedad', tarjetaPropiedad);
    formData.append('soat', soat);
    return requestFormData<{ verificacion_estado: string }>('transporte', '/repartidores/me/carga', formData);
  },
};
