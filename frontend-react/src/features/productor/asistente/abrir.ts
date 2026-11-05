// Qué asistente está abierto (registrar la siembra o completar una cosecha). Lo abren Inicio y "Mis siembras"; lo dibuja
// AsistentesDelAgricultor, que vive en el panel del productor.
export type AsistenteAbierto = { tipo: 'siembra' } | { tipo: 'cosecha'; registroId: string } | null;

let abierto: AsistenteAbierto = null;
const oyentes = new Set<() => void>();

function fijar(nuevo: AsistenteAbierto): void {
  abierto = nuevo;
  oyentes.forEach((o) => o());
}

export const leerAsistente = (): AsistenteAbierto => abierto;
export function suscribirAsistente(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => { oyentes.delete(oyente); };
}

export const abrirAsistenteSiembra = () => fijar({ tipo: 'siembra' });
export const abrirAsistenteCosecha = (registroId: string) => fijar({ tipo: 'cosecha', registroId });
export const cerrarAsistente = () => fijar(null);

// La voz del navegador: leer en voz alta lo tiene casi cualquiera; dictar, solo Chrome y Edge (y con internet).
export const puedeHablar = typeof window !== 'undefined' && 'speechSynthesis' in window;
