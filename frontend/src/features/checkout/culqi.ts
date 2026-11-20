import { CULQI_PUBLIC_KEY } from '../../lib/config';

// Culqi v4: los datos de la tarjeta se escriben dentro de su propia ventana y a esta aplicación solo le llega el token.
const URL_CULQI = 'https://checkout.culqi.com/js/v4';

export type MetodoCulqi = 'tarjeta' | 'yape';

interface CulqiGlobal {
  publicKey: string;
  token?: { id: string } | null;
  error?: { user_message?: string } | null;
  settings: (opciones: { title: string; currency: string; amount: number }) => void;
  options: (opciones: { paymentMethods: Record<MetodoCulqi, boolean> }) => void;
  open: () => void;
  close: () => void;
}

declare global {
  interface Window {
    Culqi?: CulqiGlobal;
    culqi?: () => void;
    // Culqi la llama cuando el comprador cierra su ventana (si existe). No avisa por window.culqi.
    closeCheckout?: () => void;
  }
}

interface PagoEnCurso {
  alToken: (tokenId: string) => void;
  alError: (mensaje: string) => void;
  // El comprador cerró la ventana de Culqi sin que llegara un token: el pedido sigue guardado y se puede volver a pagar.
  alCerrar?: () => void;
}

let pagoEnCurso: PagoEnCurso | null = null;
let carga: Promise<boolean> | null = null;

// Culqi llama a window.culqi tanto al tokenizar como al fallar.
function alResponderCulqi(): void {
  const culqi = window.Culqi;
  if (!pagoEnCurso || !culqi) return;
  if (culqi.token) {
    // Culqi no cierra su ventana al tokenizar: quien cree que "no pasó nada" podría volver a tocar Pagar y generar un
    // segundo token para el mismo pedido. Por eso el pago en curso se limpia ANTES de procesarlo: ese segundo token ya no
    // encuentra nada que cobrar.
    const pago = pagoEnCurso;
    pagoEnCurso = null;
    culqi.close();
    pago.alToken(culqi.token.id);
  } else if (culqi.error) {
    pagoEnCurso.alError(culqi.error.user_message || 'Revisa los datos de tu tarjeta e intenta de nuevo.');
  }
}

// Culqi tiene dos maneras de cerrar su ventana: con una llama a window.closeCheckout y con la otra no avisa a nadie (por
// eso el checkout vuelve a habilitar su botón apenas la abre, sin esperar esto). Si ya llegó el token, el pago en curso
// ya se limpió y cerrar no cambia nada.
function alCerrarCulqi(): void {
  if (!pagoEnCurso || window.Culqi?.token) return;
  pagoEnCurso.alCerrar?.();
}

export function cargarCulqi(): Promise<boolean> {
  if (window.Culqi) return Promise.resolve(true);
  carga ??= new Promise<boolean>((resolver) => {
    const script = document.createElement('script');
    script.src = URL_CULQI;
    script.async = true;
    script.onload = () => resolver(Boolean(window.Culqi));
    script.onerror = () => {
      script.remove();
      carga = null; // el próximo intento vuelve a pedirlo
      resolver(false);
    };
    document.head.appendChild(script);
  });
  return carga;
}

interface OpcionesDePago extends PagoEnCurso {
  monto: number;
  metodo: MetodoCulqi;
}

// false: no se pudo cargar la pasarela.
export async function abrirCulqi({ monto, metodo, alToken, alError, alCerrar }: OpcionesDePago): Promise<boolean> {
  const disponible = await cargarCulqi();
  const culqi = window.Culqi;
  if (!disponible || !culqi) return false;
  pagoEnCurso = { alToken, alError, alCerrar };
  window.culqi = alResponderCulqi;
  window.closeCheckout = alCerrarCulqi;
  culqi.publicKey = CULQI_PUBLIC_KEY;
  culqi.settings({ title: 'Chakra Shop', currency: 'PEN', amount: Math.round(monto * 100) });
  culqi.options({ paymentMethods: { tarjeta: metodo === 'tarjeta', yape: metodo === 'yape' } });
  // Sin restos de una apertura anterior: un token o un error viejos confundirían al cerrar o al responder.
  culqi.token = null;
  culqi.error = null;
  culqi.open();
  return true;
}
