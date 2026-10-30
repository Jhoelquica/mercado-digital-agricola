import { createContext, use, useCallback, useMemo, useState, type ReactNode } from 'react';
import { TEXTOS } from './textos';

export type CodigoIdioma = 'es' | 'qu';

export const IDIOMAS: { codigo: CodigoIdioma; nombre: string }[] = [
  { codigo: 'es', nombre: 'Castellano' },
  { codigo: 'qu', nombre: 'Runasimi' },
];

const CLAVE_GUARDADA = 'chakrashop.idioma';

// 'es' para cualquier código que no conocemos.
function normalizar(codigo: string | null): CodigoIdioma {
  return codigo === 'qu' ? 'qu' : 'es';
}

function leerGuardado(): CodigoIdioma {
  try { return normalizar(localStorage.getItem(CLAVE_GUARDADA)); } catch { return 'es'; }
}

// El texto traducido de `clave`, o undefined si no hay traducción para el idioma elegido.
function traduccion(idioma: CodigoIdioma, clave: string): string | undefined {
  return idioma === 'es' ? undefined : TEXTOS[idioma][clave];
}

interface ValorIdioma {
  actual: CodigoIdioma;
  // El texto de `clave` en el idioma elegido; si no hay traducción, el castellano que se pasa.
  t: (clave: string, castellano: string) => string;
  cambiar: (codigo: CodigoIdioma) => void;
}

const IdiomaContext = createContext<ValorIdioma | null>(null);

export function IdiomaProvider({ children }: { children: ReactNode }) {
  const [actual, setActual] = useState<CodigoIdioma>(leerGuardado);

  const cambiar = useCallback((codigo: CodigoIdioma) => {
    setActual(codigo);
    try { localStorage.setItem(CLAVE_GUARDADA, codigo); } catch { /* sin almacenamiento: solo esta vez */ }
  }, []);

  const valor = useMemo<ValorIdioma>(() => ({
    actual,
    t: (clave, castellano) => traduccion(actual, clave) || castellano,
    cambiar,
  }), [actual, cambiar]);

  return <IdiomaContext value={valor}>{children}</IdiomaContext>;
}

export function useIdioma(): ValorIdioma {
  const valor = use(IdiomaContext);
  if (!valor) throw new Error('useIdioma debe usarse dentro de IdiomaProvider');
  return valor;
}

// Texto traducible: marca lang="qu" cuando está traducido, para que el lector de pantalla lo pronuncie bien.
export function Texto({ clave, children }: { clave: string; children: string }) {
  const { actual } = useIdioma();
  const traducido = traduccion(actual, clave);
  return traducido ? <span lang={actual}>{traducido}</span> : <span>{children}</span>;
}

// Selector del panel del agricultor.
export function SelectorIdioma() {
  const { actual, cambiar } = useIdioma();
  return (
    <div className="idioma-selector" role="group" aria-label="Idioma · Simi">
      <i className="ti ti-language" aria-hidden="true" />
      {IDIOMAS.map(({ codigo, nombre }) => (
        <button key={codigo} type="button" className="idioma-btn" lang={codigo} aria-pressed={actual === codigo} onClick={() => cambiar(codigo)}>
          {nombre}
        </button>
      ))}
    </div>
  );
}
