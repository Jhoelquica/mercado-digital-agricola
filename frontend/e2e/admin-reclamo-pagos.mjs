// Fase 7, con datos reales y por las pantallas de React:
//   Reclamo de RUC → qa-negocio@chakrashop-demo.pe (comprador "negocio", credenciales en .env.qa; se crea la primera vez)
//     tiene el RUC de SUNAT; qa-comprador lo reclama desde su perfil con documentos de prueba; el admin abre los documentos
//     (blob con token) y RECHAZA el reclamo (nunca lo aprueba): el RUC sigue donde estaba.
//   Pago semanal → qa-repartidor registra cómo cobra (Yape de prueba) en "Mis ganancias"; el admin le registra UN pago en
//     "Pagos por hacer". A los demás no se les toca nada.
//   npm run e2e:reclamo-pagos
import { randomBytes } from 'node:crypto';
import { appendFileSync } from 'node:fs';
import { chromium } from 'playwright';
import { BASE, contiene, fotoDePrueba, iniciarSesionApi, leerEnvQa, marcador, sesionDelFrontend, texto, vigilarErrores } from './comun.mjs';

// El RUC de la propia SUNAT: el servidor exige un RUC real, activo y habido, y no se usa el de ningún negocio privado.
const RUC = '20131312955';
let env = leerEnvQa();
const { ok, cerrar } = marcador();
if (!env.QA_NEGOCIO_EMAIL) {
  appendFileSync(new URL('../.env.qa', import.meta.url), `QA_NEGOCIO_EMAIL=qa-negocio@chakrashop-demo.pe\nQA_NEGOCIO_PASSWORD=Qa-${randomBytes(9).toString('base64url')}\n`);
  env = leerEnvQa();
}
const admin = await iniciarSesionApi(env.QA_ADMIN_EMAIL, env.QA_ADMIN_PASSWORD);
const comprador = await iniciarSesionApi(env.QA_EMAIL, env.QA_PASSWORD);
const repartidor = await iniciarSesionApi(env.QA_REPARTIDOR_EMAIL, env.QA_REPARTIDOR_PASSWORD);

const b = await chromium.launch({ channel: 'msedge', headless: true });
const nuevo = async () => {
  const c = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' });
  const p = await c.newPage();
  return { c, p, errores: vigilarErrores(p) };
};
const conSesion = async (sesion, nombre, email) => {
  const r = await nuevo();
  await r.c.addInitScript((s) => { if (!sessionStorage.getItem('puesta')) { localStorage.setItem('agro_sesion', s); sessionStorage.setItem('puesta', '1'); } }, sesionDelFrontend(sesion, { nombre, email }));
  return r;
};
const toast = (p, t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 25000 });

// ---- Reclamo de RUC
// 1. La cuenta que tiene el RUC (se registra por React como negocio, verificado en SUNAT).
const N = await nuevo();
let negocio = null;
try { negocio = await iniciarSesionApi(env.QA_NEGOCIO_EMAIL, env.QA_NEGOCIO_PASSWORD); } catch { /* todavía no existe */ }
if (!negocio) {
  await N.p.goto(`${BASE}/registro`, { waitUntil: 'domcontentloaded' });
  await N.p.locator('label:has(input[name="registro-rol-opcion"][value="comprador"])').click();
  await N.p.fill('#registro-nombre', 'QA Negocio');
  await N.p.fill('#registro-email', env.QA_NEGOCIO_EMAIL);
  await N.p.fill('#registro-password', env.QA_NEGOCIO_PASSWORD);
  await N.p.locator('label:has(input[name="registro-tipo-comprador"][value="negocio"])').click();
  await N.p.fill('#registro-nombre-comercial', 'QA Negocio de prueba');
  await N.p.fill('#registro-ruc', RUC);
  await N.p.locator('form button[type=submit]').first().click();
  await toast(N.p, '¡Cuenta creada! Negocio verificado en SUNAT');
  negocio = await iniciarSesionApi(env.QA_NEGOCIO_EMAIL, env.QA_NEGOCIO_PASSWORD);
}
const perfilNegocio = await negocio.api(`/usuarios/usuarios/${negocio.usuarioId}`);
ok('qa-negocio tiene el RUC verificado en SUNAT', perfilNegocio.ruc === RUC && perfilNegocio.tipo_comprador === 'negocio', { ruc: perfilNegocio.ruc, tipo: perfilNegocio.tipo_comprador });

// 2. qa-comprador lo reclama desde su perfil (React).
const C = await conSesion(comprador, 'QA Comprador', env.QA_EMAIL);
const pendiente = (await comprador.api('/usuarios/usuarios/me/reclamos-ruc')).mis_reclamos.find((r) => r.ruc === RUC && r.estado === 'pendiente');
if (!pendiente) {
  await C.p.goto(`${BASE}/perfil`, { waitUntil: 'domcontentloaded' });
  await C.p.locator('button.btn-link', { hasText: 'Reclámalo' }).click({ timeout: 30000 });
  await C.p.fill('#reclamo-ruc', RUC);
  await C.p.fill('#reclamo-dni', '99999903');
  await C.p.fill('#reclamo-nombre', 'QA Prueba Reclamo');
  await C.p.locator('#reclamo-ficha').setInputFiles(await fotoDePrueba(C.p, 'ficha-ruc-qa.jpg', '#f4cccc'));
  await C.p.locator('#reclamo-foto-dni').setInputFiles(await fotoDePrueba(C.p, 'dni-qa.jpg', '#cfe2f3'));
  await C.p.fill('#reclamo-mensaje', 'Prueba QA Fase 7: documentos de prueba, se rechaza.');
  await C.p.locator('#perfil-reclamo-ruc button[type=submit]').click();
  await toast(C.p, 'Reclamo enviado');
}
const reclamo = (await comprador.api('/usuarios/usuarios/me/reclamos-ruc')).mis_reclamos.find((r) => r.ruc === RUC && r.estado === 'pendiente');
ok('el reclamo queda pendiente', Boolean(reclamo));
console.log('   DATO: reclamo', reclamo?.id, RUC, reclamo?.razon_social);

// 3. El admin abre los documentos y lo rechaza (React).
const A = await conSesion(admin, 'Admin', env.QA_ADMIN_EMAIL);
await A.p.goto(`${BASE}/admin/reclamos-ruc`, { waitUntil: 'domcontentloaded' });
const tr = A.p.locator(`.reclamo-card[data-reclamo-id="${reclamo.id}"]`);
await tr.waitFor({ timeout: 30000 });
ok('la tarjeta: SUNAT, quien reclama y quien tiene el RUC', (await contiene(tr.locator('.reclamo-sunat'), 'SUNAT:')) && (await contiene(tr, 'QA Prueba Reclamo')) && (await contiene(tr, 'QA Negocio de prueba')), await texto(tr));
ok('la pestaña cuenta el reclamo', Number(await texto(A.p.locator('.admin-tab-btn', { hasText: 'Reclamos de RUC' }).locator('.badge-contador'))) >= 1);
await tr.locator('.btn-ver-documento[data-cual="ficha"]').click();
await tr.locator('.reclamo-documento img').waitFor({ timeout: 20000 });
ok('la Ficha RUC se ve (blob con token)', await tr.locator('.reclamo-documento img').evaluate((el) => el.complete && el.naturalWidth > 0 && el.src.startsWith('blob:')));
await tr.locator('.btn-ver-documento[data-cual="dni"]').click();
await A.p.waitForTimeout(1500);
ok('el DNI se ve en su lugar', (await tr.locator('.btn-ver-documento[data-cual="dni"]').getAttribute('aria-expanded')) === 'true' && await tr.locator('.reclamo-documento img').evaluate((el) => el.naturalWidth > 0));
await tr.locator('.btn-aprobar-reclamo').click();
ok('aprobar pide confirmar (no se aprueba)', await contiene(tr.locator('.transferencia-confirmar'), `el RUC ${RUC} pasa a`));
await tr.locator('.btn-cancelar-revision').click();
await tr.locator('.btn-rechazar-reclamo').click();
await tr.locator('.form-rechazar-transferencia button[type=submit]').click();
ok('rechazar sin motivo avisa', await contiene(tr.locator('.form-entrega-mensaje'), 'Escribe el motivo: quien reclamó lo va a ver.'));
await tr.locator('textarea[name="motivo"]').fill('Prueba QA: los documentos son de prueba.');
await tr.locator('.form-rechazar-transferencia button[type=submit]').click();
await toast(A.p, 'Reclamo rechazado: quien reclamó verá el motivo en su perfil.');
await tr.waitFor({ state: 'detached', timeout: 15000 });
const rechazado = (await comprador.api('/usuarios/usuarios/me/reclamos-ruc')).mis_reclamos.find((r) => r.id === reclamo.id);
const negocioDespues = await negocio.api(`/usuarios/usuarios/${negocio.usuarioId}`);
ok('el reclamo queda rechazado con su motivo y el RUC sigue en qa-negocio', rechazado?.estado === 'rechazado' && /documentos son de prueba/.test(rechazado?.motivo_rechazo || '') && negocioDespues.ruc === RUC, { estado: rechazado?.estado, ruc: negocioDespues.ruc });
await C.p.goto(`${BASE}/perfil`, { waitUntil: 'domcontentloaded' });
await C.p.waitForTimeout(2500);
ok('quien reclamó ve el motivo en su perfil', await contiene(C.p.locator('main'), 'los documentos son de prueba'));

// ---- Pago semanal a qa-repartidor
const yo = await repartidor.api('/transporte/repartidores/me');
const R = await conSesion(repartidor, 'QA Repartidor', env.QA_REPARTIDOR_EMAIL);
if (!(await repartidor.api('/pagos/ganancias/me')).cuenta) {
  await R.p.goto(`${BASE}/repartidor/ganancias`, { waitUntil: 'domcontentloaded' });
  const fc = R.p.locator('form', { has: R.p.locator('input[name="yape_celular"]') });
  await fc.waitFor({ timeout: 30000 });
  await fc.locator('input[name="yape_celular"]').fill('900000002');
  await fc.locator('input[name="yape_titular"]').fill('QA Repartidor');
  await fc.locator('button[type=submit]').click();
  await toast(R.p, 'Listo: ya sabemos cómo pagarte');
}
ok('qa-repartidor ya registró cómo cobrar (Yape de prueba)', Boolean((await repartidor.api('/pagos/ganancias/me')).cuenta?.yape_celular));

const antes = await admin.api('/pagos/ganancias/admin/por-pagar');
const hechosAntes = await admin.api('/pagos/ganancias/admin/pagos-hechos');
const otrosAntes = JSON.stringify(antes.beneficiarios.filter((x) => x.beneficiario_id !== yo.id).map((x) => [x.beneficiario_id, x.a_pagar, x.esta_semana]));
const suyo = antes.beneficiarios.find((x) => x.beneficiario_id === yo.id);
console.log('   qa-repartidor antes:', JSON.stringify({ a_pagar: suyo?.a_pagar, esta_semana: suyo?.esta_semana }), '· pagos hechos antes:', hechosAntes.length);
await A.p.goto(`${BASE}/admin/pagos`, { waitUntil: 'domcontentloaded' });
const tp = A.p.locator(`.pph-card[data-id="${yo.id}"]`);
await tp.waitFor({ timeout: 30000 });
ok('su tarjeta muestra el Yape para copiar', (await contiene(tp.locator('.pph-medios'), '900000002')) && (await tp.locator('.btn-copiar').count()) >= 1);
await tp.locator('.btn-pph-abrir').click();
const fp = tp.locator('.pph-form');
ok('Yape sugerido y "lo de esta semana" incluido (no hay nada pendiente de antes)', (await fp.locator('input[name="metodo"][value="yape"]').isChecked()) && (suyo.a_pagar > 0 || (await fp.locator('input[name="incluir_semana_actual"]').isChecked())));
await fp.locator('button[type=submit]').click();
ok('pide el n.º de operación', await contiene(fp.locator('.form-entrega-mensaje'), 'Escribe el número de operación que te dio Yape o tu banco.'));
const operacion = `QA${Date.now().toString().slice(-8)}`;
await fp.locator('input[name="numero_operacion"]').fill(operacion);
const monto = await texto(fp.locator('.pph-monto'));
await fp.locator('button[type=submit]').click();
await toast(A.p, 'registrado: QA Repartidor ya tiene el aviso.');
await A.p.waitForTimeout(1500);
const despues = await admin.api('/pagos/ganancias/admin/por-pagar');
const hechosDespues = await admin.api('/pagos/ganancias/admin/pagos-hechos');
const nuevos = hechosDespues.filter((h) => !hechosAntes.some((x) => x.id === h.id));
ok('se registró UN pago, a qa-repartidor', nuevos.length === 1 && nuevos[0].beneficiario_id === yo.id && nuevos[0].numero_operacion === operacion, nuevos);
ok('a los demás no se les tocó nada', JSON.stringify(despues.beneficiarios.filter((x) => x.beneficiario_id !== yo.id).map((x) => [x.beneficiario_id, x.a_pagar, x.esta_semana])) === otrosAntes);
ok('el pago aparece en "Pagos ya hechos"', await contiene(A.p.locator('#pagos-hechos-lista'), operacion));
console.log('   DATO: pago', nuevos[0]?.id, monto, nuevos[0]?.destino, 'op', operacion);

ok('sin errores de página', !N.errores.length && !C.errores.length && !A.errores.length && !R.errores.length, [...N.errores, ...C.errores, ...A.errores, ...R.errores]);
cerrar();
await b.close();
