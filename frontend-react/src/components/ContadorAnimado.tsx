import { useEffect, useState } from 'react';

// Cuenta de 0 al valor en los resúmenes de los paneles. Decorativo: respeta el movimiento reducido.
export function ContadorAnimado({ valor }: { valor: number }) {
  const [mostrado, setMostrado] = useState(() => (window.matchMedia('(prefers-reduced-motion: reduce)').matches ? valor : 0));
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setMostrado(valor); return undefined; }
    const inicio = performance.now();
    let cuadro = 0;
    const paso = (ahora: number) => {
      const avance = Math.min(1, (ahora - inicio) / 600);
      setMostrado(Math.round(valor * (1 - (1 - avance) ** 3)));
      if (avance < 1) cuadro = requestAnimationFrame(paso);
    };
    cuadro = requestAnimationFrame(paso);
    return () => cancelAnimationFrame(cuadro);
  }, [valor]);
  return <span className="resumen-stat-valor">{mostrado}</span>;
}
