import { request, requestBlob, requestFormData } from './client';
import type { components } from './esquemas/transporte';
import { exigirImagenLiviana } from './errors';

type Esquemas = components['schemas'];

export type Envio = Esquemas['EnvioSalida'];
export type Propuesta = Esquemas['PropuestaSalida'];

export interface ResumenViaje {
  id: string;
  estado: string;
  destino_ciudad: string | null;
  capacidad_sacos: number;
  sacos_para_salir: number;
  sacos_cargados: number;
  fecha_salida: string | null;
  fecha_llegada: string | null;
}

export interface Viaje extends ResumenViaje {
  transportista: { id: string; nombre: string; placa: string | null; capacidad_maxima_kg: number } | null;
  envios: { id: string; pedido_id: string; sacos: number | null; estado: string | null; saldo_pagado: boolean }[];
}

// El comprador (y el admin) ven además el PIN con el que se confirma la entrega.
export interface EnvioDetalle extends Envio {
  pin_entrega?: string | null;
  viaje?: ResumenViaje | null;
}

export type RutaEnvio =
  | {
    disponible: true;
    distancia_km: number;
    tiempo_estimado_min: number;
    origen: { latitud: string | null; longitud: string | null };
    destino: { latitud: number; longitud: number };
  }
  | { disponible: false; mensaje: string };

export interface Transportista {
  id: string;
  nombre: string;
  dni: string;
  tipo_vehiculo: string | null;
  capacidad_maxima_kg: number | null;
  placa: string | null;
  licencia_numero: string | null;
  estado_disponibilidad: string | null;
  verificacion_estado: string | null;
  verificacion_motivo: string | null;
  fecha_documentos: string | null;
  documentos: Record<string, string | null>;
}

export const transporte = {
  obtenerEnvio: (pedidoId: string) => request<EnvioDetalle>('transporte', `/envios/${pedidoId}`, { auth: true }),
  // Pedido por agencia: el comprador confirma que lo recogió en la agencia de su ciudad.
  confirmarRecibido: (envioId: string) => request<Envio>('transporte', `/envios/${envioId}/recibido`, { method: 'POST', auth: true }),
  listarTodos: () => request<Envio[]>('transporte', '/envios', { auth: true }),
  actualizarEstado: (envioId: string, estado: string) => request<Envio>('transporte', `/envios/${envioId}/estado`, { method: 'PATCH', body: { estado } satisfies Esquemas['EstadoEnvio'], auth: true }),
  ruta: (envioId: string) => request<RutaEnvio>('transporte', `/envios/${envioId}/ruta`, { auth: true }),
  actualizarUbicacion: (envioId: string, latitud: number | string, longitud: number | string) => request<Envio>('transporte', `/envios/${envioId}/ubicacion`, { method: 'PATCH', body: { latitud: String(latitud), longitud: String(longitud) } satisfies Esquemas['UbicacionActualizar'], auth: true }),
  propuestas: () => request<Propuesta[]>('transporte', '/envios/propuestas', { auth: true }),
  aceptar: (envioId: string) => request<Envio>('transporte', `/envios/${envioId}/aceptar`, { method: 'POST', auth: true }),
  rechazar: (envioId: string) => request<{ mensaje: string }>('transporte', `/envios/${envioId}/rechazar`, { method: 'POST', auth: true }),
  // Rol productor: envíos en "pendiente_entrega_directa" que le corresponden (ningún repartidor tuvo capacidad suficiente)
  // y la acción de cerrarlos, que reusa el endpoint de actualizarEstado.
  pendientesEntregaDirecta: () => request<Envio[]>('transporte', '/envios/pendientes-entrega-directa', { auth: true }),
  marcarEntregado: (envioId: string) => request<Envio>('transporte', `/envios/${envioId}/estado`, { method: 'PATCH', body: { estado: 'entregado' } satisfies Esquemas['EstadoEnvio'], auth: true }),
  // Evidencia de entrega (repartidor): PIN que le dictó el comprador + foto del paquete. El backend copia el GPS del
  // repartidor a ese instante: no hace falta mandarlo desde aquí.
  confirmarEntrega: (envioId: string, pin: string, archivoFoto: File) => {
    exigirImagenLiviana(archivoFoto);
    const formData = new FormData();
    formData.append('pin', pin);
    formData.append('foto', archivoFoto);
    return requestFormData<Envio>('transporte', `/envios/${envioId}/entregar`, formData);
  },
  // Rol admin: envíos "pendiente_asignacion" cuyo motivo es específicamente falta de capacidad (no "nadie disponible en
  // absoluto", que se resuelve solo). Ver motivo_pendiente en transporte/models.py.
  pendientesPorCapacidad: () => request<Envio[]>('transporte', '/envios/pendientes-por-capacidad', { auth: true }),
  // Compradores lejanos (repartidor): dejó el pedido en la agencia de carga de Huamanga, con la guía y su foto.
  despacharAgencia: (envioId: string, agencia: string, numeroGuia: string, archivoFoto: File) => {
    exigirImagenLiviana(archivoFoto);
    const formData = new FormData();
    formData.append('agencia', agencia);
    formData.append('numero_guia', numeroGuia);
    formData.append('foto', archivoFoto);
    return requestFormData<Envio>('transporte', `/envios/${envioId}/despachar-agencia`, formData);
  },
  // Viajes compartidos en camión del transportista: él marca la salida (cuando el viaje juntó su carga) y la llegada.
  viajesMios: () => request<Viaje[]>('transporte', '/viajes/mios', { auth: true }),
  salirViaje: (viajeId: string) => request<Viaje>('transporte', `/viajes/${viajeId}/salir`, { method: 'POST', body: {} satisfies Esquemas['SalidaDeViaje'], auth: true }),
  llegarViaje: (viajeId: string) => request<Viaje>('transporte', `/viajes/${viajeId}/llegar`, { method: 'POST', auth: true }),
  admin: {
    // Transportistas de carga por estado de sus documentos ("pendiente", "verificado", "rechazado").
    transportistas: (estado = 'pendiente') => request<Transportista[]>('transporte', `/repartidores/admin/transportistas?estado=${encodeURIComponent(estado)}`, { auth: true }),
    documentoTransportista: (id: string, cual: string) => requestBlob('transporte', `/repartidores/admin/transportistas/${id}/documento/${cual}`),
    verificarTransportista: (id: string) => request<Transportista>('transporte', `/repartidores/admin/transportistas/${id}/verificar`, { method: 'POST', auth: true }),
    rechazarTransportista: (id: string, motivo: string) => request<Transportista>('transporte', `/repartidores/admin/transportistas/${id}/rechazar`, { method: 'POST', body: { motivo } satisfies Esquemas['RechazoDocumentos'], auth: true }),
    // Viajes en curso; el admin asigna el transportista y puede adelantar la salida (forzar).
    viajes: () => request<Viaje[]>('transporte', '/viajes', { auth: true }),
    asignarViaje: (viajeId: string, transportistaId: string) => request<Viaje>('transporte', `/viajes/${viajeId}/asignar`, { method: 'POST', body: { transportista_id: transportistaId } satisfies Esquemas['AsignarTransportista'], auth: true }),
    salirViaje: (viajeId: string, forzar = false) => request<Viaje>('transporte', `/viajes/${viajeId}/salir`, { method: 'POST', body: { forzar } satisfies Esquemas['SalidaDeViaje'], auth: true }),
  },
};
