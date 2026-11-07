// /admin: lo que espera una acción del admin (resumen), los productores con más productos publicados y las ventas de toda
// la plataforma con el filtro de 6 o 12 meses (Chart.js se descarga recién al dibujar).
import { useQuery } from '@tanstack/react-query';
import { useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { pagos } from '../../api/pagos';
import { productores } from '../../api/productores';
import { ventas, type VentasPlataforma } from '../../api/ventas';
import { ContadorAnimado } from '../../components/ContadorAnimado';
import { useGrafico } from '../../components/Grafico';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { formatearMoneda } from '../../lib/formato';
import { etiquetaMes, FrasesVentas, frasesVentas, GraficoVentasMes, HeroVentas, kilos, KpisVentas, RankingProductos, Reparto, TablaProductoMes, VentasVacias } from '../ventas/ventas';
import { usePendientesPorCapacidad, useProductoresAdmin, useProductosEnTransito, useTransferencias } from './comun';

function Destacado({ id, urgente, icono, alAbrir, children }: { id: string; urgente?: boolean; icono: string; alAbrir: () => void; children: ReactNode }) {
  return (
    <div className={`resumen-destacado${urgente ? ' resumen-destacado-urgente' : ''} resumen-destacado-clicable`} id={id} role="button" tabIndex={0}
      onClick={alAbrir} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); alAbrir(); } }}>
      <i className={`ti ti-${icono}`} /> {children}
    </div>
  );
}

function ResumenAdmin() {
  const navegar = useNavigate();
  const transito = useProductosEnTransito();
  const capacidad = usePendientesPorCapacidad();
  const lista = useProductoresAdmin();
  const transferencias = useTransferencias();
  if (transito.isLoading && capacidad.isLoading && lista.isLoading && transferencias.isLoading) {
    return <div className="card-panel resumen-panel"><div className="resumen-stats">{[0, 1, 2].map((i) => <div className="resumen-stat" key={i}><div className="skeleton" style={{ width: 44, height: 44, borderRadius: '50%', flexShrink: 0 }} /><div style={{ flex: 1 }}><div className="skeleton skeleton-line w-40" style={{ marginBottom: 8 }} /><div className="skeleton skeleton-line w-70" /></div></div>)}</div></div>;
  }
  const enTransito = transito.data?.length ?? 0;
  const porCapacidad = capacidad.data?.length ?? 0;
  const totalProductores = lista.data?.length ?? 0;
  const porRevisar = transferencias.data?.length ?? 0;
  const stat = (icono: string, valor: number, etiqueta: string, destacado = false) => (
    <div className={`resumen-stat${destacado ? ' resumen-stat-destacado' : ''}`}>
      <span className="resumen-stat-icono"><i className={`ti ti-${icono}`} /></span>
      <div><ContadorAnimado valor={valor} /><span className="resumen-stat-label">{etiqueta}</span></div>
    </div>
  );
  return (
    <div className="card-panel resumen-panel">
      <div className="resumen-stats">
        {stat('building-warehouse', enTransito, enTransito === 1 ? 'Producto en tránsito' : 'Productos en tránsito', true)}
        {stat('truck', porCapacidad, porCapacidad === 1 ? 'Pedido esperando vehículo' : 'Pedidos esperando vehículo')}
        {stat('tractor', totalProductores, totalProductores === 1 ? 'Productor' : 'Productores')}
      </div>
      {porRevisar > 0 && (
        <Destacado id="btn-resumen-transferencias" urgente icono="building-bank" alAbrir={() => navegar('/admin/transferencias')}>
          {porRevisar} {porRevisar === 1 ? 'transferencia espera' : 'transferencias esperan'} tu revisión: hasta entonces el pedido no sale a reparto
        </Destacado>
      )}
      {enTransito > 0 && (
        <Destacado id="btn-resumen-transito" icono="scale" alAbrir={() => navegar('/admin/transito')}>
          {enTransito} {enTransito === 1 ? 'producto espera' : 'productos esperan'} que confirmes su llegada y su peso en el almacén
        </Destacado>
      )}
      {porCapacidad > 0 && (
        <Destacado id="btn-resumen-pendientes-capacidad" urgente icono="truck" alAbrir={() => navegar('/admin/capacidad')}>
          {porCapacidad} pedido{porCapacidad === 1 ? '' : 's'} esperando un vehículo con más capacidad
        </Destacado>
      )}
      <div className="resumen-acciones">
        <button type="button" className="btn btn-primary btn-sm" id="btn-resumen-ir-transito" onClick={() => navegar('/admin/transito')}><i className="ti ti-building-warehouse" /> Confirmar llegadas al almacén</button>
      </div>
    </div>
  );
}

// Top 5 de productores por productos publicados, en barras horizontales de mayor a menor. El valor va escrito en cada
// etiqueta y el resumen en texto lo lee un lector de pantalla: nada depende del color.
function GraficoProductores() {
  const lista = useProductoresAdmin();
  const lienzo = useRef<HTMLCanvasElement>(null);
  const top = (lista.data ?? []).filter((item) => item.total > 0).slice(0, 5);
  const plural = (n: number) => `${n} producto${n === 1 ? '' : 's'}`;
  useGrafico(lienzo, () => (top.length ? {
    type: 'bar',
    data: {
      labels: top.map((item) => `${item.p.nombre} · ${item.total}`),
      datasets: [{ data: top.map((item) => item.total), backgroundColor: '#2D3B2A', borderRadius: 6, maxBarThickness: 26 }],
    },
    options: {
      indexAxis: 'y',
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => plural(Number(c.raw)) } } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { ticks: { font: { size: 12 } } } },
    },
  } : null), [JSON.stringify(top.map((item) => [item.p.id, item.p.nombre, item.total]))]);
  return (
    <div className="card-panel grafico-card">
      <h4><i className="ti ti-chart-bar" /> Productos publicados por productor</h4>
      <canvas ref={lienzo} id="grafico-productores-admin" className={top.length ? '' : 'hidden'} role="img" aria-describedby="grafico-productores-admin-resumen" aria-label={top.length ? 'Productos publicados por productor, de mayor a menor' : undefined} />
      <p id="grafico-productores-admin-resumen" className="grafico-resumen">{top.length ? `Con más productos publicados: ${top.map((item) => `${item.p.nombre} (${plural(item.total)})`).join(', ')}.` : ''}</p>
      {!lista.isLoading && !top.length && <div id="grafico-productores-admin-vacio" className="mapa-bloque-neutro"><span className="icon"><i className="ti ti-plant-2" /></span> Todavía ningún productor tiene productos publicados.</div>}
    </div>
  );
}

function RankingProductores({ d, nombres }: { d: VentasPlataforma; nombres: Record<string, string> }) {
  if (!d.productores.length) return null;
  const total = d.totales.soles || 1;
  const maximo = d.productores[0]!.soles || 1;
  return (
    <section className="card-panel ventas-card" aria-label="Productores que más venden">
      <h5><i className="ti ti-tractor" aria-hidden="true" /> Productores que más venden</h5>
      <p className="ventas-card-sub">Por lo vendido en soles en el periodo.</p>
      <ol className="ventas-ranking">
        {d.productores.slice(0, 8).map((p, i) => (
          <li key={p.productor_id}>
            <div className="vr-cabeza"><span className="vr-nombre">{i + 1}. {nombres[p.productor_id] || `Productor #${p.productor_id.slice(0, 8)}`}</span><span className="vr-valor">{formatearMoneda(p.soles)}</span></div>
            <div className="vr-barra" aria-hidden="true"><span style={{ width: `${Math.max(2, (p.soles / maximo) * 100)}%` }} /></div>
            <small>{kilos(p.kg)} · {p.pedidos} pedido{p.pedidos === 1 ? '' : 's'} · {Math.round((p.soles / total) * 100)}% del total</small>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TablaMesAMes({ d, comision }: { d: VentasPlataforma; comision: number }) {
  return (
    <section className="card-panel ventas-card" aria-label="Mes a mes">
      <h5><i className="ti ti-table" aria-hidden="true" /> Mes a mes</h5>
      <p className="ventas-card-sub">Comisión = {Math.round(comision * 100)}% de lo vendido y de los envíos. "Devueltos": pedidos pagados que se cancelaron con devolución; "Sin pagar": pedidos que vencieron sin pagarse.</p>
      <div className="tabla-wrap"><table className="tabla-simple tabla-ganancias tabla-mes-a-mes">
        <thead><tr><th>Mes</th><th className="num">Ventas</th><th className="num">Envíos</th><th className="num">Comisión</th><th className="num">Pedidos</th><th className="num">Compradores</th><th className="num">Nuevos</th><th className="num">Devueltos</th><th className="num">Sin pagar</th></tr></thead>
        <tbody>
          {[...d.por_mes].reverse().map((m) => (
            <tr key={m.mes}>
              <th scope="row">{etiquetaMes(m.mes, true).replace(/^./, (c) => c.toUpperCase())}</th>
              <td className="num">{formatearMoneda(m.soles)}</td>
              <td className="num">{formatearMoneda(m.soles_envio)}</td>
              <td className="num">{formatearMoneda((m.soles + m.soles_envio) * comision)}</td>
              <td className="num">{m.pedidos}</td>
              <td className="num">{m.compradores}</td>
              <td className="num">{m.compradores_nuevos}</td>
              <td className="num">{m.cancelados_devueltos}</td>
              <td className="num">{m.sin_pagar}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}

function frasesAdmin(d: VentasPlataforma, nombres: Record<string, string>): [string, ReactNode][] {
  const frases = frasesVentas(d, true);
  const t = d.totales;
  const lider = d.productores[0];
  if (lider && t.soles > 0) {
    frases.splice(1, 0, ['tractor', <><strong>{nombres[lider.productor_id] || 'Un productor'}</strong> vende el {Math.round((lider.soles / t.soles) * 100)}% de todo lo de la plataforma.</>]);
  }
  const actual = d.por_mes[d.por_mes.length - 1];
  if (actual?.compradores_nuevos) frases.push(['user-plus', `Este mes llegaron ${actual.compradores_nuevos} comprador${actual.compradores_nuevos === 1 ? '' : 'es'} nuevo${actual.compradores_nuevos === 1 ? '' : 's'}.`]);
  const sinPagar = d.por_mes.reduce((s, m) => s + m.sin_pagar, 0);
  const pedidosTotales = t.pedidos + sinPagar;
  if (sinPagar && pedidosTotales) frases.push(['clock-x', `${Math.round((sinPagar / pedidosTotales) * 100)}% de los pedidos del periodo vencieron sin pagarse (${sinPagar}).`]);
  return frases;
}

function VentasAdmin({ d, nombres, comision }: { d: VentasPlataforma; nombres: Record<string, string>; comision: number }) {
  if (!d.totales.pedidos) return <VentasVacias esAdmin />;
  return (
    <>
      <div className="ventas-arriba"><HeroVentas d={d} esAdmin /><KpisVentas d={d} /></div>
      <div className="ventas-grid">
        <GraficoVentasMes d={d} id="grafico-ventas-admin" />
        <FrasesVentas frases={frasesAdmin(d, nombres)} esAdmin />
      </div>
      <div className="ventas-grid ventas-grid-pares">
        <RankingProductos d={d} titulo="Productos más vendidos" />
        <RankingProductores d={d} nombres={nombres} />
      </div>
      <TablaMesAMes d={d} comision={comision} />
      <TablaProductoMes d={d} />
      <div className="ventas-grid ventas-grid-pares">
        <Reparto titulo="Dónde se vende" icono="map-pin" grupos={d.por_destino} />
        <Reparto titulo="Por mayor o por menor" icono="packages" grupos={d.por_tipo} />
      </div>
    </>
  );
}

function VentasPlataformaSeccion() {
  const [meses, setMeses] = useState(12);
  const consulta = useQuery({
    queryKey: ['admin', 'ventas', meses],
    queryFn: async () => {
      const [d, lista, condiciones] = await Promise.all([ventas.admin(meses), productores.listar().catch(() => []), pagos.condiciones().catch(() => null)]);
      return { d, nombres: Object.fromEntries(lista.map((p) => [p.id, p.nombre])) as Record<string, string>, comision: (condiciones?.comision_plataforma_porcentaje ?? 10) / 100 };
    },
    placeholderData: (anterior) => anterior,
  });
  let contenido;
  if (consulta.data) contenido = <VentasAdmin {...consulta.data} />;
  else if (consulta.isError) contenido = <p className="muted">{(consulta.error as Error).message || 'No se pudieron cargar las ventas.'}</p>;
  else contenido = <SkeletonFilas cantidad={1} />;
  return (
    <>
      <div className="section-header inner ventas-encabezado admin-ventas-encabezado">
        <h3><i className="ti ti-chart-line" /> Ventas de la plataforma</h3>
        <div className="ventas-periodo" role="group" aria-label="Periodo de las ventas" data-ventas="admin">
          {[6, 12].map((m) => <button key={m} type="button" className="ventas-periodo-btn" data-meses={m} aria-pressed={meses === m} onClick={() => setMeses(m)}>{m} meses</button>)}
        </div>
      </div>
      <p className="muted ventas-intro">Pedidos pagados de todos los productores, mes a mes: qué se vende, quién vende más, cuántos compradores llegan y cuántos pedidos se caen.</p>
      <div id="admin-ventas" className={`ventas${consulta.isFetching && consulta.data ? ' cargando' : ''}`}>{contenido}</div>
    </>
  );
}

export default function InicioPage() {
  return (
    <div className="admin-tab-panel active" data-admin-panel="inicio">
      <div className="admin-resumen-layout">
        <div id="admin-resumen"><ResumenAdmin /></div>
        <GraficoProductores />
      </div>
      <VentasPlataformaSeccion />
    </div>
  );
}
