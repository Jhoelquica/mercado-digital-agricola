import { Navigate, Outlet, useLocation } from 'react-router';
import { useAuth } from './AuthContext';
import { rutaInicialPara, type Rol } from './rutasPorRol';
import { estaSaliendoPorCierreManual } from './sesion';

// Sin sesión lleva al login (y vuelve aquí al entrar). Con un rol que no corresponde, a la ruta inicial de ese rol.
export function RequireRole({ roles }: { roles: Rol[] }) {
  const { sesion } = useAuth();
  const { pathname, search } = useLocation();
  if (!sesion.token) {
    if (estaSaliendoPorCierreManual()) return null;
    return <Navigate to="/login" replace state={{ desde: `${pathname}${search}` }} />;
  }
  if (!roles.some((rol) => rol === sesion.rol)) return <Navigate to={rutaInicialPara(sesion.rol)} replace />;
  return <Outlet />;
}
