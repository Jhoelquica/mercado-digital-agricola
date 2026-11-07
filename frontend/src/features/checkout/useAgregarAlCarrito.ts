import { useLocation, useNavigate } from 'react-router';
import { useAuth } from '../../auth/AuthContext';
import { rutaDeLogin } from '../../auth/rutasPorRol';
import { toast } from '../../components/Toast';
import { agregarLinea } from './carrito';

export function useAgregarAlCarrito() {
  const { autenticado } = useAuth();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  return (producto: Parameters<typeof agregarLinea>[0], cantidad = 1, unidad: string | null = null) => {
    if (!autenticado) {
      toast('Inicia sesión para armar tu pedido 🌱', 'error');
      void navigate(rutaDeLogin(`${pathname}${search}`));
      return;
    }
    agregarLinea(producto, cantidad, unidad);
  };
}
