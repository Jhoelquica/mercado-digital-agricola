import { Outlet } from 'react-router';

// El comprador no tiene panel con pestañas: Mis Pedidos se abre desde el menú de la cuenta y el pedido desde el carrito.

export default function CompradorLayout() {
  return <Outlet />;
}
