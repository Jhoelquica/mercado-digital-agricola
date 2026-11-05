// La voz del asistente (Web Speech API del navegador): leer la pregunta en voz alta y responder hablando. Leer lo tiene
// casi cualquier navegador; dictar, solo Chrome y Edge (y con internet). Nada depende de la voz: todo se puede tocar.

interface ResultadoDictado { isFinal: boolean; 0: { transcript: string } }
interface EventoDictado { resultIndex: number; results: ArrayLike<ResultadoDictado> }
interface Reconocedor {
  lang: string; interimResults: boolean; maxAlternatives: number; continuous: boolean;
  onresult: ((e: EventoDictado) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; abort(): void;
}
type ClaseReconocedor = new () => Reconocedor;

// Se busca al usarlo (no al cargar): así una prueba puede simularlo, y da igual en el uso normal.
const reconocimiento = (): ClaseReconocedor | undefined => {
  const w = window as unknown as { SpeechRecognition?: ClaseReconocedor; webkitSpeechRecognition?: ClaseReconocedor };
  return w.SpeechRecognition || w.webkitSpeechRecognition;
};
const CLAVE_LECTURA = 'agro_asistente_leer';
const hayVoz = typeof window !== 'undefined' && 'speechSynthesis' in window;
let voces: SpeechSynthesisVoice[] = [];
let reconocimientoActivo: Reconocedor | null = null;

function cargarVoces() {
  if (hayVoz) voces = window.speechSynthesis.getVoices();
}
cargarVoces();
if (hayVoz) window.speechSynthesis.addEventListener?.('voiceschanged', cargarVoces);

// Una voz en español, mejor de Latinoamérica (más cercana a como se habla en Ayacucho) que de España.
function vozEnEspanol(): SpeechSynthesisVoice | null {
  const preferidas = ['es-PE', 'es-419', 'es-US', 'es-MX', 'es-CO', 'es-AR', 'es-CL', 'es-ES'];
  for (const idioma of preferidas) {
    const voz = voces.find((v) => v.lang?.replace('_', '-').toLowerCase() === idioma.toLowerCase());
    if (voz) return voz;
  }
  return voces.find((v) => v.lang?.toLowerCase().startsWith('es')) || null;
}

export const Voz = {
  puedeHablar: hayVoz,
  get puedeEscuchar(): boolean { return Boolean(reconocimiento()); },

  // Si la pregunta se lee sola al cambiar de paso. Por defecto sí: es lo que se pidió para los agricultores.
  get leerSolo(): boolean {
    try { return localStorage.getItem(CLAVE_LECTURA) !== '0'; } catch { return true; }
  },
  set leerSolo(valor: boolean) {
    try { localStorage.setItem(CLAVE_LECTURA, valor ? '1' : '0'); } catch { /* sin almacenamiento: vale para esta visita */ }
  },

  // encolar: se lee después de lo que se está leyendo (p. ej. una sugerencia que llega mientras se lee la pregunta).
  hablar(texto: string | null | undefined, { encolar = false } = {}): void {
    if (!this.puedeHablar || !texto) return;
    if (!encolar) window.speechSynthesis.cancel();
    const frase = new SpeechSynthesisUtterance(texto);
    const voz = vozEnEspanol();
    if (voz) frase.voice = voz;
    frase.lang = voz?.lang || 'es-PE';
    frase.rate = 0.92; // un poco más lento que lo normal: se entiende mejor
    window.speechSynthesis.speak(frase);
  },

  callar(): void {
    if (this.puedeHablar) window.speechSynthesis.cancel();
  },

  // Escucha una frase y la devuelve por alTexto (también los resultados parciales, para mostrarlos mientras habla).
  escuchar({ alTexto, alTerminar, alError }: { alTexto?: (texto: string, final: boolean) => void; alTerminar?: (final: string) => void; alError?: (codigo: string) => void }): Reconocedor | null {
    const Reconocimiento = reconocimiento();
    if (!Reconocimiento) { alError?.('sin-soporte'); return null; }
    this.detenerEscucha();
    this.callar(); // que no se dicte a sí misma
    const rec = new Reconocimiento();
    rec.lang = 'es-PE';
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.continuous = false;
    let final = '';
    rec.onresult = (e) => {
      let parcial = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i]!;
        if (r.isFinal) final += r[0].transcript;
        else parcial += r[0].transcript;
      }
      alTexto?.((final || parcial).trim(), Boolean(final));
    };
    rec.onerror = (e) => alError?.(e.error);
    rec.onend = () => {
      if (reconocimientoActivo === rec) reconocimientoActivo = null;
      alTerminar?.(final.trim());
    };
    reconocimientoActivo = rec;
    rec.start();
    return rec;
  },

  detenerEscucha(): void {
    if (reconocimientoActivo) {
      const rec = reconocimientoActivo;
      reconocimientoActivo = null;
      try { rec.abort(); } catch { /* ya terminó */ }
    }
  },
};

export function mensajeErrorDictado(codigo: string): string {
  return ({
    'not-allowed': 'No diste permiso para usar el micrófono. Puedes escribir la respuesta, o darlo en el candado junto a la dirección de la página.',
    'service-not-allowed': 'Este navegador no deja usar el micrófono aquí. Puedes escribir la respuesta.',
    'no-speech': 'No te escuché. Toca el micrófono y habla cerca del teléfono.',
    'audio-capture': 'No se encontró un micrófono. Puedes escribir la respuesta.',
    network: 'Para dictar se necesita internet. Puedes escribir la respuesta.',
    'sin-soporte': 'Este navegador no puede dictar. Puedes escribir la respuesta (en Chrome sí se puede dictar).',
  } as Record<string, string>)[codigo] || 'No se pudo usar el micrófono. Puedes escribir la respuesta.';
}

// ---- números dichos en voz alta
// El dictado a veces trae cifras ("120") y a veces palabras ("ciento veinte", "dos y medio"). Devuelve el número o null.
const NUMEROS_EN_PALABRAS: Record<string, number> = {
  cero: 0, un: 1, uno: 1, una: 1, dos: 2, tres: 3, cuatro: 4, cinco: 5, seis: 6, siete: 7, ocho: 8, nueve: 9,
  diez: 10, once: 11, doce: 12, trece: 13, catorce: 14, quince: 15, dieciseis: 16, diecisiete: 17, dieciocho: 18,
  diecinueve: 19, veinte: 20, veintiun: 21, veintiuno: 21, veintiuna: 21, veintidos: 22, veintitres: 23,
  veinticuatro: 24, veinticinco: 25, veintiseis: 26, veintisiete: 27, veintiocho: 28, veintinueve: 29, treinta: 30,
  cuarenta: 40, cincuenta: 50, sesenta: 60, setenta: 70, ochenta: 80, noventa: 90, cien: 100, ciento: 100,
  doscientos: 200, doscientas: 200, trescientos: 300, trescientas: 300, cuatrocientos: 400, cuatrocientas: 400,
  quinientos: 500, quinientas: 500, seiscientos: 600, seiscientas: 600, setecientos: 700, setecientas: 700,
  ochocientos: 800, ochocientas: 800, novecientos: 900, novecientas: 900,
};

export function textoANumero(texto: string | null | undefined): number | null {
  const limpio = String(texto || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim();
  if (!limpio) return null;
  const medio = /\by (medio|media)\b/.test(limpio) ? 0.5 : 0;

  const cifra = limpio.match(/\d[\d.,]*/);
  if (cifra) {
    let valor = cifra[0].replace(/[.,]$/, '');
    if (/^\d{1,3}([.,]\d{3})+$/.test(valor)) valor = valor.replace(/[.,]/g, ''); // 1.500 o 1,500 = mil quinientos
    else valor = valor.replace(',', '.');
    const numero = Number(valor);
    return Number.isFinite(numero) ? numero + medio : null;
  }

  let total = 0;
  let actual = 0;
  let decimales: string | null = null;
  let encontrado = false;
  for (const palabra of limpio.split(/[\s-]+/)) {
    if (palabra === 'punto' || palabra === 'coma') { decimales = ''; continue; }
    if (palabra === 'mil') { actual = (actual || 1) * 1000; total += actual; actual = 0; encontrado = true; continue; }
    if (palabra in NUMEROS_EN_PALABRAS) {
      encontrado = true;
      if (decimales !== null) decimales += String(NUMEROS_EN_PALABRAS[palabra]);
      else actual += NUMEROS_EN_PALABRAS[palabra]!;
    }
  }
  if (!encontrado) return null;
  const entero = total + actual;
  return (decimales ? Number(`${entero}.${decimales}`) : entero) + medio;
}
