import { Navigate, Outlet, useLocation } from 'react-router';
import { useAuth } from './AuthContext';
import { rutaDeLogin, rutaInicialPara, type Rol } from './rutasPorRol';
import { estaSaliendoPorCierreManual } from './sesion';

// Sin sesión lleva al login (y vuelve aquí al entrar). Con un rol que no corresponde, a la ruta inicial de ese rol.
export function RequireRole({ roles }: { roles: Rol[] }) {
  const { sesion } = useAuth();
  const { pathname, search } = useLocation();
  if (!sesion.token) {
    if (estaSaliendoPorCierreManual()) return null;
    return <Navigate to={rutaDeLogin(`${pathname}${search}`)} replace />;
  }
  if (!roles.some((rol) => rol === sesion.rol)) return <Navigate to={rutaInicialPara(sesion.rol)} replace />;
  return <Outlet />;
}
