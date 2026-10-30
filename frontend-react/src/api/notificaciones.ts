import { request } from './client';
import type { components } from './esquemas/notificaciones';

type Esquemas = components['schemas'];

export type SuscripcionPush = Esquemas['SuscripcionPushEntrada'];

export interface Notificacion {
  id: string;
  usuario_id: string | null;
  pedido_id: string | null;
  producto_id: string | null;
  tipo: string;
  estado: string | null;
  mensaje: string;
  canal: string | null;
  fecha_envio: string | null;
  rol_destino: string | null;
}

export interface ConfiguracionAvisos {
  push: boolean;
  clave_publica: string | null;
  correo: boolean;
}

export interface EstadoAvisos {
  dispositivos: number;
  correo: string | null;
}

export const notificaciones = {
  porPedido: (pedidoId: string) => request<Notificacion[]>('notificaciones', `/notificaciones/${pedidoId}`, { auth: true }),
  listarTodas: () => request<Notificacion[]>('notificaciones', '/notificaciones', { auth: true }),
  // H6: avisos al celular (Web Push) y, de respaldo, por correo (notificaciones/avisos_externos.py).
  avisos: {
    // Con el token si hay sesión: el gateway (k8s/traefik-routes.yaml) exige JWT a todo GET de /notificaciones, y la
    // tarjeta de avisos solo se muestra con sesión.
    configuracion: () => request<ConfiguracionAvisos>('notificaciones', '/notificaciones/avisos/configuracion', { auth: 'opcional' }),
    mios: () => request<EstadoAvisos>('notificaciones', '/notificaciones/avisos/mios', { auth: true }),
    activar: (suscripcion: SuscripcionPush) => request<EstadoAvisos>('notificaciones', '/notificaciones/avisos/push', { method: 'POST', body: suscripcion, auth: true }),
    // Sin sesión a propósito: se llama al cerrar sesión, con el token ya borrado.
    quitar: (endpoint: string) => request<{ quitadas: number }>('notificaciones', '/notificaciones/avisos/push/quitar', { method: 'POST', body: { endpoint } satisfies Esquemas['EndpointPush'] }),
    correo: (activo: boolean) => request<EstadoAvisos>('notificaciones', '/notificaciones/avisos/correo', { method: 'PUT', body: { activo } satisfies Esquemas['PreferenciaCorreoEntrada'], auth: true }),
    probar: () => request<{ enviados: number }>('notificaciones', '/notificaciones/avisos/prueba', { method: 'POST', auth: true }),
  },
};
