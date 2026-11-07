import { useEffect, useRef } from 'react';

// Repite una consulta cada `intervaloMs` solo mientras el componente está montado y la pestaña se ve: al ocultarla se borra
// el intervalo, y al volver se consulta de inmediato (lo que se ve lleva un rato sin actualizarse) y se arranca otro.
export function useSondeoVisible(consulta: () => Promise<void> | void, intervaloMs: number, activo = true): void {
  const consultaRef = useRef(consulta);
  consultaRef.current = consulta;

  useEffect(() => {
    if (!activo) return undefined;
    let intervalo: ReturnType<typeof setInterval> | undefined;

    const arrancar = () => {
      clearInterval(intervalo);
      intervalo = setInterval(() => { void consultaRef.current(); }, intervaloMs);
    };
    const alCambiarVisibilidad = () => {
      if (document.hidden) {
        clearInterval(intervalo);
        intervalo = undefined;
      } else {
        void consultaRef.current();
        arrancar();
      }
    };

    if (!document.hidden) arrancar();
    document.addEventListener('visibilitychange', alCambiarVisibilidad);
    return () => {
      clearInterval(intervalo);
      document.removeEventListener('visibilitychange', alCambiarVisibilidad);
    };
  }, [intervaloMs, activo]);
}
