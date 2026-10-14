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
