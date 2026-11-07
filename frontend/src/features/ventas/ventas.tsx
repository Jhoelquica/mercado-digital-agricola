// ============ VENTAS (panel del agricultor; el admin las reutiliza) ============
// Datos: GET /ventas/mias y /ventas/admin (pedidos/ventas.py): solo pedidos pagados, por mes de Perú.
// Forma (skill dataviz): una cifra principal + indicadores del mes con su variación; un gráfico de columnas de una sola
// serie con ÉNFASIS en el mes actual (verde) y el resto en gris de contexto; ranking de productos en barras horizontales de
// un solo color; tabla producto × mes con una escala secuencial de verdes y su leyenda. Cada gráfico tiene su tabla.
// 21st.dev: "Charts & KPI Cards" y "Total Sales Chart" (filtro de periodo arriba de todo lo que afecta).
import type { Plugin } from 'chart.js';
import { useRef, type ReactNode } from 'react';
import type { ResumenVentas, VentaDeProducto } from '../../api/ventas';
import { useGrafico } from '../../components/Grafico';
import { formatearMoneda } from '../../lib/formato';

const VENTAS_VERDE = '#3E8E41';
const VENTAS_CONTEXTO = '#C3CDBE';
const VENTAS_ESCALA = ['#E6F1E1', '#C5E0BB', '#9BC98C', '#6AAC5C', '#3E8E41', '#2A6B2E'];

export function etiquetaMes(mes: string, largo = false): string {
  const [anio, numero] = mes.split('-').map(Number) as [number, number];
  const fecha = new Date(anio, numero - 1, 1);
  return largo
    ? fecha.toLocaleDateString('es-PE', { month: 'long', year: 'numeric' })
    : `${fecha.toLocaleDateString('es-PE', { month: 'short' }).replace('.', '')} ${String(anio).slice(2)}`;
}

export function kilos(valor: number | string | null | undefined): string {
  const n = Number(valor) || 0;
  return `${n.toLocaleString('es-PE', { maximumFractionDigits: n >= 100 ? 0 : 1 })} kg`;
}

interface Variacion { texto: string; clase: string; icono: string }

// Variación contra otro periodo, con flecha y signo (el color nunca va solo). null si no hay con qué comparar.
export function variacion(actual: number, antes: number): Variacion | null {
  if (!antes) return actual > 0 ? { texto: 'nuevo', clase: 'sube', icono: 'arrow-up-right' } : null;
  const pct = Math.round(((actual - antes) / antes) * 100);
  if (pct === 0) return { texto: 'igual', clase: 'igual', icono: 'equal' };
  return { texto: `${pct > 0 ? '+' : '−'}${Math.abs(pct)}%`, clase: pct > 0 ? 'sube' : 'baja', icono: pct > 0 ? 'arrow-up-right' : 'arrow-down-right' };
}

function VariacionTexto({ v, contra }: { v: Variacion | null; contra: string }) {
  if (!v) return <span className="ventas-delta sin-dato">Sin ventas en {contra}</span>;
  return <span className={`ventas-delta ${v.clase}`}><i className={`ti ti-${v.icono}`} aria-hidden="true" /> {v.texto} <span>vs {contra}</span></span>;
}

export function KpisVentas({ d }: { d: ResumenVentas }) {
  const { actual, anterior, anio_pasado: anioPasado } = d.comparativa;
  const mesCorto = etiquetaMes(anterior.mes).split(' ')[0]!.toLowerCase();
  const tile = (icono: string, etiqueta: string, valor: ReactNode, v: Variacion | null, pie: ReactNode = null) => (
    <div className="ventas-kpi" key={etiqueta}>
      <span className="ventas-kpi-etiqueta"><i className={`ti ti-${icono}`} aria-hidden="true" /> {etiqueta}</span>
      <span className="ventas-kpi-valor">{valor}</span>
      <VariacionTexto v={v} contra={mesCorto} />
      {pie}
    </div>
  );
  const haceUnAnio = anioPasado.soles > 0
    ? <span className="ventas-kpi-pie">{etiquetaMes(anioPasado.mes)}: {formatearMoneda(anioPasado.soles)} ({variacion(actual.soles, anioPasado.soles)?.texto})</span>
    : null;
  return (
    <div className="ventas-kpis" aria-label="Este mes comparado con el anterior">
      {tile('cash', `Ventas de ${etiquetaMes(actual.mes, true).split(' ')[0]}`, formatearMoneda(actual.soles), variacion(actual.soles, anterior.soles), haceUnAnio)}
      {tile('scale', 'Kilos vendidos', kilos(actual.kg), variacion(actual.kg, anterior.kg))}
      {tile('receipt', 'Pedidos', actual.pedidos, variacion(actual.pedidos, anterior.pedidos))}
      {tile('users', 'Compradores', actual.compradores, variacion(actual.compradores, anterior.compradores))}
    </div>
  );
}

export function HeroVentas({ d, esAdmin }: { d: ResumenVentas; esAdmin: boolean }) {
  const t = d.totales;
  const partes = [`${t.pedidos} pedido${t.pedidos === 1 ? '' : 's'}`, kilos(t.kg),
    `${t.compradores} comprador${t.compradores === 1 ? '' : 'es'}${t.compradores_recurrentes ? ` (${t.compradores_recurrentes} repite${t.compradores_recurrentes === 1 ? '' : 'n'})` : ''}`,
    `ticket promedio ${formatearMoneda(t.ticket_promedio)}`];
  return (
    <div className="ventas-hero">
      <span className="ventas-hero-etiqueta">{esAdmin ? 'Se vendió' : 'Vendiste'} en los últimos {d.meses.length} meses</span>
      <strong className="ventas-hero-valor">{formatearMoneda(t.soles)}</strong>
      <span className="ventas-hero-sub">{partes.join(' · ')}</span>
    </div>
  );
}

// Una etiqueta con el valor solo sobre la columna resaltada (el resto va en el eje y en el tooltip).
const etiquetaColumnaResaltada: Plugin<'bar'> = {
  id: 'etiquetaColumnaResaltada',
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    const meta = chart.getDatasetMeta(0);
    const i = meta.data.length - 1;
    const valor = chart.data.datasets[0]?.data[i] as number | undefined;
    const barra = meta.data[i];
    if (!valor || !barra) return;
    ctx.save();
    ctx.fillStyle = '#1f2a1d';
    ctx.font = '700 12px "Nunito Sans", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(formatearMoneda(valor), barra.x, barra.y - 6);
    ctx.restore();
  },
};

export function GraficoVentasMes({ d, id }: { d: ResumenVentas; id: string }) {
  const lienzo = useRef<HTMLCanvasElement>(null);
  useGrafico(lienzo, () => {
    const ultimo = d.por_mes.length - 1;
    return {
      type: 'bar',
      data: {
        labels: d.por_mes.map((m) => etiquetaMes(m.mes)),
        datasets: [{
          label: 'Ventas (S/)', data: d.por_mes.map((m) => m.soles),
          backgroundColor: d.por_mes.map((_, i) => (i === ultimo ? VENTAS_VERDE : VENTAS_CONTEXTO)),
          hoverBackgroundColor: VENTAS_VERDE,
          borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'bottom', maxBarThickness: 24,
        }],
      },
      options: {
        maintainAspectRatio: false,
        layout: { padding: { top: 22 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => etiquetaMes(d.por_mes[items[0]!.dataIndex]!.mes, true),
              label: (c) => {
                const m = d.por_mes[c.dataIndex]!;
                return [formatearMoneda(m.soles), kilos(m.kg), `${m.pedidos} pedido${m.pedidos === 1 ? '' : 's'}`];
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, border: { color: '#c3c2b7' }, ticks: { color: '#5b6458', font: { size: 11 } } },
          y: {
            beginAtZero: true, border: { display: false }, grid: { color: '#e1e0d9' },
            ticks: { color: '#5b6458', font: { size: 11 }, maxTicksLimit: 5, callback: (v) => `S/ ${Number(v).toLocaleString('es-PE')}` },
          },
        },
      },
      plugins: [etiquetaColumnaResaltada],
    };
  }, [d]);
  return (
    <section className="card-panel ventas-card" aria-labelledby={`${id}-titulo`}>
      <h5 id={`${id}-titulo`}><i className="ti ti-chart-bar" aria-hidden="true" /> Ventas por mes</h5>
      <p className="ventas-card-sub">En soles. Resaltado: {etiquetaMes(d.comparativa.actual.mes, true)} (va en curso).</p>
      <div className="ventas-chart-wrap"><canvas ref={lienzo} id={id} role="img" aria-label="Ventas en soles de cada mes; la tabla de abajo tiene los mismos datos" /></div>
      <details className="ventas-detalle"><summary>Ver como tabla</summary>
        <div className="tabla-wrap"><table className="tabla-simple tabla-ganancias"><thead><tr><th>Mes</th><th className="num">Ventas</th><th className="num">Kilos</th><th className="num">Pedidos</th></tr></thead>
          <tbody>{d.por_mes.map((m) => <tr key={m.mes}><td>{etiquetaMes(m.mes, true)}</td><td className="num">{formatearMoneda(m.soles)}</td><td className="num">{kilos(m.kg)}</td><td className="num">{m.pedidos}</td></tr>)}</tbody></table></div>
      </details>
    </section>
  );
}

type FilaRanking = Pick<VentaDeProducto, 'nombre' | 'soles' | 'kg'> & { pedidos: number | null; precio_promedio_kg: number | null; otros?: boolean };

export function RankingProductos({ d, titulo, nombreDe = (p) => p.nombre }: { d: ResumenVentas; titulo: string; nombreDe?: (p: FilaRanking) => string | null }) {
  if (!d.productos.length) return null;
  const total = d.totales.soles || 1;
  const visibles: FilaRanking[] = d.productos.slice(0, 8);
  const resto = d.productos.slice(8);
  if (resto.length) {
    visibles.push({ nombre: `Otros ${resto.length} productos`, otros: true, soles: resto.reduce((s, p) => s + p.soles, 0), kg: resto.reduce((s, p) => s + p.kg, 0), pedidos: null, precio_promedio_kg: null });
  }
  const maximo = Math.max(...visibles.map((p) => p.soles)) || 1;
  return (
    <section className="card-panel ventas-card" aria-label={titulo}>
      <h5><i className="ti ti-trophy" aria-hidden="true" /> {titulo}</h5>
      <p className="ventas-card-sub">Por lo vendido en soles en el periodo.</p>
      <ol className="ventas-ranking">
        {visibles.map((p, i) => {
          const detalle = [kilos(p.kg), p.precio_promedio_kg ? `${formatearMoneda(p.precio_promedio_kg)} el kg` : '',
            p.pedidos ? `${p.pedidos} pedido${p.pedidos === 1 ? '' : 's'}` : '', `${Math.round((p.soles / total) * 100)}% del total`].filter(Boolean).join(' · ');
          return (
            <li key={`${p.nombre}-${i}`}>
              <div className="vr-cabeza"><span className="vr-nombre">{p.otros ? '' : `${i + 1}. `}{p.otros ? p.nombre : (nombreDe(p) || 'Producto sin nombre')}</span><span className="vr-valor">{formatearMoneda(p.soles)}</span></div>
              <div className="vr-barra" aria-hidden="true"><span style={{ width: `${Math.max(2, (p.soles / maximo) * 100)}%` }} /></div>
              <small>{detalle}</small>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// Tabla producto × mes: cada celda pintada con la escala secuencial (más oscuro = más vendido) y el valor escrito.
export function TablaProductoMes({ d }: { d: ResumenVentas }) {
  const productos = d.productos.slice(0, 6);
  if (!productos.length) return null;
  const maximo = Math.max(...productos.flatMap((p) => p.soles_por_mes)) || 1;
  const celda = (v: number, i: number) => {
    if (!v) return <td key={i} className="vh-vacia"><span aria-hidden="true">·</span><span className="sr-only">sin ventas</span></td>;
    const paso = Math.min(VENTAS_ESCALA.length - 1, Math.floor((v / maximo) * VENTAS_ESCALA.length));
    return <td key={i} style={{ background: VENTAS_ESCALA[paso], color: paso >= 3 ? '#fff' : '#1f2a1d' }}>S/ {Math.round(v).toLocaleString('es-PE')}</td>;
  };
  return (
    <section className="card-panel ventas-card" aria-label="Ventas de cada producto por mes">
      <h5><i className="ti ti-table" aria-hidden="true" /> Cada producto, mes a mes</h5>
      <p className="ventas-card-sub">En soles. Compara en qué meses se vende más cada uno.</p>
      <div className="tabla-wrap"><table className="ventas-heat">
        <thead><tr><th scope="col">Producto</th>{d.meses.map((m) => <th scope="col" key={m}>{etiquetaMes(m)}</th>)}</tr></thead>
        <tbody>{productos.map((p) => <tr key={p.producto_id}><th scope="row">{p.nombre || 'Producto sin nombre'}</th>{p.soles_por_mes.map(celda)}</tr>)}</tbody>
      </table></div>
      <div className="ventas-heat-leyenda" aria-hidden="true">Menos {VENTAS_ESCALA.map((c) => <span key={c} style={{ background: c }} />)} Más</div>
    </section>
  );
}

export function Reparto({ titulo, icono, grupos }: { titulo: string; icono: string; grupos: { nombre: string; soles: number }[] }) {
  if (!grupos.length) return null;
  const total = grupos.reduce((s, g) => s + g.soles, 0) || 1;
  return (
    <section className="card-panel ventas-card ventas-reparto"><h5><i className={`ti ti-${icono}`} aria-hidden="true" /> {titulo}</h5>
      <ul className="ventas-ranking">
        {grupos.slice(0, 5).map((g) => (
          <li key={g.nombre}><div className="vr-cabeza"><span className="vr-nombre">{g.nombre}</span><span className="vr-valor">{Math.round((g.soles / total) * 100)}%</span></div>
            <div className="vr-barra" aria-hidden="true"><span style={{ width: `${Math.max(2, (g.soles / total) * 100)}%` }} /></div>
            <small>{formatearMoneda(g.soles)}</small></li>
        ))}
      </ul>
    </section>
  );
}

function diasQueQuedanDelMes() {
  const hoy = new Date();
  return new Date(hoy.getFullYear(), hoy.getMonth() + 1, 0).getDate() - hoy.getDate();
}

// Frases con lo que dicen los números (solo hechos de los datos, sin inventar).
export function frasesVentas(d: ResumenVentas, esAdmin: boolean): [string, ReactNode][] {
  const frases: [string, ReactNode][] = [];
  const t = d.totales;
  const tu = esAdmin ? 'El' : 'Tu';
  const estrella = d.productos[0];
  if (estrella && t.soles > 0) {
    frases.push(['star', <>{tu} producto más vendido es <strong>{estrella.nombre || 'sin nombre'}</strong>: {Math.round((estrella.soles / t.soles) * 100)}% de lo vendido ({formatearMoneda(estrella.soles)}).</>]);
  }
  const mejor = d.por_mes.reduce((a, b) => (b.soles > a.soles ? b : a), d.por_mes[0]!);
  if (mejor && mejor.soles > 0) frases.push(['calendar-star', <>{esAdmin ? 'El mejor mes fue' : 'Tu mejor mes fue'} <strong>{etiquetaMes(mejor.mes, true)}</strong>, con {formatearMoneda(mejor.soles)}.</>]);
  const { actual, anterior, anio_pasado: anioPasado } = d.comparativa;
  const quedan = diasQueQuedanDelMes();
  if (anterior.soles > 0) {
    const v = variacion(actual.soles, anterior.soles)!;
    const mesAnterior = etiquetaMes(anterior.mes, true).split(' ')[0];
    frases.push([actual.soles >= anterior.soles ? 'trending-up' : 'trending-down',
      actual.soles >= anterior.soles
        ? `Este mes ${esAdmin ? 'ya se vendió' : 'ya vendiste'} ${v.texto === 'igual' ? 'lo mismo que' : `${v.texto} más que`} en ${mesAnterior}.`
        : `Este mes ${esAdmin ? 'se lleva' : 'llevas'} ${formatearMoneda(actual.soles)}: ${formatearMoneda(anterior.soles - actual.soles)} menos que en ${mesAnterior}${quedan ? `, y quedan ${quedan} días` : ''}.`]);
  }
  if (anioPasado.soles > 0) {
    frases.push(['history', `Hace un año (${etiquetaMes(anioPasado.mes, true)}) ${esAdmin ? 'se vendió' : 'vendiste'} ${formatearMoneda(anioPasado.soles)}; este mes van ${formatearMoneda(actual.soles)}.`]);
  }
  if (estrella?.precio_promedio_kg) {
    frases.push(['scale', <>{esAdmin ? '' : 'Tu '}{estrella.nombre} se vendió en promedio a <strong>{formatearMoneda(estrella.precio_promedio_kg)} el kg</strong>.</>]);
  }
  // Productos que se vendían y dejaron de venderse en los dos últimos meses: quizá sin stock o con un precio alto.
  const n = d.meses.length;
  d.productos.filter((p) => p.soles_por_mes.slice(0, n - 2).some((v) => v > 0) && !p.soles_por_mes.slice(n - 2).some((v) => v > 0))
    .slice(0, 2).forEach((p) => {
      const ultimo = d.meses[p.soles_por_mes.map((v) => v > 0).lastIndexOf(true)]!;
      frases.push(['alert-circle', <><strong>{p.nombre || 'Un producto'}</strong> no se vende desde {etiquetaMes(ultimo, true)}: revisa si le queda stock o si su precio sigue bien.</>]);
    });
  if (t.compradores > 0) {
    frases.push(['users', t.compradores_recurrentes
      ? `${t.compradores_recurrentes} de ${t.compradores} compradores volvieron a comprar${esAdmin ? '' : 'te'}.`
      : `${t.compradores} comprador${t.compradores === 1 ? '' : 'es'}; ninguno volvió a comprar${esAdmin ? '' : 'te'} todavía.`]);
  }
  const porMayor = d.por_tipo.find((g) => g.nombre === 'Por mayor');
  if (porMayor && t.soles > 0) frases.push(['packages', `${Math.round((porMayor.soles / t.soles) * 100)}% de lo vendido fue por mayor (pedidos grandes).`]);
  const fuera = d.por_destino.filter((g) => g.nombre !== 'Ayacucho');
  if (fuera.length && t.soles > 0) {
    frases.push(['map-pin', `${Math.round((fuera.reduce((s, g) => s + g.soles, 0) / t.soles) * 100)}% se vendió fuera de Ayacucho (${fuera.map((g) => g.nombre).join(', ')}).`]);
  }
  return frases;
}

export function FrasesVentas({ frases, esAdmin }: { frases: [string, ReactNode][]; esAdmin: boolean }) {
  if (!frases.length) return null;
  return (
    <section className="card-panel ventas-card ventas-frases" aria-label="Lo que dicen los números">
      <h5><i className="ti ti-bulb" aria-hidden="true" /> Lo que dicen {esAdmin ? 'los' : 'tus'} números</h5>
      <ul>{frases.map(([icono, texto], i) => <li key={i}><i className={`ti ti-${icono}`} aria-hidden="true" /><span>{texto}</span></li>)}</ul>
    </section>
  );
}

export function VentasVacias({ esAdmin }: { esAdmin: boolean }) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon"><i className="ti ti-chart-bar" /></span>
      <p><strong>{esAdmin ? 'Todavía no hay ventas pagadas en este periodo' : 'Todavía no tienes ventas pagadas en este periodo'}</strong></p>
      <p className="muted">{esAdmin ? 'Cuando los compradores paguen sus pedidos, aquí se ven las ventas de cada mes.' : 'Cuando un comprador pague un pedido con tus productos, aquí ves cuánto vendiste cada mes y qué producto se vende más.'}</p>
    </div>
  );
}

export function VentasProductor({ d }: { d: ResumenVentas }) {
  if (!d.totales.pedidos) return <VentasVacias esAdmin={false} />;
  return (
    <>
      <div className="ventas-arriba"><HeroVentas d={d} esAdmin={false} /><KpisVentas d={d} /></div>
      <div className="ventas-grid">
        <GraficoVentasMes d={d} id="grafico-ventas-productor" />
        <RankingProductos d={d} titulo="Tus productos más vendidos" />
      </div>
      <div className="ventas-grid">
        <FrasesVentas frases={frasesVentas(d, false)} esAdmin={false} />
        <TablaProductoMes d={d} />
      </div>
      <div className="ventas-grid ventas-grid-pares">
        <Reparto titulo="Dónde vendes" icono="map-pin" grupos={d.por_destino} />
        <Reparto titulo="Por mayor o por menor" icono="packages" grupos={d.por_tipo} />
      </div>
    </>
  );
}
