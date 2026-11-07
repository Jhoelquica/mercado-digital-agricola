// Fase 7: las 11 pantallas del panel admin en React, con la sesión del admin QA, a 375 y 1280 px. Revisa que cada una
// cargue sin errores de página, sin desborde horizontal y con su contenido, y guarda capturas (CAPTURAS=<carpeta>).
// No escribe nada: solo lee.
//   npm run e2e:admin
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';
import { BASE, iniciarSesionApi, leerEnvQa, marcador, sesionDelFrontend, texto, vigilarErrores } from './comun.mjs';

const env = leerEnvQa();
const { ok, cerrar } = marcador();
const admin = await iniciarSesionApi(env.QA_ADMIN_EMAIL, env.QA_ADMIN_PASSWORD);
const CAPTURAS = process.env.CAPTURAS || null;
if (CAPTURAS) mkdirSync(CAPTURAS, { recursive: true });

// Ruta, lo que tiene que aparecer cuando terminó de cargar y un texto propio de la pantalla.
const PANTALLAS = [
  ['', '#admin-resumen .resumen-stats', 'Ventas de la plataforma'],
  ['transferencias', '#transferencias-lista', 'Transferencias por revisar'],
  ['pagos', '#pagos-por-hacer-cabecera .pph-cabecera', 'Pagos ya hechos'],
  ['sin-entregar', '#sin-entregar-lista', 'Pedidos pagados sin entregar'],
  ['reclamos-ruc', '#reclamos-ruc-lista', 'Reclamos de RUC por revisar'],
  ['transportistas', '#transportistas-lista', 'Transportistas de carga'],
  ['viajes', '#viajes-admin-lista', 'Viajes en camión'],
  ['destinos', '#agencia-despacho-admin .agencia-despacho-admin-cuerpo', 'Punto de despacho a agencias'],
  ['productores', '#admin-productores-lista .productor-admin-card', 'Productores registrados'],
  ['capacidad', '#pendientes-capacidad-lista', 'Pedidos esperando vehículo con más capacidad'],
  ['transito', '#productos-transito-lista', 'Productos en tránsito'],
];

const b = await chromium.launch({ channel: 'msedge', headless: true });
for (const ancho of [375, 1280]) {
  const ctx = await b.newContext({ viewport: { width: ancho, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' });
  await ctx.addInitScript((s) => localStorage.setItem('agro_sesion', s), sesionDelFrontend(admin, { nombre: 'Admin', email: env.QA_ADMIN_EMAIL }));
  const p = await ctx.newPage();
  const errores = vigilarErrores(p);
  for (const [ruta, listo, propio] of PANTALLAS) {
    await p.goto(`${BASE}/admin${ruta ? `/${ruta}` : ''}`, { waitUntil: 'domcontentloaded' });
    await p.locator(listo).first().waitFor({ timeout: 30000 }).catch(() => {});
    await p.waitForTimeout(ruta === '' || ruta === 'productores' || ruta === 'destinos' ? 2500 : 1200);
    const nombre = `${ancho}px /admin/${ruta}`;
    ok(`${nombre}: cargó`, await p.locator(listo).count() > 0);
    ok(`${nombre}: "${propio}"`, (await texto(p.locator('main'))).includes(propio.toLocaleLowerCase('es-PE')));
    const desborde = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    ok(`${nombre}: sin desborde horizontal`, desborde <= 0, `${desborde}px`);
    if (CAPTURAS) await p.screenshot({ path: `${CAPTURAS}/react-${ancho}-${ruta || 'inicio'}.png`, fullPage: true });
  }
  ok(`${ancho}px: sin errores de página`, !errores.length, errores);
  await ctx.close();
}
cerrar();
await b.close();
