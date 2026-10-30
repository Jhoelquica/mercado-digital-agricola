import { notificaciones, type SuscripcionPush } from '../api/notificaciones';

// Solo lo que toca la sesión: activar los avisos llega con la pantalla de Notificaciones.
function pushSoportado(): boolean {
  return window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

async function suscripcionPushActual(): Promise<PushSubscription | null> {
  if (!pushSoportado()) return null;
  const registro = await navigator.serviceWorker.getRegistration('/');
  return registro ? registro.pushManager.getSubscription() : null;
}

// Al entrar: si este navegador ya tenía los avisos, pasan a la cuenta que entró (el servidor cambia el dueño).
export async function sincronizarAvisosCelular(): Promise<void> {
  try {
    const suscripcion = await suscripcionPushActual();
    if (suscripcion && Notification.permission === 'granted') {
      await notificaciones.avisos.activar(suscripcion.toJSON() as SuscripcionPush);
    }
  } catch { /* sin avisos al celular: no impide usar ChakraShop */ }
}

// Al cerrar sesión: quien use después este celular no recibe los avisos de esta cuenta.
export async function quitarAvisosAlCerrarSesion(): Promise<void> {
  try {
    const suscripcion = await suscripcionPushActual();
    if (suscripcion) {
      await notificaciones.avisos.quitar(suscripcion.endpoint);
      await suscripcion.unsubscribe();
    }
  } catch { /* nada que quitar */ }
}
