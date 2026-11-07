import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link } from 'react-router';
import type { Chacra } from '../../api/chacras';
import type { RegistroProduccion } from '../../api/productores';
import { transporte, type Envio } from '../../api/transporte';
import { ContadorAnimado } from '../../components/ContadorAnimado';
import { useGrafico } from '../../components/Grafico';
import { manejarError, toast } from '../../components/Toast';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearFechaServidor } from '../../lib/formato';
import { abrirAsistenteSiembra, puedeHablar } from './asistente/abrir';
import { badgeDeEstado, ESTADOS_POR_COMPLETAR, formatearFechaCorta, useChacras, useEntregasDirectas, useMisProductos, useRegistros } from './comun';

// 21st.dev "call to action": una sola acción principal, grande, con lo que pasa al tocarla, y el acceso a lo que ya está en
// curso al lado. Los textos con clave se pasan al quechua si el agricultor lo eligió.
function CtaSiembra({ registros, chacras }: { registros: RegistroProduccion[]; chacras: Chacra[] }) {
  const enCurso = registros.filter((r) => ESTADOS_POR_COMPLETAR.includes(r.estado)).length;
  return (
    <section className="cta-siembra" aria-labelledby="cta-siembra-titulo">
      <span className="cta-siembra-icono" aria-hidden="true"><i className="ti ti-plant-2" /></span>
      <div className="cta-siembra-texto">
        <h3 id="cta-siembra-titulo"><Texto clave="cta.titulo">¿Sembraste o empezaste a producir algo?</Texto></h3>
        <p>Regístralo en unos pasos: dónde, qué, cuándo y cuánto gastaste. Así sabrás cuánto te cuesta y, al cosechar, tu producto llega al catálogo.</p>
        {puedeHablar && <span className="cta-siembra-voz"><i className="ti ti-volume" aria-hidden="true" /> Puedes escuchar las preguntas y responder hablando.</span>}
      </div>
      <div className="cta-siembra-acciones">
        {chacras.length
          ? <button type="button" className="btn btn-primary btn-cta-siembra btn-abrir-asistente-siembra" onClick={abrirAsistenteSiembra}><i className="ti ti-seedling" aria-hidden="true" /> <Texto clave="cta.registrar">Registrar siembra o producción</Texto></button>
          : <Link className="btn btn-primary btn-cta-siembra" to="/productor/chacras"><i className="ti ti-map-pin" aria-hidden="true" /> <Texto clave="cta.chacra_primero">Primero registra tu chacra</Texto></Link>}
        <Link className="btn btn-outline cta-siembra-ver" to="/productor/que-sembrar"><i className="ti ti-bulb" aria-hidden="true" /> <Texto clave="cta.que_sembrar">¿Qué me conviene sembrar?</Texto></Link>
        {enCurso > 0 && <Link className="btn btn-ghost cta-siembra-ver" to="/productor/siembras"><i className="ti ti-list-check" aria-hidden="true" /> {enCurso === 1 ? 'Ver la que está en curso' : `Ver las ${enCurso} en curso`}</Link>}
      </div>
    </section>
  );
}

function Resumen({ registros, chacras, disponibles, entregas, alVerEntregas }: { registros: RegistroProduccion[]; chacras: Chacra[]; disponibles: number; entregas: number; alVerEntregas: () => void }) {
  // "En curso" es todo lo que aún no terminó; "aprobado" equivale a "cosechado" (de antes de retirar al Verificador).
  const enCurso = registros.filter((r) => r.estado !== 'aprobado' && r.estado !== 'cosechado').length;
  // La próxima cosecha solo puede ser de una siembra que todavía no se cosechó.
  const proxima = registros
    .filter((r) => r.estado === 'planificado' && r.fecha_cosecha_estimada)
    .sort((a, b) => new Date(a.fecha_cosecha_estimada).getTime() - new Date(b.fecha_cosecha_estimada).getTime())[0];
  const stat = (icono: string, valor: number, etiqueta: string, destacado = false) => (
    <div className={`resumen-stat ${destacado ? 'resumen-stat-destacado' : ''}`}>
      <span className="resumen-stat-icono"><i className={`ti ti-${icono}`} /></span>
      <div><ContadorAnimado valor={valor} /><span className="resumen-stat-label">{etiqueta}</span></div>
    </div>
  );
  return (
    <div className="card-panel resumen-panel">
      <div className="resumen-stats">
        {stat('seedling', enCurso, `Cosecha${enCurso === 1 ? '' : 's'} en curso`, true)}
        {stat('map-pin', chacras.length, `Chacra${chacras.length === 1 ? '' : 's'}`)}
        {stat('shopping-bag', disponibles, disponibles === 1 ? 'Producto disponible' : 'Productos disponibles')}
      </div>
      {proxima && (
        <div className="resumen-destacado">
          <i className="ti ti-calendar-event" /> Próxima cosecha estimada: <strong>{proxima.cultivo}</strong> el {formatearFechaCorta(proxima.fecha_cosecha_estimada)}
        </div>
      )}
      {entregas > 0 && (
        <div className="resumen-destacado resumen-destacado-urgente resumen-destacado-clicable" id="btn-resumen-entregas-directas" role="button" tabIndex={0}
          onClick={alVerEntregas} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); alVerEntregas(); } }}>
          <i className="ti ti-truck-delivery" /> Tienes {entregas} entrega{entregas === 1 ? '' : 's'} directa{entregas === 1 ? '' : 's'} pendiente{entregas === 1 ? '' : 's'} — ningún repartidor tuvo capacidad suficiente
        </div>
      )}
      <div className="resumen-acciones">
        <Link className="btn btn-primary btn-sm" id="btn-resumen-publicar" to="/productor/borradores"><i className="ti ti-pencil" /> Completar borradores</Link>
        <Link className="btn btn-primary btn-sm" id="btn-resumen-siembra" to="/productor/siembras"><i className="ti ti-list-check" /> Ver mis siembras</Link>
        <Link className="btn btn-primary btn-sm" id="btn-resumen-chacra" to="/productor/chacras"><i className="ti ti-map-pin" /> Registrar chacra</Link>
      </div>
    </div>
  );
}

// Caso poco frecuente: ningún repartidor tuvo capacidad suficiente para el pedido y lo entrega el propio agricultor.
function TarjetaEntregaDirecta({ envio, indice }: { envio: Envio; indice: number }) {
  const queryClient = useQueryClient();
  const [enviando, setEnviando] = useState(false);
  async function marcar() {
    setEnviando(true);
    try {
      await transporte.marcarEntregado(envio.id);
      toast('¡Entrega confirmada! 🚚');
      queryClient.setQueryData<Envio[]>(['productor', 'entregas-directas'], (lista) => (lista || []).filter((e) => e.id !== envio.id));
    } catch (err) {
      manejarError(err, 'marcar la entrega como completada');
      setEnviando(false);
    }
  }
  return (
    <div className="pedido-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-envio-id={envio.id}>
      <div className="pedido-card-header"><h4>Pedido #{String(envio.pedido_id).slice(0, 8)}</h4></div>
      <div className="perfil-datos-grid">
        <div className="perfil-dato"><span className="perfil-dato-label">Envío creado</span><span className="perfil-dato-valor">{formatearFechaServidor(envio.fecha_creacion)}</span></div>
      </div>
      <p className="muted">No se encontró un repartidor con capacidad suficiente para este pedido — te toca entregarlo directamente.</p>
      <div className="tarjeta-acciones">
        <button type="button" className="btn btn-primary btn-sm btn-marcar-entregado" disabled={enviando} onClick={() => { void marcar(); }}><i className="ti ti-check" /> Marcar como entregado</button>
      </div>
    </div>
  );
}

// Siembras registradas por mes, en los últimos 6 meses (Chart.js, como en el frontend anterior).
function GraficoSiembrasPorMes({ registros }: { registros: RegistroProduccion[] }) {
  const lienzo = useRef<HTMLCanvasElement>(null);
  useGrafico(lienzo, () => {
    const ahora = new Date();
    const meses = Array.from({ length: 6 }, (_, i) => new Date(ahora.getFullYear(), ahora.getMonth() - 5 + i, 1));
    const conteos = meses.map(() => 0);
    registros.forEach((r) => {
      if (!r.fecha_siembra) return;
      const f = new Date(r.fecha_siembra);
      const idx = meses.findIndex((m) => m.getFullYear() === f.getFullYear() && m.getMonth() === f.getMonth());
      if (idx !== -1) conteos[idx] = (conteos[idx] ?? 0) + 1;
    });
    return {
      type: 'bar',
      data: { labels: meses.map((m) => m.toLocaleDateString('es-PE', { month: 'short' })), datasets: [{ label: 'Siembras', data: conteos, backgroundColor: '#2D3B2A', borderRadius: 6, maxBarThickness: 44 }] },
      options: {
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.parsed.y} ${c.parsed.y === 1 ? 'siembra' : 'siembras'}` } } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0, stepSize: 1 } } },
      },
    };
  }, [registros]);
  return <canvas ref={lienzo} id="grafico-cosechas-mes" role="img" aria-label="Siembras registradas por mes en los últimos seis meses" />;
}

function UltimasSiembras({ registros, chacras }: { registros: RegistroProduccion[]; chacras: Chacra[] }) {
  const ultimas = [...registros].filter((r) => r.fecha_siembra)
    .sort((a, b) => new Date(b.fecha_siembra).getTime() - new Date(a.fecha_siembra).getTime()).slice(0, 5);
  if (!ultimas.length) return <p className="muted">Todavía no registraste ninguna siembra.</p>;
  return (
    <table className="tabla-simple">
      <thead><tr><th>Fecha</th><th>Cultivo</th><th>Chacra</th><th>Estado</th></tr></thead>
      <tbody>
        {ultimas.map((r) => {
          const chacra = chacras.find((c) => c.id === r.chacra_id);
          const badge = badgeDeEstado(r.estado);
          return (
            <tr key={r.id}>
              <td>{formatearFechaCorta(r.fecha_siembra)}</td>
              <td>{r.cultivo}</td>
              <td>{chacra ? chacra.codigo : '—'}</td>
              <td><span className={`pill pill-estado ${badge.clase}`}><i className={`ti ti-${badge.icono}`} /> {badge.etiqueta}</span></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function InicioPage() {
  const registros = useRegistros();
  const chacras = useChacras();
  const misProductos = useMisProductos();
  const entregas = useEntregasDirectas();
  const seccionEntregas = useRef<HTMLDivElement>(null);
  const listos = !registros.isLoading && !chacras.isLoading && !misProductos.isLoading && !entregas.isLoading;
  const lista = registros.data ?? [];
  const misChacras = chacras.data ?? [];
  const pendientes = entregas.data ?? [];
  // Solo "disponible" cuenta: "en_transito" ya se publicó pero todavía no es una venta real.
  const disponibles = (misProductos.data ?? []).filter((p) => p.estado === 'disponible').length;

  return (
    <div className="productor-tab-panel active" data-tab-panel="inicio">
      <div id="productor-cta-siembra">{!registros.isLoading && !chacras.isLoading && <CtaSiembra registros={lista} chacras={misChacras} />}</div>
      <div id="productor-resumen">
        {listos
          ? <Resumen registros={lista} chacras={misChacras} disponibles={disponibles} entregas={pendientes.length} alVerEntregas={() => seccionEntregas.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })} />
          : <div className="card-panel resumen-panel"><div className="resumen-stats">{[0, 1, 2].map((i) => <div className="resumen-stat" key={i}><div className="skeleton" style={{ width: 44, height: 44, borderRadius: '50%', flexShrink: 0 }} /><div style={{ flex: 1 }}><div className="skeleton skeleton-line w-40" style={{ marginBottom: 8 }} /><div className="skeleton skeleton-line w-70" /></div></div>)}</div></div>}
      </div>
      {pendientes.length > 0 && (
        <div id="entregas-directas-section" ref={seccionEntregas}>
          <div className="section-header inner"><h3><i className="ti ti-truck-delivery" /> Entregas directas pendientes</h3></div>
          <p className="muted">Estos pedidos no encontraron un repartidor con capacidad suficiente — te toca entregarlos directamente.</p>
          <div id="entregas-directas-lista" className="pedidos-list">{pendientes.map((e, i) => <TarjetaEntregaDirecta key={e.id} envio={e} indice={i} />)}</div>
        </div>
      )}
      <div className="inicio-graficos">
        <div className="card-panel grafico-card">
          <h4><i className="ti ti-chart-bar" /> Siembras por mes (últimos 6 meses)</h4>
          <GraficoSiembrasPorMes registros={lista} />
        </div>
        <div className="card-panel">
          <h4 style={{ marginBottom: 12 }}><i className="ti ti-list" /> Últimas siembras</h4>
          <div id="productor-ultimas-siembras" className="tabla-wrap">{!registros.isLoading && <UltimasSiembras registros={lista} chacras={misChacras} />}</div>
        </div>
      </div>
    </div>
  );
}
