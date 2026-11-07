// Fase 6, entrega a domicilio de punta a punta, todo en React:
//   qa-comprador compra (Yape del sandbox de Culqi) → qa-repartidor acepta la propuesta y pasa a "en camino" con la
//   ubicación simulada → se comprueba que la ubicación se manda cada 15 s, que el GPS se detiene al ocultar la pestaña, al
//   salir del panel y al cerrar sesión, y que vuelve al regresar → entrega con el código del comprador y una foto → el GPS
//   queda detenido (sin intervalos ni vigilancias vivas) → qa-comprador ve su pedido entregado.
// Requiere que qa-repartidor esté disponible con su moto (es el vehículo más chico: le llega la propuesta).
//   npm run e2e:entrega        (PEDIDO=<id> retoma un pedido cuyo envío ya va "en camino" con qa-repartidor;
//                               MEDIO=tarjeta paga con la tarjeta de prueba de Culqi en lugar de Yape)
import { chromium } from 'playwright';
import { API, BASE, contiene, entrarPorPantalla, fotoDePrueba, iniciarSesionApi, leerEnvQa, marcador, pagarConCulqi, sesionDelFrontend, texto, vigilarErrores } from './comun.mjs';

const MEDIO = process.env.MEDIO === 'tarjeta' ? 'tarjeta' : 'yape';

const env = leerEnvQa();
const { ok, cerrar } = marcador();
const comprador = await iniciarSesionApi(env.QA_EMAIL, env.QA_PASSWORD);
const repartidor = await iniciarSesionApi(env.QA_REPARTIDOR_EMAIL, env.QA_REPARTIDOR_PASSWORD);
const perfilRepartidor = await repartidor.api('/transporte/repartidores/me');
console.log('   qa-repartidor:', perfilRepartidor.tipo_vehiculo, perfilRepartidor.capacidad_maxima_kg, 'kg,', perfilRepartidor.estado_disponibilidad);
const catalogo = await (await fetch(`${API}/productos/productos`)).json();
const producto = catalogo.find((x) => x.nombre === 'Maíz' && x.estado === 'disponible' && x.stock >= 5) || catalogo.find((x) => x.estado === 'disponible' && x.unidad_medida === 'kg' && x.stock >= 5);
console.log('   producto:', producto?.nombre, producto?.id);

const b = await chromium.launch({ channel: 'msedge', headless: true });
const UBICACION = { latitude: -13.1588, longitude: -74.2232 };

// ---- 1. Compra (o se retoma un pedido ya en camino)
const PEDIDO = process.env.PEDIDO || null;
const ctxC = await b.newContext({ viewport: { width: 1280, height: 800 }, locale: 'es-PE', timezoneId: 'America/Lima', geolocation: { latitude: -13.16, longitude: -74.22 }, permissions: ['geolocation'] });
await ctxC.addInitScript((s) => { if (!sessionStorage.getItem('puesta')) { localStorage.setItem('agro_sesion', s); sessionStorage.setItem('puesta', '1'); } }, sesionDelFrontend(comprador, { nombre: 'QA Comprador', email: env.QA_EMAIL }));
const pc = await ctxC.newPage();
const erroresC = vigilarErrores(pc);
let pedidoId = PEDIDO;
pc.on('response', async (r) => { if (r.request().method() === 'POST' && /\/api\/pedidos\/pedidos$/.test(r.url()) && r.ok()) pedidoId = (await r.json()).id; });
if (!PEDIDO) {
await pc.goto(`${BASE}/producto/${producto.id}`, { waitUntil: 'domcontentloaded' });
await pc.waitForSelector('#detalle-compra');
for (let i = 0; i < 4; i++) await pc.click('button[aria-label="Aumentar cantidad"]');
await pc.click('#detalle-compra button.btn-lg');
await pc.click('.cart-btn');
await pc.waitForSelector('#modal-pedido .carrito-item');
await pc.fill('#pedido-telefono', '912345678');
await pc.click('#btn-usar-mi-ubicacion');
await pc.waitForFunction(() => document.getElementById('mapa-destino-ayuda')?.textContent?.includes('-13.16000'));
await pc.click('#btn-checkout-paso1-siguiente');
await pc.waitForSelector('#checkout-panel-2.active');
await pagarConCulqi(pc, MEDIO, env);
ok(`qa-comprador compra y paga con ${MEDIO === 'yape' ? 'Yape' : 'tarjeta'} (sandbox)`, Boolean(pedidoId), pedidoId);
}
console.log('   DATO: pedido', pedidoId);

// La propuesta le llega a qa-repartidor (el pago crea el envío por un evento).
let propuesta = PEDIDO ? (await repartidor.api('/transporte/envios')).find((x) => x.pedido_id === PEDIDO && x.estado === 'en_camino') : null;
for (let i = 0; i < 20 && !propuesta; i++) {
  await new Promise((r) => setTimeout(r, 1500));
  propuesta = (await repartidor.api('/transporte/envios/propuestas')).find((x) => x.pedido_id === pedidoId);
}
ok('el envío se le propone a qa-repartidor', Boolean(propuesta));
if (!propuesta) { cerrar(); await b.close(); process.exit(1); }
console.log('   DATO: envío', propuesta.id);

// ---- 2. Repartidor (con la ubicación simulada y el GPS vigilado)
const ctxR = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima', geolocation: UBICACION, permissions: ['geolocation'] });
await ctxR.addInitScript(() => {
  // Cuenta las vigilancias del GPS y los intervalos que crea el seguimiento (por la pila: los crea seguimiento.tsx).
  window.__gps = { vigilancias: new Set(), intervalos: new Set() };
  const geo = navigator.geolocation;
  const vigilar = geo.watchPosition.bind(geo);
  const soltar = geo.clearWatch.bind(geo);
  geo.watchPosition = (...a) => { const id = vigilar(...a); window.__gps.vigilancias.add(id); return id; };
  geo.clearWatch = (id) => { window.__gps.vigilancias.delete(id); return soltar(id); };
  const crear = window.setInterval.bind(window);
  const borrar = window.clearInterval.bind(window);
  window.setInterval = (fn, ms, ...r) => { const id = crear(fn, ms, ...r); if (/seguimiento/.test(new Error().stack || '')) window.__gps.intervalos.add(id); return id; };
  window.clearInterval = (id) => { window.__gps.intervalos.delete(id); return borrar(id); };
});
const pr = await ctxR.newPage();
const erroresR = vigilarErrores(pr);
const envios = [];
pr.on('request', (r) => { if (r.method() === 'PATCH' && /\/envios\/[^/]+\/ubicacion$/.test(r.url())) envios.push(Date.now()); });
const gps = () => pr.evaluate(() => ({ vigilancias: window.__gps.vigilancias.size, intervalos: window.__gps.intervalos.size }));
const esperarToast = (t) => pr.locator('.toast', { hasText: t }).first().waitFor({ timeout: 20000 });

await entrarPorPantalla(pr, env.QA_REPARTIDOR_EMAIL, env.QA_REPARTIDOR_PASSWORD, '/repartidor');
const tarjeta = pr.locator('#envios-list .pedido-card', { hasText: propuesta.id.slice(0, 8) });
if (!PEDIDO) {
const tarjetaPropuesta = pr.locator('.propuesta-card', { hasText: propuesta.id.slice(0, 8) });
await tarjetaPropuesta.waitFor({ timeout: 30000 });
ok('la propuesta muestra distancia, peso y lo que gana', (await contiene(tarjetaPropuesta, 'Desde el almacén')) && (await contiene(tarjetaPropuesta, 'Peso')));
ok('antes de salir no hay GPS', JSON.stringify(await gps()) === JSON.stringify({ vigilancias: 0, intervalos: 0 }), await gps());
await tarjetaPropuesta.locator('.btn-aceptar-propuesta').click();
await esperarToast('Envío aceptado, ¡buen viaje! 🚚');
await tarjeta.waitFor({ timeout: 20000 });
await tarjeta.locator('.envio-contacto').waitFor({ timeout: 15000 }).catch(() => {});
ok('aceptado: ve el contacto del comprador', await contiene(tarjeta, '912345678'));
await pr.waitForTimeout(1000);
ok('asignado (sin salir): todavía sin GPS', (await gps()).vigilancias === 0 && (await gps()).intervalos === 0, await gps());
await tarjeta.locator('.btn-actualizar-envio').click();
await esperarToast('Listo: el envío está «En camino» ✅');
}
await pr.locator('.ubicacion-chip.ubicacion-ok').waitFor({ timeout: 20000 });
ok('en camino: "Compartiendo tu ubicación en tiempo real."', await contiene(pr.locator('.ubicacion-chip'), 'Compartiendo tu ubicación en tiempo real.'));
ok('una vigilancia de GPS y un intervalo', JSON.stringify(await gps()) === JSON.stringify({ vigilancias: 1, intervalos: 1 }), await gps());
const inicio = Date.now();
await pr.waitForTimeout(32000);
const enVentana = envios.filter((t) => t >= inicio);
const pausas = enVentana.slice(1).map((t, i) => t - enVentana[i]);
ok('manda la ubicación cada 15 s (2 envíos en 32 s)', enVentana.length === 2 && pausas.every((d) => d > 13000 && d < 17500), { envios: enVentana.length, pausas });
const enServidor = await repartidor.api(`/transporte/envios/${pedidoId}`).catch(() => null);
console.log('   ubicación guardada:', enServidor?.repartidor_latitud, enServidor?.repartidor_longitud);

// Pestaña oculta: se detiene; visible otra vez: vuelve.
await pr.evaluate(() => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  document.dispatchEvent(new Event('visibilitychange'));
});
await pr.waitForTimeout(500);
ok('pestaña oculta: GPS detenido', JSON.stringify(await gps()) === JSON.stringify({ vigilancias: 0, intervalos: 0 }), await gps());
await pr.evaluate(() => { delete document.hidden; delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange')); });
// El GPS simulado de Chromium deja de dar posiciones en la misma página cuando se soltaron todas las vigilancias, hasta que
// la ubicación cambia (un celular de verdad sigue dándolas): el repartidor "se mueve" un poco.
await ctxR.setGeolocation({ latitude: UBICACION.latitude + 0.0002, longitude: UBICACION.longitude });
await pr.locator('.ubicacion-chip.ubicacion-ok').waitFor({ timeout: 20000 });
ok('pestaña visible: vuelve el GPS', (await gps()).vigilancias === 1 && (await gps()).intervalos === 1, await gps());

// Salir del panel: se detiene y no manda nada; al volver, sigue.
await pr.locator('a[href="/"]').first().click();
await pr.waitForURL((u) => !u.pathname.startsWith('/repartidor'));
const alSalir = envios.length;
await pr.waitForTimeout(500);
ok('al salir del panel: GPS detenido', JSON.stringify(await gps()) === JSON.stringify({ vigilancias: 0, intervalos: 0 }), await gps());
await pr.waitForTimeout(17000);
ok('fuera del panel no manda la ubicación', envios.length === alSalir, envios.length - alSalir);
await pr.goto(`${BASE}/repartidor`, { waitUntil: 'domcontentloaded' });
await pr.locator('.ubicacion-chip.ubicacion-ok').waitFor({ timeout: 20000 });
ok('al volver: GPS otra vez', (await gps()).vigilancias === 1);

// Cerrar sesión: se detiene.
await pr.click('.nav-account-trigger');
await pr.click('.nav-account-logout');
await pr.waitForTimeout(800);
ok('al cerrar sesión: GPS detenido', JSON.stringify(await gps()) === JSON.stringify({ vigilancias: 0, intervalos: 0 }), await gps());
await entrarPorPantalla(pr, env.QA_REPARTIDOR_EMAIL, env.QA_REPARTIDOR_PASSWORD, '/repartidor');
await ctxR.setGeolocation({ latitude: UBICACION.latitude + 0.0004, longitude: UBICACION.longitude }); // ver arriba: se mueve
await pr.locator('.ubicacion-chip.ubicacion-ok').waitFor({ timeout: 20000 });
ok('al volver a entrar: GPS otra vez', (await gps()).vigilancias === 1);

// ---- 3. Entrega con código y foto
const pcMisPedidos = await ctxC.newPage();
await pcMisPedidos.goto(`${BASE}/comprador/pedidos`, { waitUntil: 'domcontentloaded' });
const pinCaja = pcMisPedidos.locator(`.pedido-card[data-pedido-id="${pedidoId}"] .mp-pin-digitos`);
await pinCaja.waitFor({ timeout: 30000 });
const pin = (await pinCaja.getAttribute('aria-label')).replace(/\D/g, '');
ok('el comprador ve su código de 4 dígitos', /^\d{4}$/.test(pin));
const tarjetaEnCamino = pr.locator('#envios-list .pedido-card', { hasText: propuesta.id.slice(0, 8) });
await tarjetaEnCamino.locator('.btn-abrir-entrega').click();
const form = tarjetaEnCamino.locator('.form-confirmar-entrega');
await form.waitFor();
await form.locator('input[name="pin"]').fill(pin);
await form.locator('button[type=submit]').click();
ok('sin foto no confirma', await contiene(form.locator('.form-entrega-mensaje'), 'Agrega la foto de la entrega antes de confirmar.'));
await form.locator('.foto-entrega-input').setInputFiles(await fotoDePrueba(pr, 'entrega-qa.jpg', '#e0c060'));
await form.locator('.foto-entrega[data-estado="lista"]').waitFor();
await form.locator('button[type=submit]').click();
await esperarToast('Entrega confirmada ✅');
await pr.waitForTimeout(1500);
ok('entregado: el envío pasa a "entregado"', await contiene(tarjetaEnCamino.locator('.pill-estado'), 'entregado'));
ok('después de entregar no queda GPS vivo', JSON.stringify(await gps()) === JSON.stringify({ vigilancias: 0, intervalos: 0 }), await gps());
const alEntregar = envios.length;
await pr.waitForTimeout(17000);
ok('después de entregar no manda más la ubicación', envios.length === alEntregar, envios.length - alEntregar);
ok('sin aviso de ubicación', (await pr.locator('.ubicacion-chip').count()) === 0);

// ---- 4. El comprador lo ve entregado
await pcMisPedidos.reload({ waitUntil: 'domcontentloaded' });
const suPedido = pcMisPedidos.locator(`.pedido-card[data-pedido-id="${pedidoId}"]`);
await suPedido.waitFor({ timeout: 30000 });
await pcMisPedidos.waitForTimeout(1500);
ok('qa-comprador ve su pedido entregado', await contiene(suPedido, 'Entregado'), await texto(suPedido.locator('.pill-estado').first()));
const finalServidor = await comprador.api(`/transporte/envios/${pedidoId}`);
console.log('   DATO: envío', finalServidor.id, finalServidor.estado, 'foto', finalServidor.foto_entrega_url);

ok('sin errores de página', !erroresC.length && !erroresR.length, [...erroresC, ...erroresR]);
cerrar();
await b.close();
