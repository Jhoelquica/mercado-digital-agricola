// Fase 6, carga y viajes del transportista con la API SIMULADA (no guarda nada): qa-repartidor no es transportista y los
// viajes los arma y asigna el admin (Fase 7). Se responde por él:
//   - perfil con un camión de 3000 kg sin documentos → "Enviar mis documentos": validaciones y envío (POST simulado);
//   - perfil verificado con un viaje "listo" → "Salir hacia …" con su confirmación → en ruta → "Llegué a …".
//   npm run e2e:viajes
import { chromium } from 'playwright';
import { BASE, contiene, fotoDePrueba, iniciarSesionApi, leerEnvQa, marcador, sesionDelFrontend, vigilarErrores } from './comun.mjs';

const env = leerEnvQa();
const { ok, cerrar } = marcador();
const repartidor = await iniciarSesionApi(env.QA_REPARTIDOR_EMAIL, env.QA_REPARTIDOR_PASSWORD);
const perfilReal = await repartidor.api('/transporte/repartidores/me');

const b = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' });
await ctx.addInitScript((s) => localStorage.setItem('agro_sesion', s), sesionDelFrontend(repartidor, { nombre: 'QA Repartidor', email: env.QA_REPARTIDOR_EMAIL }));

let perfil = { ...perfilReal, tipo_vehiculo: 'camion', capacidad_maxima_kg: 3000, transportista_carga: false, verificacion_estado: null, placa: null, licencia_numero: null };
let viaje = null;
const llamadas = [];
// Cualquier escritura que no esté simulada abajo se corta: esta prueba no debe guardar nada de verdad.
await ctx.route(/\/api\//, (r) => (r.request().method() === 'GET' ? r.fallback() : r.abort()));
await ctx.route(/\/api\/transporte\/repartidores\/me$/, (r) => r.fulfill({ json: perfil }));
await ctx.route(/\/api\/transporte\/repartidores\/me\/carga$/, async (r) => {
  llamadas.push(`documentos ${r.request().method()}`);
  perfil = { ...perfil, transportista_carga: true, verificacion_estado: 'pendiente', placa: 'ABC123', licencia_numero: 'Q12345678', fecha_documentos: '2026-11-06T18:00:00' };
  await r.fulfill({ json: perfil });
});
await ctx.route(/\/api\/transporte\/viajes\/mios$/, (r) => r.fulfill({ json: viaje ? [viaje] : [] }));
await ctx.route(/\/api\/transporte\/viajes\/[^/]+\/(salir|llegar)$/, async (r) => {
  const accion = r.request().url().split('/').pop();
  llamadas.push(`viaje ${accion}`);
  viaje = { ...viaje, estado: accion === 'salir' ? 'en_ruta' : 'llegado', fecha_salida: '2026-11-06T18:05:00' };
  await r.fulfill({ json: viaje });
});

const p = await ctx.newPage();
const errores = vigilarErrores(p);
const toast = (t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 15000 });

// ---- Documentos de carga
await p.goto(`${BASE}/repartidor/viajes`, { waitUntil: 'domcontentloaded' });
await p.locator('.carga-panel').waitFor({ timeout: 30000 });
ok('pide los 3 documentos', (await contiene(p.locator('.carga-requisitos'), 'Licencia de conducir')) && (await contiene(p.locator('.carga-requisitos'), 'SOAT vigente')));
await p.click('.btn-abrir-carga');
const form = p.locator('#form-carga');
await form.waitFor();
await form.locator('input[name="placa"]').fill('12');
await form.locator('button[type=submit]').click();
ok('valida la placa', await contiene(form.locator('.form-entrega-mensaje'), 'La placa son 6 letras o números'));
await form.locator('input[name="placa"]').fill('abc-123');
await form.locator('input[name="licencia_numero"]').fill('q12345678');
await form.locator('button[type=submit]').click();
ok('pide cada documento', await contiene(form.locator('.form-entrega-mensaje'), 'Falta subir: Licencia de conducir.'));
const doc = await fotoDePrueba(p, 'documento.jpg', '#cccccc');
for (const campo of ['licencia', 'tarjeta_propiedad', 'soat']) await form.locator(`input[name="${campo}"]`).setInputFiles({ ...doc, name: `${campo}.jpg` });
ok('muestra el archivo elegido', await contiene(form.locator('.reclamo-archivo').first(), 'licencia.jpg'));
await form.locator('button[type=submit]').click();
await toast('Documentos enviados: el admin los revisa y el resultado aparece aquí 🚛');
await p.locator('.carga-estado.pendiente').waitFor({ timeout: 15000 });
ok('queda "En revisión" con su placa', (await contiene(p.locator('.carga-panel'), 'Tus documentos están en revisión')) && (await contiene(p.locator('.carga-datos'), 'ABC123')));

// ---- Un viaje: salir y llegar
perfil = { ...perfil, verificacion_estado: 'verificado' };
viaje = { id: 'viaje-qa-0001', estado: 'listo', destino_ciudad: 'Lima', capacidad_sacos: 60, sacos_para_salir: 48, sacos_cargados: 50, fecha_salida: null, fecha_llegada: null, transportista: null, envios: [{ id: 'e1', pedido_id: 'pedido-qa-0001', sacos: 50, estado: 'asignado', saldo_pagado: false }] };
await p.reload({ waitUntil: 'domcontentloaded' });
const tarjeta = p.locator('.viaje-card');
await tarjeta.waitFor({ timeout: 30000 });
ok('el viaje: Huamanga → Lima, 50 sacos cargados', (await contiene(tarjeta, 'Huamanga → Lima')) && (await contiene(tarjeta, '50 sacos cargados')));
await tarjeta.locator('.btn-viaje-accion').click();
ok('salir pide confirmar', await tarjeta.locator('.viaje-confirmar').isVisible() && await contiene(tarjeta.locator('.viaje-confirmar'), '¿Ya cargaste los 50 sacos?'));
await tarjeta.locator('.btn-viaje-cancelar').click();
ok('"Todavía no" vuelve atrás', await tarjeta.locator('.viaje-confirmar').isHidden());
await tarjeta.locator('.btn-viaje-accion').click();
await tarjeta.locator('.btn-viaje-confirmar').click();
await toast('¡Buen viaje! Los compradores ya ven que su pedido va a Lima 🚛');
await p.waitForTimeout(800);
ok('en ruta: "Llegué a Lima", habilitado y sin la confirmación anterior abierta', (await contiene(tarjeta.locator('.btn-viaje-accion'), 'Llegué a Lima')) && (await tarjeta.locator('.btn-viaje-accion').isEnabled()) && (await tarjeta.locator('.viaje-confirmar').isHidden()));
await tarjeta.locator('.btn-viaje-accion').click();
await tarjeta.locator('.btn-viaje-confirmar').click();
await toast('Llegaste a Lima: descarga cada pedido cuando su saldo figure pagado.');
await p.waitForTimeout(800);
ok('llegado: "Cobra antes de descargar" y el saldo de cada pedido', (await contiene(tarjeta, 'Cobra antes de descargar')) && (await contiene(tarjeta, 'Falta el saldo')));
ok('llamó a la API de documentos, salir y llegar', JSON.stringify(llamadas) === JSON.stringify(['documentos POST', 'viaje salir', 'viaje llegar']), llamadas);

ok('sin errores de página', !errores.length, errores);
cerrar();
await b.close();
