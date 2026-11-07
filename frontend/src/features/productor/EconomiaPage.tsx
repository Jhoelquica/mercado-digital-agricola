// ============ GESTIÓN ECONÓMICA (pestaña del panel del productor) ============
// Arriba, "Mis ventas" (GET /ventas/mias, con el filtro de 6 o 12 meses); abajo, los costos y la ganancia de sus cosechas,
// calculados con lo que anotó al sembrar y cosechar (las mismas siembras que ya cargó el panel).
import { useQuery } from '@tanstack/react-query';
import { useRef, useState, type ReactNode } from 'react';
import type { RegistroProduccion } from '../../api/productores';
import { ventas } from '../../api/ventas';
import { useGrafico } from '../../components/Grafico';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearMoneda } from '../../lib/formato';
import { VentasProductor } from '../ventas/ventas';
import { badgeDeEstado, calcularResumenEconomico, useRegistros } from './comun';

// Semillas, insumos, mano de obra. El beige va más oscuro que el fondo de la tarjeta para que la porción se distinga.
const COLORES_COSTOS = ['#2D3B2A', '#82907A', '#CDBF9F'];

function MisVentas() {
  const [meses, setMeses] = useState(12);
  const consulta = useQuery({ queryKey: ['productor', 'ventas', meses], queryFn: () => ventas.mias(meses), placeholderData: (anterior) => anterior });
  let contenido;
  if (consulta.data) contenido = <VentasProductor d={consulta.data} />;
  else if (consulta.isError) contenido = <p className="muted">{(consulta.error as Error).message || 'No se pudieron cargar tus ventas.'}</p>;
  else contenido = <SkeletonFilas cantidad={1} />;
  return (
    <>
      <div className="section-header inner ventas-encabezado">
        <h3><i className="ti ti-chart-bar" /> <Texto clave="seccion.ventas">Mis ventas</Texto></h3>
        <div className="ventas-periodo" role="group" aria-label="Periodo de las ventas" data-ventas="productor">
          {[6, 12].map((m) => <button key={m} type="button" className="ventas-periodo-btn" data-meses={m} aria-pressed={meses === m} onClick={() => setMeses(m)}>{m} meses</button>)}
        </div>
      </div>
      <p className="muted ventas-intro">Lo que vendiste en ChakraShop: solo pedidos pagados, por mes y por producto, para decidir qué y cuándo vender.</p>
      <div id="productor-ventas" className={`ventas${consulta.isFetching && consulta.data ? ' cargando' : ''}`}>{contenido}</div>
    </>
  );
}

function EconomiaStats({ registros }: { registros: RegistroProduccion[] }) {
  const { totalInvertido, cosechados, totalCosechadoKg, conGanancia, gananciaAcumulada } = calcularResumenEconomico(registros);
  // Margen sobre el ingreso estimado: misma definición que margen_porcentaje de cada registro (Productores).
  const ingresoTotal = conGanancia.reduce((suma, r) => suma + (Number(r.ingreso_estimado) || 0), 0);
  const margen = conGanancia.length && ingresoTotal > 0 ? (gananciaAcumulada / ingresoTotal) * 100 : null;
  const plural = (n: number, uno: string, varios: string) => `${n} ${n === 1 ? uno : varios}`;
  const tarjeta = (icono: string, etiqueta: string, valor: string, pie: ReactNode, claseValor = '') => (
    <div className="economia-stat" key={etiqueta}>
      <div className="economia-stat-cabeza">
        <span className="economia-stat-icono"><i className={`ti ti-${icono}`} aria-hidden="true" /></span>
        <span className="economia-stat-label">{etiqueta}</span>
      </div>
      <span className={`economia-stat-valor ${claseValor}`}>{valor}</span>
      {pie}
    </div>
  );
  const pieGanancia = margen === null
    ? <span className="economia-stat-sub">Aún sin cosechas verificadas</span>
    : <span className={`economia-badge ${margen >= 0 ? 'positivo' : 'negativo'}`}><i className={`ti ti-trending-${margen >= 0 ? 'up' : 'down'}`} aria-hidden="true" /> {margen >= 0 ? '+' : '-'}{Math.abs(margen).toFixed(1)}% de margen</span>;
  return (
    <div className="economia-stats">
      {tarjeta('coin', 'Total invertido', formatearMoneda(totalInvertido), <span className="economia-stat-sub">en {plural(registros.length, 'siembra', 'siembras')}</span>)}
      {tarjeta('scale', 'Total cosechado', cosechados.length ? `${totalCosechadoKg.toFixed(2)} kg` : '—', <span className="economia-stat-sub">{cosechados.length ? `de ${plural(cosechados.length, 'cosecha', 'cosechas')}` : 'Aún sin cosechas'}</span>)}
      {tarjeta(gananciaAcumulada >= 0 ? 'trending-up' : 'trending-down', 'Ganancia acumulada', conGanancia.length ? formatearMoneda(gananciaAcumulada) : '—', pieGanancia, gananciaAcumulada >= 0 ? 'ganancia-positiva' : 'ganancia-negativa')}
    </div>
  );
}

// Ganancia acumulada por cultivo (solo los registros con ganancia calculada): varias siembras del mismo cultivo, una barra.
function GraficoGananciaCultivo({ registros }: { registros: RegistroProduccion[] }) {
  const lienzo = useRef<HTMLCanvasElement>(null);
  useGrafico(lienzo, () => {
    const mapa = new Map<string, number>();
    registros.filter((r) => r.ganancia_estimada != null).forEach((r) => mapa.set(r.cultivo, (mapa.get(r.cultivo) || 0) + Number(r.ganancia_estimada)));
    const labels = [...mapa.keys()];
    const valores = [...mapa.values()];
    const datos = valores.length ? valores : [0];
    // Las pérdidas en el mismo rojo que .ganancia-negativa de la tabla, para verlas de un vistazo.
    return {
      type: 'bar',
      data: { labels: labels.length ? labels : ['Sin datos'], datasets: [{ label: 'Ganancia (S/)', data: datos, backgroundColor: datos.map((v) => (v < 0 ? '#b3401f' : '#2D3B2A')), borderRadius: 6 }] },
      options: {
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => formatearMoneda(c.parsed.y) } } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { callback: (v) => `${Number(v) < 0 ? '-' : ''}S/ ${Math.abs(Number(v))}` } } },
      },
    };
  }, [registros]);
  return <canvas ref={lienzo} id="grafico-ganancia-cultivo" role="img" aria-label="Ganancia acumulada por cultivo, en soles" />;
}

// Donut + lista de detalle: el color solo no dice cuánto es cada costo, así que la leyenda lleva monto y porcentaje (y sirve
// de alternativa de texto al gráfico). Suma de los 3 costos de todos los registros, sin importar su estado.
function DistribucionCostos({ registros }: { registros: RegistroProduccion[] }) {
  const lienzo = useRef<HTMLCanvasElement>(null);
  const nombres = ['Semillas', 'Insumos', 'Mano de obra'];
  const valores = [
    registros.reduce((s, r) => s + (Number(r.costo_semillas) || 0), 0),
    registros.reduce((s, r) => s + (Number(r.costo_insumos) || 0), 0),
    registros.reduce((s, r) => s + (Number(r.costo_mano_obra) || 0), 0),
  ];
  const total = valores.reduce((s, v) => s + v, 0);
  useGrafico(lienzo, () => ({
    type: 'doughnut',
    data: { labels: nombres, datasets: [{ data: valores, backgroundColor: COLORES_COSTOS, borderColor: '#fff', borderWidth: 2 }] },
    options: {
      cutout: '62%',
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${formatearMoneda(c.parsed)} (${total ? Math.round((c.parsed / total) * 100) : 0}%)` } },
      },
    },
  }), [registros]);
  return (
    <div className="costos-cuerpo">
      <div className="costos-donut"><canvas ref={lienzo} id="grafico-costos" role="img" aria-label="Distribución de costos entre semillas, insumos, mano de obra y envío" /></div>
      <ul className="costos-leyenda" id="costos-leyenda" aria-label="Detalle de costos">
        {!total ? <li className="muted">Aún no hay costos registrados.</li> : (
          <>
            {nombres.map((nombre, i) => (
              <li key={nombre}>
                <span className="costos-chip" style={{ background: COLORES_COSTOS[i] }} aria-hidden="true" />
                <span className="costos-nombre">{nombre}</span>
                <span className="costos-monto">{formatearMoneda(valores[i])}</span>
                <span className="costos-pct">{Math.round(((valores[i] ?? 0) / total) * 100)}%</span>
              </li>
            ))}
            <li className="costos-total"><span className="costos-nombre">Total</span><span className="costos-monto">{formatearMoneda(total)}</span></li>
          </>
        )}
      </ul>
    </div>
  );
}

function HistorialCosechas({ registros }: { registros: RegistroProduccion[] }) {
  if (!registros.length) return <p className="muted">Todavía no registraste ninguna siembra.</p>;
  const signo = (v: number | string | null) => (Number(v) >= 0 ? 'ganancia-positiva' : 'ganancia-negativa');
  return (
    <table className="tabla-simple tabla-numerica">
      <thead><tr><th>Cultivo</th><th className="num">Costo total</th><th className="num">Cosechado</th><th className="num">Ingreso est.</th><th className="num">Ganancia</th><th className="num">Margen</th><th>Estado</th></tr></thead>
      <tbody>
        {registros.map((r) => {
          const badge = badgeDeEstado(r.estado);
          return (
            <tr key={r.id}>
              <td>{r.cultivo}</td>
              <td className="num">{formatearMoneda(r.costo_total)}</td>
              <td className="num">{r.cantidad_cosechada != null ? `${Number(r.cantidad_cosechada).toFixed(2)} ${r.unidad_medida}` : '—'}</td>
              <td className="num">{r.ingreso_estimado != null ? formatearMoneda(r.ingreso_estimado) : '—'}</td>
              <td className="num">{r.ganancia_estimada != null ? <span className={signo(r.ganancia_estimada)}>{formatearMoneda(r.ganancia_estimada)}</span> : '—'}</td>
              <td className="num">{r.margen_porcentaje != null ? <span className={signo(r.margen_porcentaje)}>{Number(r.margen_porcentaje) < 0 ? '-' : ''}{Math.abs(Number(r.margen_porcentaje)).toFixed(1)}%</span> : '—'}</td>
              <td><span className={`pill pill-estado ${badge.clase}`}><i className={`ti ti-${badge.icono}`} /> {badge.etiqueta}</span></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function EconomiaPage() {
  const registros = useRegistros();
  const lista = registros.data ?? [];
  return (
    <div className="productor-tab-panel active" data-tab-panel="economia">
      <MisVentas />

      <div className="section-header inner economia-estimada-titulo">
        <h3><i className="ti ti-calculator" /> <Texto clave="seccion.costos">Costos y ganancia de tus cosechas</Texto></h3>
      </div>
      <p className="muted ventas-intro">Calculado con lo que anotaste al sembrar y cosechar (inversión, kilos y precio estimado).</p>
      <div id="productor-economia-stats">{!registros.isLoading && <EconomiaStats registros={lista} />}</div>
      <div className="economia-graficos">
        <div className="card-panel grafico-card">
          <h4><i className="ti ti-chart-bar" /> Ganancia por cultivo (S/)</h4>
          {!registros.isLoading && <GraficoGananciaCultivo registros={lista} />}
        </div>
        <div className="card-panel grafico-card">
          <h4><i className="ti ti-chart-pie" /> Distribución de costos</h4>
          {!registros.isLoading && <DistribucionCostos registros={lista} />}
        </div>
      </div>
      <div className="card-panel">
        <h4 style={{ marginBottom: 12 }}>Historial de cosechas</h4>
        <div id="productor-economia-historial" className="tabla-wrap">{!registros.isLoading && <HistorialCosechas registros={lista} />}</div>
      </div>
    </div>
  );
}
