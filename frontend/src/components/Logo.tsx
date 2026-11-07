const VARIANTES = {
  // Navbar.
  color: { cuerpo: '#2d6a4f', rayas: '#1b4332', opacidadRayas: 0.45, tallo: '#40916c', opacidadTallo: 1, hoja1: '#74c69d', opacidadHoja1: 1, hoja2: '#95d5b2' },
  // Sobre el panel oscuro del acceso.
  claro: { cuerpo: '#74c69d', rayas: '#1b4332', opacidadRayas: 0.45, tallo: '#95d5b2', opacidadTallo: 1, hoja1: '#d8f3dc', opacidadHoja1: 1, hoja2: '#b7e4c7' },
  // Barra de /verificar: toma el color del texto que lo rodea.
  texto: { cuerpo: 'currentColor', rayas: 'white', opacidadRayas: 0.45, tallo: 'currentColor', opacidadTallo: 0.7, hoja1: 'currentColor', opacidadHoja1: 0.85, hoja2: 'currentColor' },
};

export function Logo({ variante = 'color' }: { variante?: keyof typeof VARIANTES }) {
  const c = VARIANTES[variante];
  return (
    <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M22 40 L78 40 L70 82 Q50 90 30 82 Z" fill={c.cuerpo} />
      <path d="M28 56 L72 56" stroke={c.rayas} strokeWidth="3" strokeLinecap="round" opacity={c.opacidadRayas} />
      <path d="M30 70 L70 70" stroke={c.rayas} strokeWidth="3" strokeLinecap="round" opacity={c.opacidadRayas} />
      <path d="M50 40 Q50 28 50 18" stroke={c.tallo} strokeWidth="6" strokeLinecap="round" fill="none" opacity={c.opacidadTallo} />
      <path d="M50 22 Q40 14 44 6 Q56 10 50 22 Z" fill={c.hoja1} opacity={c.opacidadHoja1} />
      <path d="M50 18 Q60 10 56 2 Q46 6 50 18 Z" fill={c.hoja2} />
    </svg>
  );
}
