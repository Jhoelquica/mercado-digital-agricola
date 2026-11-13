// La geocerca por contorno del lado del navegador (src/lib/geocerca.ts): la misma regla que el servidor para avisarle al
// agricultor, y la lectura de la frase sellada para la página del QR.   npm test
import type { Polygon } from 'geojson';
import { expect, test } from 'vitest';
import { distanciaAlBordeM, etiquetaDeGeocercaDelAlmacen, evaluarGeocerca, leerFraseDeGeocerca, textoDeGeocerca, textoPublicoDeGeocerca } from './geocerca';

const LAT = -13.16;
const LNG = -74.22;
const METROS_POR_GRADO = 111_320;
const alEste = (m: number) => LNG + m / (METROS_POR_GRADO * Math.cos((LAT * Math.PI) / 180));
// Un cuadrado de 100 m de lado centrado en (LAT, LNG): el borde queda a 50 m hacia cualquier lado.
const d = 50 / METROS_POR_GRADO;
const CUADRADO: Polygon = {
  type: 'Polygon',
  coordinates: [[[alEste(-50), LAT - d], [alEste(50), LAT - d], [alEste(50), LAT + d], [alEste(-50), LAT + d], [alEste(-50), LAT - d]]],
};
const HUELLA = 'c0'.repeat(32);

test('Geocerca: metros hasta el borde del contorno (0 si cae dentro)', () => {
  expect(distanciaAlBordeM(CUADRADO, LAT, LNG)).toBe(0);
  expect(distanciaAlBordeM(CUADRADO, LAT, alEste(49))).toBe(0);
  expect(distanciaAlBordeM(CUADRADO, LAT, alEste(80))).toBeCloseTo(30, 0);
  expect(distanciaAlBordeM(CUADRADO, LAT, alEste(250))).toBeCloseTo(200, 0);
  // en diagonal, la esquina es lo más cercano: 30 m al este y 40 m al norte de ella son 50 m
  expect(distanciaAlBordeM(CUADRADO, LAT + 90 / METROS_POR_GRADO, alEste(80))).toBeCloseTo(50, 0);
});

test('Geocerca: los seis resultados, en el mismo orden que el servidor', () => {
  const en = (m: number, precision?: number) => ({ lat: LAT, lng: alEste(m), precision });
  expect(evaluarGeocerca(CUADRADO, en(0, 12))).toEqual({ metodo: 'poligono', resultado: 'dentro', distanciaBordeM: 0 });
  expect(evaluarGeocerca(CUADRADO, en(0, 100)).resultado).toBe('dentro');            // 100 m todavía alcanza
  expect(evaluarGeocerca(CUADRADO, en(0)).resultado).toBe('dentro');                 // sin precisión conocida
  expect(evaluarGeocerca(CUADRADO, en(80, 12))).toEqual({ metodo: 'poligono', resultado: 'cerca', distanciaBordeM: 30 });
  expect(evaluarGeocerca(CUADRADO, en(250, 12))).toEqual({ metodo: 'poligono', resultado: 'fuera', distanciaBordeM: 200 });
  expect(evaluarGeocerca(CUADRADO, en(0, 150)).resultado).toBe('precision_baja');
  expect(evaluarGeocerca(CUADRADO, en(250, 150)).resultado).toBe('precision_baja');  // la precisión se mira antes que la posición
  expect(evaluarGeocerca(CUADRADO, null)).toEqual({ metodo: 'poligono', resultado: 'sin_gps', distanciaBordeM: null });
  // sin contorno: el radio de siempre, tenga la precisión que tenga
  expect(evaluarGeocerca(null, en(300, 150))).toEqual({ metodo: 'radio', resultado: 'radio', distanciaBordeM: null });
  expect(evaluarGeocerca(null, null)).toEqual({ metodo: 'radio', resultado: 'sin_gps', distanciaBordeM: null });
});

// Las frases tal como las sella productores/contornos.py (frase_de_geocerca); las mismas de sus pruebas.
const SELLADAS = {
  dentro: `Geocerca siembra: metodo=poligono; resultado=dentro; distancia_borde_m=0; precision_m=12; contorno_sha256=${HUELLA}.`,
  cerca: `Geocerca cosecha: metodo=poligono; resultado=cerca; distancia_borde_m=30; precision_m=20; contorno_sha256=${HUELLA}.`,
  fuera: `Geocerca siembra: metodo=poligono; resultado=fuera; distancia_borde_m=200; precision_m=15; contorno_sha256=${HUELLA}.`,
  precision_baja: `Geocerca siembra: metodo=poligono; resultado=precision_baja; distancia_borde_m=0; precision_m=150; contorno_sha256=${HUELLA}.`,
  sin_gps: `Geocerca siembra: metodo=poligono; resultado=sin_gps; contorno_sha256=${HUELLA}.`,
  radio: 'Geocerca cosecha: metodo=radio; resultado=radio; precision_m=12.',
};

test('Geocerca: la frase sellada se lee campo por campo', () => {
  expect(leerFraseDeGeocerca(SELLADAS.dentro)).toEqual({ momento: 'siembra', metodo: 'poligono', resultado: 'dentro', distanciaBordeM: 0, precisionM: 12, contornoSha256: HUELLA });
  expect(leerFraseDeGeocerca(SELLADAS.cerca)).toEqual({ momento: 'cosecha', metodo: 'poligono', resultado: 'cerca', distanciaBordeM: 30, precisionM: 20, contornoSha256: HUELLA });
  expect(leerFraseDeGeocerca(SELLADAS.sin_gps)).toEqual({ momento: 'siembra', metodo: 'poligono', resultado: 'sin_gps', distanciaBordeM: null, precisionM: null, contornoSha256: HUELLA });
  expect(leerFraseDeGeocerca(SELLADAS.radio)).toEqual({ momento: 'cosecha', metodo: 'radio', resultado: 'radio', distanciaBordeM: null, precisionM: 12, contornoSha256: null });
  // las frases de siempre no son de geocerca
  expect(leerFraseDeGeocerca('Al registrar la siembra: a 0,03 km de la chacra (dentro del radio de 1,5 km).')).toBeNull();
  expect(leerFraseDeGeocerca('Foto de la cosecha (SHA-256 abc).')).toBeNull();
  expect(leerFraseDeGeocerca('Geocerca siembra: sin formato.')).toBeNull();
});

test('Geocerca: el QR dice "confirmada" solo con dentro o cerca; lo demás, la frase tal como está en la cadena', () => {
  for (const resultado of ['dentro', 'cerca'] as const) {
    const publico = textoPublicoDeGeocerca(SELLADAS[resultado]);
    expect(publico).toMatchObject({ confirmada: true, texto: 'Ubicación confirmada en la chacra', resultado });
  }
  for (const resultado of ['fuera', 'precision_baja', 'sin_gps', 'radio'] as const) {
    const publico = textoPublicoDeGeocerca(SELLADAS[resultado]);
    expect(publico).toMatchObject({ confirmada: false, texto: SELLADAS[resultado], resultado });
    expect(publico?.texto).not.toMatch(/confirmada/i);
  }
  expect(textoPublicoDeGeocerca('Al completar la cosecha: sin ubicación GPS (advertencia).')).toBeNull();
  // un "dentro" que no venga del contorno no se da por confirmado
  expect(textoPublicoDeGeocerca('Geocerca siembra: metodo=radio; resultado=dentro.')?.confirmada).toBe(false);
});

test('Geocerca: lo que lee el agricultor y la etiqueta del almacén', () => {
  expect(textoDeGeocerca('dentro', {})).toMatchObject({ texto: 'dentro de tu chacra', advertencia: false });
  expect(textoDeGeocerca('cerca', { distanciaBordeM: 30 })).toMatchObject({ texto: 'a 30 m del borde de tu chacra (cuenta como dentro)', advertencia: false });
  expect(textoDeGeocerca('fuera', { distanciaBordeM: 200 })).toMatchObject({ advertencia: true });
  expect(textoDeGeocerca('precision_baja', { precisionM: 150 })).toMatchObject({ texto: 'ubicación poco precisa (±150 m)', advertencia: true });
  expect(textoDeGeocerca('radio', {})).toBeNull();
  expect(textoDeGeocerca(null, {})).toBeNull();

  expect(etiquetaDeGeocercaDelAlmacen({ resultado: 'dentro', distancia_borde_m: 0 }, 1.5)).toMatchObject({ resultado: 'dentro', texto: 'Cosecha registrada dentro de la chacra', advertencia: false });
  expect(etiquetaDeGeocercaDelAlmacen({ resultado: 'fuera', distancia_borde_m: 200 }, 1.5)).toMatchObject({ advertencia: true, icono: 'alert-triangle' });
  expect(etiquetaDeGeocercaDelAlmacen({ resultado: 'radio', distancia_km: 0.03 }, 1.5)?.texto).toBe('Chacra sin contorno: cosecha registrada a 30 m de su punto');
  expect(etiquetaDeGeocercaDelAlmacen({ resultado: null, distancia_km: 3.1 }, 1.5)).toMatchObject({ resultado: 'radio', advertencia: true });
  expect(etiquetaDeGeocercaDelAlmacen({ resultado: null, distancia_km: null }, 1.5)).toBeNull();
});
