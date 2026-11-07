import { conectarSesion } from '../api/client';
import { ApiError, MENSAJE_SESION_EXPIRADA } from '../api/errors';
import { usuarios } from '../api/usuarios';
import { vaciarCarrito } from '../features/checkout/carrito';
import { quitarAvisosAlCerrarSesion, sincronizarAvisosCelular } from '../lib/avisos';
import { decodeJwt, tokenVigente } from './jwt';

// La sesión vive fuera de React: el cliente de la API necesita el token nuevo en la misma línea en que se inicia sesión,
// y el manejo del 401 ocurre fuera de cualquier componente. AuthContext solo la expone a las pantallas.
export interface Sesion {
  token: string | null;
  usuarioId: string | null;
  nombre: string | null;
  email: string | null;
  rol: string | null;
  tipoComprador: string | null;
  productorId: string | null;
  repartidorId: string | null;
}

export type MotivoCierre = 'manual' | 'expirada';

const SESSION_KEY = 'agro_sesion';
const SIN_SESION: Sesion = {
  token: null,
  usuarioId: null,
  nombre: null,
  email: null,
  rol: null,
  tipoComprador: null,
  productorId: null,
  repartidorId: null,
};

let sesion: Sesion = SIN_SESION;
const oyentes = new Set<() => void>();
const oyentesDeCierre = new Set<(motivo: MotivoCierre) => void>();

// Había una sesión guardada, pero su token ya había vencido.
export let sesionVencidaAlIniciar = false;

// La sesión expiró: una ruta privada lleva al login con el motivo, y una pública se queda donde está con un aviso.
// RequireRole, por su parte, no manda al login a quien está saliendo a propósito (el router tarda en llegar a "/").
let sesionRecienExpirada = false;
let saliendoPorCierreManual = false;

export const haySesionRecienExpirada = (): boolean => sesionRecienExpirada;
export const estaSaliendoPorCierreManual = (): boolean => saliendoPorCierreManual;

export function olvidarSesionRecienExpirada(): void {
  if (!sesionRecienExpirada) return;
  sesionRecienExpirada = false;
  oyentes.forEach((o) => o());
}

export function terminarSalidaPorCierreManual(): void {
  saliendoPorCierreManual = false;
}

function publicar(nueva: Sesion): void {
  sesion = nueva;
  oyentes.forEach((o) => o());
}

// Misma clave y mismo formato que el frontend actual, para no cerrar las sesiones que ya existen.
function guardarSesion(): void {
  const { token, usuarioId, nombre, email, rol, tipoComprador } = sesion;
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ token, usuarioId, nombre, email, rol, tipoComprador }));
  } catch { /* sin almacenamiento: la sesión dura lo que esta pestaña */ }
}

function borrarSesionGuardada(): void {
  try { localStorage.removeItem(SESSION_KEY); } catch { /* sin almacenamiento */ }
}

function cargarSesionGuardada(): void {
  let raw: string | null = null;
  try { raw = localStorage.getItem(SESSION_KEY); } catch { return; }
  if (!raw) return;
  try {
    const datos = JSON.parse(raw) as Partial<Sesion>;
    if (!datos.token || !tokenVigente(datos.token)) {
      // Los tokens duran 60 minutos: restaurar uno vencido dejaría la interfaz "con sesión" y cada llamada fallando.
      borrarSesionGuardada();
      sesionVencidaAlIniciar = true;
      return;
    }
    sesion = { ...SIN_SESION, ...datos };
  } catch { /* sesión corrupta, se ignora */ }
}

export const leerSesion = (): Sesion => sesion;

export function suscribirSesion(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => { oyentes.delete(oyente); };
}

export function alCerrarSesion(oyente: (motivo: MotivoCierre) => void): () => void {
  oyentesDeCierre.add(oyente);
  return () => { oyentesDeCierre.delete(oyente); };
}

export function actualizarSesion(cambios: Partial<Sesion>): void {
  publicar({ ...sesion, ...cambios });
  guardarSesion();
}

// El único camino para cerrar sesión. Limpia todo; lo único que distingue el motivo:
// - 'manual' quita los avisos de este celular y vacía el carrito.
// - 'expirada' conserva el carrito e ignora la respuesta tardía de un token que ya no es el de la sesión actual
//   (varias peticiones en vuelo pueden responder 401 a la vez).
export function cerrarSesion(motivo: MotivoCierre, tokenUsado?: string): void {
  if (motivo === 'expirada' && (!sesion.token || (tokenUsado && tokenUsado !== sesion.token))) return;
  if (motivo === 'manual') {
    void quitarAvisosAlCerrarSesion();
    vaciarCarrito();
    saliendoPorCierreManual = true;
  } else {
    sesionRecienExpirada = true;
  }
  borrarSesionGuardada();
  publicar(SIN_SESION);
  oyentesDeCierre.forEach((o) => o(motivo));
}

export async function iniciarSesionConToken(token: string): Promise<void> {
  const payload = decodeJwt(token);
  if (!payload) throw new Error('Token inválido recibido del servidor.');

  sesionRecienExpirada = false;
  publicar({ ...SIN_SESION, token, usuarioId: payload.sub, rol: payload.rol });

  let cambios: Partial<Sesion> = { nombre: 'Usuario' };
  try {
    const usuario = await usuarios.obtener(payload.sub);
    cambios = { nombre: usuario.nombre, email: usuario.email, tipoComprador: usuario.tipo_comprador || null };
  } catch { /* sin el nombre también se puede entrar */ }

  // El token se rechazó mientras se pedían los datos: la sesión ya se cerró y no hay que volver a guardarla.
  if (sesion.token !== token) throw new ApiError(MENSAJE_SESION_EXPIRADA, 401);

  actualizarSesion(cambios);
  void sincronizarAvisosCelular();
}

export async function iniciarSesionConCorreo(email: string, password: string): Promise<void> {
  const respuesta = await usuarios.login({ email, password });
  await iniciarSesionConToken(respuesta.access_token);
}

cargarSesionGuardada();
conectarSesion({
  token: () => sesion.token,
  alExpirar: (tokenUsado) => cerrarSesion('expirada', tokenUsado),
});
