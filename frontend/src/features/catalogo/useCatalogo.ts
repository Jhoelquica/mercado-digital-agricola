import { useQuery } from '@tanstack/react-query';
import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { productos } from '../../api/productos';

// El catálogo público: lo comparten la landing, el menú de categorías, la búsqueda y "También te puede gustar".
export function useCatalogo(enabled = true) {
  return useQuery({ queryKey: ['catalogo'], queryFn: productos.listar, enabled });
}

export interface EstadoDetalle {
  volverA?: string;
  // Sección a la que bajar al abrirlo ("Califica" en Mis Pedidos va directo a las reseñas). Se usa una vez.
  seccion?: string;
}

// Recuerda desde dónde se llegó al detalle para que su botón "Volver" regrese ahí. Si ya se estaba en un detalle (un
// producto relacionado), se conserva la página original en vez de sobrescribirla.
export function useAbrirProducto() {
  const navigate = useNavigate();
  const { pathname, state } = useLocation();
  return useCallback((id: string) => {
    const volverA = pathname.startsWith('/producto/') ? (state as EstadoDetalle | null)?.volverA : pathname;
    void navigate(`/producto/${id}`, { state: { volverA } satisfies EstadoDetalle });
  }, [navigate, pathname, state]);
}
