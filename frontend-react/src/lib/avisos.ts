import { notificaciones, type ConfiguracionAvisos, type SuscripcionPush } from '../api/notificaciones';
import { toast } from '../components/Toast';
import { claveVapidABytes, mismaClaveVapid } from './reglas';

// H6: avisos al celular (Web Push). La tarjeta para administrarlos llega con la pantalla de Notificaciones; aquí está lo
// que toca la sesión y lo que usa la reserva de un producto ("Avisarme también al celular").
let configAvisos: ConfiguracionAvisos | null = null;
let avisosCelularActivos = false;

export function pushSoportado(): boolean {
  return window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

export const configuracionDeAvisos = (): ConfiguracionAvisos | null => configAvisos;
export const hayAvisosCelularActivos = (): boolean => avisosCelularActivos;

async function cargarConfigAvisos(): Promise<ConfiguracionAvisos> {
  if (!configAvisos) configAvisos = await notificaciones.avisos.configuracion().catch(() => null);
  return configAvisos || { push: false, clave_publica: null, correo: false };
}

async function suscripcionPushActual(): Promise<PushSubscription | null> {
  if (!pushSoportado()) return null;
  const registro = await navigator.serviceWorker.getRegistration('/');
  return registro ? registro.pushManager.getSubscription() : null;
}

export async function activarAvisosCelular(): Promise<boolean> {
  const config = await cargarConfigAvisos();
  if (!config.push || !pushSoportado()) {
    toast('Este celular no puede recibir los avisos de ChakraShop.', 'error');
    return false;
  }
  const permiso = await Notification.requestPermission();
  if (permiso !== 'granted') {
    toast('No diste el permiso: los avisos seguirán llegando solo dentro de ChakraShop.', 'error');
    return false;
  }
  const registro = await navigator.serviceWorker.register('/sw.js');
  await navigator.serviceWorker.ready;
  let suscripcion = await registro.pushManager.getSubscription();
  if (suscripcion && !mismaClaveVapid(suscripcion.options?.applicationServerKey, config.clave_publica)) {
    await suscripcion.unsubscribe(); // se cambiaron las claves del servidor: hay que suscribirse de nuevo
    suscripcion = null;
  }
  suscripcion ||= await registro.pushManager.subscribe({
    userVisibleOnly: true, applicationServerKey: claveVapidABytes(config.clave_publica),
  });
  await notificaciones.avisos.activar(suscripcion.toJSON() as SuscripcionPush);
  avisosCelularActivos = true;
  toast('Listo: los avisos te llegarán a este celular 🔔');
  return true;
}

// Al entrar: si este navegador ya tenía los avisos, pasan a la cuenta que entró (el servidor cambia el dueño).
export async function sincronizarAvisosCelular(): Promise<void> {
  void cargarConfigAvisos();
  try {
    const suscripcion = await suscripcionPushActual();
    avisosCelularActivos = Boolean(suscripcion) && Notification.permission === 'granted';
    if (suscripcion && avisosCelularActivos) await notificaciones.avisos.activar(suscripcion.toJSON() as SuscripcionPush);
  } catch { /* sin avisos al celular: no impide usar ChakraShop */ }
}

// Al cerrar sesión: quien use después este celular no recibe los avisos de esta cuenta.
export async function quitarAvisosAlCerrarSesion(): Promise<void> {
  avisosCelularActivos = false;
  try {
    const suscripcion = await suscripcionPushActual();
    if (suscripcion) {
      await notificaciones.avisos.quitar(suscripcion.endpoint);
      await suscripcion.unsubscribe();
    }
  } catch { /* nada que quitar */ }
}
