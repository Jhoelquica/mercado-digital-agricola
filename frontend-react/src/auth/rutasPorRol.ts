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
