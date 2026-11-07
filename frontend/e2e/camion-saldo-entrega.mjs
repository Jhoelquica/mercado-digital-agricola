// Un pedido por camión que ya llegó a su ciudad, hasta el final, por las pantallas de React:
//   qa-comprador paga el saldo (30%) con la tarjeta de prueba de Culqi desde Mis Pedidos → el transportista ve "Saldo
//   pagado", descarga y entrega con el código del comprador y una foto → el pedido queda entregado y el transportista,
//   libre.
// PEDIDO=<id>; por defecto, el último pedido por camión de qa-comprador cuyo envío está "en_destino".
//   npm run e2e:camion-saldo
import { chromium } from 'playwright';
import { BASE, contiene, fotoDePrueba, iniciarSesionApi, leerEnvQa, marcador, pagarConCulqi, sesionDelFrontend, texto, vigilarErrores } from './comun.mjs';

const env = leerEnvQa();
const { ok, cerrar } = marcador();
const comprador = await iniciarSesionApi(env.QA_EMAIL, env.QA_PASSWORD);
const transportista = await iniciarSesionApi(env.QA_TRANSPORTISTA_EMAIL, env.QA_TRANSPORTISTA_PASSWORD);
let pedidoId = process.env.PEDIDO || null;
if (!pedidoId) {
  for (const p of await comprador.api('/pedidos/pedidos/mios')) {
    if (p.modalidad_envio !== 'camion') continue;
    const envio = await comprador.api(`/transporte/envios/${p.id}`, { conEstado: true });
    if (envio.estado === 200 && envio.datos.estado === 'en_destino') { pedidoId = p.id; break; }
  }
}
if (!pedidoId) { console.log('No hay un pedido por camión de qa-comprador en destino.'); process.exit(1); }
const pagoAntes = await comprador.api(`/pagos/pagos/${pedidoId}`);
console.log('   pedido', pedidoId, '· pago', pagoAntes.estado, '· por pagar', pagoAntes.monto_a_pagar, pagoAntes.parte_a_pagar);

const b = await chromium.launch({ channel: 'msedge', headless: true });
const conSesion = async (sesion, nombre, email) => {
  const c = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' });
  await c.addInitScript((s) => { if (!sessionStorage.getItem('puesta')) { localStorage.setItem('agro_sesion', s); sessionStorage.setItem('puesta', '1'); } }, sesionDelFrontend(sesion, { nombre, email }));
  const p = await c.newPage();
  return { c, p, errores: vigilarErrores(p) };
};
const toast = (p, t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 25000 });

// ---- 1. El comprador paga el saldo con tarjeta (React)
const C = await conSesion(comprador, 'QA Comprador', env.QA_EMAIL);
await C.p.goto(`${BASE}/comprador/pedidos`, { waitUntil: 'domcontentloaded' });
const suPedido = C.p.locator(`.pedido-card[data-pedido-id="${pedidoId}"]`);
await suPedido.waitFor({ timeout: 30000 });
if (pagoAntes.estado === 'adelanto_pagado') {
  const boton = suPedido.locator('.btn-reintentar-pago');
  ok('Mis Pedidos ofrece "Pagar el saldo"', await contiene(boton, 'Pagar el saldo'));
  await boton.click();
  await C.p.locator('#checkout-panel-2.active').waitFor({ timeout: 20000 });
  ok('el checkout cobra el saldo (30%)', await contiene(C.p.locator('#checkout-panel-2'), 'Saldo (30%)'), await texto(C.p.locator('#checkout-panel-2 .checkout-resumen')).catch(() => ''));
  console.log('   a pagar:', await texto(C.p.locator('#checkout-total-paso2')));
  await pagarConCulqi(C.p, 'tarjeta', env);
  ok('el pago del saldo se confirma en el checkout', true);
}
const pagoDespues = await comprador.api(`/pagos/pagos/${pedidoId}`);
let envio = await comprador.api(`/transporte/envios/${pedidoId}`);
for (let i = 0; i < 10 && !envio.saldo_pagado; i++) { await new Promise((r) => setTimeout(r, 1500)); envio = await comprador.api(`/transporte/envios/${pedidoId}`); }
console.log('   DATO: tras el saldo: pago', pagoDespues.estado, pagoDespues.metodo, '/', pagoDespues.metodo_saldo, '· envío', envio.estado, 'saldo_pagado', envio.saldo_pagado);
ok('el saldo queda pagado con tarjeta (pago aprobado y envío con saldo pagado)', pagoDespues.estado === 'aprobado' && pagoDespues.metodo_saldo === 'tarjeta' && envio.saldo_pagado === true, { pago: pagoDespues.estado, metodo_saldo: pagoDespues.metodo_saldo, saldo: envio.saldo_pagado });

// El código de entrega aparece al comprador recién con el saldo pagado.
await C.p.goto(`${BASE}/comprador/pedidos`, { waitUntil: 'domcontentloaded' });
const pinCaja = C.p.locator(`.pedido-card[data-pedido-id="${pedidoId}"] .mp-pin-digitos`);
await pinCaja.waitFor({ timeout: 30000 });
const pin = (await pinCaja.getAttribute('aria-label')).replace(/\D/g, '');
ok('el comprador ve su código de 4 dígitos', /^\d{4}$/.test(pin));

// ---- 2. El transportista descarga y entrega (React)
const T = await conSesion(transportista, 'QA Transportista', env.QA_TRANSPORTISTA_EMAIL);
await T.p.goto(`${BASE}/repartidor`, { waitUntil: 'domcontentloaded' });
const tarjeta = T.p.locator('#envios-list .pedido-card', { hasText: envio.id.slice(0, 8) });
await tarjeta.waitFor({ timeout: 30000 });
ok('el transportista ve "Saldo pagado: ya puedes descargar"', await contiene(tarjeta, 'Saldo pagado: ya puedes descargar'));
await tarjeta.locator('.btn-abrir-entrega').click();
const form = tarjeta.locator('.form-confirmar-entrega');
await form.waitFor();
await form.locator('input[name="pin"]').fill(pin === '0000' ? '1111' : '0000');
await form.locator('.foto-entrega-input').setInputFiles(await fotoDePrueba(T.p, 'entrega-camion-qa.jpg', '#b6d7a8'));
await form.locator('.foto-entrega[data-estado="lista"]').waitFor();
await form.locator('button[type=submit]').click();
await T.p.waitForTimeout(2500);
ok('con un código equivocado no entrega', (await transportista.api('/transporte/envios')).find((e) => e.id === envio.id)?.estado === 'en_destino');
await form.locator('input[name="pin"]').fill(pin);
await form.locator('button[type=submit]').click();
await toast(T.p, 'Entrega confirmada ✅');
await T.p.waitForTimeout(1500);

const envioFinal = await comprador.api(`/transporte/envios/${pedidoId}`);
const pedidoFinal = await comprador.api(`/pedidos/pedidos/${pedidoId}`);
const perfilT = await transportista.api('/transporte/repartidores/me');
const viaje = envioFinal.viaje_id ? (await transportista.api('/transporte/viajes/mios')).find((v) => v.id === envioFinal.viaje_id) : null;
console.log('   DATO: entregado: envío', envioFinal.estado, '· pedido', pedidoFinal.estado, '· viaje', viaje?.estado ?? '(ya no figura en sus viajes)', '· transportista', perfilT.estado_disponibilidad);
ok('el envío y el pedido quedan entregados', envioFinal.estado === 'entregado' && pedidoFinal.estado === 'entregado', { envio: envioFinal.estado, pedido: pedidoFinal.estado });
ok('el transportista queda libre', perfilT.estado_disponibilidad === 'disponible', perfilT.estado_disponibilidad);

await C.p.reload({ waitUntil: 'domcontentloaded' });
await C.p.locator(`.pedido-card[data-pedido-id="${pedidoId}"]`).waitFor({ timeout: 30000 });
ok('el comprador lo ve entregado', await contiene(C.p.locator(`.pedido-card[data-pedido-id="${pedidoId}"]`), 'Entregado'));
// Esperados: Mis Pedidos pide el envío de pedidos viejos que nunca tuvieron uno (404, igual que el frontend anterior) y el
// servidor rechaza el código equivocado de la prueba.
const esperado = (e) => /404 .*\/api\/transporte\/envios\/[0-9a-f-]+\)$/.test(e) || /\/entregar\)$/.test(e) || /El código no coincide/.test(e);
const inesperados = [...C.errores, ...T.errores].filter((e) => !esperado(e));
ok('sin errores de página', !inesperados.length, inesperados);
cerrar();
await b.close();
