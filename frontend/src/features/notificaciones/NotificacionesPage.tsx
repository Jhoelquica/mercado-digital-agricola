import { useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { notificaciones, type Notificacion } from '../../api/notificaciones';
import { useAuth } from '../../auth/AuthContext';
import { manejarError } from '../../components/Toast';
import { ETIQUETAS_ESTADO_ENVIO, tiempoRelativo } from '../../lib/envios';
import { formatearFechaServidor, parsearFechaServidor } from '../../lib/formato';
import { abrirPanelBusqueda } from '../catalogo/menus';
import { leerPedidosRecordados } from '../pedidos/pedidosRecordados';
import { TarjetaAvisos } from './TarjetaAvisos';

// Cada tipo de aviso: a qué filtro pertenece, su ícono y su título. Un tipo desconocido igual se muestra (con ícono de
// campana) y solo aparece en "Todas".
const TIPOS: Record<string, { grupo: string; icono: string; titulo: string }> = {
  pedido_creado: { grupo: 'pedido', icono: 'package', titulo: 'Pedido registrado' },
  envio_actualizado: { grupo: 'envio', icono: 'truck', titulo: 'Envío actualizado' },
  producto_disponible: { grupo: 'producto', icono: 'basket', titulo: 'Producto disponible' },
  pago_confirmado: { grupo: 'pedido', icono: 'wallet', titulo: 'Pago recibido' },
  saldo_pagado: { grupo: 'pedido', icono: 'wallet', titulo: 'Saldo recibido' },
  pago_rechazado: { grupo: 'pedido', icono: 'alert-circle', titulo: 'Pago no completado' },
  saldo_rechazado: { grupo: 'pedido', icono: 'alert-circle', titulo: 'Saldo no aprobado' },
  pago_reembolsado: { grupo: 'pedido', icono: 'receipt-refund', titulo: 'Pedido cancelado: dinero devuelto' },
  pago_a_beneficiario: { grupo: 'pedido', icono: 'cash', titulo: 'Te pagamos' },
  recojo_actualizado: { grupo: 'envio', icono: 'plant-2', titulo: 'Recojo de tu cosecha' },
  // H8, para los admins
  recojo_sin_vehiculo: { grupo: 'envio', icono: 'truck-off', titulo: 'Recojo sin vehículo' },
  recojo_anulado_contratado: { grupo: 'envio', icono: 'truck-return', titulo: 'Recojo anulado' },
};
const tipoDe = (tipo: string) => TIPOS[tipo] || { grupo: 'otro', icono: 'bell', titulo: 'Aviso' };

type Filtro = 'todas' | 'pedido' | 'envio' | 'producto';
const FILTROS: { clave: Filtro; etiqueta: string }[] = [
  { clave: 'todas', etiqueta: 'Todas' },
  { clave: 'pedido', etiqueta: 'Pedidos' },
  { clave: 'envio', etiqueta: 'Envíos' },
  { clave: 'producto', etiqueta: 'Productos' },
];

const ICONOS_ESTADO: Record<string, string> = {
  pendiente: 'hourglass', pendiente_asignacion: 'hourglass', propuesto: 'mail', confirmado: 'receipt', asignado: 'clipboard-list',
  en_camino: 'truck', en_transito: 'truck', en_ruta: 'truck', entregado: 'circle-check', cancelado: 'circle-x', rechazado: 'circle-x',
  aprobado: 'circle-check', en_agencia: 'building-warehouse', en_destino: 'map-pin', esperando_camion: 'hourglass', recogido: 'package',
  en_almacen: 'building-warehouse', completado: 'circle-check', error: 'alert-triangle',
};
const ESTADOS_CON_COLOR = new Set(['pendiente', 'pendiente_asignacion', 'propuesto', 'confirmado', 'asignado', 'en_camino', 'en_transito', 'en_ruta',
  'entregado', 'aprobado', 'cancelado', 'rechazado', 'error', 'en_agencia', 'en_destino', 'esperando_camion', 'recogido', 'en_almacen', 'completado']);

const TAMANO_PAGINA = 10;

// El estado de un envío como etiqueta legible, no la clave cruda.
function PillEstado({ estado }: { estado: string }) {
  const clave = estado.toLowerCase();
  const etiqueta = ETIQUETAS_ESTADO_ENVIO[clave] || clave.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  return (
    <span className={`pill pill-estado ${ESTADOS_CON_COLOR.has(clave) ? `pill-${clave}` : 'pill-default'}`}>
      {ICONOS_ESTADO[clave] && <i className={`ti ti-${ICONOS_ESTADO[clave]}`} aria-hidden="true" />} {etiqueta}
    </span>
  );
}


// Grupo de día para los encabezados de la lista (por día calendario local, no por horas transcurridas).
function grupoDeDia(fecha: Date): string {
  if (isNaN(fecha.getTime())) return 'Anteriores';
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  const dia = new Date(fecha); dia.setHours(0, 0, 0, 0);
  const diferencia = Math.round((hoy.getTime() - dia.getTime()) / 86400000);
  if (diferencia <= 0) return 'Hoy';
  if (diferencia === 1) return 'Ayer';
  if (diferencia < 7) return 'Esta semana';
  return 'Anteriores';
}

function TarjetaNotificacion({ n, i, nueva }: { n: Notificacion; i: number; nueva: boolean }) {
  const navigate = useNavigate();
  const tipo = tipoDe(n.tipo);
  const fecha = parsearFechaServidor(n.fecha_envio);
  // Los avisos nuevos traen el estado aparte; los de antes lo traían dentro del mensaje ("...cambió de estado a: en_camino.").
  const estadoViejo = n.tipo === 'envio_actualizado' && !n.estado ? /estado a:\s*([a-z_]+)/i.exec(n.mensaje || '')?.[1] : null;

  let acciones = null;
  if (n.tipo === 'recojo_actualizado') {
    acciones = <button type="button" className="notif-accion" onClick={() => { void navigate('/productor/productos'); }}>Ver mis cosechas <i className="ti ti-arrow-right" aria-hidden="true" /></button>;
  } else if (n.tipo === 'recojo_sin_vehiculo' || n.tipo === 'recojo_anulado_contratado') {
    acciones = <button type="button" className="notif-accion" onClick={() => { void navigate('/admin/transito'); }}>Ver productos en tránsito <i className="ti ti-arrow-right" aria-hidden="true" /></button>;
  } else if (n.pedido_id) {
    acciones = (
      <>
        <span className="notif-chip"><i className="ti ti-receipt" aria-hidden="true" /> Pedido #{String(n.pedido_id).slice(0, 8)}</span>
        <button type="button" className="notif-accion" onClick={() => { void navigate('/comprador/pedidos'); }}>Ver mis pedidos <i className="ti ti-arrow-right" aria-hidden="true" /></button>
      </>
    );
  } else if (n.producto_id) {
    acciones = <button type="button" className="notif-accion" onClick={() => { void navigate(`/producto/${n.producto_id}`, { state: { volverA: '/notificaciones' } }); }}>Ver producto <i className="ti ti-arrow-right" aria-hidden="true" /></button>;
  }

  return (
    <article className={`notif-card${nueva ? ' notif-card--nueva' : ''}`} style={{ animationDelay: `${Math.min(i, 10) * 35}ms` }}>
      <div className={`notif-icon tipo-${n.tipo}`}><i className={`ti ti-${tipo.icono}`} aria-hidden="true" /></div>
      <div className="notif-cuerpo">
        <div className="notif-cabecera">
          <h4 className="notif-titulo">{tipo.titulo}{nueva && <> <span className="notif-nueva">Nueva</span></>}</h4>
          <time className="notif-tiempo" dateTime={isNaN(fecha.getTime()) ? undefined : fecha.toISOString()} title={formatearFechaServidor(n.fecha_envio)}>{tiempoRelativo(n.fecha_envio)}</time>
        </div>
        {n.estado ? (
          <><p className="notif-msg">{n.mensaje}</p><div className="notif-estado-envio"><PillEstado estado={n.estado} /></div></>
        ) : estadoViejo ? (
          <><p className="notif-msg">Tu envío cambió de estado.</p><div className="notif-estado-envio"><PillEstado estado={estadoViejo} /></div></>
        ) : <p className="notif-msg">{n.mensaje}</p>}
        {acciones && <div className="notif-acciones">{acciones}</div>}
      </div>
    </article>
  );
}

function SkeletonNotificaciones() {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <div className="notif-card" aria-hidden="true" key={i}>
          <div className="skeleton notif-skel-icono" />
          <div className="notif-cuerpo">
            <div className="skeleton skeleton-line tall w-40" />
            <div className="skeleton skeleton-line w-70" style={{ marginTop: 10 }} />
          </div>
        </div>
      ))}
    </>
  );
}

const claveVisto = (usuarioId: string | null | undefined) => `agro_notif_visto_${usuarioId}`;

// Si este navegador recuerda pedidos, solo se muestran los avisos de esos pedidos (y los de productos, que no cuelgan de
// ninguno). "Nuevas" son las posteriores a la última visita a esta pantalla, guardada solo en este navegador.
async function cargarNotificaciones(usuarioId: string | null | undefined) {
  const ids = new Set(leerPedidosRecordados(usuarioId));
  const todas = await notificaciones.listarTodas();
  const lista = ids.size ? todas.filter((n) => !n.pedido_id || ids.has(String(n.pedido_id))) : todas;
  let visto: number | null = null;
  try { visto = Number(localStorage.getItem(claveVisto(usuarioId))) || null; } catch { /* sin almacenamiento: no hay marcas */ }
  const nuevas = new Set(visto ? lista.filter((n) => parsearFechaServidor(n.fecha_envio).getTime() > (visto as number)).map((n) => n.id) : []);
  try { localStorage.setItem(claveVisto(usuarioId), String(Date.now())); } catch { /* idem */ }
  return { lista, nuevas };
}

export default function NotificacionesPage() {
  const { sesion } = useAuth();
  const [filtro, setFiltro] = useState<Filtro>('todas');
  const [mostrar, setMostrar] = useState(TAMANO_PAGINA);
  const filtros = useRef<HTMLDivElement>(null);
  const { data, isFetching, refetch } = useQuery({
    queryKey: ['notificaciones', sesion.usuarioId],
    queryFn: async () => {
      try {
        return await cargarNotificaciones(sesion.usuarioId);
      } catch (err) {
        manejarError(err, 'cargar notificaciones');
        throw err;
      }
    },
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const lista = data?.lista ?? [];
  const nuevas = data?.nuevas ?? new Set<string>();
  const conteos: Record<Filtro, number> = { todas: lista.length, pedido: 0, envio: 0, producto: 0 };
  lista.forEach((n) => {
    const grupo = tipoDe(n.tipo).grupo as Filtro;
    if (grupo in conteos) conteos[grupo] += 1;
  });
  const filtradas = filtro === 'todas' ? lista : lista.filter((n) => tipoDe(n.tipo).grupo === filtro);
  const visibles = filtradas.slice(0, mostrar);

  function elegirFiltro(clave: Filtro) {
    setFiltro(clave);
    setMostrar(TAMANO_PAGINA);
    requestAnimationFrame(() => filtros.current?.querySelector<HTMLElement>(`[data-filtro="${clave}"]`)?.focus());
  }

  function refrescar() {
    setFiltro('todas');
    setMostrar(TAMANO_PAGINA);
    void refetch();
  }

  // Encabezados de día: los avisos vienen del más nuevo al más viejo.
  const grupos: { titulo: string; avisos: { n: Notificacion; i: number }[] }[] = [];
  visibles.forEach((n, i) => {
    const titulo = grupoDeDia(parsearFechaServidor(n.fecha_envio));
    if (grupos.at(-1)?.titulo !== titulo) grupos.push({ titulo, avisos: [] });
    grupos.at(-1)?.avisos.push({ n, i });
  });

  const cargando = isFetching;
  return (
    <section id="view-notificaciones" className="view active">
      <div className="section-header panel-header-hero notif-hero">
        <div>
          <h2><i className="ti ti-bell" /> Notificaciones</h2>
          <p className="pedidos-hero-sub">Avisos de tus pedidos, envíos y productos reservados, apenas ocurren.</p>
        </div>
        <button type="button" className="btn btn-outline" id="btn-refrescar-notificaciones" onClick={refrescar}><i className="ti ti-refresh" /> Refrescar</button>
      </div>

      <div className="notif-layout">
        <div className="notif-principal">
          <div ref={filtros} className="notif-filtros" id="notif-filtros" role="group" aria-label="Filtrar avisos por tipo">
            {!cargando && lista.length > 0 && FILTROS.map((f) => (
              <button key={f.clave} type="button" className={`notif-filtro${f.clave === filtro ? ' active' : ''}`} data-filtro={f.clave} aria-pressed={f.clave === filtro} onClick={() => elegirFiltro(f.clave)}>
                {f.etiqueta} <span className="notif-filtro-cuenta">{conteos[f.clave]}</span>
              </button>
            ))}
          </div>
          <p className="mp-solo-lectores" id="notif-estado" role="status" aria-atomic="true">
            {!cargando && lista.length > 0 && `${filtradas.length} ${filtradas.length === 1 ? 'aviso' : 'avisos'}${nuevas.size ? `, ${nuevas.size} ${nuevas.size === 1 ? 'nuevo' : 'nuevos'}` : ''}`}
          </p>
          <div id="notificaciones-list" className="notif-list" aria-busy={cargando || undefined}>
            {cargando ? <SkeletonNotificaciones /> : grupos.map((g) => (
              <section className="notif-grupo" key={g.titulo}>
                <h3 className="notif-grupo-titulo">{g.titulo}</h3>
                {g.avisos.map(({ n, i }) => <TarjetaNotificacion key={n.id} n={n} i={i} nueva={nuevas.has(n.id)} />)}
              </section>
            ))}
          </div>
          {!cargando && mostrar < filtradas.length && (
            <button type="button" className="btn btn-outline btn-block" id="btn-notif-cargar-mas" onClick={() => setMostrar((m) => m + TAMANO_PAGINA)}>Cargar más</button>
          )}
          {!cargando && data && !lista.length && (
            <div id="notificaciones-empty" className="empty-state">
              <span className="empty-state-icon"><i className="ti ti-bell" /></span>
              <p><strong>No tienes notificaciones todavía</strong></p>
              <p className="muted">Aquí verás avisos sobre tus pedidos y envíos apenas ocurran.</p>
              <button type="button" className="btn btn-primary" onClick={() => abrirPanelBusqueda('', { explorar: true })}>Explorar el catálogo</button>
            </div>
          )}
          {!cargando && lista.length > 0 && !filtradas.length && (
            <div id="notificaciones-empty" className="empty-state">
              <span className="empty-state-icon"><i className="ti ti-filter-off" /></span>
              <p><strong>No hay avisos de este tipo</strong></p>
              <button type="button" className="btn btn-outline" onClick={() => elegirFiltro('todas')}>Ver todas</button>
            </div>
          )}
        </div>

        <aside className="notif-lateral" aria-label="Resumen de avisos">
          <TarjetaAvisos />
          <div className="notif-resumen" id="notif-resumen">
            {!cargando && lista.length > 0 && (
              <>
                <div className="notif-resumen-cab">
                  <span className="notif-resumen-icono"><i className="ti ti-bell-ringing" aria-hidden="true" /></span>
                  <div><strong>{conteos.todas}</strong><span>{conteos.todas === 1 ? 'aviso en total' : 'avisos en total'}</span></div>
                </div>
                <ul className="notif-resumen-lista">
                  <li><i className="ti ti-package" aria-hidden="true" /><span>Pedidos registrados</span><strong>{conteos.pedido}</strong></li>
                  <li><i className="ti ti-truck" aria-hidden="true" /><span>Envíos actualizados</span><strong>{conteos.envio}</strong></li>
                  <li><i className="ti ti-basket" aria-hidden="true" /><span>Productos disponibles</span><strong>{conteos.producto}</strong></li>
                </ul>
                {nuevas.size > 0 && <div className="notif-resumen-nuevas"><i className="ti ti-sparkles" aria-hidden="true" /> {nuevas.size} {nuevas.size === 1 ? 'nueva' : 'nuevas'} desde tu última visita</div>}
              </>
            )}
          </div>
          <div className="notif-cta">
            <span className="notif-cta-icono"><i className="ti ti-truck-delivery" /></span>
            <h3>Sigue tus pedidos</h3>
            <p>Mira el estado de tu pago y de tu entrega en un solo lugar.</p>
            <Link className="btn btn-primary" to="/comprador/pedidos">Ir a Mis Pedidos</Link>
          </div>
        </aside>
      </div>
    </section>
  );
}
