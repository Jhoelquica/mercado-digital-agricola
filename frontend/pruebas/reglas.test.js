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
