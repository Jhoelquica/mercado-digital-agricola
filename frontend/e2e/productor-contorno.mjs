// Contorno de la chacra: el agricultor QA (QA_PRODUCTOR_* de .env.qa) abre "Mis chacras", dibuja el contorno de su chacra
// CH-01 sobre la imagen satelital, lo guarda (el servidor valida y calcula el área), lo ve en la tarjeta (chip con las
// hectáreas y el polígono en el mapa chico) y en "Mi perfil", vuelve a abrir el editor con el polígono cargado y al final
// lo quita, para no dejar datos. Capturas en e2e/capturas/contorno-*.png (no se versionan).
//   BASE=http://localhost:5173 CANAL=msedge npm run e2e:contorno
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';
import { BASE, iniciarSesionApi, leerEnvQa, marcador, rutaDelRepo, sesionDelFrontend, vigilarErrores } from './comun.mjs';

const CANAL = process.env.CANAL || 'msedge';
const env = leerEnvQa();
const { ok, cerrar } = marcador();
const CAPTURAS = rutaDelRepo('frontend/e2e/capturas');
mkdirSync(CAPTURAS, { recursive: true });
const captura = (p, nombre) => p.screenshot({ path: `${CAPTURAS}/contorno-${process.env.ANCHO ? `${process.env.ANCHO}-` : ''}${nombre}.png` });

const sesion = await iniciarSesionApi(env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD);
const chacras = await sesion.api('/productores/chacras/me');
const chacra = chacras.find((c) => c.codigo === 'CH-01') || chacras[0];
if (!chacra) {
  console.log('MAL la cuenta QA del productor no tiene chacras');
  process.exit(1);
}
const miChacra = async () => (await sesion.api('/productores/chacras/me')).find((c) => c.id === chacra.id);
// Parte de cero: sin contorno de una corrida anterior.
if (chacra.contorno) await sesion.api(`/productores/chacras/${chacra.id}/contorno`, { method: 'DELETE' });

const b = await chromium.launch({ channel: CANAL, headless: true });
// ANCHO=390 lo corre con el ancho de un celular (el diálogo ocupa toda la pantalla).
const ANCHO = Number(process.env.ANCHO) || 1280;
const ctx = await b.newContext({ viewport: { width: ANCHO, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' });
await ctx.addInitScript((s) => localStorage.setItem('agro_sesion', s), sesionDelFrontend(sesion, { nombre: 'Agricultor QA', email: env.QA_PRODUCTOR_EMAIL }));
const p = await ctx.newPage();
const errores = vigilarErrores(p);
const tarjeta = () => p.locator('.chacra-card', { has: p.locator('.chacra-codigo-badge', { hasText: chacra.codigo }) }).first();
const dialogo = p.locator('dialog.contorno-dialogo');
const mapa = dialogo.locator('.contorno-editor-mapa');
const area = dialogo.locator('.contorno-editor-area');
const toast = (t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 25000 });
const abrirEditor = async () => {
  await p.goto(`${BASE}/productor/chacras`, { waitUntil: 'domcontentloaded' });
  await tarjeta().waitFor({ timeout: 30000 });
  await tarjeta().locator('.btn-contorno-chacra').click();
  await dialogo.waitFor({ state: 'visible', timeout: 15000 });
  await mapa.locator('.leaflet-pm-toolbar').first().waitFor({ timeout: 30000 });
  // La imagen satelital (si hay internet); sin ella el editor funciona igual.
  await mapa.locator('.leaflet-tile-loaded').first().waitFor({ timeout: 30000 }).catch(() => {});
};

try {
  await p.goto(`${BASE}/productor/chacras`, { waitUntil: 'domcontentloaded' });
  await tarjeta().waitFor({ timeout: 30000 });
  ok('la tarjeta ofrece "Dibujar contorno"', (await tarjeta().locator('.btn-contorno-chacra').textContent()).includes('Dibujar contorno'));
  await abrirEditor();

  const capas = await dialogo.locator('.leaflet-control-layers-base label').allTextContents();
  const activa = await dialogo.locator('.leaflet-control-layers-base label', { has: p.locator('input:checked') }).textContent();
  ok('el editor arranca en Satélite y ofrece Calles', capas.join(' ').includes('Satélite') && capas.join(' ').includes('Calles') && activa.includes('Satélite'), { capas, activa });
  // Leaflet solo marca "loaded" las teselas que cargaron de verdad: si la CSP del frontend (security-headers.conf) no
  // dejara pasar las de Esri, no habría ninguna. Necesita internet.
  const teselasEsri = await mapa.locator('img.leaflet-tile-loaded[src*="server.arcgisonline.com"]').count();
  ok('la imagen satelital de Esri carga (la CSP la permite)', teselasEsri > 0, teselasEsri);
  ok('el alfiler de la chacra se ve como referencia', (await mapa.locator('.marcador-mapa').count()) === 1);
  const botones = await mapa.locator('.leaflet-pm-toolbar .control-icon').evaluateAll((els) => els.map((e) => e.className));
  ok('la barra solo tiene dibujar polígono, editar y quitar', botones.length === 3 && /polygon/.test(botones[0]) && /edit/.test(botones[1]) && /delete/.test(botones[2]), botones);
  ok('sin figura: "Guardar" apagado y el área avisa que no hay contorno', (await dialogo.locator('#btn-guardar-contorno').isDisabled()) && (await area.textContent()).includes('Todavía no hay contorno'));
  await captura(p, '1-editor-vacio');

  // Dibujar: el botón del polígono y cuatro esquinas alrededor del alfiler (centro del mapa), cerrando en la primera.
  await mapa.locator('.leaflet-pm-icon-polygon').click();
  const caja = await mapa.boundingBox();
  const cx = caja.x + caja.width / 2;
  const cy = caja.y + caja.height / 2;
  // A escala del mapa, y con las esquinas de arriba por debajo del botón del polígono: al dibujar, Geoman abre a su
  // derecha la fila "Finalizar · Eliminar último vértice · Cancelar", que en un celular cruza casi todo el mapa.
  const boton = await mapa.locator('.leaflet-pm-icon-polygon').boundingBox();
  const dx = Math.min(125, caja.width * 0.2);
  const dy = Math.min(85, caja.height * 0.18);
  const arriba = (y) => Math.max(y, boton.y + boton.height + 22);
  const esquinas = [[cx - dx, arriba(cy - dy)], [cx + dx * 1.05, arriba(cy - dy * 1.1)], [cx + dx * 0.9, cy + dy * 1.1], [cx - dx * 0.8, cy + dy * 0.85]];
  for (const [x, y] of esquinas) {
    await p.mouse.click(x, y);
    await p.waitForTimeout(150);
  }
  await p.mouse.click(esquinas[0][0], esquinas[0][1]);
  await area.locator('strong').waitFor({ timeout: 10000 });
  const textoArea = await area.textContent();
  ok('al cerrar el polígono aparece el área en ha y m²', /\d+[.,]\d{2} ha/.test(textoArea) && /m²/.test(textoArea), textoArea);
  ok('con la figura dibujada, "Guardar" se habilita', !(await dialogo.locator('#btn-guardar-contorno').isDisabled()));
  await captura(p, '2-poligono-dibujado');

  await dialogo.locator('#btn-guardar-contorno').click();
  await toast('Contorno guardado');
  await dialogo.waitFor({ state: 'hidden', timeout: 15000 });
  const guardada = await miChacra();
  ok('el servidor guardó el polígono y calculó el área', guardada.contorno?.type === 'Polygon' && guardada.area_m2 > 50, guardada.area_m2);
  await tarjeta().locator('.chacra-area-chip').waitFor({ timeout: 15000 });
  await tarjeta().locator('path.leaflet-interactive').first().waitFor({ timeout: 15000 });
  ok('la tarjeta muestra las hectáreas, el polígono en el mapa chico y "Editar contorno"',
    (await tarjeta().locator('.chacra-area-chip').textContent()).includes('ha') && (await tarjeta().locator('.btn-contorno-chacra').textContent()).includes('Editar contorno'));
  await tarjeta().scrollIntoViewIfNeeded();
  await captura(p, '3-tarjeta-con-contorno');

  // Mi perfil: la tarjeta de la chacra con el polígono y el área.
  await p.goto(`${BASE}/perfil`, { waitUntil: 'domcontentloaded' });
  const tarjetaPerfil = p.locator('.perfil-produccion .chacra-card', { hasText: chacra.codigo }).first();
  await tarjetaPerfil.waitFor({ timeout: 30000 });
  await tarjetaPerfil.locator('path.leaflet-interactive').first().waitFor({ timeout: 15000 });
  ok('Mi perfil muestra el contorno y las hectáreas de la chacra', (await tarjetaPerfil.textContent()).includes('Contorno dibujado'));
  await tarjetaPerfil.scrollIntoViewIfNeeded();
  await captura(p, '4-perfil');

  // Editar: el polígono guardado ya está en el mapa; quitarlo pide confirmación y deja la chacra con su punto.
  await abrirEditor();
  ok('al editar, el polígono guardado ya está en el mapa con su área', (await mapa.locator('path.leaflet-interactive').count()) >= 1 && / ha/.test(await area.textContent()));
  await captura(p, '5-editor-con-poligono');
  await dialogo.locator('#btn-quitar-contorno').click();
  await dialogo.locator('#btn-si-quitar-contorno').click();
  await toast('Contorno quitado');
  const sinContorno = await miChacra();
  ok('quitar deja la chacra sin contorno y con su punto de siempre',
    sinContorno.contorno === null && sinContorno.area_m2 === null && sinContorno.ubicacion_latitud === chacra.ubicacion_latitud, sinContorno);
  ok('sin errores en la consola del navegador', errores.length === 0, errores);
} catch (e) {
  ok('la prueba terminó entera', false, e.message);
  await captura(p, 'error').catch(() => {});
} finally {
  // Por si algo quedó a medias: la chacra vuelve a estar sin contorno.
  await sesion.api(`/productores/chacras/${chacra.id}/contorno`, { method: 'DELETE' }).catch(() => {});
  await b.close();
  cerrar('contorno');
}
