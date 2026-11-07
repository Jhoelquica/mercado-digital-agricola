import { request } from './client';
import type { components } from './esquemas/pedidos';

type Esquemas = components['schemas'];

export interface PedidoItem {
  id: string;
  pedido_id: string;
  producto_id: string;
  cantidad: number;
  precio_unitario: number;
  unidad: string | null;
  peso_kg: number | null;
  productor_id: string | null;
  producto_nombre: string | null;
}

export interface Pedido {
  id: string;
  usuario_id: string | null;
  comprador_nombre: string;
  comprador_telefono: string | null;
  estado: string | null;
  fecha_creacion: string | null;
  destino_latitud: number;
  destino_longitud: number;
  costo_envio: number;
  peso_total_kg: number | null;
  requiere_vehiculo_grande: boolean;
  recargo_envio_peso: number | null;
  modalidad_envio: string | null;
  destino_lejano_id: string | null;
  destino_ciudad: string | null;
  receptor_dni: string | null;
  receptor_nombre: string | null;
  sacos: number | null;
  flete_destino_estimado: number | null;
  monto_adelanto: number | null;
  porcentaje_adelanto: number | null;
  motivo_cancelacion: string | null;
  fecha_cancelacion: string | null;
  items?: PedidoItem[];
}

export interface PedidoCancelado extends Pedido {
  devolucion: unknown;
}

export interface CondicionesDeCompra {
  monto_minimo: number;
  umbral_por_mayor_kg: number;
  recargo_envio_por_kg: number;
  kg_por_saco: number;
  porcentaje_adelanto_camion: number;
}

export interface DestinoPublico {
  id: string;
  ciudad: string;
  departamento: string | null;
  latitud: number;
  longitud: number;
  radio_km: number;
  nota: string | null;
  agencia: { nombre: string | null; direccion: string | null; flete_kg: number | null; dias: number | null } | null;
  camion: {
    precio_saco: number;
    minimo_sacos: number;
    capacidad_sacos: number;
    porcentaje_salida: number;
    sacos_para_salir: number;
    dias: number | null;
  } | null;
}

export interface DestinoLejano {
  id: string;
  ciudad: string;
  departamento: string | null;
  latitud: number;
  longitud: number;
  radio_km: number;
  activo: boolean;
  agencia_disponible: boolean;
  agencia_nombre: string | null;
  agencia_direccion: string | null;
  agencia_flete_kg: number | null;
  agencia_dias: number | null;
  camion_disponible: boolean;
  camion_precio_saco: number | null;
  camion_minimo_sacos: number;
  camion_capacidad_sacos: number;
  camion_porcentaje_salida: number;
  camion_dias: number | null;
  nota: string | null;
  fecha_actualizacion: string | null;
}

export interface OpcionAgencia {
  agencia: string | null;
  direccion: string | null;
  dias: number | null;
  costo_envio: number;
  recargo_envio_peso: number;
  flete_kg: number | null;
  flete_destino_estimado: number | null;
  motivo_no_disponible: string | null;
}

export interface OpcionCamion {
  sacos: number | null;
  precio_saco: number;
  minimo_sacos: number;
  capacidad_sacos: number;
  porcentaje_salida: number;
  sacos_para_salir: number;
  dias: number | null;
  costo_envio: number | null;
  porcentaje_adelanto: number;
  adelanto: number | null;
  saldo: number | null;
  motivo_no_disponible: string | null;
}

export type Cotizacion =
  | {
    tipo: 'local';
    distancia_km: number;
    subtotal: number;
    peso_total_kg: number | null;
    umbral_kg: number;
    por_mayor: boolean;
    kg_extra: number;
    recargo_por_kg: number;
    costo_envio_base: number;
    recargo_envio_peso: number;
    costo_envio: number;
  }
  | {
    tipo: 'lejano';
    destino: DestinoPublico;
    subtotal: number;
    peso_total_kg: number | null;
    kg_por_saco: number;
    opciones: { agencia: OpcionAgencia | null; camion: OpcionCamion | null };
  };

export interface UbicacionConfigurada {
  latitud: number | null;
  longitud: number | null;
  fecha_actualizacion: string | null;
}

export const pedidos = {
  crear: (datos: Esquemas['PedidoCrear']) => request<Pedido>('pedidos', '/pedidos', { method: 'POST', body: datos, auth: true }),
  obtener: (id: string) => request<Pedido>('pedidos', `/pedidos/${id}`, { auth: true }),
  // Los pedidos del comprador con sesión (del más nuevo al más viejo) y los productos que ya recibió (solo esos se
  // pueden calificar).
  mios: () => request<Pedido[]>('pedidos', '/pedidos/mios', { auth: true }),
  productosRecibidos: () => request<{ productos: string[] }>('pedidos', '/pedidos/mios/productos-recibidos', { auth: true }),
  // Lo que hay que saber antes de armar el pedido.
  condiciones: () => request<CondicionesDeCompra>('pedidos', '/pedidos/condiciones'),
  // Costo de envío según la distancia del destino al almacén (público): permite mostrar el total real antes de crear el pedido.
  cotizarEnvio: (latitud: number, longitud: number) => request<{ costo_envio: number; distancia_km: number }>('pedidos', `/pedidos/costo-envio?latitud=${encodeURIComponent(latitud)}&longitud=${encodeURIComponent(longitud)}`),
  // El envío exacto de este carrito a este destino (con el recargo por peso si es una compra por mayor).
  cotizar: (datos: Esquemas['CotizacionCrear']) => request<Cotizacion>('pedidos', '/pedidos/cotizar', { method: 'POST', body: datos, auth: true }),
  // Compradores lejanos: las ciudades a las que se llega (público) y el DNI de quien recibe (nombre enmascarado).
  destinosLejanos: () => request<DestinoPublico[]>('pedidos', '/destinos-lejanos'),
  consultarDni: (dni: string) => request<{ dni: string; nombre_para_confirmar: string }>('pedidos', `/pedidos/dni/${encodeURIComponent(dni)}`, { auth: true }),
  // Dónde deja el repartidor los pedidos que van por agencia de carga (público; lo cambia el admin).
  agenciaDespacho: () => request<UbicacionConfigurada>('pedidos', '/configuracion/agencia-despacho'),
  // Dónde está el almacén (público): el repartidor lleva ahí la cosecha que recogió en una chacra.
  almacen: () => request<UbicacionConfigurada>('pedidos', '/configuracion/almacen'),
  admin: {
    // Todas las ciudades lejanas (también las ocultas) con sus condiciones, para editarlas.
    destinosLejanos: () => request<DestinoLejano[]>('pedidos', '/destinos-lejanos/admin', { auth: true }),
    crearDestino: (datos: Esquemas['DestinoLejanoDatos']) => request<DestinoLejano>('pedidos', '/destinos-lejanos', { method: 'POST', body: datos, auth: true }),
    actualizarDestino: (id: string, datos: Esquemas['DestinoLejanoDatos']) => request<DestinoLejano>('pedidos', `/destinos-lejanos/${id}`, { method: 'PUT', body: datos, auth: true }),
    actualizarAgenciaDespacho: (latitud: number, longitud: number) => request<{ latitud: number; longitud: number }>('pedidos', '/configuracion/agencia-despacho', { method: 'PATCH', body: { latitud, longitud } satisfies Esquemas['AgenciaDespachoActualizar'], auth: true }),
    // Pedidos pagados que todavía no se entregaron, y cancelar uno devolviendo el dinero (si el envío no salió).
    sinEntregar: () => request<Pedido[]>('pedidos', '/pedidos/admin/sin-entregar', { auth: true }),
    cancelar: (id: string, motivo: string) => request<PedidoCancelado>('pedidos', `/pedidos/${id}/cancelar`, { method: 'POST', body: { motivo } satisfies Esquemas['CancelacionPedido'], auth: true }),
  },
};
