import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { pagos } from '../../api/pagos';
import { pedidos, type Pedido } from '../../api/pedidos';
import { transporte } from '../../api/transporte';
import { useAuth } from '../../auth/AuthContext';
import { toast } from '../../components/Toast';
import { formatearMoneda } from '../../lib/formato';
import { ContadorAnimado } from '../../components/ContadorAnimado';
import { abrirPanelBusqueda } from '../catalogo/menus';
import { useCatalogo } from '../catalogo/useCatalogo';
import { esPedidoEntregado, pagoConfirmadoPedido, totalPedido, type DatosPedido } from './estadoPedido';
import { guardarPedidoRecordado, leerPedidosRecordados } from './pedidosRecordados';
import { TarjetaPedido } from './TarjetaPedido';

type Filtro = 'todos' | 'en_curso' | 'entregados';

async function cargarDatosPedido(pedidoId: string, cargado: Pedido | null = null): Promise<DatosPedido | null> {
  let pedido = cargado;
  if (!pedido) {
    try {
      pedido = await pedidos.obtener(pedidoId);
    } catch {
      return null;
    }
  }
  const envio = await transporte.obtenerEnvio(pedidoId).catch(() => null); // aún no tiene envío asignado
  const pago = await pagos.obtener(pedidoId).catch(() => null); // aún no hay un pago registrado
  const ruta = envio ? await transporte.ruta(envio.id).catch(() => null) : null; // seguimiento no disponible por ahora
  return { pedido, envio, pago, ruta };
}

// Los pedidos salen de la cuenta (se ven igual en otro celular). Si no se pudo consultar, al menos los que este navegador recuerda.
async function cargarMisPedidos(usuarioId: string | null | undefined): Promise<{ datos: DatosPedido[]; habia: boolean }> {
  const deLaCuenta = await pedidos.mios().catch(() => null);
  const ids = deLaCuenta ? deLaCuenta.map((p) => p.id) : leerPedidosRecordados(usuarioId);
  const datos = await Promise.all(ids.map((id, i) => cargarDatosPedido(id, deLaCuenta?.[i] ?? null)));
  return { datos: datos.filter((d): d is DatosPedido => d !== null), habia: ids.length > 0 };
}

function Estadistica({ icono, etiqueta, destacada = false, children }: { icono: string; etiqueta: string; destacada?: boolean; children: ReactNode }) {
  return (
    <div className={`resumen-stat ${destacada ? 'resumen-stat-destacado' : ''}`}>
      <span className="resumen-stat-icono"><i className={`ti ti-${icono}`} /></span>
      <div>{children}<span className="resumen-stat-label">{etiqueta}</span></div>
    </div>
  );
}

function ResumenPedidos({ datos, alIrAlPedido }: { datos: DatosPedido[]; alIrAlPedido: (id: string) => void }) {
  const entregados = datos.filter(esPedidoEntregado).length;
  // Solo lo pagado: un pedido sin pagar, rechazado o devuelto no es plata gastada.
  const gastado = datos
    .filter((d) => pagoConfirmadoPedido(d) && d.pago?.estado !== 'reembolsado' && d.pedido.estado !== 'cancelado')
    .reduce((total, d) => total + totalPedido(d.pedido), 0);
  const enCamino = datos.filter((d) => d.envio?.estado === 'en_camino' && !esPedidoEntregado(d));
  const primero = enCamino[0];

  function alTeclear(e: KeyboardEvent<HTMLDivElement>) {
    if (!primero || (e.key !== 'Enter' && e.key !== ' ')) return;
    e.preventDefault();
    alIrAlPedido(primero.pedido.id);
  }

  return (
    <div className="card-panel resumen-panel mp-resumen">
      <div className="resumen-stats">
        <Estadistica icono="truck-delivery" etiqueta="En curso" destacada><ContadorAnimado valor={datos.length - entregados} /></Estadistica>
        <Estadistica icono="shopping-bag" etiqueta="Pedidos totales"><ContadorAnimado valor={datos.length} /></Estadistica>
        <Estadistica icono="circle-check" etiqueta="Entregados"><ContadorAnimado valor={entregados} /></Estadistica>
        <Estadistica icono="wallet" etiqueta="Total comprado"><span className="resumen-stat-valor">{formatearMoneda(gastado)}</span></Estadistica>
      </div>
      {primero && (
        <div className="resumen-destacado resumen-destacado-clicable resumen-destacado-camino" role="button" tabIndex={0} onClick={() => alIrAlPedido(primero.pedido.id)} onKeyDown={alTeclear}>
          <i className="ti ti-truck" />
          <span>{enCamino.length === 1
            ? <>Tu pedido <strong>#{primero.pedido.id.slice(0, 8)}</strong> va en camino — ten a mano tu código de entrega.</>
            : <>Tienes <strong>{enCamino.length} pedidos</strong> en camino.</>}</span>
          <i className="ti ti-chevron-right mp-aviso-flecha" />
        </div>
      )}
    </div>
  );
}

function SkeletonResumen() {
  const stat = (i: number) => (
    <div className="resumen-stat" key={i}>
      <div className="skeleton" style={{ width: 44, height: 44, borderRadius: '50%', flexShrink: 0 }} />
      <div style={{ flex: 1 }}>
        <div className="skeleton skeleton-line w-40" style={{ marginBottom: 8 }} />
        <div className="skeleton skeleton-line w-70" />
      </div>
    </div>
  );
  return <div className="card-panel resumen-panel"><div className="resumen-stats">{[0, 1, 2, 3].map(stat)}</div></div>;
}

// Con la misma forma que la tarjeta final para que no haya salto de layout al cargar.
function SkeletonPedidos({ n }: { n: number }) {
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <div className="mp-card mp-skel" aria-hidden="true" key={i}>
          <div className="mp-cabecera">
            <div className="skeleton mp-skel-thumb" />
            <div className="mp-skel-lineas">
              <div className="skeleton skeleton-line tall w-70" />
              <div className="skeleton skeleton-line w-40" />
            </div>
            <div className="skeleton mp-skel-total" />
          </div>
          <div className="mp-skel-progreso">{[0, 1, 2, 3].map((j) => <div className="skeleton" key={j} />)}</div>
          <div className="mp-skel-cuerpo">
            <div className="skeleton skeleton-line w-70" />
            <div className="skeleton skeleton-line w-60" />
          </div>
        </div>
      ))}
    </>
  );
}

function PedidosVacios({ tipo }: { tipo: 'sin-pedidos' | 'sin-en-curso' | 'sin-entregados' }) {
  return (
    <div id="pedidos-empty" className="empty-state">
      {tipo === 'sin-pedidos' && (
        <>
          <span className="empty-state-icon"><i className="ti ti-shopping-bag" /></span>
          <p><strong>Aún no tienes pedidos</strong></p>
          <p className="muted">Busca productos y arma tu primer pedido directo de productores locales.</p>
          <button type="button" className="btn btn-primary" id="btn-vacio-catalogo" onClick={() => abrirPanelBusqueda()}>Buscar productos</button>
        </>
      )}
      {tipo === 'sin-en-curso' && (
        <>
          <span className="empty-state-icon"><i className="ti ti-circle-check" /></span>
          <p><strong>No tienes pedidos en curso</strong></p>
          <p className="muted">Todos tus pedidos ya fueron entregados.</p>
        </>
      )}
      {tipo === 'sin-entregados' && (
        <>
          <span className="empty-state-icon"><i className="ti ti-package-off" /></span>
          <p><strong>Aún no tienes pedidos entregados</strong></p>
          <p className="muted">Aquí verás el historial una vez que se complete una entrega.</p>
        </>
      )}
    </div>
  );
}

export default function MisPedidosPage() {
  const { sesion } = useAuth();
  const [filtro, setFiltro] = useState<Filtro>('todos');
  const [textoBusqueda, setTextoBusqueda] = useState('');
  const [encontrado, setEncontrado] = useState<DatosPedido | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [refrescando, setRefrescando] = useState(false);
  const [aResaltar, setAResaltar] = useState<string | null>(null);
  const lista = useRef<HTMLDivElement>(null);

  // Sin caché entre visitas: al entrar siempre se consulta, igual que en el frontend actual.
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['mis-pedidos', sesion.usuarioId],
    queryFn: () => cargarMisPedidos(sesion.usuarioId),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const { data: catalogoCargado, isLoading: cargandoCatalogo } = useCatalogo();
  const catalogo = catalogoCargado ?? [];
  const datos = data?.datos ?? [];
  // Un pedido por agencia muestra la oficina de recojo de su ciudad.
  const hayAgencia = datos.some((d) => d.envio?.modalidad_envio === 'agencia') || encontrado?.envio?.modalidad_envio === 'agencia';
  const { data: destinos = [] } = useQuery({ queryKey: ['destinos-lejanos'], queryFn: pedidos.destinosLejanos, enabled: hayAgencia });

  const cargando = isLoading || cargandoCatalogo || refrescando || buscando;

  async function recargar() {
    setEncontrado(null);
    setRefrescando(true);
    await refetch();
    setRefrescando(false);
  }

  async function buscarPorId() {
    const id = textoBusqueda.trim();
    if (!id) return;
    setBuscando(true);
    const d = await cargarDatosPedido(id);
    setBuscando(false);
    if (!d) {
      console.error('buscar el pedido', id);
      toast('No se encontró un pedido con ese ID.', 'error');
      void recargar();
      return;
    }
    guardarPedidoRecordado(sesion.usuarioId, id);
    setEncontrado(d);
  }

  function elegirFiltro(nuevo: Filtro) {
    setEncontrado(null);
    setFiltro(nuevo);
  }

  // Lleva a la tarjeta de un pedido: scroll suave (salvo movimiento reducido), resalte breve y foco para quien navega con teclado.
  function irAlPedido(id: string) {
    if (filtro === 'entregados') setFiltro('todos');
    setAResaltar(id);
  }

  useEffect(() => {
    if (!aResaltar) return undefined;
    const tarjeta = lista.current?.querySelector<HTMLElement>(`.pedido-card[data-pedido-id="${CSS.escape(aResaltar)}"]`);
    setAResaltar(null);
    if (!tarjeta) return undefined;
    const reducido = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    tarjeta.scrollIntoView({ behavior: reducido ? 'auto' : 'smooth', block: 'center' });
    tarjeta.setAttribute('tabindex', '-1');
    tarjeta.focus({ preventScroll: true });
    tarjeta.classList.remove('mp-resaltado');
    void tarjeta.offsetWidth; // reinicia la animación si ya estaba resaltada
    tarjeta.classList.add('mp-resaltado');
    const temporizador = setTimeout(() => tarjeta.classList.remove('mp-resaltado'), 2200);
    return () => clearTimeout(temporizador);
  }, [aResaltar, filtro]);

  const entregados = datos.filter(esPedidoEntregado).length;
  const conteos: Record<Filtro, number> = { todos: datos.length, en_curso: datos.length - entregados, entregados };
  const filtrados = encontrado ? [encontrado]
    : filtro === 'en_curso' ? datos.filter((d) => !esPedidoEntregado(d))
      : filtro === 'entregados' ? datos.filter(esPedidoEntregado) : datos;
  const n = filtrados.length;
  const sufijo = encontrado ? '' : filtro === 'en_curso' ? ' en curso' : filtro === 'entregados' ? (n === 1 ? ' entregado' : ' entregados') : '';

  return (
    <section id="view-mis-pedidos" className="view active">
      <div className="section-header panel-header-hero pedidos-hero">
        <div>
          <h2><i className="ti ti-package" /> Mis Pedidos</h2>
          <p className="pedidos-hero-sub">Sigue tu compra, tu pago y tu entrega en un solo lugar.</p>
        </div>
        <button type="button" className="btn btn-outline" id="btn-refrescar-pedidos" onClick={() => { void recargar(); }}><i className="ti ti-refresh" /> Refrescar</button>
      </div>

      <div id="pedidos-resumen" className="pedidos-resumen">
        {cargando ? <SkeletonResumen /> : datos.length > 0 && <ResumenPedidos datos={datos} alIrAlPedido={irAlPedido} />}
      </div>

      <div className="pedidos-toolbar">
        <div className="pedidos-tabs" id="pedidos-tabs" role="tablist" aria-label="Filtrar pedidos por estado">
          {([['todos', 'Todos'], ['en_curso', 'En curso'], ['entregados', 'Entregados']] as [Filtro, string][]).map(([valor, etiqueta]) => (
            <button key={valor} type="button" className={`pedidos-tab ${filtro === valor ? 'active' : ''}`} data-filtro={valor} role="tab" aria-selected={filtro === valor} onClick={() => elegirFiltro(valor)}>
              {etiqueta} <span className="pedidos-tab-count" data-conteo={valor}>{conteos[valor]}</span>
            </button>
          ))}
        </div>
        <div className="lookup-box pedidos-buscador">
          <i className="ti ti-search" aria-hidden="true" />
          <input
            type="text"
            id="input-buscar-pedido"
            placeholder="Buscar pedido por ID..."
            aria-label="Buscar pedido por ID"
            autoComplete="off"
            value={textoBusqueda}
            onChange={(e) => setTextoBusqueda(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void buscarPorId(); } }}
          />
          <button type="button" className="btn btn-primary" id="btn-buscar-pedido" onClick={() => { void buscarPorId(); }}>Buscar</button>
        </div>
      </div>
      <p className="mp-solo-lectores" id="pedidos-status" role="status" aria-atomic="true">{!cargando && n > 0 && `Mostrando ${n} pedido${n === 1 ? '' : 's'}${sufijo}.`}</p>

      <div ref={lista} id="pedidos-list" className="pedidos-list" aria-busy={cargando || undefined}>
        {cargando && <SkeletonPedidos n={buscando ? 1 : 2} />}
        {!cargando && data?.habia && !datos.length && <p className="muted">No se pudieron cargar los pedidos.</p>}
        {!cargando && filtrados.map((d, i) => <TarjetaPedido key={d.pedido.id} datos={d} indice={i} catalogo={catalogo} destinos={destinos} alCambiar={() => { void recargar(); }} />)}
      </div>
      {!cargando && !filtrados.length && !(data?.habia && !datos.length) && (
        <PedidosVacios tipo={filtro === 'en_curso' ? 'sin-en-curso' : filtro === 'entregados' ? 'sin-entregados' : 'sin-pedidos'} />
      )}
    </section>
  );
}
