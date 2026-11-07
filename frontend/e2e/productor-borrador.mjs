// Fase 5b: flujo real del agricultor (agricultor01) por React sobre un borrador (BORRADOR=…, o el primer borrador
// "Papa amarilla" que tenga; lo deja e2e:siembra): editar → subir 2 fotos → reordenarlas → unidades (agregar, editar,
// borrar) → publicar → catálogo público → pedir un recojo y anularlo. Además: Mis Productos (reseñas, fotos,
// certificación, unidades), Ganancias (solo la validación de la cuenta: no guarda nada), Economía y quechua.
//   npm run e2e:borrador
import { chromium } from 'playwright';
import {
  API, BASE, contiene, empiezaCon, entrarPorPantalla, esIgual, fotoDePrueba, iniciarSesionApi, leerEnvQa, marcador, texto, textos, vigilarErrores,
} from './comun.mjs';

const env = leerEnvQa();
const { ok, cerrar } = marcador();
const { api } = await iniciarSesionApi(env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD);
const publicoApi = async (ruta) => (await fetch(`${API}${ruta}`)).json(); // sin sesión, como un visitante
const BORRADOR = process.env.BORRADOR || (await api('/productos/productos/mios')).find((x) => x.estado === 'borrador' && x.nombre === 'Papa amarilla')?.id;
if (!BORRADOR) { console.log('No hay un borrador "Papa amarilla": corre antes npm run e2e:siembra.'); process.exit(1); }
console.log('   borrador:', BORRADOR);
const chacras = await api('/productores/chacras/me');
const ch01 = chacras.find((c) => c.codigo === 'CH-01');

const b = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima', permissions: ['geolocation'], geolocation: { latitude: Number(ch01.ubicacion_latitud), longitude: Number(ch01.ubicacion_longitud) } });
const p = await ctx.newPage();
const errores = vigilarErrores(p);
const esperarToast = (t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 20000 });
const insignia = p.locator('a.productor-tab-btn[href="/productor/productos"] .badge-contador');
const porCoordinar = async () => ((await insignia.count()) ? Number(await texto(insignia)) : 0);

await entrarPorPantalla(p, env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD, '/productor');

// ---- Borrador
await p.goto(`${BASE}/productor/borradores`, { waitUntil: 'domcontentloaded' });
const tarjeta = p.locator(`.borrador-card[data-producto-id="${BORRADOR}"]`);
await tarjeta.waitFor({ timeout: 30000 });
await p.waitForTimeout(800);
const antesPorCoordinar = await porCoordinar();
console.log('   "Cosechas por coordinar" antes:', antesPorCoordinar);
console.log('   sugerencia de precio:', await texto(tarjeta.locator('.borrador-sugerencia')));
ok('Publicar empieza deshabilitado (sin fotos)', await tarjeta.locator('.btn-publicar-borrador').isDisabled());
await tarjeta.locator('.borrador-categoria').selectOption('tuberculo');
await tarjeta.locator('.borrador-precio').fill('3.80');
const descripcion = `${await tarjeta.locator('.borrador-descripcion').inputValue()} Papa amarilla de altura, ideal para puré.`.trim();
await tarjeta.locator('.borrador-descripcion').fill(descripcion);
ok('vista previa con precio y categoría', (await contiene(tarjeta.locator('.borrador-preview-card .product-price'), 'S/ 3.80')) && (await contiene(tarjeta.locator('.borrador-preview-card .pill-categoria'), 'Tubérculo')));
ok('contador de la descripción', await esIgual(tarjeta.locator('.borrador-descripcion-contador'), `${descripcion.length}/600`));
ok('progreso 67% (falta la foto)', await empiezaCon(tarjeta.locator('.borrador-progreso-wrap'), '67% completo'));

// ---- 2 fotos
const fotos = [await fotoDePrueba(p, 'papa-amarilla-1.jpg', '#d9b44a'), await fotoDePrueba(p, 'papa-amarilla-2.jpg', '#c99a2e')];
await tarjeta.locator('.gestor-fotos .upload-input').setInputFiles(fotos);
await p.waitForFunction((id) => document.querySelectorAll(`.borrador-card[data-producto-id="${id}"] .upload-miniatura`).length === 2, BORRADOR, { timeout: 30000 });
ok('2 fotos subidas', await esIgual(tarjeta.locator('.fotos-contador'), '2/5 fotos'));
ok('Publicar se habilita y el progreso llega a 100%', (await tarjeta.locator('.btn-publicar-borrador').isEnabled()) && (await empiezaCon(tarjeta.locator('.borrador-progreso-wrap'), '100% completo')));
const primeraAntes = await tarjeta.locator('.upload-miniatura img').first().getAttribute('src');
ok('la vista previa usa la foto principal', (await tarjeta.locator('.borrador-preview-card img').getAttribute('src')) === primeraAntes);

// ---- Reordenar
await tarjeta.locator('.upload-miniatura').first().locator('.miniatura-mover-btn[aria-label="Mover foto a la derecha"]').click();
await p.waitForTimeout(1500);
const primeraDespues = await tarjeta.locator('.upload-miniatura img').first().getAttribute('src');
ok('reordenar: la segunda pasa a ser la principal', primeraDespues !== primeraAntes && (await tarjeta.locator('.upload-miniatura').first().locator('.miniatura-badge-principal').count()) === 1);
ok('la vista previa cambia de foto', (await tarjeta.locator('.borrador-preview-card img').getAttribute('src')) === primeraDespues);
const detalle = await api(`/productos/productos/${BORRADOR}`);
const ordenServidor = [...detalle.imagenes].sort((x, y) => x.orden - y.orden).map((i) => i.url.split('/').pop());
ok('el orden quedó guardado en el servidor', primeraDespues.endsWith(ordenServidor[0]), { primeraDespues, ordenServidor });

// ---- Unidades
await tarjeta.locator('.btn-toggle-unidades').click();
await tarjeta.locator('.unidades-alt-gestor').waitFor({ timeout: 15000 });
const opciones = await tarjeta.locator('.unidad-alt-nueva-select option').evaluateAll((os) => os.map((o) => o.value));
console.log('   unidades que se pueden agregar:', opciones.join(', '));
ok('la ayuda del factor', await empiezaCon(tarjeta.locator('.unidad-alt-factor-ayuda'), '¿Cuántos kg equivalen a 1'));
const agregar = async (unidad, precio, factor) => {
  await tarjeta.locator('.unidad-alt-nueva-select').selectOption(unidad);
  await tarjeta.locator('.unidad-alt-nuevo-precio').fill(String(precio));
  await tarjeta.locator('.unidad-alt-nuevo-factor').fill(String(factor));
  await tarjeta.locator('.unidad-alt-form button[type=submit]').click();
  await esperarToast('Unidad de venta agregada');
  await p.waitForTimeout(500);
};
const principal = opciones.includes('saco') ? 'saco' : opciones[0];
await agregar(principal, 140, 50);
ok(`unidad agregada (${principal})`, (await tarjeta.locator('.unidades-alt-fila').count()) === 1 && await contiene(tarjeta.locator('.unidades-alt-fila'), 'S/ 140.00'));
await tarjeta.locator('.btn-editar-unidad-alt').click();
await tarjeta.locator('.unidad-alt-editar-precio').fill('150');
await tarjeta.locator('.btn-guardar-unidad-alt').click();
await esperarToast('Unidad de venta actualizada');
await p.waitForTimeout(500);
ok('unidad editada', await contiene(tarjeta.locator('.unidades-alt-fila'), 'S/ 150.00'));
const segunda = (await tarjeta.locator('.unidad-alt-nueva-select option').evaluateAll((os) => os.map((o) => o.value)))[0];
if (segunda) {
  await agregar(segunda, 40, 11.5);
  ok('segunda unidad agregada', (await tarjeta.locator('.unidades-alt-fila').count()) === 2);
  await tarjeta.locator('.unidades-alt-fila', { hasText: 'S/ 40.00' }).locator('.btn-eliminar-unidad-alt').click();
  await esperarToast('Unidad de venta eliminada');
  await p.waitForTimeout(500);
  ok('unidad borrada', (await tarjeta.locator('.unidades-alt-fila').count()) === 1);
}

// ---- Publicar
await tarjeta.locator('.btn-publicar-borrador').click();
await esperarToast('Producto publicado — ya está visible en el catálogo 🎉');
await p.waitForTimeout(1500);
ok('el borrador sale de la lista', (await p.locator(`.borrador-card[data-producto-id="${BORRADOR}"]`).count()) === 0);
const publico = await publicoApi(`/productos/productos/${BORRADOR}`);
ok('en el catálogo público: en tránsito, con precio, categoría, fotos y unidad', publico.estado === 'en_transito' && Number(publico.precio) === 3.8 && publico.categoria === 'tuberculo' && publico.imagenes?.length === 2 && publico.unidades_alternativas?.length === 1, { estado: publico.estado, precio: publico.precio, categoria: publico.categoria, imagenes: publico.imagenes?.length, unidades: publico.unidades_alternativas });
ok('aparece en el listado público', (await publicoApi('/productos/productos')).some((x) => x.id === BORRADOR));
ok('"Cosechas por coordinar" sube en 1', (await porCoordinar()) === antesPorCoordinar + 1, { antes: antesPorCoordinar, ahora: await porCoordinar() });
const visitante = await (await b.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
await visitante.goto(`${BASE}/producto/${BORRADOR}`, { waitUntil: 'domcontentloaded' });
await visitante.waitForFunction(() => document.body.textContent.includes('Papa amarilla'), null, { timeout: 30000 }).catch(() => {});
ok('la página pública del producto lo muestra', await contiene(visitante.locator('body'), 'Papa amarilla'));
await visitante.context().close();

// ---- Mis Productos: recojo
await p.goto(`${BASE}/productor/productos`, { waitUntil: 'domcontentloaded' });
const alm = p.locator(`.alm-card[data-producto-id="${BORRADOR}"]`);
await alm.waitFor({ timeout: 30000 });
ok('"Cosechas por llevar al almacén" con la papa', await contiene(alm.locator('.pill-en_transito'), 'Por llegar al almacén'));
await alm.locator('input[value="repartidor"]').check();
await alm.locator('.alm-mapa.leaflet-container').waitFor({ timeout: 20000 });
ok('el mapa arranca en la chacra, con su punto', (await alm.locator('.leaflet-marker-icon').count()) === 1);
await alm.locator('.alm-cotizacion-total').waitFor({ timeout: 20000 });
console.log('   cotización:', await texto(alm.locator('.alm-cotizacion')));
ok('el botón dice el costo', await contiene(alm.locator('.btn-pedir-recojo'), 'Pedir recojo por S/'));
await alm.locator('.btn-pedir-recojo').click();
ok('sin celular no se pide', await contiene(alm.locator('.form-entrega-mensaje.error'), 'Escribe tu celular de 9 dígitos'));
await alm.locator('input[name="referencia"]').fill('Prueba QA: pasando el puente');
await alm.locator('input[name="telefono"]').fill('987 654 321');
await alm.locator('.btn-pedir-recojo').click();
await p.locator('.toast', { hasText: 'Recojo pedido' }).first().waitFor({ timeout: 20000 });
console.log('   aviso:', await texto(p.locator('.toast', { hasText: 'Recojo pedido' }).first()));
await alm.locator('.alm-pasos').waitFor({ timeout: 15000 });
ok('se ven los pasos del recojo', (await alm.locator('.alm-pasos li').count()) === 5);
await p.waitForTimeout(800);
ok('"Cosechas por coordinar" baja en 1', (await porCoordinar()) === antesPorCoordinar);
const recojo = (await api('/transporte/recojos/mios')).find((r) => r.producto_id === BORRADOR && r.estado !== 'cancelado');
console.log('   DATO: recojo', recojo?.id, recojo?.estado, 'costo', recojo?.costo, 'repartidor', recojo?.repartidor?.nombre || '—');
await alm.locator('.btn-anular-recojo').click();
ok('anular pide confirmar', await alm.locator('.alm-anular-confirmar').isVisible());
await alm.locator('.btn-anular-si').click();
await esperarToast('Listo: ya puedes elegir otra vez cómo llega tu cosecha.');
await alm.locator('.alm-form').waitFor({ timeout: 15000 });
ok('anulado: vuelve a pedir cómo llega', true);
await p.waitForTimeout(800);
ok('"Cosechas por coordinar" vuelve a subir', (await porCoordinar()) === antesPorCoordinar + 1);
console.log('   DATO: recojo', recojo?.id, (await api('/transporte/recojos/mios')).find((r) => r.id === recojo?.id)?.estado);

// ---- Publicados: reseñas, fotos, certificación, unidades
const conResenas = p.locator('.productor-card', { has: p.locator('.btn-toggle-resenas', { hasText: 'reseña' }) }).first();
const card = (await conResenas.count()) ? conResenas : p.locator('.productor-card').first();
await card.locator('.btn-toggle-resenas').click();
await card.locator('[data-panel="resenas"] .resena-card, [data-panel="resenas"] .muted').first().waitFor({ timeout: 15000 });
ok('reseñas de mis productos', await card.locator('[data-panel="resenas"]').isVisible());
await card.locator('.btn-toggle-fotos').click();
await card.locator('[data-panel="fotos"] .gestor-fotos').waitFor({ timeout: 15000 });
ok('fotos: se turna con reseñas', (await card.locator('[data-panel="resenas"]').isHidden()) && (await card.locator('[data-panel="fotos"]').isVisible()));
await card.locator('.btn-toggle-unidades').click();
await card.locator('[data-panel="unidades"] .unidades-alt-gestor').waitFor({ timeout: 15000 });
ok('unidades cierra los demás paneles', await card.locator('[data-panel="fotos"]').isHidden());
await card.locator('.btn-ver-certificacion').click();
await p.locator('#modal-certificacion').waitFor({ timeout: 15000 }).catch(() => {});
ok('certificación abre su ventana', await p.locator('#modal-certificacion').isVisible().catch(() => false));
await p.keyboard.press('Escape');

// ---- Ganancias: solo la validación de la cuenta (no se guarda nada)
await p.goto(`${BASE}/productor/ganancias`, { waitUntil: 'domcontentloaded' });
await p.locator('#productor-ganancias .economia-stats, #productor-ganancias .muted').first().waitFor({ timeout: 20000 });
ok('resumen de ganancias', await contiene(p.locator('#productor-ganancias'), 'Por cobrar'));
const cuentaAntes = (await api('/pagos/ganancias/me')).cuenta;
if (await p.locator('.btn-cambiar-cuenta-cobro').count()) await p.click('.btn-cambiar-cuenta-cobro');
const form = p.locator('.form-cuenta-cobro');
await form.waitFor();
await form.locator('input[name="yape_celular"]').fill('123');
await form.locator('input[name="yape_titular"]').fill('Prueba');
await form.locator('button[type=submit]').click();
ok('valida el celular de Yape sin guardar', (await contiene(form.locator('.form-entrega-mensaje'), '9 dígitos')) && (await form.locator('input[name="yape_celular"]').getAttribute('aria-invalid')) === 'true');
ok('la cuenta del servidor no cambió', JSON.stringify((await api('/pagos/ganancias/me')).cuenta) === JSON.stringify(cuentaAntes));

// ---- Economía
await p.goto(`${BASE}/productor/economia`, { waitUntil: 'domcontentloaded' });
await p.locator('#grafico-ganancia-cultivo').waitFor({ timeout: 20000 });
await p.waitForTimeout(1500);
ok('gráficos de economía dibujados', await p.evaluate(() => ['grafico-ganancia-cultivo', 'grafico-costos'].every((id) => document.getElementById(id)?.width > 0)));
await p.click('.ventas-periodo-btn[data-meses="6"]');
await p.waitForTimeout(1500);
ok('filtro de 6 meses', (await p.getAttribute('.ventas-periodo-btn[data-meses="6"]', 'aria-pressed')) === 'true');
console.log('   ventas:', (await textos(p.locator('#productor-ventas .ventas-hero > *'))).join(' | '));

// ---- Quechua
await p.locator('button:has-text("Runasimi")').first().click();
await p.waitForTimeout(300);
ok('quechua en Economía (Rantikusqaykuna, Gastasqayki…)', (await contiene(p.locator('main'), 'Rantikusqaykuna')) && (await contiene(p.locator('main'), 'Gastasqayki, ganasqaykipas')));
await p.goto(`${BASE}/productor/borradores`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(800);
ok('quechua en Borradores', await contiene(p.locator('main'), 'Manaraq tukusqa rantikunaykuna'));
await p.goto(`${BASE}/productor/ganancias`, { waitUntil: 'domcontentloaded' });
await p.waitForTimeout(800);
ok('quechua en Ganancias (Qullqiy)', await contiene(p.locator('.section-header.inner h3').first(), 'Qullqiy'));
await p.locator('button:has-text("Castellano")').first().click();

ok('sin errores de página', !errores.length, errores);
cerrar();
await b.close();
