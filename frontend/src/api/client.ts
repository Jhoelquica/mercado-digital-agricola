import { ApiError, MENSAJE_SESION_EXPIRADA, mensajeDeDetalle } from './errors';

export type Servicio = 'usuarios' | 'productores' | 'productos' | 'pedidos' | 'transporte' | 'notificaciones' | 'pagos' | 'certificacion';

// true: exige sesión. 'opcional': ruta pública que muestra más a su dueño (un borrador). false: nunca manda token.
export type ModoToken = boolean | 'opcional';

interface OpcionesPeticion {
  method?: string;
  body?: unknown;
  auth?: ModoToken;
}

interface PuenteDeSesion {
  token: () => string | null;
  // Un servicio respondió 401 a una petición hecha CON sesión: el token venció (duran 60 minutos) o ya no vale.
  // Recibe el token con el que se hizo la petición, para que la respuesta tardía de una sesión anterior no cierre la nueva.
  alExpirar: (tokenUsado: string) => void;
}

let sesion: PuenteDeSesion = { token: () => null, alExpirar: () => {} };

// La sesión vive en auth/sesion.ts, que se conecta aquí al cargarse: así este módulo no depende de React ni de auth.
export function conectarSesion(puente: PuenteDeSesion): void {
  sesion = puente;
}

// Todo pasa por el gateway (Traefik) en <VITE_API_URL>/<servicio>: un solo origen, sin CORS y sin exponer los puertos.
const API_URL = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '');

export function apiBase(servicio: Servicio): string {
  return `${API_URL}/${servicio}`;
}

function errorDeConexion(servicio: Servicio): ApiError {
  return new ApiError(`No se pudo conectar con el servicio de ${servicio}. ¿Está corriendo?`, 0);
}

function exigirToken(): string {
  const token = sesion.token();
  if (!token) throw new ApiError('Debes iniciar sesión para continuar.', 401);
  return token;
}

async function leerJson(resp: Response): Promise<unknown> {
  const text = await resp.text();
  if (!text) return null;
  try { return JSON.parse(text); } catch { return null; }
}

function detalleDe(data: unknown): unknown {
  return data && typeof data === 'object' && 'detail' in data ? data.detail : null;
}

export async function request<T>(servicio: Servicio, ruta: string, { method = 'GET', body, auth = false }: OpcionesPeticion = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  let tokenUsado: string | null = null;
  if (auth === 'opcional') {
    // Un token vencido no cierra la sesión aquí (el servidor lo trata como visitante, sin 401).
    const token = sesion.token();
    if (token) headers.Authorization = `Bearer ${token}`;
  } else if (auth) {
    tokenUsado = exigirToken();
    headers.Authorization = `Bearer ${tokenUsado}`;
  }

  let resp: Response;
  try {
    resp = await fetch(`${apiBase(servicio)}${ruta}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw errorDeConexion(servicio);
  }

  const data = await leerJson(resp);

  if (!resp.ok) {
    if (resp.status === 401 && tokenUsado) {
      sesion.alExpirar(tokenUsado);
      throw new ApiError(MENSAJE_SESION_EXPIRADA, 401);
    }
    if (resp.status === 403) throw new ApiError('No tienes permiso para esta acción.', 403);
    throw new ApiError(mensajeDeDetalle(detalleDe(data) || `Error ${resp.status} en ${servicio}`), resp.status);
  }

  return data as T;
}

export async function requestFormData<T>(servicio: Servicio, ruta: string, formData: FormData): Promise<T> {
  const token = exigirToken();

  let resp: Response;
  try {
    resp = await fetch(`${apiBase(servicio)}${ruta}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
    });
  } catch {
    throw errorDeConexion(servicio);
  }

  const data = await leerJson(resp);

  if (!resp.ok) {
    if (resp.status === 401) {
      sesion.alExpirar(token);
      throw new ApiError(MENSAJE_SESION_EXPIRADA, 401);
    }
    if (resp.status === 403) throw new ApiError('No tienes permiso para esta acción.', 403);
    throw new ApiError(mensajeDeDetalle(detalleDe(data) || `Error ${resp.status} en ${servicio}`), resp.status);
  }

  return data as T;
}

// Un archivo protegido (la constancia de una transferencia, que solo ve el admin): se pide con el token y se devuelve
// como Blob para mostrarlo con URL.createObjectURL, sin exponer el almacenamiento. Aquí el 403 trae el detalle del servidor.
export async function requestBlob(servicio: Servicio, ruta: string): Promise<Blob> {
  const token = exigirToken();

  let resp: Response;
  try {
    resp = await fetch(`${apiBase(servicio)}${ruta}`, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    throw errorDeConexion(servicio);
  }

  if (!resp.ok) {
    if (resp.status === 401) {
      sesion.alExpirar(token);
      throw new ApiError(MENSAJE_SESION_EXPIRADA, 401);
    }
    let detalle: unknown = null;
    try { detalle = detalleDe(await resp.json()); } catch { /* sin cuerpo JSON */ }
    throw new ApiError(detalle ? mensajeDeDetalle(detalle) : `Error ${resp.status} en ${servicio}`, resp.status);
  }

  return resp.blob();
}
