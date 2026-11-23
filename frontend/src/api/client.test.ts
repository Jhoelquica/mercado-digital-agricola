import { afterEach, describe, expect, it, vi } from 'vitest';
import { conectarSesion, request } from './client';
import { tokenVigente } from '../auth/jwt';

// Un token de mentira con su exp (el cliente solo lo decodifica para saber si venció; quien decide es el servidor).
function token(expEnSegundos: number): string {
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `${b64({ alg: 'RS256', typ: 'JWT' })}.${b64({ sub: 'u-1', rol: 'comprador', exp: expEnSegundos })}.firma`;
}
const ahora = () => Math.floor(Date.now() / 1000);

function conSesion(actual: string | null) {
  const expiradas: string[] = [];
  conectarSesion({
    token: () => actual,
    tokenVigente: () => (actual && tokenVigente(actual) ? actual : null),
    alExpirar: (t) => { expiradas.push(t); },
  });
  return expiradas;
}

function respuestas(...estados: number[]) {
  const llamadas: { url: string; autorizacion: string | undefined }[] = [];
  const fetchFalso = vi.fn(async (url: string, init?: RequestInit) => {
    llamadas.push({ url, autorizacion: (init?.headers as Record<string, string> | undefined)?.Authorization });
    const estado = estados[Math.min(llamadas.length - 1, estados.length - 1)] ?? 200;
    return new Response(JSON.stringify(estado === 200 ? { vista: llamadas.at(-1)?.autorizacion ? 'dueño' : 'visitante' } : { detail: 'no' }), { status: estado });
  });
  vi.stubGlobal('fetch', fetchFalso);
  return llamadas;
}

afterEach(() => { vi.unstubAllGlobals(); });

describe('una ruta pública que muestra más a su dueño (auth: opcional)', () => {
  it('no manda un token vencido: pide como visitante y no cierra nada', async () => {
    const expiradas = conSesion(token(ahora() - 60));
    const llamadas = respuestas(200);
    expect(await request('productos', '/productos/1', { auth: 'opcional' })).toEqual({ vista: 'visitante' });
    expect(llamadas).toHaveLength(1);
    expect(llamadas[0]?.autorizacion).toBeUndefined();
    expect(expiradas).toEqual([]);
  });

  it('con un token vigente que el gateway rechaza (sesión revocada): cierra la sesión y repite como visitante', async () => {
    const vigente = token(ahora() + 600);
    const expiradas = conSesion(vigente);
    const llamadas = respuestas(401, 200);
    expect(await request('productos', '/productos/1', { auth: 'opcional' })).toEqual({ vista: 'visitante' });
    expect(llamadas.map((l) => l.autorizacion)).toEqual([`Bearer ${vigente}`, undefined]);
    expect(expiradas).toEqual([vigente]);
  });

  it('con un token vigente que vale: una sola petición, con el token', async () => {
    const vigente = token(ahora() + 600);
    const expiradas = conSesion(vigente);
    const llamadas = respuestas(200);
    expect(await request('productos', '/productos/1', { auth: 'opcional' })).toEqual({ vista: 'dueño' });
    expect(llamadas).toHaveLength(1);
    expect(expiradas).toEqual([]);
  });
});

describe('una ruta con sesión (auth: true)', () => {
  it('un 401 cierra la sesión y avisa, sin reintentar', async () => {
    const vigente = token(ahora() + 600);
    const expiradas = conSesion(vigente);
    const llamadas = respuestas(401);
    await expect(request('pedidos', '/pedidos/mios', { auth: true })).rejects.toMatchObject({ status: 401 });
    expect(llamadas).toHaveLength(1);
    expect(expiradas).toEqual([vigente]);
  });
});
