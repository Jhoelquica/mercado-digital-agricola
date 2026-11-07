import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';

// Traefik (ver k8s/traefik.yaml): las APIs van por /api/<servicio> y las fotos de MinIO por /media/...
const GATEWAY_LOCAL = 'http://localhost:8000';
// La ventana de "Continuar con Google" (popup de GIS) le escribe a esta página con postMessage: con COOP same-origin se
// cortaría. same-origin-allow-popups aísla la página igual y deja hablar a los popups que ella abre (mismo valor que
// frontend/security-headers.conf, que servirá también a frontend-react).
const CABECERAS = { 'Cross-Origin-Opener-Policy': 'same-origin-allow-popups' };

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      // El service worker no guarda nada en caché (igual que frontend/sw.js), así que no hay manifiesto que inyectar.
      injectManifest: { injectionPoint: undefined },
      // Se registra solo cuando el usuario activa los avisos, nunca al abrir la página.
      injectRegister: false,
      manifest: {
        name: 'ChakraShop — Mercado Digital Agrícola',
        short_name: 'ChakraShop',
        description: 'Del productor a tu mesa, sin intermediarios.',
        lang: 'es-PE',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        background_color: '#F4F1EA',
        theme_color: '#2D3B2A',
        icons: [
          { src: '/assets/icono-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/assets/icono-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
        ],
      },
    }),
  ],
  server: {
    headers: CABECERAS,
    proxy: {
      '/api': { target: GATEWAY_LOCAL, changeOrigin: true },
      '/media': { target: GATEWAY_LOCAL, changeOrigin: true },
    },
  },
  preview: { headers: CABECERAS },
});
