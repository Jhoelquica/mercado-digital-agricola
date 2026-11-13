// Geocerca por contorno, de punta a punta contra el clúster local. ESCRIBE DATOS REALES y al final los borra:
//   1. Por la API, como el agricultor QA: le pone un contorno (cuadrado de 120 m) a su chacra CH-02, registra una siembra
//      a 250 m (fuera), sube la foto y completa la cosecha dentro del contorno. Publica el producto (en tránsito).
//   2. La cadena de certificación selló las dos frases de formato fijo, con la huella del contorno y sin coordenadas.
//   3. Página pública del QR (visitante sin sesión): la cosecha dice "Ubicación confirmada en la chacra"; la siembra, que
//      quedó fuera, se muestra tal como está en la cadena, sin la palabra "confirmada".
//   4. Mis siembras (agricultor): lo que quedó de cada momento.
//   5. Llegada al almacén (admin): la etiqueta con el resultado de la geocerca de la cosecha, sin mapa y sin que la red
//      traiga coordenadas.
// Limpieza: borra el producto y la siembra con los DELETE de limpieza QA, llamados DENTRO de cada pod (kubectl exec: el
// secreto no sale del clúster), y le quita el contorno a la chacra. Queda un bloque en la cadena de certificación (los
// bloques no se borran) y las versiones del contorno. Capturas en e2e/capturas/geocerca-*.png (no se versionan).
//   BASE=http://localhost:8000 npm run e2e:geocerca        (ANCHO=390 para el ancho de un celular)
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { chromium } from 'playwright';
import { API, BASE, iniciarSesionApi, leerEnvQa, marcador, rutaDelRepo, sesionDelFrontend, vigilarErrores } from './comun.mjs';

const CANAL = process.env.CANAL || 'msedge';
const CONTEXTO = process.env.CONTEXTO || 'docker-desktop';
const ANCHO = Number(process.env.ANCHO) || 1280;
const FOTO = rutaDelRepo('frontend/public/static/hero-valle-andino-1600.jpg');
const CAPTURAS = rutaDelRepo('frontend/e2e/capturas');
mkdirSync(CAPTURAS, { recursive: true });
const captura = (p, nombre) => p.screenshot({ path: `${CAPTURAS}/geocerca-${process.env.ANCHO ? `${process.env.ANCHO}-` : ''}${nombre}.png` });
const env = leerEnvQa();
const { ok, cerrar } = marcador();
// Una coordenada escrita con decimales (-13.1849). Las distancias de la cadena van con coma ("0,03 km").
const COORDENADA = /-\d{1,2}\.\d{3,}/;
const CLAVE_DE_UBICACION = /"(latitud|longitud|coordinates|contorno|[a-z_]*_latitud|[a-z_]*_longitud)"/;

const productor = await iniciarSesionApi(env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD);
const admin = await iniciarSesionApi(env.QA_ADMIN_EMAIL, env.QA_ADMIN_PASSWORD);
const json = (cuerpo, method) => ({ method, body: JSON.stringify(cuerpo), conEstado: true });
const chacras = await productor.api('/productores/chacras/me');
const chacra = chacras.find((c) => c.codigo === 'CH-02') || chacras[0];
if (!chacra || chacra.contorno) {
  console.log(`MAL ${chacra ? `la chacra ${chacra.codigo} ya tiene un contorno: esta prueba no lo cambia` : 'la cuenta QA del productor no tiene chacras'}`);
  process.exit(1);
}
const lat = Number(chacra.ubicacion_latitud);
const lng = Number(chacra.ubicacion_longitud);
const METROS_POR_GRADO = 111_320;
const alEste = (m) => lng + m / (METROS_POR_GRADO * Math.cos((lat * Math.PI) / 180));
const d = 60 / METROS_POR_GRADO;   // cuadrado de 120 m de lado: el borde queda a 60 m del punto de la chacra
const contorno = { type: 'Polygon', coordinates: [[[alEste(-60), lat - d], [alEste(60), lat - d], [alEste(60), lat + d], [alEste(-60), lat + d], [alEste(-60), lat - d]]] };
const dia = (desdeHoy) => new Date(Date.now() + desdeHoy * 864e5).toISOString().slice(0, 10);

// El DELETE de limpieza QA de un servicio, llamado dentro de su pod.
function borrarEnElPod(servicio, puerto, ruta) {
  const programa = [
    'import os, urllib.request',
    `peticion = urllib.request.Request('http://127.0.0.1:${puerto}${ruta}', method='DELETE', headers={'X-Servicio-Secreto': os.environ.get('QA_LIMPIEZA_SECRETO', '')})`,
    'try:',
    '    urllib.request.urlopen(peticion, timeout=10)',
    "    print('ok')",
    'except Exception as error:',
    "    print('no', error)",
  ].join('\n');
  try {
    return execFileSync('kubectl', ['--context', CONTEXTO, 'exec', `deploy/${servicio}`, '--', 'python', '-c', programa], { encoding: 'utf8' }).trim() === 'ok';
  } catch {
    return false;
  }
}

let registroId = null;
let productoId = null;
let navegador = null;
try {
  // ---- 1. los datos, por la API
  const puesto = await productor.api(`/productores/chacras/${chacra.id}/contorno`, json({ contorno }, 'PUT'));
  ok(`contorno de 120 m puesto en la chacra ${chacra.codigo}`, puesto.estado === 200, puesto);
  const huella = (await productor.api(`/productores/chacras/${chacra.id}/contorno`)).hash_sha256;

  const siembra = await productor.api('/productores/productores/produccion', json({
    chacra_id: chacra.id, cultivo: 'QA geocerca', numero_parcelas: 1, unidad_medida: 'kg', fecha_siembra: dia(-100), fecha_cosecha_estimada: dia(20),
    latitud: lat, longitud: alEste(250), precision_m: 15,
  }, 'POST'));
  registroId = siembra.datos?.id ?? null;
  ok('siembra a 250 m del punto: se registra igual y queda "fuera"', siembra.estado === 200 && siembra.datos.siembra_metodo_geocerca === 'poligono' && siembra.datos.siembra_resultado_geocerca === 'fuera' && siembra.datos.siembra_contorno_hash === huella, siembra);

  const formulario = new FormData();
  formulario.append('archivo', new Blob([readFileSync(FOTO)], { type: 'image/jpeg' }), 'cosecha.jpg');
  const foto = await fetch(`${API}/productores/productores/produccion/${registroId}/foto-cosecha`, { method: 'POST', headers: { Authorization: `Bearer ${productor.token}` }, body: formulario });
  ok('foto de la cosecha subida', foto.status === 200, foto.status);

  const cosecha = await productor.api(`/productores/productores/produccion/${registroId}/completar-cosecha`, json({
    cantidad_cosechada: 20, fecha_cosecha_real: dia(0), gastos: [], latitud: lat, longitud: lng, precision_m: 9,
  }, 'PATCH'));
  productoId = cosecha.datos?.producto_id ?? null;
  ok('cosecha dentro del contorno: queda "dentro" y se registra en la cadena',
    cosecha.estado === 200 && cosecha.datos.cosecha_resultado_geocerca === 'dentro' && cosecha.datos.cosecha_distancia_borde_m === 0 && cosecha.datos.certificacion_registrada === true && Boolean(productoId), cosecha);

  // Para publicar: precio, categoría y al menos una foto del producto.
  await productor.api(`/productos/productos/${productoId}`, json({ precio: 2.5, categoria: 'tuberculo' }, 'PATCH'));
  const imagen = new FormData();
  imagen.append('archivo', new Blob([readFileSync(FOTO)], { type: 'image/jpeg' }), 'producto.jpg');
  await fetch(`${API}/productos/productos/${productoId}/imagenes/subir?orden=0`, { method: 'POST', headers: { Authorization: `Bearer ${productor.token}` }, body: imagen });
  const publicado = await productor.api(`/productos/productos/${productoId}/publicar`, { method: 'PATCH', conEstado: true });
  ok('producto publicado: en tránsito al almacén', publicado.estado === 200 && publicado.datos.estado === 'en_transito', publicado);

  // ---- 2. lo que quedó sellado en la cadena
  const historial = await (await fetch(`${API}/certificacion/certificacion/${productoId}/historial`)).json();
  const sellado = historial.find((bloque) => bloque.evento === 'cosecha_registrada')?.datos || '';
  const fraseSiembra = new RegExp(`Geocerca siembra: metodo=poligono; resultado=fuera; distancia_borde_m=(\\d+); precision_m=15; contorno_sha256=${huella}\\.`).exec(sellado);
  const fraseCosecha = `Geocerca cosecha: metodo=poligono; resultado=dentro; distancia_borde_m=0; precision_m=9; contorno_sha256=${huella}.`;
  ok('la cadena selló la frase de la siembra (fuera, a unos 190 m del borde) con la huella del contorno', Boolean(fraseSiembra) && Math.abs(Number(fraseSiembra[1]) - 190) <= 3, sellado);
  ok('la cadena selló la frase de la cosecha (dentro) con la misma huella', sellado.includes(fraseCosecha), sellado);
  ok('con contorno no va la frase de los km, y la cadena no trae coordenadas ni el polígono',
    !/km de la chacra/.test(sellado) && !COORDENADA.test(JSON.stringify(historial)) && !JSON.stringify(historial).includes('coordinates'), sellado);
  const verificacion = await (await fetch(`${API}/certificacion/certificacion/${productoId}/verificar`)).json();
  ok('la cadena nueva verifica', verificacion.valido === true, verificacion);

  // ---- 3. página pública del QR, sin sesión
  navegador = await chromium.launch({ channel: CANAL, headless: true });
  const contexto = () => navegador.newContext({ viewport: { width: ANCHO, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' });
  const visitante = await contexto();
  const pv = await visitante.newPage();
  const erroresVisitante = vigilarErrores(pv);
  await pv.goto(`${BASE}/verificar?producto_id=${productoId}`, { waitUntil: 'domcontentloaded' });
  await pv.locator('.timeline-datos-lista').first().waitFor({ timeout: 30000 });
  const confirmadas = pv.locator('.timeline-datos-lista li.es-confirmada');
  const talCual = pv.locator('.timeline-datos-lista li.es-geocerca');
  const textoConfirmada = (await confirmadas.count()) === 1 ? (await confirmadas.first().textContent()).replace(/\s+/g, ' ').trim() : '';
  ok('QR: la cosecha (dentro) dice "Ubicación confirmada en la chacra"', textoConfirmada === 'Ubicación confirmada en la chacra (al cosechar)', textoConfirmada);
  const textoTalCual = (await talCual.count()) === 1 ? (await talCual.first().textContent()).trim() : '';
  ok('QR: la siembra (fuera) se muestra tal como está en la cadena, como advertencia y sin "confirmada"',
    Boolean(fraseSiembra) && textoTalCual === fraseSiembra[0] && !/confirmada/i.test(textoTalCual) && (await talCual.first().getAttribute('class')).includes('es-advertencia'), textoTalCual);
  const lineaDeTiempo = await pv.locator('.timeline').textContent();
  ok('QR: "confirmada" aparece una sola vez en la cadena', (lineaDeTiempo.match(/confirmada/gi) || []).length === 1);
  await confirmadas.first().scrollIntoViewIfNeeded();
  await captura(pv, '1-qr');
  ok('QR: sin errores en la consola', erroresVisitante.length === 0, erroresVisitante);
  await visitante.close();

  // ---- 4. Mis siembras, como el agricultor
  const ctxProductor = await contexto();
  await ctxProductor.addInitScript((s) => localStorage.setItem('agro_sesion', s), sesionDelFrontend(productor, { nombre: 'Agricultor QA', email: env.QA_PRODUCTOR_EMAIL }));
  const pp = await ctxProductor.newPage();
  await pp.goto(`${BASE}/productor/siembras`, { waitUntil: 'domcontentloaded' });
  const evidencia = pp.locator('.evidencia-resumen-lista', { hasText: 'del borde de tu chacra (fuera)' }).first();
  await evidencia.waitFor({ timeout: 30000 });
  const textoEvidencia = (await evidencia.textContent()).replace(/\s+/g, ' ');
  ok('Mis siembras: la siembra quedó fuera (advertencia) y la cosecha dentro de la chacra',
    /Siembra: a 1\d\d m del borde de tu chacra \(fuera\) — quedó como advertencia/.test(textoEvidencia) && /Cosecha: dentro de tu chacra/.test(textoEvidencia), textoEvidencia);
  await evidencia.scrollIntoViewIfNeeded();
  await captura(pp, '2-mis-siembras');
  await ctxProductor.close();

  // ---- 5. llegada al almacén, como el admin
  const ctxAdmin = await contexto();
  await ctxAdmin.addInitScript((s) => localStorage.setItem('agro_sesion', s), sesionDelFrontend(admin, { nombre: 'Admin', email: env.QA_ADMIN_EMAIL }));
  const pa = await ctxAdmin.newPage();
  const erroresAdmin = vigilarErrores(pa);
  const respuestas = [];
  pa.on('response', (r) => {
    if (r.url().includes('/geocerca')) respuestas.push(r.text().then((cuerpo) => ({ url: r.url(), cuerpo })).catch(() => null));
  });
  await pa.goto(`${BASE}/admin/transito`, { waitUntil: 'domcontentloaded' });
  const tarjeta = pa.locator(`.pedido-card[data-producto-id="${productoId}"]`);
  await tarjeta.waitFor({ timeout: 40000 });
  const etiqueta = tarjeta.locator('.alm-admin-geocerca');
  await etiqueta.waitFor({ timeout: 20000 });
  ok('almacén: la tarjeta de la cosecha lleva la etiqueta de la geocerca, con ícono y texto',
    (await etiqueta.getAttribute('data-resultado')) === 'dentro' && (await etiqueta.textContent()).trim() === 'Cosecha registrada dentro de la chacra' && (await etiqueta.locator('i.ti').count()) === 1,
    await etiqueta.textContent());
  ok('almacén: la tarjeta no tiene mapa', (await tarjeta.locator('.leaflet-container').count()) === 0);
  await pa.waitForLoadState('networkidle').catch(() => {});
  const cuerpos = (await Promise.all(respuestas)).filter(Boolean);
  const laDelProducto = cuerpos.find((r) => r.url.includes(`/produccion/${registroId}/geocerca`));
  ok('almacén: la respuesta de la geocerca no trae coordenadas ni el polígono',
    Boolean(laDelProducto) && cuerpos.every((r) => !COORDENADA.test(r.cuerpo) && !CLAVE_DE_UBICACION.test(r.cuerpo)), cuerpos.map((r) => r.cuerpo.slice(0, 200)));
  await tarjeta.scrollIntoViewIfNeeded();
  await captura(pa, '3-almacen');
  ok('almacén: sin errores en la consola', erroresAdmin.length === 0, erroresAdmin);
  await ctxAdmin.close();
} catch (e) {
  ok('la prueba terminó entera', false, e.message);
} finally {
  if (navegador) await navegador.close();
  // Limpieza: el producto, la siembra y el contorno de la chacra (queda como estaba, sin contorno).
  const sinProducto = productoId ? borrarEnElPod('productos', 8002, `/productos/${productoId}`) : true;
  const sinSiembra = registroId ? borrarEnElPod('productores', 8001, `/productores/produccion/${registroId}`) : true;
  const sinContorno = await productor.api(`/productores/chacras/${chacra.id}/contorno`, { method: 'DELETE', conEstado: true }).then((r) => r.estado === 200, () => false);
  ok('limpieza: producto, siembra y contorno de prueba borrados', sinProducto && sinSiembra && sinContorno, { sinProducto, sinSiembra, sinContorno });
  if (!sinProducto && productoId) console.log(`DATO quedó el producto ${productoId}`);
  if (!sinSiembra && registroId) console.log(`DATO quedó la siembra ${registroId}`);
  cerrar('geocerca');
}
