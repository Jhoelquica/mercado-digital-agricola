// Prueba del mapa del detalle de producto, contra un servidor en marcha: espera a que cargue una tesela real, 3 veces
// seguidas y con un navegador nuevo en cada una. Usa el Edge instalado para no descargar otro navegador.
//   BASE=http://localhost:5173 npm run e2e:mapa
import { chromium } from 'playwright';

const BASE = process.env.BASE || 'http://localhost:5173';
const lista = await (await fetch(`${BASE}/api/productos/productos`)).json();
const producto = lista.find((p) => p.total_resenas > 0 && p.estado === 'disponible') || lista[0];

let fallas = 0;
for (let corrida = 1; corrida <= 3; corrida++) {
  const navegador = await chromium.launch({ channel: 'msedge', headless: true });
  const pagina = await (await navegador.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  const inicio = Date.now();
  try {
    await pagina.goto(`${BASE}/producto/${producto.id}`, { waitUntil: 'domcontentloaded' });
    await pagina.waitForSelector('.leaflet-tile-loaded', { timeout: 30000 });
    console.log(`corrida ${corrida}: bien en ${Date.now() - inicio} ms`);
  } catch (error) {
    fallas++;
    console.log(`corrida ${corrida}: mal, ${error.message.slice(0, 200)}`);
  }
  await navegador.close();
}
if (fallas) process.exit(1);
