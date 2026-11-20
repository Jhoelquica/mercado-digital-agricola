export const MENSAJE_SESION_EXPIRADA = 'Tu sesión expiró. Inicia sesión de nuevo para continuar.';

export class ApiError extends Error {
  status: number;
  // El "detail" tal como vino del servidor: algunas respuestas traen datos además del mensaje (p. ej. el 409 de un pedido
  // sin pagar trae ese pedido, para que el checkout ofrezca pagarlo o reemplazarlo).
  detalle: unknown;

  constructor(message: string, status: number, detalle: unknown = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detalle = detalle;
  }
}

// Mismo tope que el servidor (imagenes.py): se avisa antes de subir un archivo que igual sería rechazado.
const TAMANO_MAXIMO_IMAGEN = 5 * 1024 * 1024;

export function exigirImagenLiviana(archivo: File | null | undefined): void {
  if (archivo && archivo.size > TAMANO_MAXIMO_IMAGEN) throw new ApiError('La imagen no puede pesar más de 5 MB.', 413);
}

const ETIQUETAS_DE_CAMPO: Record<string, string> = {
  password: 'Contraseña',
  email: 'Correo',
  nombre: 'Nombre',
  ruc: 'RUC',
  nombre_comercial: 'Nombre del negocio',
};

interface ErrorDeValidacion {
  loc?: (string | number)[];
  msg?: string;
}

// FastAPI devuelve los errores de validación (422) como una lista de {loc, msg}: se convierte en un
// texto legible ("costo_semillas: debe estar entre 0 y 9999999.99") en vez de mostrar el JSON crudo.
export function mensajeDeDetalle(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'mensaje' in detail && typeof detail.mensaje === 'string') return detail.mensaje;
  if (Array.isArray(detail)) {
    return (detail as ErrorDeValidacion[]).map((e) => {
      const campo = Array.isArray(e.loc) ? e.loc.filter((parte) => parte !== 'body').join(' › ') : '';
      const mensaje = String(e.msg || '').replace(/^Value error, /, '');
      return campo ? `${ETIQUETAS_DE_CAMPO[campo] || campo}: ${mensaje}` : mensaje;
    }).join(' · ');
  }
  return JSON.stringify(detail);
}
