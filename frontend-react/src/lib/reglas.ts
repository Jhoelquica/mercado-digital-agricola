// Reglas de negocio puras: las mismas de frontend/js/reglas.js que usan estas pantallas.

// H7: lo que el comprador dice que le interesa al reservar. Sin cantidad, la reserva es solo el aviso.
export function cantidadDeReserva(texto: string | undefined, unidad: string | undefined) {
  const limpio = String(texto ?? '').trim().replace(',', '.');
  if (!limpio) return { datos: null, error: null };
  const cantidad = Number(limpio);
  if (!Number.isFinite(cantidad) || cantidad <= 0) return { datos: null, error: 'Escribe cuánto te interesa con un número mayor que 0, o déjalo vacío.' };
  return { datos: { cantidad, unidad: unidad || null }, error: null };
}

// H6: la clave pública VAPID (base64url, como la da el servidor) en bytes, como la pide pushManager.subscribe.
export function claveVapidABytes(base64url: string | null | undefined): Uint8Array<ArrayBuffer> {
  const texto = String(base64url || '');
  const binario = atob((texto + '='.repeat((4 - (texto.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(binario, (c) => c.charCodeAt(0));
}

// H6: ¿la suscripción del navegador es con la clave que tiene hoy el servidor? Si se cambiaron las claves, hay que volver
// a suscribirse; si no, el navegador rechaza la suscripción nueva.
export function mismaClaveVapid(bytesDelNavegador: ArrayBuffer | null | undefined, base64url: string | null | undefined): boolean {
  if (!bytesDelNavegador) return false;
  const a = new Uint8Array(bytesDelNavegador);
  const b = claveVapidABytes(base64url);
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

// Mismo criterio que Usuarios (ruc_valido): 11 dígitos, empieza con 10, 15, 17 o 20 y el último es el dígito
// verificador de SUNAT. Aquí solo para avisar antes de enviar; el servidor lo vuelve a revisar.
export function rucValido(ruc: string): boolean {
  if (!/^(10|15|17|20)\d{9}$/.test(ruc)) return false;
  const pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
  const resto = 11 - (pesos.reduce((acc, p, i) => acc + p * Number(ruc[i]), 0) % 11);
  const esperado = resto === 10 ? 0 : resto === 11 ? 1 : resto;
  return Number(ruc[10]) === esperado;
}
