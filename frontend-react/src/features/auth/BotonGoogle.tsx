import { useEffect, useRef } from 'react';
import { GOOGLE_CLIENT_ID } from '../../lib/config';

// Google Identity Services: el botón "Continuar con Google" lo dibuja Google en un marco propio, y la cuenta elegida
// llega como un id_token que se canjea en POST /usuarios/oauth/google. A esta aplicación nunca le llega la contraseña.
const URL_GIS = 'https://accounts.google.com/gsi/client';

interface RespuestaCredencial {
  credential: string;
}

interface GoogleIdentity {
  accounts: {
    id: {
      initialize: (opciones: { client_id: string; callback: (r: RespuestaCredencial) => void }) => void;
      renderButton: (contenedor: HTMLElement, opciones: Record<string, unknown>) => void;
    };
  };
}

declare global {
  interface Window {
    google?: GoogleIdentity;
  }
}

let carga: Promise<boolean> | null = null;

function cargarGis(): Promise<boolean> {
  if (window.google?.accounts?.id) return Promise.resolve(true);
  carga ??= new Promise<boolean>((resolver) => {
    const script = document.createElement('script');
    script.src = URL_GIS;
    script.async = true;
    script.onload = () => resolver(Boolean(window.google?.accounts?.id));
    script.onerror = () => {
      script.remove();
      carga = null;
      resolver(false);
    };
    document.head.appendChild(script);
  });
  return carga;
}

// Si el script no carga (bloqueado, sin red) o no hay Client ID, no hay botón; el resto del formulario funciona igual.
export function BotonGoogle({ id, alRecibirCredencial }: { id: string; alRecibirCredencial: (credencial: string) => void }) {
  const contenedor = useRef<HTMLDivElement>(null);
  const callback = useRef(alRecibirCredencial);
  callback.current = alRecibirCredencial;

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return undefined;
    let vigente = true;
    let observador: ResizeObserver | undefined;
    void cargarGis().then((ok) => {
      const el = contenedor.current;
      const google = window.google;
      if (!ok || !vigente || !el || !google) return;
      // Un solo callback para toda la página: el que esté montado (login o registro) recibe la credencial.
      google.accounts.id.initialize({ client_id: GOOGLE_CLIENT_ID, callback: (r) => callback.current(r.credential) });
      // Google admite entre 200 y 400 px: se vuelve a dibujar solo si cambia el ancho disponible.
      let anchoDibujado = 0;
      const dibujar = () => {
        const ancho = Math.max(200, Math.min(400, Math.floor(el.clientWidth)));
        if (!el.clientWidth || ancho === anchoDibujado) return;
        anchoDibujado = ancho;
        el.innerHTML = '';
        google.accounts.id.renderButton(el, { theme: 'outline', size: 'large', shape: 'rectangular', text: 'continue_with', width: ancho });
      };
      dibujar();
      observador = new ResizeObserver(dibujar);
      observador.observe(el);
    });
    return () => {
      vigente = false;
      observador?.disconnect();
    };
  }, []);

  return <div ref={contenedor} id={id} className="auth-google-btn" />;
}
