// Pruebas de las reglas puras del frontend (js/reglas.js). Uso: node --test "frontend/pruebas/*.test.js"
const { test } = require('node:test');
const assert = require('node:assert/strict');
const Reglas = require('../js/reglas.js');

const OPCIONES_SIEMBRA = [{ valor: 'kg' }, { valor: 'unidad' }, { valor: 'litro' }];
const TODAS = ['kg', 'unidad', 'saco', 'arroba', 'litro'];

test('H1: la siembra de papa solo ofrece kilos; sin alimento reconocido, todas', () => {
  assert.deepEqual(Reglas.opcionesPermitidas(OPCIONES_SIEMBRA, ['kg']).map((o) => o.valor), ['kg']);
  assert.deepEqual(Reglas.opcionesPermitidas(OPCIONES_SIEMBRA, ['kg', 'unidad']).map((o) => o.valor), ['kg', 'unidad']);
  assert.equal(Reglas.opcionesPermitidas(OPCIONES_SIEMBRA, null).length, 3);
  assert.equal(Reglas.opcionesPermitidas(OPCIONES_SIEMBRA, []).length, 3);
});

test('H1: a una papa solo se le pueden agregar saco o arroba', () => {
  assert.deepEqual(Reglas.unidadesParaAgregar(TODAS, ['kg', 'saco', 'arroba'], 'kg'), ['saco', 'arroba']);
  assert.deepEqual(Reglas.unidadesParaAgregar(TODAS, ['kg', 'saco', 'arroba'], 'kg', ['saco']), ['arroba']);
  assert.deepEqual(Reglas.unidadesParaAgregar(TODAS, ['unidad'], 'unidad'), []);
  assert.deepEqual(Reglas.unidadesParaAgregar(TODAS, null, 'kg', ['saco']), ['unidad', 'arroba', 'litro']);
});

test('H1: al cambiar de cultivo, la unidad pasa a una permitida', () => {
  assert.equal(Reglas.unidadDeCosecha('unidad', 'kg', ['kg']), 'kg');          // papa: de "unidad" a kg
  assert.equal(Reglas.unidadDeCosecha('kg', 'unidad', ['unidad']), 'unidad'); // huevos
  assert.equal(Reglas.unidadDeCosecha('unidad', 'kg', ['kg', 'unidad']), 'unidad'); // maíz: respeta lo elegido
  assert.equal(Reglas.unidadDeCosecha('litro', 'kg', null), 'litro');         // sin plantilla: lo que eligió
});

test('H3: al cosechar se muestra lo ya gastado y lo que ya se anotó en la cosecha', () => {
  const etapas = [
    { clave: 'siembra', nombre: 'Siembra', momento: 'inicio', total: 300.5 },
    { clave: 'aporque', nombre: 'Aporque', momento: 'cuidado', total: 0 },
    { clave: 'fumigacion', nombre: 'Fumigación', momento: 'cuidado', total: 80.25 },
    { clave: 'cosecha', nombre: 'Cosecha', momento: 'final', total: 50 },
  ];
  const ya = Reglas.gastosYaRegistrados(etapas);
  assert.equal(ya.total, 430.75);
  assert.deepEqual(ya.etapas.map((e) => e.nombre), ['Siembra', 'Fumigación', 'Cosecha']);
  assert.deepEqual(ya.enElFinal.map((e) => [e.clave, e.total]), [['cosecha', 50]]);
  assert.deepEqual(Reglas.gastosYaRegistrados(null), { total: 0, etapas: [], enElFinal: [] });
});

test('H5: "¿qué sembrar?" empieza con una frase con la respuesta', () => {
  const papa = { nombre: 'Papa', puntaje: 78, riesgo: 'bajo', margen_kg: 0.8 };
  assert.equal(Reglas.fraseResumenSiembra(papa),
    'Lo que más te conviene sembrar ahora es Papa (78 de 100, conviene mucho): ganas S/ 0.80 por kg y hay poco riesgo de que sobre.');
  assert.equal(Reglas.fraseResumenSiembra({ nombre: 'Haba', puntaje: 40, riesgo: 'alto', margen_kg: null }),
    'Lo que más te conviene sembrar ahora es Haba (40 de 100, puede ser): todavía falta saber cuánto cuesta producirlo y hay mucho riesgo de que sobre.');
  assert.equal(Reglas.fraseResumenSiembra({ nombre: 'Oca', puntaje: 20, riesgo: 'medio', margen_kg: -0.5 }),
    'Ahora ningún cultivo conviene mucho; el mejor es Oca (20 de 100, conviene poco): pierdes S/ 0.50 por kg y hay algo de riesgo de que sobre.');
  assert.equal(Reglas.fraseResumenSiembra(undefined), null);
});

test('H7: al reservar, la cantidad es opcional y si se escribe tiene que ser mayor que 0', () => {
  assert.deepEqual(Reglas.cantidadDeReserva('', 'saco'), { datos: null, error: null });
  assert.deepEqual(Reglas.cantidadDeReserva('2', 'saco'), { datos: { cantidad: 2, unidad: 'saco' }, error: null });
  assert.deepEqual(Reglas.cantidadDeReserva('2,5', 'kg'), { datos: { cantidad: 2.5, unidad: 'kg' }, error: null });
  assert.ok(Reglas.cantidadDeReserva('0', 'kg').error);
  assert.ok(Reglas.cantidadDeReserva('-3', 'kg').error);
});

test('H6: la clave pública VAPID se pasa a bytes y se reconoce si cambió', () => {
  const clave = Buffer.from(Uint8Array.from({ length: 65 }, (_, i) => (i * 7 + 4) % 256)).toString('base64url');
  const bytes = Reglas.claveVapidABytes(clave);
  assert.equal(bytes.length, 65);
  assert.equal(bytes[0], 4);
  assert.equal(Buffer.from(bytes).toString('base64url'), clave);
  assert.ok(Reglas.mismaClaveVapid(bytes.buffer, clave));
  const otra = Buffer.from(Uint8Array.from({ length: 65 }, () => 9)).toString('base64url');
  assert.ok(!Reglas.mismaClaveVapid(bytes.buffer, otra));
  assert.ok(!Reglas.mismaClaveVapid(null, clave));
});

test('H6: la tarjeta de avisos dice qué hacer en cada caso', () => {
  const base = { configurado: true, soportado: true, iphoneSinInstalar: false, permiso: 'default', suscrito: false };
  assert.equal(Reglas.estadoAvisosCelular({ ...base, configurado: false }), 'apagado-servidor');
  assert.equal(Reglas.estadoAvisosCelular({ ...base, soportado: false, iphoneSinInstalar: true }), 'instalar-iphone');
  assert.equal(Reglas.estadoAvisosCelular({ ...base, soportado: false }), 'no-soportado');
  assert.equal(Reglas.estadoAvisosCelular({ ...base, permiso: 'denied' }), 'bloqueado');
  assert.equal(Reglas.estadoAvisosCelular(base), 'inactivo');
  assert.equal(Reglas.estadoAvisosCelular({ ...base, permiso: 'granted', suscrito: true }), 'activo');
  assert.equal(Reglas.estadoAvisosCelular({ ...base, permiso: 'default', suscrito: true }), 'inactivo');
});

test('H8: el transporte contratado se valida como en el servidor y dice qué campo corregir', () => {
  const bueno = { nombre: 'Juan Quispe', telefono: '987 654 321', placa: 'abc-123', capacidadKg: '4000', capacidadSacos: '80',
    fecha: '2026-10-15T08:30', flete: '350' };
  assert.deepEqual(Reglas.datosTransporteContratado(bueno, { pesoKg: 1600, sacos: 32 }).datos, {
    nombre: 'Juan Quispe', telefono: '987654321', placa: 'ABC-123', capacidad_kg: 4000, capacidad_sacos: 80,
    fecha_programada: '2026-10-15T08:30', flete: 350 });
  assert.equal(Reglas.datosTransporteContratado({ ...bueno, capacidadSacos: '' }).datos.capacidad_sacos, null);
  const campo = (cambios, contexto = { pesoKg: 1600, sacos: 32 }) => Reglas.datosTransporteContratado({ ...bueno, ...cambios }, contexto).campo;
  assert.equal(campo({ telefono: '12345' }), 'telefono');
  assert.equal(campo({ placa: '??' }), 'placa');
  assert.equal(campo({ capacidadKg: '500' }), 'capacidadKg');
  assert.equal(campo({ capacidadSacos: '20' }), 'capacidadSacos');
  assert.equal(campo({ fecha: '' }), 'fecha');
  assert.equal(campo({ flete: '0' }), 'flete');
});

test('H8: el día y la hora del recojo se leen sin cambiar de zona horaria', () => {
  assert.equal(Reglas.fechaHoraLegible('2026-10-15T08:30:00'), 'jueves 15/10 a las 8:30');
  assert.equal(Reglas.fechaHoraLegible('2026-10-18T17:05'), 'domingo 18/10 a las 17:05');
  assert.equal(Reglas.fechaHoraLegible(null), '');
});
