// Lo que comparten las pruebas e2e: dónde está la aplicación, las cuentas de prueba de .env.qa (ignorado por git), el
// conteo de resultados y, sobre todo, cómo se comparan los textos.
//
// Los textos se comparan NORMALIZADOS: textContent (no innerText, que depende del CSS: un text-transform: uppercase o un
// salto de línea entre bloques cambian lo que devuelve), sin distinguir mayúsculas y con los espacios colapsados.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const BASE = process.env.BASE || 'http://localhost:5173';
export const API = process.env.API || 'http://localhost:8000/api';

// Una ruta del repositorio a partir de esta carpeta (e2e/).
export const rutaDelRepo = (relativa) => fileURLToPath(new URL(`../../${relativa}`, import.meta.url));

export function leerEnvQa() {
  const texto = readFileSync(new URL('../.env.qa', import.meta.url), 'utf8');
  return Object.fromEntries(texto.split(/\r?\n/).filter((l) => l.includes('=') && !l.startsWith('#'))
    .map((l) => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()]));
}

// ---- textos
export const normalizar = (s) => String(s ?? '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('es-PE');

// El texto normalizado de un elemento (el locator debe apuntar a uno solo).
export async function texto(locator) {
  return normalizar(await locator.textContent());
}

// Los textos normalizados de todos los elementos del locator.
export async function textos(locator) {
  return (await locator.allTextContents()).map(normalizar);
}

export async function contiene(locator, esperado) {
  return (await texto(locator)).includes(normalizar(esperado));
}

export async function empiezaCon(locator, esperado) {
  return (await texto(locator)).startsWith(normalizar(esperado));
}

export async function esIgual(locator, esperado) {
  return (await texto(locator)) === normalizar(esperado);
}

// Para un texto que ya se tiene (de la API, de la voz espiada…).
export const incluye = (textoCompleto, esperado) => normalizar(textoCompleto).includes(normalizar(esperado));

// ---- resultados
export function marcador() {
  let bien = 0;
  let mal = 0;
  const ok = (nombre, condicion, detalle = '') => {
    if (condicion) bien++; else mal++;
    console.log(`${condicion ? 'OK ' : 'MAL'} ${nombre}${!condicion && detalle !== '' ? ` -> ${JSON.stringify(detalle).slice(0, 400)}` : ''}`);
  };
  const cerrar = (etiqueta = '') => {
    console.log(`${bien} bien, ${mal} mal${etiqueta ? ` (${etiqueta})` : ''}`);
    if (mal) process.exitCode = 1;
  };
  return { ok, cerrar };
}

// ---- sesión y API
export async function iniciarSesionApi(email, password) {
  const r = await fetch(`${API}/usuarios/usuarios/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
  if (!r.ok) throw new Error(`No se pudo iniciar sesión con ${email}: ${r.status}`);
  const { access_token: token } = await r.json();
  const carga = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString());
  const api = async (ruta, opciones = {}) => {
    const resp = await fetch(`${API}${ruta}`, { ...opciones, headers: { Authorization: `Bearer ${token}`, ...(opciones.body ? { 'Content-Type': 'application/json' } : {}), ...opciones.headers } });
    const datos = await resp.json().catch(() => null);
    return opciones.conEstado ? { estado: resp.status, datos } : datos;
  };
  return { token, usuarioId: carga.sub, rol: carga.rol, api };
}

// La sesión tal como la guarda el frontend (para entrar sin pasar por la pantalla de login).
export function sesionDelFrontend({ token, usuarioId, rol }, { nombre = 'QA', email = '' } = {}) {
  return JSON.stringify({ token, usuarioId, nombre, email, rol, tipoComprador: null });
}

// Login por la pantalla de React.
export async function entrarPorPantalla(pagina, email, password, destino) {
  await pagina.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await pagina.fill('#login-email', email);
  await pagina.fill('#login-password', password);
  await pagina.click('button[type=submit]');
  await pagina.waitForURL((u) => u.pathname.startsWith(destino), { timeout: 20000 });
}

// Errores de la página, sin el ruido conocido (teselas del mapa, Google, favicon).
export function vigilarErrores(pagina) {
  const errores = [];
  pagina.on('pageerror', (e) => errores.push(e.message));
  pagina.on('console', (m) => { if (m.type() === 'error' && !/favicon|openstreetmap|tile|GSI|403/i.test(m.text())) errores.push(m.text().slice(0, 200)); });
  return errores;
}

// Una foto JPEG de prueba dibujada en el navegador (sin usar fotos de nadie): un fondo de color con círculos.
export async function fotoDePrueba(pagina, nombre, color) {
  const base64 = await pagina.evaluate((c) => {
    const lienzo = document.createElement('canvas');
    lienzo.width = 800; lienzo.height = 600;
    const ctx = lienzo.getContext('2d');
    ctx.fillStyle = '#5b4a32'; ctx.fillRect(0, 0, 800, 600);
    for (let i = 0; i < 40; i++) {
      ctx.fillStyle = c; ctx.beginPath();
      ctx.ellipse(40 + (i % 8) * 100, 60 + Math.floor(i / 8) * 115, 42, 32, (i % 5) / 3, 0, Math.PI * 2); ctx.fill();
    }
    return lienzo.toDataURL('image/jpeg', 0.85).split(',')[1];
  }, color);
  return { name: nombre, mimeType: 'image/jpeg', buffer: Buffer.from(base64, 'base64') };
}
