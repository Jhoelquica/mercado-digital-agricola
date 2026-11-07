// Fases 6 y 7, recojo de una cosecha de punta a punta, todo en React:
//   qa-repartidor cambia su vehículo (PATCH desde /repartidor/vehiculo) a uno que carga la cosecha → agricultor01 pide el
//   recojo → qa-repartidor lo acepta, lo marca recogido y en el almacén → el agricultor lo ve "en el almacén" → el admin
//   pesa y confirma la llegada en /admin/transito → el producto queda a la venta → qa-repartidor vuelve a su vehículo.
// PRODUCTO=<id de un producto en_transito de agricultor01 sin recojo>; por defecto, el publicado más recientemente.
// RECOJO=<id> VEHICULO=moto:50 retoma un recojo ya pedido (sin cambiar el vehículo; al final lo deja en VEHICULO).
//   npm run e2e:recojo
import { chromium } from 'playwright';
import { API, BASE, contiene, entrarPorPantalla, iniciarSesionApi, leerEnvQa, marcador, sesionDelFrontend, texto, vigilarErrores } from './comun.mjs';

const env = leerEnvQa();
const { ok, cerrar } = marcador();
const agricultor = await iniciarSesionApi(env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD);
const repartidor = await iniciarSesionApi(env.QA_REPARTIDOR_EMAIL, env.QA_REPARTIDOR_PASSWORD);
const admin = await iniciarSesionApi(env.QA_ADMIN_EMAIL, env.QA_ADMIN_PASSWORD);
const RECOJO = process.env.RECOJO || null;
const vehiculoActual = await repartidor.api('/transporte/repartidores/me');
const [tipoAntes, kgAntes] = (process.env.VEHICULO || `${vehiculoActual.tipo_vehiculo}:${vehiculoActual.capacidad_maxima_kg}`).split(':');
const vehiculoAntes = { tipo_vehiculo: tipoAntes, capacidad_maxima_kg: Number(kgAntes) };
const recojosAgricultor = await agricultor.api('/transporte/recojos/mios');
const conRecojo = new Set(recojosAgricultor.filter((r) => !['cancelado', 'completado'].includes(r.estado)).map((r) => r.producto_id));
const mios = await agricultor.api('/productos/productos/mios');
const recojoRetomado = RECOJO ? recojosAgricultor.find((r) => r.id === RECOJO) : null;
const recientes = [...mios].sort((a, b) => String(b.fecha_publicacion || '').localeCompare(String(a.fecha_publicacion || '')));
const producto = mios.find((x) => x.id === (recojoRetomado?.producto_id || process.env.PRODUCTO)) || recientes.find((x) => x.estado === 'en_transito' && !conRecojo.has(x.id));
console.log('   producto:', producto?.id, producto?.nombre, producto?.stock, 'kg; qa-repartidor:', vehiculoAntes.tipo_vehiculo, vehiculoAntes.capacidad_maxima_kg, 'kg');
if (!producto) { console.log('No hay un producto en tránsito sin recojo.'); process.exit(1); }

const b = await chromium.launch({ channel: 'msedge', headless: true });
const nuevo = async () => { const c = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: 'es-PE', timezoneId: 'America/Lima' }); const p = await c.newPage(); return { c, p, errores: vigilarErrores(p) }; };
const toast = (p, t) => p.locator('.toast', { hasText: t }).first().waitFor({ timeout: 20000 });

// ---- 1. El vehículo del repartidor (PATCH)
const R = await nuevo();
await entrarPorPantalla(R.p, env.QA_REPARTIDOR_EMAIL, env.QA_REPARTIDOR_PASSWORD, '/repartidor');
async function cambiarVehiculo(tipo, kg) {
  await R.p.goto(`${BASE}/repartidor/vehiculo`, { waitUntil: 'domcontentloaded' });
  await R.p.locator('.btn-editar-vehiculo').click();
  await R.p.locator('#form-vehiculo select[name="tipo_vehiculo"]').selectOption(tipo);
  await R.p.locator('#form-vehiculo input[name="capacidad_maxima_kg"]').fill(String(kg));
  await R.p.locator('#form-vehiculo button[type=submit]').click();
  await toast(R.p, 'Vehículo actualizado');
  await R.p.waitForTimeout(1000);
}
if (!RECOJO) {
await R.p.goto(`${BASE}/repartidor/vehiculo`, { waitUntil: 'domcontentloaded' });
await R.p.locator('.btn-editar-vehiculo').click();
await R.p.locator('#form-vehiculo input[name="capacidad_maxima_kg"]').fill('0');
await R.p.locator('#form-vehiculo button[type=submit]').click();
ok('valida la capacidad (1 a 40 000 kg)', await contiene(R.p.locator('#form-vehiculo .form-entrega-mensaje'), 'de 1 a 40 000 kg'));
await cambiarVehiculo('camioneta', 1000);
ok('vehículo cambiado a camioneta de 1000 kg', (await repartidor.api('/transporte/repartidores/me')).capacidad_maxima_kg == 1000 && await contiene(R.p.locator('.vehiculo-panel'), 'Camioneta'));

// ---- 2. El agricultor pide el recojo (React)
}
const A = await nuevo();
await A.c.grantPermissions(['geolocation']);
await entrarPorPantalla(A.p, env.QA_PRODUCTOR_EMAIL, env.QA_PRODUCTOR_PASSWORD, '/productor');
await A.p.goto(`${BASE}/productor/productos`, { waitUntil: 'domcontentloaded' });
const alm = A.p.locator(`.alm-card[data-producto-id="${producto.id}"]`);
await alm.waitFor({ timeout: 30000 });
let recojo = recojoRetomado;
if (!RECOJO) {
await alm.locator('input[value="repartidor"]').check();
await alm.locator('.alm-cotizacion-total').waitFor({ timeout: 20000 });
console.log('   cotización:', await texto(alm.locator('.alm-cotizacion-total')));
await alm.locator('input[name="referencia"]').fill('Prueba QA Fase 6: pasando el puente');
await alm.locator('input[name="telefono"]').fill('987654321');
await alm.locator('.btn-pedir-recojo').click();
await toast(A.p, 'Recojo pedido');
ok('el aviso dice que se le ofreció a un repartidor', await contiene(A.p.locator('.toast', { hasText: 'Recojo pedido' }).first(), 'ya se lo ofrecimos a un repartidor'));
recojo = (await agricultor.api('/transporte/recojos/mios')).find((r) => r.producto_id === producto.id && r.estado !== 'cancelado');
const propuestos = await repartidor.api('/transporte/recojos/propuestas');
ok('se le propone a qa-repartidor', propuestos.some((r) => r.id === recojo?.id), { recojo: recojo?.estado });
console.log('   DATO: recojo', recojo?.id, recojo?.estado, recojo?.peso_kg, 'kg, costo', recojo?.costo);
}

// ---- 3. El repartidor lo lleva (React)
await R.p.goto(`${BASE}/repartidor/recojos`, { waitUntil: 'domcontentloaded' });
const tarjeta = R.p.locator(`.recojo-card[data-recojo-id="${recojo.id}"]`);
await tarjeta.waitFor({ timeout: 20000 });
if (!RECOJO) {
ok('la propuesta muestra distancia, peso y lo que gana', (await contiene(tarjeta, 'Del almacén')) && (await contiene(tarjeta, 'Peso')) && (await contiene(tarjeta, 'Prueba QA Fase 6')));
await tarjeta.locator('.btn-recojo-accion[data-accion="aceptar"]').click();
await toast(R.p, 'Recojo aceptado: llama al agricultor antes de ir 🚚');
}
await tarjeta.locator('.btn-recojo-accion[data-accion="recogido"]').waitFor({ timeout: 15000 });
ok('aceptado: los pasos y "Llamar al agricultor"', (await contiene(tarjeta, 'Ve a la chacra y recoge la cosecha')) && (await tarjeta.locator('a[href^="tel:"]').count()) === 1);
await tarjeta.locator('.btn-recojo-accion[data-accion="recogido"]').click();
await toast(R.p, 'Listo: ahora llévala al almacén.');
await tarjeta.locator('.btn-recojo-accion[data-accion="en-almacen"]').waitFor({ timeout: 15000 });
ok('recogido: "Llévala al almacén"', await contiene(tarjeta, 'Llévala al almacén'));
await tarjeta.locator('.btn-recojo-accion[data-accion="en-almacen"]').click();
await toast(R.p, 'Gracias: lo que ganas se registra cuando el almacén la pese.');
await R.p.waitForTimeout(1500);
const estadoRecojo = (await agricultor.api('/transporte/recojos/mios')).find((r) => r.id === recojo.id)?.estado;
ok('en el almacén', estadoRecojo === 'en_almacen', estadoRecojo);

// El agricultor lo ve.
await A.p.reload({ waitUntil: 'domcontentloaded' });
await alm.waitFor({ timeout: 30000 });
ok('el agricultor ve "En el almacén, por pesar"', await contiene(alm.locator('.alm-pasos li.actual'), 'En el almacén, por pesar'));

// ---- 4. El admin pesa y confirma la llegada (React, /admin/transito)
const V = await nuevo();
await V.c.addInitScript((s) => localStorage.setItem('agro_sesion', s), sesionDelFrontend(admin, { nombre: 'Admin', email: env.QA_ADMIN_EMAIL }));
await V.p.goto(`${BASE}/admin/transito`, { waitUntil: 'domcontentloaded' });
const tarjetaAdmin = V.p.locator(`.pedido-card[data-producto-id="${producto.id}"]`);
await tarjetaAdmin.waitFor({ timeout: 30000 });
ok('el admin ve que la dejaron en el almacén', await contiene(tarjetaAdmin.locator('.alm-admin-llega'), 'la dejó en el almacén: pésala y confirma la llegada'));
const formLlegada = tarjetaAdmin.locator('.form-llegada');
const declarada = Number(await formLlegada.getAttribute('data-declarada'));
await formLlegada.locator('input[name="cantidad"]').fill(String(declarada + 1));
ok('no deja confirmar más de lo declarado', (await contiene(formLlegada.locator('.form-llegada-ayuda'), 'No puede llegar más de lo declarado')) && await formLlegada.locator('button[type=submit]').isDisabled());
await formLlegada.locator('input[name="cantidad"]').fill(String(declarada));
ok('con lo declarado: "Llegó todo lo declarado."', await contiene(formLlegada.locator('.form-llegada-ayuda'), 'Llegó todo lo declarado.'));
await formLlegada.locator('input[name="observaciones"]').fill('Prueba QA Fase 7: pesada en el almacén');
await formLlegada.locator('button[type=submit]').click();
await toast(V.p, 'Llegada confirmada');
await tarjetaAdmin.waitFor({ state: 'detached', timeout: 15000 });
ok('el admin confirma la llegada (React) y la tarjeta sale de la lista', true);
await V.p.waitForTimeout(2000);
const publico = await (await fetch(`${API}/productos/productos/${producto.id}`)).json();
ok('el producto queda a la venta (disponible)', publico.estado === 'disponible', publico.estado);
const recojoFinal = (await agricultor.api('/transporte/recojos/mios')).find((r) => r.id === recojo.id);
console.log('   DATO: recojo', recojo.id, recojoFinal?.estado, '· producto', producto.id, publico.estado, publico.stock, 'kg');

// ---- 5. El repartidor vuelve a su vehículo
await cambiarVehiculo(vehiculoAntes.tipo_vehiculo, Number(vehiculoAntes.capacidad_maxima_kg));
const restaurado = await repartidor.api('/transporte/repartidores/me');
ok('qa-repartidor vuelve a su vehículo', restaurado.tipo_vehiculo === vehiculoAntes.tipo_vehiculo && Number(restaurado.capacidad_maxima_kg) === Number(vehiculoAntes.capacidad_maxima_kg), { tipo: restaurado.tipo_vehiculo, kg: restaurado.capacidad_maxima_kg });

ok('sin errores de página (React)', !R.errores.length && !A.errores.length && !V.errores.length, [...R.errores, ...A.errores, ...V.errores]);
cerrar();
await b.close();
