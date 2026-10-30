import { useSyncExternalStore } from 'react';

interface Aviso {
  id: number;
  mensaje: string;
  tipo: 'ok' | 'error';
}

const DURACION_MS = 4200;
let avisos: Aviso[] = [];
let siguienteId = 1;
const oyentes = new Set<() => void>();

function publicar(nuevos: Aviso[]): void {
  avisos = nuevos;
  oyentes.forEach((o) => o());
}

export function toast(mensaje: string, tipo: Aviso['tipo'] = 'ok'): void {
  const id = siguienteId++;
  publicar([...avisos, { id, mensaje, tipo }]);
  setTimeout(() => publicar(avisos.filter((a) => a.id !== id)), DURACION_MS);
}

function suscribir(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => { oyentes.delete(oyente); };
}

export function Toasts() {
  const lista = useSyncExternalStore(suscribir, () => avisos);
  return (
    <div className="toast-container" role="status" aria-live="polite">
      {lista.map((a) => <div key={a.id} className={`toast ${a.tipo === 'error' ? 'error' : ''}`}>{a.mensaje}</div>)}
    </div>
  );
}
