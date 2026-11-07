// La prueba del quechua de frontend/pruebas/reglas.test.js: cada texto marcado en las pantallas (<Texto clave>,
// claveIdioma de las pestañas y t('…')) tiene traducción, y lo que no la tiene vuelve al castellano.   npm test
import { expect, test } from 'vitest';
import { normalizar, TEXTOS, traduccion } from './textos';

// Las fuentes de las pantallas como texto (Vite las lee al correr la prueba).
const fuentes = import.meta.glob(['../**/*.tsx', '../**/*.ts', '!../**/*.test.ts'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

test('Quechua: el diccionario cubre cada texto marcado en las pantallas y vuelve al castellano', () => {
  const marcadas = new Set<string>();
  for (const texto of Object.values(fuentes)) {
    for (const m of texto.matchAll(/clave="([a-z_.]+)"|claveIdioma: '([a-z_.]+)'|\bt\('([a-z_.]+)'/g)) marcadas.add((m[1] ?? m[2] ?? m[3]) as string);
  }
  expect(marcadas.size).toBeGreaterThanOrEqual(20);
  expect([...marcadas].filter((clave) => !TEXTOS.qu[clave])).toEqual([]);
  const t = (idioma: 'es' | 'qu', clave: string, castellano: string) => traduccion(idioma, clave) || castellano;
  expect(t('qu', 'tab.chacras', 'Mis Chacras')).toBe('Chakraykuna');
  expect(t('qu', 'clave.que.no.existe', 'Castellano')).toBe('Castellano');
  expect(t('es', 'tab.chacras', 'Mis Chacras')).toBe('Mis Chacras');
  expect(normalizar('fr')).toBe('es');
  expect(normalizar('qu')).toBe('qu');
});
