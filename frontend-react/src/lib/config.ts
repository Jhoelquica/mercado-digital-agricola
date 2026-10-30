// Llave pública de Culqi y Client ID de Google: no son secretos (viajan en el código del navegador), pero cambian por
// entorno. Los usarán el pago y el acceso con Google cuando se migren.
export const CULQI_PUBLIC_KEY = import.meta.env.VITE_CULQI_PUBLIC_KEY ?? '';
export const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? '';
