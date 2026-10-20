// Retira el service worker de la versión anterior (PWA) que quedó instalado en los navegadores y tablets:
// borra su caché, se desinstala y recarga las pestañas abiertas para que carguen este frontend.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (evento) => {
  evento.waitUntil((async () => {
    for (const nombre of await caches.keys()) await caches.delete(nombre);
    await self.registration.unregister();
    for (const cliente of await self.clients.matchAll({ type: 'window' })) cliente.navigate(cliente.url);
  })());
});
