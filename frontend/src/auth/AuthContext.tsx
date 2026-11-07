import { createContext, use, useMemo, useSyncExternalStore, type ReactNode } from 'react';
import { cerrarSesion, iniciarSesionConCorreo, leerSesion, suscribirSesion, type Sesion } from './sesion';

interface ValorAuth {
  sesion: Sesion;
  autenticado: boolean;
  iniciarSesion: (email: string, password: string) => Promise<void>;
  cerrarSesion: () => void;
}

const AuthContext = createContext<ValorAuth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const sesion = useSyncExternalStore(suscribirSesion, leerSesion);
  const valor = useMemo<ValorAuth>(() => ({
    sesion,
    autenticado: Boolean(sesion.token),
    iniciarSesion: iniciarSesionConCorreo,
    cerrarSesion: () => cerrarSesion('manual'),
  }), [sesion]);
  return <AuthContext value={valor}>{children}</AuthContext>;
}

export function useAuth(): ValorAuth {
  const valor = use(AuthContext);
  if (!valor) throw new Error('useAuth debe usarse dentro de AuthProvider');
  return valor;
}
