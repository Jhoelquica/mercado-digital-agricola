import type { Chart, ChartConfiguration } from 'chart.js';
import { useEffect, type DependencyList, type RefObject } from 'react';

// Chart.js se descarga recién cuando una pantalla dibuja un gráfico (como Leaflet con los mapas): no va en el paquete
// principal. Una sola descarga para todos los gráficos.
let carga: Promise<typeof Chart> | null = null;
export function cargarChart(): Promise<typeof Chart> {
  carga ??= import('chart.js/auto').then((m) => m.default);
  return carga;
}

// Dibuja un gráfico en el lienzo cuando cambian `deps`, y lo destruye al cambiar o al salir (si no, Chart.js avisa
// "Canvas is already in use"). `configurar` devuelve null si no hay nada que dibujar.
export function useGrafico(lienzo: RefObject<HTMLCanvasElement | null>, configurar: () => ChartConfiguration | null, deps: DependencyList): void {
  useEffect(() => {
    let vigente = true;
    let grafico: Chart | null = null;
    void cargarChart().then((ChartJs) => {
      const config = vigente && lienzo.current ? configurar() : null;
      if (config && lienzo.current) grafico = new ChartJs(lienzo.current, config);
    });
    return () => {
      vigente = false;
      grafico?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
