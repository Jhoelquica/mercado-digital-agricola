// Pruebas del puente con la ventana de Culqi (src/features/checkout/culqi.ts), con un Culqi falso.   npm test
import { afterEach, expect, test, vi } from 'vitest';
import { abrirCulqi } from './culqi';

interface CulqiFalso {
  publicKey: string;
  token: { id: string } | null;
  error: { user_message?: string } | null;
  settings: ReturnType<typeof vi.fn>;
  options: ReturnType<typeof vi.fn>;
  open: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
}

type VentanaFalsa = { Culqi: CulqiFalso; culqi?: () => void; closeCheckout?: () => void };

function ventana(): VentanaFalsa {
  const culqi: CulqiFalso = { publicKey: '', token: null, error: null, settings: vi.fn(), options: vi.fn(), open: vi.fn(), close: vi.fn() };
  const falsa: VentanaFalsa = { Culqi: culqi };
  (globalThis as unknown as { window: VentanaFalsa }).window = falsa;
  return falsa;
}

async function abrir(w: VentanaFalsa) {
  const alToken = vi.fn(), alError = vi.fn(), alCerrar = vi.fn();
  expect(await abrirCulqi({ monto: 45.5, metodo: 'tarjeta', alToken, alError, alCerrar })).toBe(true);
  expect(w.Culqi.open).toHaveBeenCalledTimes(1);
  return { alToken, alError, alCerrar };
}

afterEach(() => { delete (globalThis as { window?: unknown }).window; });

test('cerrar la ventana de Culqi sin pagar avisa al checkout (y no hay token que cobrar)', async () => {
  const w = ventana();
  const { alToken, alCerrar } = await abrir(w);
  w.closeCheckout?.();
  expect(alCerrar).toHaveBeenCalledTimes(1);
  expect(alToken).not.toHaveBeenCalled();
});

test('con el token, se cobra una sola vez: un segundo token o cerrar después ya no hacen nada', async () => {
  const w = ventana();
  const { alToken, alCerrar } = await abrir(w);
  w.Culqi.token = { id: 'tkn_test_1' };
  w.culqi?.();
  expect(alToken).toHaveBeenCalledWith('tkn_test_1');
  expect(w.Culqi.close).toHaveBeenCalledTimes(1);
  w.Culqi.token = { id: 'tkn_test_2' };
  w.culqi?.();
  w.closeCheckout?.();
  expect(alToken).toHaveBeenCalledTimes(1);
  expect(alCerrar).not.toHaveBeenCalled();
});

test('un error de Culqi llega al checkout con su mensaje', async () => {
  const w = ventana();
  const { alError, alToken } = await abrir(w);
  w.Culqi.error = { user_message: 'Tarjeta sin fondos' };
  w.culqi?.();
  expect(alError).toHaveBeenCalledWith('Tarjeta sin fondos');
  expect(alToken).not.toHaveBeenCalled();
});

test('un token viejo de otra apertura no se toma como pagado al cerrar', async () => {
  const w = ventana();
  w.Culqi.token = { id: 'tkn_de_antes' };
  const { alCerrar } = await abrir(w);
  expect(w.Culqi.token).toBeNull();
  w.closeCheckout?.();
  expect(alCerrar).toHaveBeenCalledTimes(1);
});
