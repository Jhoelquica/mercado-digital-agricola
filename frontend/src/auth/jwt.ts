export interface PayloadToken {
  sub: string;
  rol: string;
  exp?: number;
}

export function decodeJwt(token: string): PayloadToken | null {
  try {
    const payload = token.split('.')[1] as string;
    const json = decodeURIComponent(
      atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join(''),
    );
    return JSON.parse(json) as PayloadToken;
  } catch {
    return null;
  }
}

// ¿El token sigue vigente por su fecha de vencimiento (exp)? Es solo una comprobación local para no
// restaurar una sesión que ya venció: quien decide de verdad es el servidor.
export function tokenVigente(token: string): boolean {
  const payload = decodeJwt(token);
  if (!payload) return false;
  if (typeof payload.exp !== 'number') return true;
  return payload.exp * 1000 > Date.now();
}
