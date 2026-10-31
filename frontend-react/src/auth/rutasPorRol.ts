// Los roles que existen hoy. Una cuenta antigua puede traer otro ("verificador", retirado en la presentación del
// 2026-10-01): por eso el rol de la sesión es un texto, y sin panel propio aterriza en "/".
export type Rol = 'comprador' | 'productor' | 'repartidor' | 'admin';

export const ROLES_CON_SESION: Rol[] = ['comprador', 'productor', 'repartidor', 'admin'];

// A qué ruta aterriza cada rol apenas hay sesión. Solo decide el destino inicial, no restringe la navegación: cualquiera
// puede volver a "/" desde el navbar. Quien restringe es RequireRole.
const RUTA_INICIAL_POR_ROL: Partial<Record<string, string>> = {
  productor: '/productor',
  repartidor: '/repartidor',
  admin: '/admin',
};

export function rutaInicialPara(rol: string | null | undefined): string {
  return (rol && RUTA_INICIAL_POR_ROL[rol]) || '/';
}

// Excepción: quien se registra para reclamar un RUC que está en otra cuenta sigue en su perfil, donde se reclama.
export function rutaTrasRegistro(rol: string | null | undefined, { reclamaRuc = false } = {}): string {
  return reclamaRuc ? '/perfil' : rutaInicialPara(rol);
}

// El valor de ?next=: solo una ruta de esta misma aplicación. Cualquier otra cosa ("//otro.sitio", "https://…", "/\otro")
// se descarta para que un enlace no pueda llevar a otro sitio después de iniciar sesión.
export function rutaInternaSegura(next: string | null | undefined): string | null {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return null;
  try {
    const url = new URL(next, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    if (url.pathname === '/login' || url.pathname === '/registro') return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

export function rutaDeLogin(next: string): string {
  return `/login?next=${encodeURIComponent(next)}`;
}
