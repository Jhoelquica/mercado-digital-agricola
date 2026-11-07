import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { RouterProvider } from 'react-router/dom';
import { ApiError } from './api/errors';
import { AuthProvider } from './auth/AuthContext';
import { alCerrarSesion, leerSesion, terminarSalidaPorCierreManual, type MotivoCierre } from './auth/sesion';
import { Toasts, toast } from './components/Toast';
import { IdiomaProvider } from './idioma/IdiomaContext';
import { router } from './router';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Un 4xx no se arregla reintentando; un corte de red o un 5xx puede que sí.
      retry: (intentos, error) => intentos < 2 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
    },
  },
});

// Lo que tiene que pasar en la interfaz al cerrarse la sesión, venga de donde venga (botón o 401). Fuera de un componente
// para que quede registrado una sola vez. Si expiró no se navega aquí: una ruta privada la saca RequireRole y una pública
// se queda donde está, con el aviso de AppLayout.
alCerrarSesion((motivo: MotivoCierre) => {
  queryClient.clear(); // los datos de esta cuenta no deben quedar a la vista de la siguiente
  if (motivo === 'manual') {
    void router.navigate('/').finally(terminarSalidaPorCierreManual);
    toast('Sesión cerrada. ¡Hasta pronto! 👋');
  }
});

export function App() {
  // Con la aplicación ya abierta, tocar un aviso del celular llega como mensaje del service worker (ver src/sw.ts).
  useEffect(() => {
    const alMensaje = (e: MessageEvent) => {
      if (e.data?.tipo === 'abrir-notificaciones' && leerSesion().token) void router.navigate('/notificaciones');
    };
    navigator.serviceWorker?.addEventListener('message', alMensaje);
    return () => navigator.serviceWorker?.removeEventListener('message', alMensaje);
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <IdiomaProvider>
        <AuthProvider>
          <RouterProvider router={router} />
          <Toasts />
        </AuthProvider>
      </IdiomaProvider>
    </QueryClientProvider>
  );
}
