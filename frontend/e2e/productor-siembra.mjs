// Fase 5a: flujo real del agricultor (agricultor01, QA_PRODUCTOR_* de .env.qa) por las pantallas de React.
//   MODO=voz (por defecto): asistente de siembra con voz (lectura espiada y dictado simulado) → corregir → anotar y borrar
//     gastos → completar la cosecha con foto. Deja datos en el clúster (se imprimen como "DATO").
//   MODO=sin-voz: navegador sin speechSynthesis ni SpeechRecognition y sin permiso de ubicación: otra producción (huevos,
//     tipo crianza) solo tocando y escribiendo, en quechua, Escape con confirmación; al final se anula.
//   MODO=solo-voz: abre el asistente, prueba la lectura y el dictado y lo cierra sin guardar.
//   MODO=resto SIEMBRA_ID=…: desde una siembra ya registrada (gastos y cosecha).
//   CANAL=msedge | chrome.      npm run e2e:siembra
import { chromium } from 'playwright';
import {
  BASE, contiene, empiezaCon, entrarPorPantalla, esIgual, iniciarSesionApi, leerEnvQa, marcador, rutaDelRepo, texto, textos, vigilarErrores,
} from './comun.mjs';

const MODO = process.env.MODO || 'voz';
const CANAL = process.env.CANAL || 'msedge';
const FOTO = rutaDelRepo('frontend/public/static/hero-valle-andino-1600.jpg');
const env = leerEnvQa();
const { ok, cerrar } = marcador();

const { api } = await iniciarSesionApi(env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD);
const chacras = await api('/productores/chacras/me');
const ch01 = chacras.find((c) => c.codigo === 'CH-01');
const ch02 = chacras.find((c) => c.codigo === 'CH-02') || ch01;
const antes = new Set((await api('/productores/productores/produccion/me')).map((r) => r.id));
const productosAntes = new Set((await api('/productos/productos/mios')).map((p) => p.id));

const b = await chromium.launch({ channel: CANAL, headless: true });
const conVoz = MODO !== 'sin-voz';
const ctx = await b.newContext({
  viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima',
  ...(conVoz ? { permissions: ['geolocation'], geolocation: { latitude: Number(ch01.ubicacion_latitud) + 0.0003, longitude: Number(ch01.ubicacion_longitud) } } : {}),
});
if (conVoz) {
  // La lectura se espía (sin sonido) y el dictado se simula: el navegador sin interfaz no tiene micrófono.
  await ctx.addInitScript(() => {
    window.__habla = [];
    window.__dictados = [];
    speechSynthesis.speak = (u) => { window.__habla.push({ texto: u.text, lang: u.lang, rate: u.rate, voz: u.voice?.name || null }); };
    speechSynthesis.cancel = () => {};
    class Simulado {
      start() {
        const sig = window.__dictados.shift() || { error: 'no-speech' };
        setTimeout(() => {
          if (sig.error) { this.onerror?.({ error: sig.error }); this.onend?.(); return; }
          const r = [{ transcript: sig.texto }]; r.isFinal = true;
          this.onresult?.({ resultIndex: 0, results: [r] });
          this.onend?.();
        }, 300);
      }
      abort() { this.onerror?.({ error: 'aborted' }); this.onend?.(); }
    }
    window.SpeechRecognition = Simulado;
    window.webkitSpeechRecognition = Simulado;
  });
} else {
  await ctx.addInitScript(() => {
    for (const n of ['speechSynthesis', 'SpeechRecognition', 'webkitSpeechRecognition', 'SpeechSynthesisUtterance']) {
      try { delete Window.prototype[n]; } catch { /* */ }
      try { delete window[n]; } catch { /* */ }
    }
  });
}
const p = await ctx.newPage();
const errores = vigilarErrores(p);
const habla = () => p.evaluate(() => window.__habla?.map((h) => h.texto) || []);
const ultimaHabla = async () => (await habla()).at(-1) || '';
const siguiente = () => p.click('.btn-asistente-siguiente');
const esperarPregunta = (t) => p.waitForFunction((x) => document.querySelector('#asistente-pregunta')?.textContent.includes(x), t, { timeout: 15000 });
const esperarToast = (t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 20000 });
const error = p.locator('.asistente-error');

await entrarPorPantalla(p, env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD, '/productor');
ok('aterriza en /productor', true);
if (!conVoz) ok('sin la API de voz en este navegador', await p.evaluate(() => !('speechSynthesis' in window) && !('webkitSpeechRecognition' in window)));

await p.goto(`${BASE}/productor/siembras`, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('.siembra-cta');
ok(conVoz ? 'la tarjeta avisa que se puede hablar' : 'sin voz, la tarjeta no ofrece hablar', (await p.locator('.siembra-cta-voz').count()) === (conVoz ? 1 : 0));

if (MODO === 'voz' || MODO === 'solo-voz' || MODO === 'resto') {
  let siembra = MODO === 'resto' ? { id: process.env.SIEMBRA_ID } : null;
  if (MODO !== 'resto') {
    await p.click('.btn-abrir-asistente-siembra');
    await p.waitForSelector('#modal-asistente .asistente');
    await esperarPregunta('¿En qué chacra o granja es?');
    await p.waitForTimeout(300);
    ok('lee la primera pregunta en voz alta', (await ultimaHabla()).startsWith('¿En qué chacra o granja es? Tus lugares son:'), await habla());
    const frase = await p.evaluate(() => window.__habla.at(-1));
    ok('voz en español, un poco más lenta', /^es/i.test(frase.lang) && Math.abs(frase.rate - 0.92) < 0.001, frase);
    console.log('   voz elegida por el navegador:', frase.voz || '(ninguna en español: usa la del sistema con lang es-PE)', frase.lang);
    ok('botones Escuchar, ayuda y "leer en voz alta"', await p.locator('.btn-asistente-escuchar').isVisible() && await p.locator('.btn-asistente-ayuda').isVisible() && await p.locator('.asistente-voz-lectura').isVisible());
    await p.click('.btn-asistente-ayuda');
    ok('la ayuda se abre y se lee', (await p.locator('#asistente-ayuda').isVisible()) && (await ultimaHabla()).startsWith('Toca el lugar donde está'));
    await siguiente();
    ok('sin elegir chacra no avanza', await contiene(error, 'Toca una de tus chacras'));
    await p.locator('.asistente-opcion', { hasText: 'CH-01' }).click();
    ok('elegir quita el error', await error.isHidden());
    await siguiente();
    await esperarPregunta('¿Estás en tu chacra?');
    await p.waitForSelector('.evidencia-gps[data-estado="dentro"]', { timeout: 20000 });
    ok('GPS: dentro de la chacra', await empiezaCon(p.locator('.evidencia-gps-texto'), 'Estás dentro de tu chacra CH-01'));
    await p.waitForTimeout(200);
    ok('lee el resultado del GPS', (await habla()).some((h) => h.startsWith('Estás dentro de tu chacra CH-01')));
    await siguiente();
    await esperarPregunta('¿Qué sembraste o qué produces?');
    await p.evaluate(() => window.__dictados.push({ texto: 'papa amarilla.' }));
    await p.click('.asistente-mic');
    await p.waitForFunction(() => document.querySelector('#asistente-cultivo')?.value === 'Papa amarilla', null, { timeout: 5000 }).catch(() => {});
    ok('dictado de texto: "papa amarilla." → "Papa amarilla"', (await p.inputValue('#asistente-cultivo')) === 'Papa amarilla', await p.inputValue('#asistente-cultivo'));
    ok('repite lo que escuchó', (await habla()).includes('Escuché Papa amarilla'));
    await p.waitForFunction(() => document.querySelector('#asistente-plantilla')?.textContent.includes('Etapas'), null, { timeout: 8000 }).catch(() => {});
    ok('reconoce la plantilla del cultivo', await contiene(p.locator('#asistente-plantilla'), 'Etapas:'), await texto(p.locator('#asistente-plantilla')));
    console.log('   precio de referencia:', JSON.stringify(await texto(p.locator('#asistente-precio-referencia'))));
  }
  if (MODO === 'solo-voz') {
    await siguiente();
    await esperarPregunta('parcelas');
    await p.evaluate(() => window.__dictados.push({ texto: 'ciento veinte' }));
    await p.click('.asistente-mic');
    await p.waitForTimeout(800);
    ok('dictado de número: "ciento veinte" → 120', (await p.inputValue('#asistente-parcelas')) === '120');
    await p.evaluate(() => window.__dictados.push({ error: 'not-allowed' }));
    await p.click('.asistente-mic');
    await p.waitForTimeout(800);
    ok('sin permiso del micrófono: mensaje claro', await contiene(error, 'No diste permiso para usar el micrófono'));
    p.once('dialog', (d) => d.accept());
    await p.click('.btn-asistente-cerrar');
    await p.waitForSelector('#modal-asistente', { state: 'detached' });
    ok('cerrar a mitad de camino pide confirmación y cierra', true);
  } else {
    if (MODO !== 'resto') {
      await siguiente();
      await esperarPregunta('¿En cuántas parcelas sembraste?');
      await p.evaluate(() => window.__dictados.push({ texto: 'dos' }));
      await p.click('.asistente-mic');
      await p.waitForTimeout(800);
      ok('dictado de número: "dos" → 2', (await p.inputValue('#asistente-parcelas')) === '2');
      await p.evaluate(() => window.__dictados.push({ texto: 'muchas' }));
      await p.click('.asistente-mic');
      await p.waitForTimeout(800);
      ok('si no es número, lo dice y deja el valor anterior', (await contiene(error, 'pero no es un número')) && (await p.inputValue('#asistente-parcelas')) === '2');
      await p.evaluate(() => window.__dictados.push({ error: 'not-allowed' }));
      await p.click('.asistente-mic');
      await p.waitForTimeout(800);
      ok('sin permiso del micrófono: mensaje claro', await contiene(error, 'No diste permiso para usar el micrófono'));
      await p.click('.asistente-paso-num[aria-label="Más 1"]');
      await p.click('.asistente-paso-num[aria-label="Menos 1"]');
      ok('botones − / +', (await p.inputValue('#asistente-parcelas')) === '2');
      await siguiente();
      await esperarPregunta('¿Cuándo sembraste?');
      await p.locator('.asistente-atajo', { hasText: 'Hace un mes' }).click();
      ok('el atajo marca la fecha', await empiezaCon(p.locator('.asistente-fecha-elegida'), 'Elegiste:'));
      await siguiente();
      await esperarPregunta('¿Cuándo crees que vas a cosechar?');
      await p.waitForSelector('.sugerencia-cosecha:not(.cargando), .sugerencia-cosecha-sin-dato', { timeout: 45000 });
      if (await p.locator('.btn-usar-sugerencia').count()) {
        console.log('   sugerencia:', await texto(p.locator('.sugerencia-cosecha-fecha')), '·', await texto(p.locator('.sugerencia-cosecha-fuente')));
        await p.waitForTimeout(400);
        ok('lee la sugerencia (en cola)', (await habla()).some((h) => h.startsWith('Podrías cosechar entre el')));
        await p.click('.btn-usar-sugerencia');
        ok('usar la fecha más probable', (await p.getAttribute('.btn-usar-sugerencia', 'aria-pressed')) === 'true' && (await p.locator('.sugerencia-cosecha.usada').count()) === 1);
        ok('atajos del rango del cultivo', (await p.locator('.asistente-atajo', { hasText: 'Lo más probable' }).count()) === 1);
      } else {
        console.log('   sin sugerencia:', await texto(p.locator('.sugerencia-cosecha-sin-dato')));
        await p.locator('.asistente-atajo').first().click();
      }
      await siguiente();
      if (await p.locator('input[name="asistente-unidad"]').count()) { await p.locator('.asistente-opcion').first().click(); await siguiente(); }
      await esperarPregunta('¿Cuánto gastaste para empezar?');
      await p.waitForSelector('.ag-etapa, #asistente-gastos-etapas .muted:not(:has-text("Buscando"))', { timeout: 15000 });
      const montos = p.locator('.ag-etapa .ag-campo input[type=number]:not([id$="-peones"]):not([id$="-precio"])');
      console.log('   etapas del inicio:', await textos(p.locator('.ag-etapa legend')));
      await montos.first().fill('150');
      const jornal = p.locator('.ag-jornales').first();
      await jornal.locator('input[id$="-peones"]').fill('2');
      await siguiente();
      ok('mano de obra a medias: avisa', await contiene(error, 'En la mano de obra'));
      await jornal.locator('input[id$="-dias"]').fill('1');
      await jornal.locator('input[id$="-precio"]').fill('50');
      ok('total de jornales', await contiene(jornal.locator('.ag-jornales-total'), '= S/ 100.00'), await texto(jornal.locator('.ag-jornales-total')));
      await siguiente();
      await esperarPregunta('Revisa y confirma');
      await p.waitForTimeout(300);
      const resumen = await textos(p.locator('.asistente-resumen li'));
      console.log('   resumen:', resumen.join(' | '));
      ok('resumen con los gastos', resumen.some((t) => t.includes('s/ 250.00 en 2 gastos')), resumen);
      ok('lee el resumen', (await ultimaHabla()).startsWith('Revisa y confirma. Lugar: CH-01'));
      await p.click('.asistente-cambiar[aria-label="Cambiar Parcelas"]');
      await esperarPregunta('parcelas');
      ok('"Cambiar" lleva al paso y ofrece volver al resumen', await contiene(p.locator('.btn-asistente-siguiente'), 'Volver al resumen'));
      await siguiente();
      await esperarPregunta('Revisa y confirma');
      await p.click('.btn-asistente-siguiente.es-final');
      await esperarToast('Siembra registrada 🌱');
      await p.waitForSelector('#modal-asistente', { state: 'detached' });
      ok('registra la siembra y cierra', true);
      ok('dice "Listo. Quedó registrado."', (await habla()).includes('Listo. Quedó registrado.'));

      const nuevos = (await api('/productores/productores/produccion/me')).filter((r) => !antes.has(r.id));
      siembra = nuevos.find((r) => r.cultivo === 'Papa amarilla');
      ok('la siembra quedó en la base', Boolean(siembra), nuevos.map((r) => r.cultivo));
      console.log('   DATO: siembra', siembra?.id, siembra?.cultivo, siembra?.estado, 'plantilla', siembra?.plantilla, 'distancia', siembra?.siembra_distancia_km);
    }
    const tarjeta = p.locator('.produccion-card', { has: p.locator('h4', { hasText: /^Papa amarilla$/ }) }).filter({ has: p.locator('.btn-completar-cosecha') }).first();
    await tarjeta.waitFor();
    const dato = (etiqueta) => tarjeta.locator('.perfil-dato', { has: p.locator('.perfil-dato-label', { hasText: new RegExp(`^${etiqueta}$`) }) }).first().locator('.perfil-dato-valor');

    if (MODO !== 'resto') {
      await tarjeta.locator('.btn-corregir-siembra').click();
      await tarjeta.locator('input[name="numero_parcelas"]').fill('3');
      await tarjeta.locator('.form-corregir-siembra button[type=submit]').click();
      await esperarToast('Siembra corregida ✅');
      await p.waitForTimeout(800);
      ok('corregir: se guarda y se cierra el formulario', (await tarjeta.locator('.form-corregir-siembra').count()) === 0 && await esIgual(dato('Parcelas'), '3'), await texto(dato('Parcelas')));
    }

    // Gastos: anotar dos, ver la lista y borrar uno.
    const anotar = async (etapa, monto) => {
      await tarjeta.locator(`.btn-anotar-gasto[aria-label="Anotar un gasto en ${etapa}"]`).click();
      const opcion = await tarjeta.locator('.form-gasto select[name="rubro"] option').evaluateAll((os) => os.map((o) => o.value).find((v) => v !== 'mano_obra'));
      await tarjeta.locator('.form-gasto select[name="rubro"]').selectOption(opcion);
      await tarjeta.locator('.form-gasto input[name="monto"]').fill(String(monto));
      await tarjeta.locator('.form-gasto button[type=submit]').click();
      await esperarToast('Gasto anotado 🧾');
      await p.waitForTimeout(800);
    };
    console.log('   etapas:', (await textos(tarjeta.locator('.eg-etapa strong'))).join(' → '));
    const conAnotar = await tarjeta.locator('.btn-anotar-gasto').evaluateAll((bs) => bs.map((x) => x.getAttribute('aria-label').replace('Anotar un gasto en ', '')));
    const [e1, e2] = conAnotar.filter((e) => !/^(Preparación|Siembra)/.test(e));
    const totalAntes = Number((await texto(tarjeta.locator('.eg-cabeza strong'))).replace(/[^\d.]/g, ''));
    await anotar(e1, 80);
    ok('anotar gasto: se cierra el formulario', (await tarjeta.locator('.form-gasto').count()) === 0);
    await anotar(e2, 30);
    await tarjeta.locator('.eg-detalle summary').click();
    await tarjeta.locator('.eg-lista li').first().waitFor();
    const filasAntes = await tarjeta.locator('.eg-lista li').count();
    ok('la lista tiene los gastos anotados', filasAntes >= 2, await textos(tarjeta.locator('.eg-lista li')));
    const fila = tarjeta.locator('.eg-lista li', { hasText: 'S/ 30.00' }).first();
    await fila.locator('.btn-borrar-gasto').click();
    ok('borrar pide confirmar en el mismo botón', await contiene(fila.locator('.btn-borrar-gasto'), '¿Borrar?'));
    await fila.locator('.btn-borrar-gasto').click();
    await esperarToast('Gasto borrado.');
    await p.waitForTimeout(1000);
    ok('borrado: la lista se actualiza', (await tarjeta.locator('.eg-lista li').count()) === filasAntes - 1);
    ok('total de gastos por etapa (+80)', await esIgual(tarjeta.locator('.eg-cabeza strong'), `S/ ${(totalAntes + 80).toFixed(2)}`), await texto(tarjeta.locator('.eg-cabeza strong')));

    // Completar la cosecha con foto.
    await p.evaluate(() => { window.__habla.length = 0; });
    await tarjeta.locator('.btn-completar-cosecha').click();
    await esperarPregunta('¿Estás en tu chacra?');
    await p.waitForSelector('.evidencia-gps[data-estado="dentro"]', { timeout: 20000 });
    await siguiente();
    await esperarPregunta('Toma una foto de tu cosecha');
    await siguiente();
    ok('sin foto no avanza', await contiene(error, 'Falta la foto'));
    await p.setInputFiles('#asistente-foto-cosecha', FOTO);
    await p.waitForSelector('.foto-entrega[data-estado="lista"]');
    ok('foto lista con vista previa', (await p.locator('.foto-entrega-previa img').getAttribute('src'))?.startsWith('blob:') && await esIgual(p.locator('.foto-nombre'), FOTO.split(/[\\/]/).pop()));
    ok('dice "Foto lista. Toca Siguiente."', (await habla()).includes('Foto lista. Toca Siguiente.'));
    await siguiente();
    await esperarPregunta('¿Cuánto cosechaste?');
    await p.fill('#asistente-cantidad', '300');
    await p.press('#asistente-cantidad', 'Enter');
    await esperarPregunta('¿Cuándo cosechaste?');
    ok('Enter en un campo avanza', true);
    await p.locator('.asistente-atajo', { hasText: 'Hoy' }).click();
    await siguiente();
    await esperarPregunta('¿Cuánto gastaste al cosechar?');
    console.log('   ya registrado:', (await p.locator('.ag-ya-registrado').count()) ? await texto(p.locator('.ag-ya-registrado')) : '—');
    const jornalFinal = p.locator('.ag-jornales').first();
    await jornalFinal.locator('input[id$="-peones"]').fill('3');
    await jornalFinal.locator('input[id$="-dias"]').fill('0,5');
    await jornalFinal.locator('input[id$="-precio"]').fill('50');
    ok('medio día con coma', await contiene(jornalFinal.locator('.ag-jornales-total'), '3 peones × 0,5 días'), await texto(jornalFinal.locator('.ag-jornales-total')));
    await siguiente();
    await esperarPregunta('Revisa y confirma');
    console.log('   resumen:', (await textos(p.locator('.asistente-resumen li'))).join(' | '));
    await p.click('.btn-asistente-siguiente.es-final');
    await esperarToast('Listo ✅ Tu producto ya está en "Mis productos" como borrador');
    await p.waitForSelector('#modal-asistente', { state: 'detached' });
    const final = (await api('/productores/productores/produccion/me')).find((r) => r.id === siembra.id);
    ok('cosecha completada en la base', final?.estado === 'cosechado' && Boolean(final?.foto_cosecha_url), { estado: final?.estado, foto: final?.foto_cosecha_url });
    console.log('   DATO: cosecha', final?.cantidad_cosechada, final?.unidad_medida, 'costo total', final?.costo_total, 'foto', final?.foto_cosecha_url, 'distancia', final?.cosecha_distancia_km);
    const productosNuevos = (await api('/productos/productos/mios')).filter((x) => !productosAntes.has(x.id));
    console.log('   DATO: productos nuevos', JSON.stringify(productosNuevos.map((x) => ({ id: x.id, nombre: x.nombre, estado: x.estado, stock: x.stock }))));
    ok('el borrador del producto quedó en "Mis productos"', productosNuevos.length >= 1);
    const cosechada = p.locator('.produccion-card', { has: p.locator('h4', { hasText: /^Papa amarilla$/ }) }).filter({ has: p.locator('.evidencia-resumen') }).first();
    await cosechada.waitFor({ timeout: 10000 });
    ok('la tarjeta muestra la foto y dónde estaba', (await cosechada.locator('.evidencia-resumen img').count()) === 1 && await contiene(cosechada.locator('.evidencia-resumen-lista'), 'Cosecha: a'));
  }
} else {
  // ---- sin voz ni GPS: solo tocando y escribiendo; quechua; Escape; anular.
  await p.goto(`${BASE}/productor`, { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.cta-siembra, .siembra-cta, [data-tab-panel="inicio"]');
  await p.locator('button:has-text("Runasimi")').first().click();
  await p.waitForTimeout(300);
  const principal = p.locator('main');
  ok('quechua: título y pestañas', (await contiene(principal, 'Tarpuqpa panelnin')) && (await contiene(principal, 'Qallariy')) && (await contiene(principal, 'Chakraykuna')));
  ok('quechua: tarjeta de inicio', await contiene(principal, 'Tarpusqaykita qillqay'));
  await p.goto(`${BASE}/productor/siembras`, { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('.siembra-cta');
  ok('quechua: título de Mis siembras', await contiene(p.locator('.section-header.inner h3').first(), 'Tarpusqaykuna, uywasqaykuna'));
  await p.click('.btn-abrir-asistente-siembra');
  await p.waitForSelector('#modal-asistente .asistente');
  ok('sin voz: no hay "Escuchar" ni lectura en voz alta', (await p.locator('.btn-asistente-escuchar').count()) === 0 && await p.locator('.asistente-voz-lectura').isHidden());
  ok('quechua: botón Qatiqnin', await contiene(p.locator('.btn-asistente-siguiente'), 'Qatiqnin'));
  ok('sin dictado: no hay micrófono', (await p.locator('.asistente-mic').count()) === 0);
  await p.locator('.asistente-opcion', { hasText: ch02.codigo }).click();
  await siguiente();
  await esperarPregunta('¿Estás en tu chacra?');
  await p.waitForSelector('.evidencia-gps[data-estado="sin-gps"]', { timeout: 20000 });
  ok('sin GPS: se registra igual con advertencia', await contiene(p.locator('.evidencia-gps-texto'), 'Se registrará igual, pero quedará como advertencia'), await texto(p.locator('.evidencia-gps-texto')));
  let dialogo = '';
  p.once('dialog', (d) => { dialogo = d.message(); d.dismiss(); });
  await p.keyboard.press('Escape');
  await p.waitForTimeout(300);
  ok('Escape pide confirmar y "Cancelar" lo deja abierto', dialogo === '¿Quieres salir? Se perderá lo que llenaste.' && await p.locator('#modal-asistente').isVisible());
  await siguiente();
  await esperarPregunta('¿Qué sembraste o qué produces?');
  ok('quechua: botón Qipaman (atrás)', await contiene(p.locator('.btn-asistente-atras'), 'Qipaman'));
  await p.fill('#asistente-cultivo', 'Huevos de gallina');
  await p.press('#asistente-cultivo', 'Enter');
  await p.waitForFunction(() => !document.querySelector('#asistente-pregunta')?.textContent.includes('¿Qué sembraste'), null, { timeout: 8000 });
  ok('crianza: pregunta por los animales', await esIgual(p.locator('#asistente-pregunta'), '¿Cuántos animales tiene tu lote?'), await texto(p.locator('#asistente-pregunta')));
  await p.fill('#asistente-parcelas', '12');
  await siguiente();
  await esperarPregunta('¿Cuándo empezó este periodo?');
  await p.locator('.asistente-atajo', { hasText: 'Hoy' }).click();
  await siguiente();
  await esperarPregunta('¿Hasta cuándo es este periodo?');
  ok('crianza: sin sugerencia de IA y atajos en semanas', (await p.locator('.sugerencia-cosecha').count()) === 0 && (await p.locator('.asistente-atajo', { hasText: 'En 1 semana' }).count()) === 1);
  await p.locator('.asistente-atajo', { hasText: 'En 1 semana' }).click();
  await siguiente();
  if (await contiene(p.locator('#asistente-pregunta'), 'medir')) {
    console.log('   opciones de unidad:', await textos(p.locator('.asistente-opcion strong')));
    await p.locator('.asistente-opcion').first().click();
    if (await p.locator('.asistente-equivalencia:not(.hidden)').count()) await p.fill('#asistente-equivalencia', '0.06');
    await siguiente();
  }
  await esperarPregunta('¿Cuánto gastaste para empezar?');
  await p.waitForSelector('.ag-etapa, #asistente-gastos-etapas .muted:not(:has-text("Buscando"))', { timeout: 15000 });
  await siguiente();
  await esperarPregunta('Revisa y confirma');
  console.log('   resumen:', (await textos(p.locator('.asistente-resumen li'))).join(' | '));
  ok('resumen: sin ubicación como advertencia', (await p.locator('.asistente-resumen li.es-advertencia').count()) === 1);
  ok('quechua: "Tikray" en el resumen', await esIgual(p.locator('.asistente-cambiar').first(), 'Tikray'));
  await p.click('.btn-asistente-siguiente.es-final');
  await esperarToast('Registrado 🌱');
  await p.waitForSelector('#modal-asistente', { state: 'detached' });
  const nuevos = (await api('/productores/productores/produccion/me')).filter((r) => !antes.has(r.id));
  const huevos = nuevos.find((r) => r.cultivo === 'Huevos de gallina');
  ok('registrado sin voz ni GPS', Boolean(huevos) && huevos.siembra_distancia_km == null, huevos);
  console.log('   DATO: registro', huevos?.id, huevos?.cultivo, huevos?.tipo_produccion, huevos?.unidad_medida, huevos?.estado);
  const tarjeta = p.locator('.produccion-card', { has: p.locator('h4', { hasText: /^Huevos de gallina$/ }) }).filter({ has: p.locator('.btn-anular-siembra') }).first();
  await tarjeta.waitFor();
  ok('crianza en curso: "En curso" y "Registrar lo recogido"', (await contiene(tarjeta.locator('.pill-estado'), 'En curso')) && (await contiene(tarjeta.locator('.btn-completar-cosecha'), 'Registrar lo recogido')), { estado: await texto(tarjeta.locator('.pill-estado')), boton: await texto(tarjeta.locator('.btn-completar-cosecha')) });
  await tarjeta.locator('.btn-anular-siembra').click();
  ok('anular pide confirmar', await tarjeta.locator('.anular-siembra-confirmar').isVisible());
  await tarjeta.locator('.btn-confirmar-anular').click();
  await esperarToast('Siembra anulada.');
  await p.waitForTimeout(1000);
  const tras = (await api('/productores/productores/produccion/me')).find((r) => r.id === huevos.id);
  ok('anulada: ya no aparece en la lista', !tras && (await p.locator('.produccion-card', { has: p.locator('h4', { hasText: /^Huevos de gallina$/ }) }).count()) === 0, tras?.estado);
  console.log('   DATO: anulado', huevos.id);
}

ok('sin errores de página', !errores.length, errores);
cerrar(`${CANAL}, ${MODO}`);
await b.close();
