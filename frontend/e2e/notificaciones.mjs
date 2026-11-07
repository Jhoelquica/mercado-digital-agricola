// Notificaciones de qa-comprador por React (solo lee: no activa los avisos al celular ni cambia nada):
//   la lista coincide con la API, los filtros por tipo cuentan y filtran, el resumen lateral, "Cargar más", el acceso a
//   un pedido y la tarjeta de avisos al celular. También /?vista=notificaciones (lo que abre un aviso del celular).
//   npm run e2e:notificaciones
import { chromium } from 'playwright';
import { BASE, contiene, iniciarSesionApi, leerEnvQa, marcador, sesionDelFrontend, texto, vigilarErrores } from './comun.mjs';

const env = leerEnvQa();
const { ok, cerrar } = marcador();
const comprador = await iniciarSesionApi(env.QA_EMAIL, env.QA_PASSWORD);
const avisos = await comprador.api('/notificaciones/notificaciones');
console.log('   avisos de qa-comprador en la API:', avisos.length);

const b = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' });
await ctx.addInitScript((s) => { if (!sessionStorage.getItem('puesta')) { localStorage.setItem('agro_sesion', s); sessionStorage.setItem('puesta', '1'); } }, sesionDelFrontend(comprador, { nombre: 'QA Comprador', email: env.QA_EMAIL }));
const p = await ctx.newPage();
const errores = vigilarErrores(p);

// Lo que abre un aviso del celular.
await p.goto(`${BASE}/?vista=notificaciones`, { waitUntil: 'domcontentloaded' });
await p.waitForURL((u) => u.pathname === '/notificaciones', { timeout: 20000 });
ok('/?vista=notificaciones abre Notificaciones', true);
const lista = p.locator('#notificaciones-list');
await p.locator('#notificaciones-list .notif-card, #notificaciones-empty').first().waitFor({ timeout: 30000 });

const cuenta = async (clave) => Number(await texto(p.locator(`.notif-filtro[data-filtro="${clave}"] .notif-filtro-cuenta`)));
ok('el filtro "Todas" cuenta lo mismo que la API', (await cuenta('todas')) === avisos.length, { pantalla: await cuenta('todas'), api: avisos.length });
const porTipo = {};
for (const clave of ['pedido', 'envio', 'producto']) porTipo[clave] = await cuenta(clave);
console.log('   por tipo:', JSON.stringify(porTipo));
const enResumen = async (etiqueta) => Number(await texto(p.locator('#notif-resumen li', { hasText: etiqueta }).locator('strong')));
ok('el resumen lateral repite los conteos', (await enResumen('Pedidos registrados')) === porTipo.pedido && (await enResumen('Envíos actualizados')) === porTipo.envio);
const visibles = await lista.locator('.notif-card').count();
ok('muestra de a 10 como máximo al empezar', visibles === Math.min(10, avisos.length), visibles);
if (avisos.length > 10) {
  await p.click('#btn-notif-cargar-mas');
  ok('"Cargar más" trae 10 más', (await lista.locator('.notif-card').count()) === Math.min(20, avisos.length));
}

// Cada filtro deja solo los suyos.
for (const clave of ['envio', 'pedido']) {
  if (!porTipo[clave]) continue;
  await p.click(`.notif-filtro[data-filtro="${clave}"]`);
  await p.waitForTimeout(400);
  ok(`el filtro "${clave}" queda marcado y muestra sus avisos`, (await p.locator(`.notif-filtro[data-filtro="${clave}"]`).getAttribute('aria-pressed')) === 'true'
    && (await lista.locator('.notif-card').count()) === Math.min(10, porTipo[clave]));
}
await p.click('.notif-filtro[data-filtro="todas"]');

// Un aviso de pedido lleva a Mis Pedidos.
const deUnPedido = lista.locator('.notif-card', { has: p.locator('.notif-chip') }).first();
if (await deUnPedido.count()) {
  await deUnPedido.locator('.notif-accion').click();
  await p.waitForURL((u) => u.pathname === '/comprador/pedidos', { timeout: 15000 });
  ok('"Ver mis pedidos" lleva a Mis Pedidos', true);
  await p.goBack();
  await p.locator('#notificaciones-list .notif-card').first().waitFor({ timeout: 20000 });
}

// Avisos al celular: la tarjeta dice en qué estado está (en un navegador de prueba no se activa).
const tarjeta = p.locator('#avisos-celular');
if (await tarjeta.isVisible()) ok('la tarjeta de avisos al celular dice el estado de este navegador', await contiene(tarjeta, 'En este celular'));
else ok('sin avisos al celular configurados en el servidor, la tarjeta no se muestra', true);

await p.click('#btn-refrescar-notificaciones');
await p.waitForTimeout(1500);
ok('Refrescar mantiene la lista', (await cuenta('todas')) === avisos.length);
// Esperado: Mis Pedidos pide el envío de pedidos viejos que nunca tuvieron uno (404, igual que el frontend anterior).
const inesperados = errores.filter((e) => !/404 .*\/api\/transporte\/envios\/[0-9a-f-]+\)$/.test(e));
ok('sin errores de página', !inesperados.length, inesperados);
cerrar();
await b.close();
