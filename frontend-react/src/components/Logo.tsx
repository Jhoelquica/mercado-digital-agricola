// El mismo logo de frontend/verificar.html: toma el color del texto que lo rodea.
export function Logo() {
  return (
    <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M22 40 L78 40 L70 82 Q50 90 30 82 Z" fill="currentColor" />
      <path d="M28 56 L72 56" stroke="white" strokeWidth="3" strokeLinecap="round" opacity="0.45" />
      <path d="M30 70 L70 70" stroke="white" strokeWidth="3" strokeLinecap="round" opacity="0.45" />
      <path d="M50 40 Q50 28 50 18" stroke="currentColor" strokeWidth="6" strokeLinecap="round" fill="none" opacity="0.7" />
      <path d="M50 22 Q40 14 44 6 Q56 10 50 22 Z" fill="currentColor" opacity="0.85" />
      <path d="M50 18 Q60 10 56 2 Q46 6 50 18 Z" fill="currentColor" />
    </svg>
  );
}
