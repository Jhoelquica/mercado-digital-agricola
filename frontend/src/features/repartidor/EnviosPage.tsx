// /repartidor: resumen, disponibilidad, ubicación en vivo, gráfico, propuestas por responder y "Mis envíos".
import { useRef } from 'react';
import { ContadorAnimado } from '../../components/ContadorAnimado';
import { useGrafico } from '../../components/Grafico';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import type { Envio } from '../../api/transporte';
import { ESTADOS_ENVIO_CUMPLIDOS, ESTADOS_ENVIO_EN_CURSO, useMiRepartidor, useMisEnvios, useRecojosRepartidor } from './comun';
import { AvisoUbicacion, useEstadoUbicacion } from './seguimiento';
import { TarjetaEnvio, TarjetaPropuesta } from './TarjetaEnvio';

// El ícono es un punto de color dibujado en CSS (.disponibilidad-punto): un anillo hueco comunica peor el "semáforo".
const DISPONIBILIDAD_INFO: Record<string, { texto: string; clase: string; detalle: string }> = {
  disponible: { texto: 'Disponible', clase: 'disponibilidad-verde', detalle: 'Puedes recibir nuevas propuestas de envío.' },
  ocupado: { texto: 'Ocupado', clase: 'disponibilidad-ambar', detalle: 'Estás atendiendo un envío en este momento.' },
  desconectado: { texto: 'Desconectado', clase: 'disponibilidad-gris', detalle: 'No recibirás nuevas propuestas hasta que te conectes.' },
};

function BannerDisponibilidad({ estado, propuestas, activos }: { estado: string | null | undefined; propuestas: number; activos: number }) {
  const clave = (estado || 'desconectado').toLowerCase();
  let info = DISPONIBILIDAD_INFO[clave] || DISPONIBILIDAD_INFO.desconectado!;
  // "Ocupado" también es lo que queda mientras hay una propuesta sin responder (el servidor reserva al repartidor al
  // proponérsela): decir "atendiendo un envío" en ese caso confundía.
  if (clave === 'ocupado' && activos === 0 && propuestas > 0) {
    info = { ...info, detalle: `Tienes ${propuestas === 1 ? 'una propuesta' : `${propuestas} propuestas`} por responder: acéptala o recházala.` };
  }
  return (
    <div id="disponibilidad-banner">
      <div className={`disponibilidad-banner ${info.clase}`}>
        <span className="disponibilidad-icono"><span className="disponibilidad-punto" /></span>
        <div>
          <strong>{info.texto}</strong>
          <span>{info.detalle}</span>
        </div>
      </div>
    </div>
  );
}

function Resumen({ envios, alVerEnvios }: { envios: Envio[]; alVerEnvios: () => void }) {
  const pendientes = envios.filter((e) => ESTADOS_ENVIO_EN_CURSO.includes(e.estado || '')).length;
  const entregados = envios.filter((e) => ESTADOS_ENVIO_CUMPLIDOS.includes(e.estado || '')).length;
  const enCamino = envios.filter((e) => e.estado === 'en_camino').length;
  return (
    <div className="card-panel resumen-panel">
      <div className="resumen-stats">
        <div className="resumen-stat resumen-stat-destacado">
          <span className="resumen-stat-icono"><i className="ti ti-truck-delivery" /></span>
          <div><span className="resumen-stat-valor"><ContadorAnimado valor={pendientes} /></span><span className="resumen-stat-label">Pendiente{pendientes === 1 ? '' : 's'} de entrega</span></div>
        </div>
        <div className="resumen-stat">
          <span className="resumen-stat-icono"><i className="ti ti-circle-check" /></span>
          <div><span className="resumen-stat-valor"><ContadorAnimado valor={entregados} /></span><span className="resumen-stat-label">Entregado{entregados === 1 ? '' : 's'}</span></div>
        </div>
      </div>
      {enCamino > 0 && <div className="resumen-destacado resumen-destacado-urgente"><i className="ti ti-navigation" /> Tienes {enCamino} entrega{enCamino === 1 ? '' : 's'} en camino ahora</div>}
      <button type="button" className="btn btn-ghost btn-sm resumen-ver-mas" id="btn-resumen-ver-envios" onClick={alVerEnvios}><i className="ti ti-chevron-down" /> Ver mis envíos</button>
    </div>
  );
}

// Tus envíos por estado (de lo ya cargado: no pide nada nuevo).
function GraficoEnviosPorEstado({ envios }: { envios: Envio[] }) {
  const lienzo = useRef<HTMLCanvasElement>(null);
  const pendientes = envios.filter((e) => ESTADOS_ENVIO_EN_CURSO.includes(e.estado || '')).length;
  const entregados = envios.filter((e) => ESTADOS_ENVIO_CUMPLIDOS.includes(e.estado || '')).length;
  const otros = envios.filter((e) => ['cancelado', 'rechazado'].includes(e.estado || '')).length;
  useGrafico(lienzo, () => ({
    type: 'doughnut',
    data: { labels: ['Pendientes', 'Entregados', 'Cancelados/Rechazados'], datasets: [{ data: [pendientes, entregados, otros], backgroundColor: ['#2D3B2A', '#82907A', '#A23B22'] }] },
    options: { plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } } },
  }), [pendientes, entregados, otros]);
  return <canvas ref={lienzo} id="grafico-envios-estado" />;
}

export default function EnviosPage() {
  const perfil = useMiRepartidor();
  const { propuestas, envios, mios, cargando } = useMisEnvios(perfil.data?.id);
  const recojos = useRecojosRepartidor(Boolean(perfil.data));
  const ubicacion = useEstadoUbicacion();
  const lista = useRef<HTMLDivElement>(null);
  const listaPropuestas = propuestas.data ?? [];
  const listaRecojos = recojos.data ?? [];
  const activos = mios.filter((e) => ESTADOS_ENVIO_EN_CURSO.includes(e.estado || '')).length + listaRecojos.filter((r) => ['asignado', 'recogido'].includes(r.estado)).length;
  const conRecojos = listaRecojos.length > 0;
  const listos = !cargando && !recojos.isLoading;

  return (
    <div className="repartidor-shell">
      <div className="repartidor-resumen-layout">
        <div className="repartidor-col-principal">
          <div id="repartidor-resumen">
            {listos ? <Resumen envios={mios} alVerEnvios={() => lista.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })} />
              : <div className="card-panel resumen-panel"><div className="resumen-stats">{[0, 1].map((i) => <div className="resumen-stat" key={i}><div className="skeleton" style={{ width: 44, height: 44, borderRadius: '50%', flexShrink: 0 }} /><div style={{ flex: 1 }}><div className="skeleton skeleton-line w-40" style={{ marginBottom: 8 }} /><div className="skeleton skeleton-line w-70" /></div></div>)}</div></div>}
          </div>
          <div className="repartidor-estado-row">
            <BannerDisponibilidad estado={perfil.data?.estado_disponibilidad} propuestas={listaPropuestas.length + listaRecojos.filter((r) => r.estado === 'propuesto').length} activos={activos} />
            <AvisoUbicacion estado={ubicacion} />
          </div>
        </div>
        <div className="card-panel grafico-card">
          <h4><i className="ti ti-chart-pie" /> Tus envíos por estado</h4>
          {listos && <GraficoEnviosPorEstado envios={mios} />}
        </div>
      </div>

      {listaPropuestas.length > 0 && (
        <div id="propuestas-section" className="propuestas-section">
          <div className="section-header inner">
            <h3><i className="ti ti-mail" /> Propuestas pendientes {listaPropuestas.length > 1 && <span id="propuestas-badge" className="badge-contador">{listaPropuestas.length}</span>}</h3>
          </div>
          <div id="propuestas-list" className="propuestas-grid">{listaPropuestas.map((e, i) => <TarjetaPropuesta key={e.id} envio={e} indice={i} />)}</div>
        </div>
      )}

      <div className="section-header inner">
        <h3><i className="ti ti-clipboard-list" /> Mis envíos</h3>
      </div>
      {/* H11 (validación con usuarios del 2026-10-13, R03: necesitó una pista en las 4 tareas): la guía corta. */}
      <ol className="envios-guia" aria-label="Cómo se lleva un envío">
        <li><strong>Acepta</strong> la propuesta.</li>
        <li>Cuando salgas con el pedido, toca <strong>Ya salí</strong>.</li>
        <li>Al llegar, toca <strong>Ya llegué</strong>: pide el código de 4 dígitos al comprador y toma la foto.</li>
      </ol>
      <div id="envios-list" className="envios-grid" ref={lista}>
        {cargando ? <SkeletonFilas cantidad={2} /> : mios.map((e, i) => <TarjetaEnvio key={e.id} envio={e} indice={i} />)}
      </div>
      {!cargando && !envios.isError && !mios.length && !listaPropuestas.length && (
        <div id="envios-empty" className="empty-state">
          <span className="empty-state-icon"><i className="ti ti-cloud" /></span>
          {/* Con recojos en chacras, "Todo tranquilo" no es cierto: lo que falta son envíos a compradores. */}
          <p><strong>{conRecojos ? 'Sin envíos a compradores por ahora' : 'Todo tranquilo por ahora'}</strong></p>
          <p className="muted">{conRecojos ? 'Tus recojos en chacras están en «Recojos en chacras».' : 'No tienes propuestas ni envíos activos en este momento.'}</p>
        </div>
      )}
      <datalist id="agencias-sugeridas">
        <option value="Shalom" />
        <option value="Marvisur" />
        <option value="Olva Courier" />
      </datalist>
    </div>
  );
}
