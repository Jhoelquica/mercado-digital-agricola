import { describe, expect, it } from 'vitest';
import { campoDelErrorDelServidor, errorDeContrasenaNueva, erroresDeCambioDeContrasena } from './contrasena';

describe('errorDeContrasenaNueva: las mismas reglas que el registro (usuarios/acceso.py)', () => {
  it('pide 8 caracteres o más, como mucho 72 bytes y no solo espacios', () => {
    expect(errorDeContrasenaNueva('corta12')).toMatch(/al menos 8 caracteres/);
    expect(errorDeContrasenaNueva('x'.repeat(73))).toMatch(/demasiado larga/);
    expect(errorDeContrasenaNueva('ñ'.repeat(40))).toMatch(/demasiado larga/); // 40 caracteres pero 80 bytes
    expect(errorDeContrasenaNueva('        ')).toMatch(/solo espacios/);
    expect(errorDeContrasenaNueva('clave-larga-y-buena')).toBeUndefined();
  });

  it('no puede ser el correo (sin importar mayúsculas ni espacios) ni la actual', () => {
    expect(errorDeContrasenaNueva(' Ana@Chakra.pe ', 'ana@chakra.pe')).toMatch(/igual a tu correo/);
    expect(errorDeContrasenaNueva('la-de-siempre-1', null, 'la-de-siempre-1')).toMatch(/distinta de la actual/);
  });
});

describe('erroresDeCambioDeContrasena', () => {
  it('marca cada campo con su error, junto al campo', () => {
    expect(erroresDeCambioDeContrasena({ actual: '', nueva: '', confirmacion: '' })).toEqual({
      actual: 'Escribe tu contraseña actual.', nueva: 'Escribe tu contraseña nueva.', confirmacion: 'Repite la contraseña nueva.',
    });
    expect(erroresDeCambioDeContrasena({ actual: 'vieja-123', nueva: 'nueva-clave-456', confirmacion: 'nueva-clave-45' }))
      .toEqual({ confirmacion: 'No coincide con la contraseña nueva.' });
  });

  it('sin errores cuando todo está bien', () => {
    expect(erroresDeCambioDeContrasena({ actual: 'vieja-123', nueva: 'nueva-clave-456', confirmacion: 'nueva-clave-456' }, 'a@b.pe')).toEqual({});
  });
});

describe('campoDelErrorDelServidor', () => {
  it('400 es la contraseña actual, 422 la nueva, lo demás va arriba', () => {
    expect(campoDelErrorDelServidor(400)).toBe('actual');
    expect(campoDelErrorDelServidor(422)).toBe('nueva');
    expect(campoDelErrorDelServidor(429)).toBeNull();
    expect(campoDelErrorDelServidor(409)).toBeNull();
  });
});
