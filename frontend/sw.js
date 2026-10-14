// Service worker de ChakraShop (H6 de la validación con usuarios del 2026-10-13): recibe los avisos al celular (Web Push)
// y abre la aplicación en Notificaciones al tocarlos. No guarda nada en caché: la página sigue cargando como siempre.
// Va en la raíz del sitio para que su alcance sea todo el sitio; nginx lo sirve sin caché (frontend/nginx.conf).

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (evento) => evento.waitUntil(self.clients.claim()));

self.addEventListener('push', (evento) => {
  let aviso = {};
  try {
    aviso = evento.data ? evento.data.json() : {};
  } catch {
    aviso = { cuerpo: evento.data ? evento.data.text() : '' };
  }
  evento.waitUntil(self.registration.showNotification(aviso.titulo || 'ChakraShop', {
    body: aviso.cuerpo || 'Tienes un aviso nuevo.',
    icon: '/assets/icono-192.png',
    badge: '/assets/icono-192.png',
    lang: 'es',
    data: { url: aviso.url || '/?vista=notificaciones' },
  }));
});

self.addEventListener('notificationclick', (evento) => {
  evento.notification.close();
  const url = new URL(evento.notification.data?.url || '/?vista=notificaciones', self.location.origin).href;
  evento.waitUntil((async () => {
    const ventanas = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const abierta = ventanas.find((v) => new URL(v.url).origin === self.location.origin);
    if (abierta) {
      await abierta.focus();
      abierta.postMessage({ tipo: 'abrir-notificaciones' });
      return;
    }
    await self.clients.openWindow(url);
  })());
});
