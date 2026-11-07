// Pruebas de las reglas puras de las pantallas (src/lib/reglas.ts). Son las de pruebas/reglas.test.js del frontend anterior, caso por
// caso; la del quechua está en src/idioma/textos.test.ts.   npm test
import { expect, test } from 'vitest';
import {
  atajosDeCosecha, cantidadDeReserva, claveVapidABytes, datosTransporteContratado, descripcionSugerida, estadoAvisosCelular, fechaHoraLegible,
  fraseResumenSiembra, gastosYaRegistrados, manoDeObra, mismaClaveVapid, opcionesPermitidas, precioSugerido, siguienteEstadoEnvio, unidadDeCosecha,
  unidadesParaAgregar,
} from './reglas';

const OPCIONES_SIEMBRA = [{ valor: 'kg' }, { valor: 'unidad' }, { valor: 'litro' }];
const TODAS = ['kg', 'unidad', 'saco', 'arroba', 'litro'];

// base64url de unos bytes, sin Buffer (estas pruebas corren con los tipos del navegador).
const aBase64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

test('H1: la siembra de papa solo ofrece kilos; sin alimento reconocido, todas', () => {
  expect(opcionesPermitidas(OPCIONES_SIEMBRA, ['kg']).map((o) => o.valor)).toEqual(['kg']);
  expect(opcionesPermitidas(OPCIONES_SIEMBRA, ['kg', 'unidad']).map((o) => o.valor)).toEqual(['kg', 'unidad']);
  expect(opcionesPermitidas(OPCIONES_SIEMBRA, null)).toHaveLength(3);
  expect(opcionesPermitidas(OPCIONES_SIEMBRA, [])).toHaveLength(3);
});

test('H1: a una papa solo se le pueden agregar saco o arroba', () => {
  expect(unidadesParaAgregar(TODAS, ['kg', 'saco', 'arroba'], 'kg')).toEqual(['saco', 'arroba']);
  expect(unidadesParaAgregar(TODAS, ['kg', 'saco', 'arroba'], 'kg', ['saco'])).toEqual(['arroba']);
  expect(unidadesParaAgregar(TODAS, ['unidad'], 'unidad')).toEqual([]);
  expect(unidadesParaAgregar(TODAS, null, 'kg', ['saco'])).toEqual(['unidad', 'arroba', 'litro']);
});

test('H1: al cambiar de cultivo, la unidad pasa a una permitida', () => {
  expect(unidadDeCosecha('unidad', 'kg', ['kg'])).toBe('kg'); // papa: de "unidad" a kg
  expect(unidadDeCosecha('kg', 'unidad', ['unidad'])).toBe('unidad'); // huevos
  expect(unidadDeCosecha('unidad', 'kg', ['kg', 'unidad'])).toBe('unidad'); // maíz: respeta lo elegido
  expect(unidadDeCosecha('litro', 'kg', null)).toBe('litro'); // sin plantilla: lo que eligió
});

test('H3: al cosechar se muestra lo ya gastado y lo que ya se anotó en la cosecha', () => {
  const etapas = [
    { clave: 'siembra', nombre: 'Siembra', momento: 'inicio', total: 300.5 },
    { clave: 'aporque', nombre: 'Aporque', momento: 'cuidado', total: 0 },
    { clave: 'fumigacion', nombre: 'Fumigación', momento: 'cuidado', total: 80.25 },
    { clave: 'cosecha', nombre: 'Cosecha', momento: 'final', total: 50 },
  ];
  const ya = gastosYaRegistrados(etapas);
  expect(ya.total).toBe(430.75);
  expect(ya.etapas.map((e) => e.nombre)).toEqual(['Siembra', 'Fumigación', 'Cosecha']);
  expect(ya.enElFinal.map((e) => [e.clave, e.total])).toEqual([['cosecha', 50]]);
  expect(gastosYaRegistrados(null)).toEqual({ total: 0, etapas: [], enElFinal: [] });
});

test('H5: "¿qué sembrar?" empieza con una frase con la respuesta', () => {
  const papa = { nombre: 'Papa', puntaje: 78, riesgo: 'bajo', margen_kg: 0.8 };
  expect(fraseResumenSiembra(papa)).toBe(
    'Lo que más te conviene sembrar ahora es Papa (78 de 100, conviene mucho): ganas S/ 0.80 por kg y hay poco riesgo de que sobre.');
  expect(fraseResumenSiembra({ nombre: 'Haba', puntaje: 40, riesgo: 'alto', margen_kg: null })).toBe(
    'Lo que más te conviene sembrar ahora es Haba (40 de 100, puede ser): todavía falta saber cuánto cuesta producirlo y hay mucho riesgo de que sobre.');
  expect(fraseResumenSiembra({ nombre: 'Oca', puntaje: 20, riesgo: 'medio', margen_kg: -0.5 })).toBe(
    'Ahora ningún cultivo conviene mucho; el mejor es Oca (20 de 100, conviene poco): pierdes S/ 0.50 por kg y hay algo de riesgo de que sobre.');
  expect(fraseResumenSiembra(undefined)).toBeNull();
});

test('H7: al reservar, la cantidad es opcional y si se escribe tiene que ser mayor que 0', () => {
  expect(cantidadDeReserva('', 'saco')).toEqual({ datos: null, error: null });
  expect(cantidadDeReserva('2', 'saco')).toEqual({ datos: { cantidad: 2, unidad: 'saco' }, error: null });
  expect(cantidadDeReserva('2,5', 'kg')).toEqual({ datos: { cantidad: 2.5, unidad: 'kg' }, error: null });
  expect(cantidadDeReserva('0', 'kg').error).toBeTruthy();
  expect(cantidadDeReserva('-3', 'kg').error).toBeTruthy();
});

test('H6: la clave pública VAPID se pasa a bytes y se reconoce si cambió', () => {
  const clave = aBase64url(Uint8Array.from({ length: 65 }, (_, i) => (i * 7 + 4) % 256));
  const bytes = claveVapidABytes(clave);
  expect(bytes).toHaveLength(65);
  expect(bytes[0]).toBe(4);
  expect(aBase64url(bytes)).toBe(clave);
  expect(mismaClaveVapid(bytes.buffer, clave)).toBe(true);
  const otra = aBase64url(Uint8Array.from({ length: 65 }, () => 9));
  expect(mismaClaveVapid(bytes.buffer, otra)).toBe(false);
  expect(mismaClaveVapid(null, clave)).toBe(false);
});

test('H6: la tarjeta de avisos dice qué hacer en cada caso', () => {
  const base = { configurado: true, soportado: true, iphoneSinInstalar: false, permiso: 'default' as NotificationPermission, suscrito: false };
  expect(estadoAvisosCelular({ ...base, configurado: false })).toBe('apagado-servidor');
  expect(estadoAvisosCelular({ ...base, soportado: false, iphoneSinInstalar: true })).toBe('instalar-iphone');
  expect(estadoAvisosCelular({ ...base, soportado: false })).toBe('no-soportado');
  expect(estadoAvisosCelular({ ...base, permiso: 'denied' })).toBe('bloqueado');
  expect(estadoAvisosCelular(base)).toBe('inactivo');
  expect(estadoAvisosCelular({ ...base, permiso: 'granted', suscrito: true })).toBe('activo');
  expect(estadoAvisosCelular({ ...base, permiso: 'default', suscrito: true })).toBe('inactivo');
});

test('H8: el transporte contratado se valida como en el servidor y dice qué campo corregir', () => {
  const bueno = { nombre: 'Juan Quispe', telefono: '987 654 321', placa: 'abc-123', capacidadKg: '4000', capacidadSacos: '80', fecha: '2026-10-15T08:30', flete: '350' };
  expect(datosTransporteContratado(bueno, { pesoKg: 1600, sacos: 32 }).datos).toEqual({
    nombre: 'Juan Quispe', telefono: '987654321', placa: 'ABC-123', capacidad_kg: 4000, capacidad_sacos: 80, fecha_programada: '2026-10-15T08:30', flete: 350,
  });
  expect(datosTransporteContratado({ ...bueno, capacidadSacos: '' }).datos?.capacidad_sacos).toBeNull();
  const campo = (cambios: Partial<typeof bueno>, contexto = { pesoKg: 1600, sacos: 32 }) => datosTransporteContratado({ ...bueno, ...cambios }, contexto).campo;
  expect(campo({ telefono: '12345' })).toBe('telefono');
  expect(campo({ placa: '??' })).toBe('placa');
  expect(campo({ capacidadKg: '500' })).toBe('capacidadKg');
  expect(campo({ capacidadSacos: '20' })).toBe('capacidadSacos');
  expect(campo({ fecha: '' })).toBe('fecha');
  expect(campo({ flete: '0' })).toBe('flete');
});

test('H8: el día y la hora del recojo se leen sin cambiar de zona horaria', () => {
  expect(fechaHoraLegible('2026-10-15T08:30:00')).toBe('jueves 15/10 a las 8:30');
  expect(fechaHoraLegible('2026-10-18T17:05')).toBe('domingo 18/10 a las 17:05');
  expect(fechaHoraLegible(null)).toBe('');
});

test('Mano de obra en peones × días × pago por día (validación 2026-10-14, A04)', () => {
  const r = manoDeObra({ peones: '3', dias: '2', pago: '50' });
  expect(r.datos).toEqual({ peones: 3, dias: 2, precio_unitario: 50, unidad: 'jornal' });
  expect(r.total).toBe(300);
  expect(manoDeObra({ peones: '', dias: '', pago: '' })).toEqual({ datos: null, total: 0, error: null, campo: null });
  expect(manoDeObra({ peones: '2.5', dias: '1', pago: '50' }).campo).toBe('peones');
  expect(manoDeObra({ peones: '2', dias: '0', pago: '50' }).campo).toBe('dias');
  expect(manoDeObra({ peones: '2', dias: '0,001', pago: '50' }).campo).toBe('dias'); // con 2 decimales es 0
  expect(manoDeObra({ peones: '2', dias: '', pago: '' }).campo).toBe('dias');
  expect(manoDeObra({ peones: '2', dias: '3', pago: '' }).campo).toBe('pago');
  expect(manoDeObra({ peones: '4', dias: '1', pago: '45,5' }).total).toBe(182);
});

test('Los días de la mano de obra aceptan medio día (validación 2026-10-14, A04)', () => {
  const medio = manoDeObra({ peones: '3', dias: '0,5', pago: '60' });
  expect(medio.datos).toEqual({ peones: 3, dias: 0.5, precio_unitario: 60, unidad: 'jornal' });
  expect(medio.total).toBe(90);
  expect(manoDeObra({ peones: '2', dias: '1.5', pago: '50' }).total).toBe(150);
  expect(manoDeObra({ peones: '2', dias: '1,256', pago: '10' }).datos?.dias).toBe(1.26); // como el servidor
});

test('Los atajos de la cosecha salen del rango del cultivo, no de meses fijos (validación 2026-10-14, A05)', () => {
  const kiwicha = { fecha_desde: '2027-03-03', fecha_sugerida: '2027-03-23', fecha_hasta: '2027-04-12' };
  expect(atajosDeCosecha(kiwicha).map((a) => [a.etiqueta, a.fecha])).toEqual([
    ['Lo más pronto', '2027-03-03'], ['Lo más probable', '2027-03-23'], ['Lo más tarde', '2027-04-12']]);
  expect(atajosDeCosecha({ ...kiwicha, fecha_desde: '2027-03-23' })).toHaveLength(2); // sin repetir la fecha
  expect(atajosDeCosecha(null)).toEqual([]);
});

test('H6: el precio sugerido sale del precio de referencia por kg, en la unidad del producto', () => {
  expect(precioSugerido({ precioReferenciaKg: 3.456, unidad: 'kg' })).toBe(3.46);
  expect(precioSugerido({ precioReferenciaKg: 4, unidad: 'unidad', equivalenciaKg: 0.25 })).toBe(1);
  expect(precioSugerido({ precioReferenciaKg: 4, unidad: 'litro' })).toBeNull(); // sin equivalencia, nada
  expect(precioSugerido({ precioReferenciaKg: null, unidad: 'kg' })).toBeNull();
  expect(precioSugerido({ precioReferenciaKg: 0, unidad: 'kg' })).toBeNull();
});

test('H6: la descripción sugerida usa los datos de la siembra y la cosecha, y solo los que hay', () => {
  expect(descripcionSugerida({ cultivo: 'Kiwicha', chacra: 'Chacra del cerro', fechaSiembra: '2026-05-07T00:00:00', fechaCosecha: '2026-10-14T00:00:00', cantidad: '1000.00', unidad: 'kg' }))
    .toBe('Kiwicha de la chacra «Chacra del cerro». Se sembró el 7 de mayo de 2026 y se cosechó el 14 de octubre de 2026. De esta cosecha salieron 1000 kg.');
  expect(descripcionSugerida({ cultivo: 'Choclo', fechaCosecha: '2026-09-01', cantidad: 1, unidad: 'unidad' }))
    .toBe('Choclo. Se cosechó el 1 de setiembre de 2026. De esta cosecha salieron 1 unidad.');
  expect(descripcionSugerida({ cultivo: 'Miel', cantidad: 12.5, unidad: 'litro' })).toBe('Miel. De esta cosecha salieron 12,5 litros.');
  expect(descripcionSugerida({})).toBeNull();
});

test('H11: el envío propone el estado que sigue (Asignado → En camino → Entregado)', () => {
  const local = ['asignado', 'en_camino', 'entregado'];
  expect(siguienteEstadoEnvio('asignado', local)).toBe('en_camino');
  expect(siguienteEstadoEnvio('en_camino', local)).toBe('entregado');
  expect(siguienteEstadoEnvio('entregado', local)).toBeNull();
  expect(siguienteEstadoEnvio('en_camino', ['asignado', 'en_camino', 'en_agencia'])).toBe('en_agencia');
  expect(siguienteEstadoEnvio('en_destino', local)).toBeNull(); // fuera del orden: sin botón
});
