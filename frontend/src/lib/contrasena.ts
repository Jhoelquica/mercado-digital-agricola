// Las reglas de una contraseña nueva: las mismas que comprueba Usuarios (usuarios/acceso.py) al registrarse y al
// cambiarla, para que el error salga junto al campo antes de enviar. La lista de contraseñas demasiado comunes solo la
// tiene el servidor: su respuesta también se muestra junto al campo (campoDelErrorDelServidor).
export const CONTRASENA_MINIMA = 8;
export const CONTRASENA_MAXIMA_BYTES = 72;

export interface CambioDeContrasena {
  actual: string;
  nueva: string;
  confirmacion: string;
}
export type CampoDeContrasena = keyof CambioDeContrasena;
export type ErroresDeContrasena = Partial<Record<CampoDeContrasena, string>>;

export function errorDeContrasenaNueva(nueva: string, email?: string | null, actual = ''): string | undefined {
  if (!nueva) return 'Escribe tu contraseña nueva.';
  if (nueva.length < CONTRASENA_MINIMA) return `La contraseña debe tener al menos ${CONTRASENA_MINIMA} caracteres`;
  if (new TextEncoder().encode(nueva).length > CONTRASENA_MAXIMA_BYTES) {
    return `La contraseña es demasiado larga (máximo ${CONTRASENA_MAXIMA_BYTES} caracteres)`;
  }
  if (!nueva.trim()) return 'La contraseña no puede ser solo espacios';
  if (email && nueva.trim().toLowerCase() === email.trim().toLowerCase()) return 'La contraseña no puede ser igual a tu correo';
  if (actual && nueva === actual) return 'La contraseña nueva tiene que ser distinta de la actual';
  return undefined;
}

export function erroresDeCambioDeContrasena(datos: CambioDeContrasena, email?: string | null): ErroresDeContrasena {
  const errores: ErroresDeContrasena = {};
  if (!datos.actual) errores.actual = 'Escribe tu contraseña actual.';
  const nueva = errorDeContrasenaNueva(datos.nueva, email, datos.actual);
  if (nueva) errores.nueva = nueva;
  if (!datos.confirmacion) errores.confirmacion = 'Repite la contraseña nueva.';
  else if (datos.confirmacion !== datos.nueva) errores.confirmacion = 'No coincide con la contraseña nueva.';
  return errores;
}

// A qué campo va un error del servidor: 400 es la contraseña actual (incorrecta), 422 la nueva (una regla, como "demasiado
// común"). Lo demás (429 por demasiados intentos, 409 de una cuenta de Google, sin conexión) va arriba del formulario.
export function campoDelErrorDelServidor(status: number): CampoDeContrasena | null {
  if (status === 400) return 'actual';
  if (status === 422) return 'nueva';
  return null;
}
