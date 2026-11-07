// Fase 7, con datos reales y todo por las pantallas de React:
//   1. El admin agrega (o vuelve a mostrar) una ciudad de prueba, "Ica (prueba QA)", con un camión chico: 2 sacos, sale con
//      el 50% (1 saco) y desde 1 saco por comprador. Revisa el círculo del radio en el mapa y el cálculo del camión.
//   2. qa-comprador compra 10 kg de Maíz por camión a esa ciudad (ubicación simulada en Ica, DNI simulado de RENIEC) y paga
//      el adelanto por transferencia, con una constancia de prueba.
//   3. El admin ve la constancia (blob con token) y aprueba la transferencia → el pedido queda confirmado; en "Pedidos sin
//      entregar" se ve con su envío y su pago.
//   4. qa-transportista@chakrashop-demo.pe se registra (credenciales en .env.qa; se crean la primera vez), arma su perfil
//      con un camión de 3000 kg y envía sus documentos (imágenes de prueba, nada real).
//   5. El admin abre sus documentos, lo verifica y le asigna el viaje.
//   6. El transportista hace salir y llegar el viaje desde /repartidor/viajes.
//   7. El admin oculta la ciudad de prueba (los compradores dejan de verla).
//   npm run e2e:transferencia-viaje
import { randomBytes } from 'node:crypto';
import { appendFileSync } from 'node:fs';
import { chromium } from 'playwright';
import { API, BASE, contiene, fotoDePrueba, iniciarSesionApi, leerEnvQa, marcador, sesionDelFrontend, texto, vigilarErrores } from './comun.mjs';

const CIUDAD = 'Ica (prueba QA)';
const ICA = { lat: -14.06777, lng: -75.72861 };
const DNI_RECEPTOR = '99999901'; // RENIEC simulado en el clúster local (pedidos/reniec.py)
let env = leerEnvQa();
const { ok, cerrar } = marcador();

// Las credenciales del transportista de prueba: se generan la primera vez y quedan en .env.qa (ignorado por git).
if (!env.QA_TRANSPORTISTA_EMAIL) {
  appendFileSync(new URL('../.env.qa', import.meta.url), `\nQA_TRANSPORTISTA_EMAIL=qa-transportista@chakrashop-demo.pe\nQA_TRANSPORTISTA_PASSWORD=Qa-${randomBytes(9).toString('base64url')}\n`);
  env = leerEnvQa();
}

const admin = await iniciarSesionApi(env.QA_ADMIN_EMAIL, env.QA_ADMIN_PASSWORD);
const comprador = await iniciarSesionApi(env.QA_EMAIL, env.QA_PASSWORD);
const catalogo = await (await fetch(`${API}/productos/productos`)).json();
const maiz = catalogo.find((x) => x.id === process.env.PRODUCTO) || catalogo.find((x) => x.nombre === 'Maíz' && x.estado === 'disponible' && x.unidad_medida === 'kg' && x.stock >= 10);
console.log('   producto:', maiz?.id, maiz?.nombre, maiz?.stock, maiz?.unidad_medida, 'S/', maiz?.precio);

const b = await chromium.launch({ channel: 'msedge', headless: true });
const nuevo = async (opciones = {}) => {
  const c = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima', ...opciones });
  const p = await c.newPage();
  return { c, p, errores: vigilarErrores(p) };
};
const conSesion = async (sesion, nombre, email, opciones) => {
  const r = await nuevo(opciones);
  await r.c.addInitScript((s) => { if (!sessionStorage.getItem('puesta')) { localStorage.setItem('agro_sesion', s); sessionStorage.setItem('puesta', '1'); } }, sesionDelFrontend(sesion, { nombre, email }));
  return r;
};
const toast = (p, t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 25000 });

// ---- 1. Ciudad de prueba (admin, /admin/destinos)
const A = await conSesion(admin, 'Admin', env.QA_ADMIN_EMAIL);
await A.p.goto(`${BASE}/admin/destinos`, { waitUntil: 'domcontentloaded' });
await A.p.locator('#agencia-despacho-admin .agencia-despacho-admin-cuerpo').waitFor({ timeout: 30000 });
const yaExiste = (await admin.api('/pedidos/destinos-lejanos/admin')).find((d) => d.ciudad === CIUDAD);
if (yaExiste) await A.p.locator(`.destino-card[data-destino-id="${yaExiste.id}"] .btn-editar-destino`).click();
else await A.p.click('#btn-nuevo-destino');
const fd = A.p.locator('#form-destino');
await fd.waitFor();
await fd.locator('input[name="ciudad"]').fill('');
await fd.locator('button[type=submit]').click();
ok('valida la ciudad', await contiene(fd.locator('.form-entrega-mensaje'), 'Escribe el nombre de la ciudad.'));
await fd.locator('input[name="ciudad"]').fill(CIUDAD);
await fd.locator('input[name="departamento"]').fill('Ica');
await fd.locator('input[name="latitud"]').fill(String(ICA.lat));
await fd.locator('input[name="longitud"]').fill(String(ICA.lng));
await fd.locator('input[name="radio_km"]').fill('15');
await fd.locator('input[name="activo"]').setChecked(true);
await fd.locator('input[name="agencia_disponible"]').setChecked(false);
ok('sin agencia, sus campos se apagan', await fd.locator('input[name="agencia_nombre"]').isDisabled());
await fd.locator('input[name="camion_disponible"]').setChecked(true);
await fd.locator('input[name="camion_precio_saco"]').fill('10');
await fd.locator('input[name="camion_minimo_sacos"]').fill('1');
await fd.locator('input[name="camion_capacidad_sacos"]').fill('2');
await fd.locator('input[name="camion_porcentaje_salida"]').fill('50');
await fd.locator('input[name="camion_dias"]').fill('1');
await fd.locator('textarea[name="nota"]').fill('Ciudad de prueba QA (Fase 7): no usar.');
ok('el cálculo del camión: sale con 1 saco, flete S/ 10.00', (await contiene(fd.locator('.form-destino-calculo'), 'El camión sale con 1 sacos (50% de 2)')) && (await contiene(fd.locator('.form-destino-calculo'), 'S/ 10.00')));
await A.p.waitForTimeout(800);
ok('el radio se dibuja como un círculo en el mapa', (await fd.locator('#mapa-form-destino path.leaflet-interactive').count()) === 1);
await fd.locator('button[type=submit]').click();
await toast(A.p, `${CIUDAD} guardada: los compradores ya ven estas condiciones.`);
const destino = (await admin.api('/pedidos/destinos-lejanos/admin')).find((d) => d.ciudad === CIUDAD);
ok('la ciudad queda visible con su camión', destino?.activo && destino?.camion_disponible && !destino?.agencia_disponible && destino?.camion_capacidad_sacos === 2, destino);
console.log('   DATO: destino', destino?.id, CIUDAD);

// ---- 2. qa-comprador compra por camión y paga el adelanto por transferencia (React)
const C = await conSesion(comprador, 'QA Comprador', env.QA_EMAIL, { permissions: ['geolocation'], geolocation: { latitude: ICA.lat, longitude: ICA.lng } });
await C.p.goto(`${BASE}/producto/${maiz.id}`, { waitUntil: 'domcontentloaded' });
const compra = C.p.locator('#detalle-compra');
await compra.waitFor({ timeout: 30000 });
for (let i = 0; i < 9; i++) await compra.locator('button[aria-label="Aumentar cantidad"]').click();
await compra.locator('button', { hasText: '+ Agregar al pedido' }).click();
await C.p.waitForTimeout(600);
await C.p.locator('.cart-btn').first().click();
await C.p.locator('#pedido-telefono').waitFor({ timeout: 20000 });
await C.p.fill('#pedido-telefono', '987654321');
await C.p.click('#btn-usar-mi-ubicacion');
await C.p.locator('#mapa-destino-ayuda', { hasText: CIUDAD }).waitFor({ timeout: 20000 });
ok('la ubicación cae en la ciudad de prueba', await contiene(C.p.locator('#mapa-destino-ayuda'), `Tu punto está en ${CIUDAD}`));
await C.p.click('#btn-checkout-paso1-siguiente');
const lejano = C.p.locator('#checkout-bloque-lejano');
await lejano.waitFor({ timeout: 20000 });
await lejano.locator('label:has(input[name="modalidad-envio"][value="camion"])').click();
ok('eligió el camión', await lejano.locator('input[name="modalidad-envio"][value="camion"]').isChecked());
await C.p.fill('#receptor-dni', DNI_RECEPTOR);
await C.p.click('#btn-verificar-dni');
await C.p.locator('#btn-confirmar-receptor').click({ timeout: 20000 });
await C.p.click('#metodo-transferencia-card');
ok('eligió transferencia', await C.p.locator('input[name="metodo-pago"][value="transferencia"]').isChecked());
console.log('   total del checkout:', await texto(C.p.locator('#checkout-total-paso2')));
await C.p.click('#btn-checkout-pagar');
const panelTransferencia = C.p.locator('#checkout-transferencia');
await panelTransferencia.waitFor({ timeout: 30000 });
const montoTransferencia = await texto(panelTransferencia.locator('.transferencia-monto strong'));
const pedido = (await comprador.api('/pedidos/pedidos/mios'))[0];
console.log('   DATO: pedido', pedido?.id, pedido?.estado, pedido?.modalidad_envio, pedido?.destino_ciudad, '· adelanto a transferir', montoTransferencia);
await C.p.locator('#foto-constancia').setInputFiles(await fotoDePrueba(C.p, 'constancia-qa.jpg', '#9fc5e8'));
const operacion = `QA${Date.now().toString().slice(-7)}`;
await C.p.fill('#numero-operacion', operacion);
await panelTransferencia.locator('button[type=submit]').click();
await C.p.getByText('¡Recibimos tu constancia!').waitFor({ timeout: 25000 });
const pagoEnRevision = await comprador.api(`/pagos/pagos/${pedido.id}`);
ok('el adelanto queda "en revisión" por transferencia', pagoEnRevision?.estado === 'en_revision' && pagoEnRevision?.metodo === 'transferencia', pagoEnRevision?.estado);

// ---- 3. El admin revisa y aprueba la transferencia (React)
await A.p.goto(`${BASE}/admin/transferencias`, { waitUntil: 'domcontentloaded' });
const tarjeta = A.p.locator(`.transferencia-card[data-pedido-id="${pedido.id}"]`);
await tarjeta.waitFor({ timeout: 30000 });
ok('la tarjeta muestra el n.º de operación', await contiene(tarjeta, operacion));
ok('la pestaña cuenta la transferencia', Number(await texto(A.p.locator('.admin-tab-btn', { hasText: 'Transferencias' }).locator('.badge-contador'))) >= 1);
await tarjeta.locator('.btn-ver-constancia').click();
const img = tarjeta.locator('.transferencia-constancia img');
await img.waitFor({ timeout: 20000 });
ok('la constancia se ve (blob pedido con el token)', await img.evaluate((el) => el.complete && el.naturalWidth > 0 && el.src.startsWith('blob:')));
await tarjeta.locator('.btn-rechazar-transferencia').click();
await tarjeta.locator('textarea[name="motivo"]').fill('no');
await tarjeta.locator('.form-rechazar-transferencia button[type=submit]').click();
ok('rechazar pide un motivo de 3 letras o más', await contiene(tarjeta.locator('.form-entrega-mensaje'), 'Escribe el motivo: el comprador lo va a leer'));
await tarjeta.locator('.btn-cancelar-revision').click();
await tarjeta.locator('.btn-aprobar-transferencia').click();
ok('aprobar pide confirmar con el monto y la operación', await contiene(tarjeta.locator('.transferencia-confirmar'), operacion));
await tarjeta.locator('.btn-confirmar-aprobacion').click();
await toast(A.p, 'Transferencia aprobada: el pedido sale a reparto 🚚');
await tarjeta.waitFor({ state: 'detached', timeout: 15000 });
const pedidoAprobado = await comprador.api(`/pedidos/pedidos/${pedido.id}`);
const pagoAprobado = await comprador.api(`/pagos/pagos/${pedido.id}`);
console.log('   DATO: tras aprobar: pedido', pedidoAprobado?.estado, '· pago', pagoAprobado?.estado, pagoAprobado?.metodo);
ok('el pedido queda confirmado (adelanto pagado)', pedidoAprobado?.estado === 'confirmado' && pagoAprobado?.estado === 'adelanto_pagado', { pedido: pedidoAprobado?.estado, pago: pagoAprobado?.estado });

// En "Pedidos sin entregar": su envío y su pago.
await A.p.goto(`${BASE}/admin/sin-entregar`, { waitUntil: 'domcontentloaded' });
const sinEntregar = A.p.locator(`.sin-entregar-card[data-pedido-id="${pedido.id}"]`);
await sinEntregar.waitFor({ timeout: 30000 });
ok('sin entregar: destino por camión, lo pagado y el envío', (await contiene(sinEntregar, `${CIUDAD} · camión`)) && (await contiene(sinEntregar, 'Adelanto pagado (70%) · transferencia')) && (await contiene(sinEntregar, 'Esperando que se llene el camión')),
  await texto(sinEntregar));

// ---- 4. El transportista se registra, arma su perfil y envía sus documentos (React)
const T = await nuevo();
let transportista = null;
try { transportista = await iniciarSesionApi(env.QA_TRANSPORTISTA_EMAIL, env.QA_TRANSPORTISTA_PASSWORD); } catch { /* todavía no existe */ }
if (!transportista) {
  await T.p.goto(`${BASE}/registro?rol=repartidor`, { waitUntil: 'domcontentloaded' });
  await T.p.locator('label:has(input[name="registro-rol-opcion"][value="repartidor"])').click();
  await T.p.fill('#registro-nombre', 'QA Transportista');
  await T.p.fill('#registro-email', env.QA_TRANSPORTISTA_EMAIL);
  await T.p.fill('#registro-password', env.QA_TRANSPORTISTA_PASSWORD);
  await T.p.locator('form button[type=submit]').first().click();
  await T.p.waitForURL((u) => u.pathname.startsWith('/repartidor'), { timeout: 30000 });
  transportista = await iniciarSesionApi(env.QA_TRANSPORTISTA_EMAIL, env.QA_TRANSPORTISTA_PASSWORD);
  ok('qa-transportista se registró como repartidor', transportista.rol === 'repartidor', transportista.rol);
} else {
  await T.c.addInitScript((s) => { if (!sessionStorage.getItem('puesta')) { localStorage.setItem('agro_sesion', s); sessionStorage.setItem('puesta', '1'); } }, sesionDelFrontend(transportista, { nombre: 'QA Transportista', email: env.QA_TRANSPORTISTA_EMAIL }));
  await T.p.goto(`${BASE}/repartidor`, { waitUntil: 'domcontentloaded' });
}
const perfilAntes = await transportista.api('/transporte/repartidores/me', { conEstado: true });
if (perfilAntes.estado === 404) {
  await T.p.locator('#form-repartidor').waitFor({ timeout: 30000 });
  await T.p.fill('#repartidor-nombre', 'QA Transportista');
  await T.p.fill('#repartidor-dni', '99999904');
  await T.p.selectOption('#repartidor-tipo-vehiculo', 'camion');
  await T.p.fill('#repartidor-capacidad-maxima', '3000');
  await T.p.locator('#form-repartidor button[type=submit]').click();
  await toast(T.p, '¡Perfil de repartidor creado!');
}
let perfilT = await transportista.api('/transporte/repartidores/me');
console.log('   DATO: transportista', perfilT.id, perfilT.tipo_vehiculo, perfilT.capacidad_maxima_kg, 'kg ·', perfilT.verificacion_estado);
if (perfilT.verificacion_estado !== 'verificado') {
  await T.p.goto(`${BASE}/repartidor/viajes`, { waitUntil: 'domcontentloaded' });
  await T.p.locator('.carga-panel').waitFor({ timeout: 30000 });
  if (perfilT.verificacion_estado !== 'pendiente') {
    await T.p.click('.btn-abrir-carga');
    const form = T.p.locator('#form-carga');
    await form.locator('input[name="placa"]').fill('ZZZ-999');
    await form.locator('input[name="licencia_numero"]').fill('Q99999904');
    const doc = await fotoDePrueba(T.p, 'documento-qa.jpg', '#d9d2e9');
    for (const campo of ['licencia', 'tarjeta_propiedad', 'soat']) await form.locator(`input[name="${campo}"]`).setInputFiles({ ...doc, name: `${campo}-qa.jpg` });
    await form.locator('button[type=submit]').click();
    await toast(T.p, 'Documentos enviados');
  }
  perfilT = await transportista.api('/transporte/repartidores/me');
  ok('sus documentos quedan por revisar', perfilT.verificacion_estado === 'pendiente', perfilT.verificacion_estado);

  // ---- 5. El admin revisa los documentos y lo verifica (React)
  await A.p.goto(`${BASE}/admin/transportistas`, { waitUntil: 'domcontentloaded' });
  const tt = A.p.locator(`.transportista-card[data-transportista-id="${perfilT.id}"]`);
  await tt.waitFor({ timeout: 30000 });
  ok('la tarjeta: placa, licencia y camión', (await contiene(tt, 'ZZZ999') || await contiene(tt, 'ZZZ-999')) && (await contiene(tt, '60 sacos')), await texto(tt));
  await tt.locator('.btn-ver-documento[data-cual="licencia"]').click();
  const docImg = tt.locator('.reclamo-documento img');
  await docImg.waitFor({ timeout: 20000 });
  ok('la licencia se ve (blob con token)', await docImg.evaluate((el) => el.complete && el.naturalWidth > 0));
  await tt.locator('.btn-ver-documento[data-cual="soat"]').click();
  await A.p.waitForTimeout(1200);
  ok('el SOAT reemplaza a la licencia en el visor', (await tt.locator('.btn-ver-documento[data-cual="soat"]').getAttribute('aria-expanded')) === 'true' && (await tt.locator('.reclamo-documento img').count()) === 1);
  await tt.locator('.btn-ver-documento[data-cual="soat"]').click();
  ok('tocar el documento abierto lo cierra', await tt.locator('.reclamo-documento').evaluate((el) => el.classList.contains('hidden')));
  await tt.locator('.btn-aprobar-transportista').click();
  await tt.locator('.btn-confirmar-aprobacion').click();
  await toast(A.p, 'Transportista verificado: ya se le pueden asignar viajes 🚛');
  perfilT = await transportista.api('/transporte/repartidores/me');
  ok('queda verificado', perfilT.verificacion_estado === 'verificado', perfilT.verificacion_estado);
  await A.p.locator('.admin-filtro[data-estado="verificado"]').click();
  ok('aparece en "Verificados"', await A.p.locator(`.transportista-card[data-transportista-id="${perfilT.id}"]`).waitFor({ timeout: 15000 }).then(() => true, () => false));
}

// ---- 6. El admin le asigna el viaje (React)
const viaje = (await admin.api('/transporte/viajes')).find((v) => v.envios.some((e) => e.pedido_id === pedido.id));
console.log('   DATO: viaje', viaje?.id, viaje?.estado, viaje?.destino_ciudad, viaje?.sacos_cargados, 'de', viaje?.capacidad_sacos, 'sacos');
await A.p.goto(`${BASE}/admin/viajes`, { waitUntil: 'domcontentloaded' });
const tv = A.p.locator(`.viaje-card[data-viaje-id="${viaje.id}"]`);
await tv.waitFor({ timeout: 30000 });
ok('el viaje está listo para salir y sin transportista', (await contiene(tv, 'Listo para salir')) && (await contiene(tv, 'Sin transportista asignado')));
await tv.locator('select[name="transportista"]').selectOption(perfilT.id);
await tv.locator('.form-asignar-viaje button[type=submit]').click();
await toast(A.p, `Viaje a ${CIUDAD} asignado a QA Transportista 🚛`);
await A.p.waitForTimeout(1200);
ok('la tarjeta muestra al transportista y "Salir hacia…"', (await contiene(A.p.locator(`.viaje-card[data-viaje-id="${viaje.id}"]`), 'QA Transportista')) && (await contiene(A.p.locator(`.viaje-card[data-viaje-id="${viaje.id}"] .btn-viaje-accion`), `Salir hacia ${CIUDAD}`)));

// ---- 7. El transportista sale y llega (React, /repartidor/viajes)
await T.p.goto(`${BASE}/repartidor/viajes`, { waitUntil: 'domcontentloaded' });
const tvT = T.p.locator('.viaje-card', { hasText: CIUDAD });
await tvT.waitFor({ timeout: 30000 });
await tvT.locator('.btn-viaje-accion').click();
await tvT.locator('.btn-viaje-confirmar').click();
await toast(T.p, `¡Buen viaje! Los compradores ya ven que su pedido va a ${CIUDAD} 🚛`);
await T.p.waitForTimeout(1000);
ok('en ruta: "Llegué a…"', await contiene(tvT.locator('.btn-viaje-accion'), `Llegué a ${CIUDAD}`));
await tvT.locator('.btn-viaje-accion').click();
await tvT.locator('.btn-viaje-confirmar').click();
await toast(T.p, `Llegaste a ${CIUDAD}: descarga cada pedido cuando su saldo figure pagado.`);
await T.p.waitForTimeout(1000);
const viajeFinal = (await admin.api('/transporte/viajes')).find((v) => v.id === viaje.id);
ok('el viaje llegó (en la API y en la tarjeta: falta el saldo)', viajeFinal?.estado === 'llegado' && (await contiene(tvT, 'Falta el saldo')), viajeFinal?.estado);
const envioFinal = await comprador.api(`/transporte/envios/${pedido.id}`);
console.log('   DATO: al llegar: viaje', viajeFinal?.estado, '· envío', envioFinal?.estado, '· pedido', (await comprador.api(`/pedidos/pedidos/${pedido.id}`))?.estado);

// ---- 8. La ciudad de prueba se oculta (admin, React)
await A.p.goto(`${BASE}/admin/destinos`, { waitUntil: 'domcontentloaded' });
await A.p.locator(`.destino-card[data-destino-id="${destino.id}"] .btn-editar-destino`).click();
await fd.locator('input[name="activo"]').setChecked(false);
await fd.locator('button[type=submit]').click();
await toast(A.p, `${CIUDAD} guardada (oculta: los compradores no la ven).`);
const publicos = await (await fetch(`${API}/pedidos/destinos-lejanos`)).json();
ok('la ciudad de prueba ya no se ofrece a los compradores', !publicos.some((d) => d.ciudad === CIUDAD));

// Esperado: la cuenta nueva todavía no tiene perfil de repartidor (404 en /repartidores/me: así el panel sabe que debe
// pedirle completarlo).
const inesperados = [...A.errores, ...C.errores, ...T.errores].filter((e) => !/\/api\/transporte\/repartidores\/me\)/.test(e));
ok('sin errores de página', !inesperados.length, inesperados);
cerrar();
await b.close();
