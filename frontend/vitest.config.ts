import { defineConfig } from 'vitest/config';

// Pruebas de las reglas puras (src/**/*.test.ts): sin los plugins de React ni de la PWA, en Node.
export default defineConfig({
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
});
