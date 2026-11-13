// Geocerca por contorno de la chacra: dónde estaba el agricultor respecto de su chacra al sembrar y al cosechar.
// El servidor es quien decide y lo que queda sellado en la cadena es su resultado (productores/contornos.py). Acá va la
// misma regla para avisarle al agricultor antes de enviar, y la lectura de la frase sellada para mostrarla.
import type { Polygon } from 'geojson';

// Mismos valores que TOLERANCIA_PUNTO_M y PRECISION_MAXIMA_M en productores/contornos.py.
export const TOLERANCIA_BORDE_M = 50;
export const PRECISION_MAXIMA_M = 100;

export type ResultadoGeocerca = 'dentro' | 'cerca' | 'fuera' | 'precision_baja' | 'sin_gps' | 'radio';
// Los únicos resultados con los que la página pública dice "Ubicación confirmada en la chacra".
const RESULTADOS_CONFIRMADOS: readonly string[] = ['dentro', 'cerca'];

export interface PuntoGps { lat: number; lng: number; precision?: number | null }
export interface Geocerca { metodo: 'poligono' | 'radio'; resultado: ResultadoGeocerca; distanciaBordeM: number | null }

const METROS_POR_GRADO = 111_320;

// Metros desde un punto hasta el contorno; 0 si cae dentro. Se calcula en un plano local alrededor del punto: a la
// escala de una chacra la diferencia con la cuenta del servidor (shapely + haversine) es de centímetros.
export function distanciaAlBordeM(contorno: Polygon, lat: number, lng: number): number {
  const escalaX = METROS_POR_GRADO * Math.cos((lat * Math.PI) / 180);
  const vertices = (contorno.coordinates[0] ?? []).map((p) => ({ x: (Number(p[0]) - lng) * escalaX, y: (Number(p[1]) - lat) * METROS_POR_GRADO }));
  let dentro = false;
  let minima = Infinity;
  for (let i = 0; i < vertices.length; i++) {
    const a = vertices[i];
    const b = vertices[(i + 1) % vertices.length];
    if (!a || !b) continue;
    // ¿Cruza este lado el rayo que sale del punto (el origen) hacia la derecha?
    if ((a.y > 0) !== (b.y > 0) && a.x + ((0 - a.y) * (b.x - a.x)) / (b.y - a.y) > 0) dentro = !dentro;
    // Distancia del punto al lado.
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const largo2 = dx * dx + dy * dy;
    const t = largo2 === 0 ? 0 : Math.max(0, Math.min(1, -(a.x * dx + a.y * dy) / largo2));
    minima = Math.min(minima, Math.hypot(a.x + t * dx, a.y + t * dy));
  }
  return dentro || !Number.isFinite(minima) ? 0 : minima;
}

// El mismo orden que el servidor: sin_gps → radio → precision_baja → dentro / cerca / fuera.
export function evaluarGeocerca(contorno: Polygon | null | undefined, punto: PuntoGps | null | undefined): Geocerca {
  const metodo = contorno ? 'poligono' : 'radio';
  if (!punto) return { metodo, resultado: 'sin_gps', distanciaBordeM: null };
  if (!contorno) return { metodo: 'radio', resultado: 'radio', distanciaBordeM: null };
  const distancia = distanciaAlBordeM(contorno, punto.lat, punto.lng);
  const resultado: ResultadoGeocerca = punto.precision != null && punto.precision > PRECISION_MAXIMA_M ? 'precision_baja'
    : distancia === 0 ? 'dentro' : distancia <= TOLERANCIA_BORDE_M ? 'cerca' : 'fuera';
  return { metodo: 'poligono', resultado, distanciaBordeM: Math.round(distancia) };
}

// ---------------------------------------------------------------- la frase sellada en la cadena de certificación
export interface FraseGeocerca {
  momento: 'siembra' | 'cosecha';
  metodo: string;
  resultado: string;
  distanciaBordeM: number | null;
  precisionM: number | null;
  contornoSha256: string | null;
}

// "Geocerca siembra: metodo=poligono; resultado=dentro; distancia_borde_m=0; precision_m=12; contorno_sha256=<hash>."
// El formato lo escribe frase_de_geocerca en productores/contornos.py. null si la frase no es de geocerca.
export function leerFraseDeGeocerca(frase: string): FraseGeocerca | null {
  const partes = /^Geocerca (siembra|cosecha): (.+?)\.?$/.exec(frase.trim());
  if (!partes) return null;
  const campos = new Map<string, string>();
  for (const par of (partes[2] ?? '').split(';')) {
    const [clave = '', ...valor] = par.trim().split('=');
    if (clave) campos.set(clave, valor.join('='));
  }
  const metodo = campos.get('metodo');
  const resultado = campos.get('resultado');
  if (!metodo || !resultado) return null;
  const numero = (clave: string) => {
    const valor = campos.get(clave);
    return valor != null && valor !== '' && Number.isFinite(Number(valor)) ? Number(valor) : null;
  };
  return {
    momento: partes[1] === 'cosecha' ? 'cosecha' : 'siembra',
    metodo,
    resultado,
    distanciaBordeM: numero('distancia_borde_m'),
    precisionM: numero('precision_m'),
    contornoSha256: campos.get('contorno_sha256') || null,
  };
}

// Lo que la página pública (el QR y el detalle del producto) muestra por una frase de geocerca: "Ubicación confirmada
// en la chacra" solo si lo sellado es dentro o cerca. En cualquier otro caso, la frase tal como está en la cadena: nada
// que no esté confirmado se presenta como confirmado.
export function textoPublicoDeGeocerca(frase: string): { confirmada: boolean; momento: 'siembra' | 'cosecha'; resultado: string; texto: string } | null {
  const leida = leerFraseDeGeocerca(frase);
  if (!leida) return null;
  const confirmada = leida.metodo === 'poligono' && RESULTADOS_CONFIRMADOS.includes(leida.resultado);
  return { confirmada, momento: leida.momento, resultado: leida.resultado, texto: confirmada ? 'Ubicación confirmada en la chacra' : frase };
}

// ---------------------------------------------------------------- cómo se le dice a cada quien
export interface TextoGeocerca { texto: string; icono: string; advertencia: boolean }
const metros = (valor: number | null | undefined) => `${Math.round(valor ?? 0)} m`;
const kilometros = (km: number) => (km < 1 ? `${Math.max(10, Math.round((km * 1000) / 10) * 10)} m` : `${km.toFixed(1).replace('.', ',')} km`);

// Para el agricultor, en "Mis siembras": qué quedó anotado de cada momento. null con el método del radio y en los
// registros de antes: ahí se sigue diciendo la distancia al punto de la chacra.
export function textoDeGeocerca(resultado: string | null | undefined, datos: { distanciaBordeM?: number | null; precisionM?: number | null }): TextoGeocerca | null {
  switch (resultado) {
    case 'dentro': return { texto: 'dentro de tu chacra', icono: 'map-pin-check', advertencia: false };
    case 'cerca': return { texto: `a ${metros(datos.distanciaBordeM)} del borde de tu chacra (cuenta como dentro)`, icono: 'map-pin-check', advertencia: false };
    case 'fuera': return { texto: `a ${metros(datos.distanciaBordeM)} del borde de tu chacra (fuera)`, icono: 'alert-triangle', advertencia: true };
    case 'precision_baja': return { texto: `ubicación poco precisa (±${metros(datos.precisionM)})`, icono: 'alert-triangle', advertencia: true };
    case 'sin_gps': return { texto: 'sin ubicación', icono: 'map-pin-off', advertencia: true };
    default: return null;
  }
}

// Para el admin, al recibir la cosecha en el almacén: una sola etiqueta con el resultado de la geocerca de la cosecha.
// null si no hay nada que decir (un registro de antes y sin distancia).
export function etiquetaDeGeocercaDelAlmacen(
  cosecha: { resultado?: string | null; distancia_borde_m?: number | null; precision_m?: number | null; distancia_km?: number | null },
  radioKm: number,
): (TextoGeocerca & { resultado: string }) | null {
  const resultado = cosecha.resultado || 'radio';
  switch (resultado) {
    case 'dentro': return { resultado, texto: 'Cosecha registrada dentro de la chacra', icono: 'map-pin-check', advertencia: false };
    case 'cerca': return { resultado, texto: `Cosecha registrada a ${metros(cosecha.distancia_borde_m)} del borde de la chacra (dentro de la tolerancia)`, icono: 'map-pin-check', advertencia: false };
    case 'fuera': return { resultado, texto: `Cosecha registrada a ${metros(cosecha.distancia_borde_m)} del borde de la chacra: fuera de su contorno`, icono: 'alert-triangle', advertencia: true };
    case 'precision_baja': return { resultado, texto: `Cosecha con GPS poco preciso (±${metros(cosecha.precision_m)}): ubicación sin confirmar`, icono: 'alert-triangle', advertencia: true };
    case 'sin_gps': return { resultado, texto: 'Cosecha registrada sin ubicación GPS', icono: 'map-pin-off', advertencia: true };
    default: {
      // Chacra sin contorno (o un registro de antes): lo de siempre, la distancia a su punto.
      if (cosecha.distancia_km == null) return null;
      const fuera = cosecha.distancia_km > radioKm;
      return {
        resultado: 'radio',
        texto: `Chacra sin contorno: cosecha registrada a ${kilometros(cosecha.distancia_km)} de su punto${fuera ? `, fuera del radio de ${String(radioKm).replace('.', ',')} km` : ''}`,
        icono: fuera ? 'alert-triangle' : 'map-pin',
        advertencia: fuera,
      };
    }
  }
}
