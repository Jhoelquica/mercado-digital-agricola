import { createBrowserRouter, redirect, type LoaderFunctionArgs, type RouteObject } from 'react-router';
import { RequireRole } from '../auth/RequireRole';
import { ROLES_CON_SESION, rutaInicialPara } from '../auth/rutasPorRol';
import { leerSesion } from '../auth/sesion';
import { AppLayout } from '../layouts/AppLayout';
import { ErrorDeRuta, NoEncontrado } from '../pages/NoEncontrado';
import { SECCIONES_ADMIN, SECCIONES_PRODUCTOR, SECCIONES_REPARTIDOR, type SeccionDePanel } from './secciones';

const Cargando = (
  <div className="cargando-ruta" role="status" aria-label="Cargando">
    <div className="spinner" />
  </div>
);

// Carga diferida: cada rol tiene su propio archivo y solo se descarga al entrar a su panel.
const componente = (cargar: () => Promise<{ default: React.ComponentType }>) => async () => ({ Component: (await cargar()).default });

const paginaPendiente = componente(() => import('../pages/PaginaPendiente'));

function pendiente(path: string, titulo: string): RouteObject {
  return { path, handle: { titulo }, lazy: paginaPendiente };
}

function rutasDePanel(secciones: SeccionDePanel[]): RouteObject[] {
  return secciones.map(({ ruta, etiqueta }) => (ruta ? pendiente(ruta, etiqueta) : { index: true, handle: { titulo: etiqueta }, lazy: paginaPendiente }));
}

// El comprador aterriza en "/" (rutaInicialPara), los demás roles en su panel: igual que al recargar en el frontend actual.
// Solo vale para la página con la que se abre la aplicación; después, el logo sí lleva a "/" a cualquier rol.
let aterrizajePendiente = window.location.pathname === '/';

function cargarInicio({ request }: LoaderFunctionArgs) {
  const debeAterrizar = aterrizajePendiente;
  aterrizajePendiente = false;
  // Un aviso del celular abre "/?vista=notificaciones" (ver src/sw.ts y notificaciones/avisos_externos.py).
  if (new URL(request.url).searchParams.get('vista') === 'notificaciones') return redirect('/notificaciones');
  if (debeAterrizar) {
    const destino = rutaInicialPara(leerSesion().rol);
    if (destino !== '/') return redirect(destino);
  }
  return null;
}

export const router = createBrowserRouter([
  {
    path: '/login',
    errorElement: <ErrorDeRuta />,
    hydrateFallbackElement: Cargando,
    lazy: componente(() => import('../features/auth/LoginPage')),
  },
  {
    path: '/registro',
    errorElement: <ErrorDeRuta />,
    hydrateFallbackElement: Cargando,
    lazy: componente(() => import('../features/auth/RegistroPage')),
  },
  // Página pública del QR: sin navbar y sin token. Los QR ya impresos apuntan a /verificar.html (ver certificacion/main.py).
  {
    path: '/verificar',
    errorElement: <ErrorDeRuta />,
    hydrateFallbackElement: Cargando,
    lazy: componente(() => import('../features/verificar/VerificarPage')),
  },
  {
    path: '/verificar.html',
    loader: ({ request }) => redirect(`/verificar${new URL(request.url).search}`),
  },
  {
    element: <AppLayout />,
    errorElement: <ErrorDeRuta />,
    hydrateFallbackElement: Cargando,
    children: [
      { index: true, loader: cargarInicio, lazy: componente(() => import('../features/catalogo/LandingPage')) },
      { path: 'producto/:id', lazy: componente(() => import('../features/producto/DetallePage')) },
      {
        element: <RequireRole roles={ROLES_CON_SESION} />,
        children: [
          { path: 'notificaciones', handle: { titulo: 'Notificaciones' }, lazy: componente(() => import('../features/notificaciones/NotificacionesPage')) },
          { path: 'perfil', handle: { titulo: 'Mi perfil' }, lazy: componente(() => import('../features/perfil/PerfilPage')) },
        ],
      },
      {
        path: 'comprador',
        element: <RequireRole roles={['comprador']} />,
        children: [{
          lazy: componente(() => import('../features/comprador/CompradorLayout')),
          children: [
            { index: true, loader: () => redirect('/comprador/pedidos') },
            { path: 'pedidos', handle: { titulo: 'Mis Pedidos' }, lazy: componente(() => import('../features/pedidos/MisPedidosPage')) },
            // Es un modal, como en el frontend actual: AppLayout mantiene la página de atrás a la vista.
            { path: 'checkout', handle: { titulo: 'Tu pedido', modal: true }, lazy: componente(() => import('../features/checkout/CheckoutPage')) },
          ],
        }],
      },
      {
        path: 'productor',
        element: <RequireRole roles={['productor']} />,
        children: [{
          lazy: componente(() => import('../features/productor/ProductorLayout')),
          children: rutasDePanel(SECCIONES_PRODUCTOR),
        }],
      },
      {
        path: 'repartidor',
        element: <RequireRole roles={['repartidor']} />,
        children: [{
          lazy: componente(() => import('../features/repartidor/RepartidorLayout')),
          children: rutasDePanel(SECCIONES_REPARTIDOR),
        }],
      },
      {
        path: 'admin',
        element: <RequireRole roles={['admin']} />,
        children: [{
          lazy: componente(() => import('../features/admin/AdminLayout')),
          children: rutasDePanel(SECCIONES_ADMIN),
        }],
      },
      { path: '*', element: <NoEncontrado /> },
    ],
  },
]);
