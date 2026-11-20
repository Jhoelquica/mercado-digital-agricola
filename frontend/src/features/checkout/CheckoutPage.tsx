import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, lazy, Suspense, useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent, type RefObject } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { ApiError } from '../../api/errors';
import { pagos, type Pago, type ResultadoCobro } from '../../api/pagos';
import { pedidoPendienteDe, pedidos, type Cotizacion, type DestinoPublico, type Pedido, type PedidoPendiente } from '../../api/pedidos';
import { useAuth } from '../../auth/AuthContext';
import { manejarError, toast } from '../../components/Toast';
import { atraparFoco } from '../../lib/foco';
import { obtenerUbicacionActual } from '../../lib/ubicacion';
import { formatearMoneda } from '../../lib/formato';
import { horaDeLima, pagoSeEstaVerificando, venceDelPedido, type LineaDePedido } from '../../lib/pedidoPendiente';
import { unidadCorta, unidadPlural } from '../../lib/productos';
import { abrirPanelBusqueda } from '../catalogo/menus';
import { MediaProducto } from '../catalogo/TarjetasProducto';
import { useCatalogo } from '../catalogo/useCatalogo';
import { totalPedido } from '../pedidos/estadoPedido';
import { guardarPedidoRecordado } from '../pedidos/pedidosRecordados';
import { BloqueLejano, InfoLejanos, SelectorCiudades, type CotizacionLejana, type Modalidad, type Receptor } from './BloquesLejanos';
import {
  cambiarCantidad, carritoCoincideCon, guardarTelefonoDeEntrega, itemsParaPedido, leerCarrito, leerTelefonoDeEntrega, quitarLinea, subtotalCarrito, suscribirCarrito,
  vaciarCarritoSiCoincide, vincularCarritoAlPedido, type LineaCarrito,
} from './carrito';
import { abrirCulqi, cargarCulqi, type MetodoCulqi } from './culqi';
import { ciudadDelPunto, distanciaKmAprox, estaEnAyacucho, formatearKg, netoDelRepartidor } from './lejanos';
import { ConstanciaRecibida, PanelTransferencia } from './Transferencia';
import type { MapaSeleccionableApi } from '../../components/Mapa';

const MapaSeleccionable = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaSeleccionable })));

export interface PagoPendiente {
  id: string;
  monto: number;
  envio: number;
  parte?: string | null;
  // Lo que tiene el pedido (al pagarlo se vacía el carrito si tiene lo mismo) y hasta cuándo se guarda.
  items?: LineaDePedido[];
  venceEn?: string | null;
}

// Lo que recibe la ruta al abrirse: si hay una página detrás (se puede volver a ella) y, desde Mis Pedidos, el pedido por pagar.
export interface EstadoCheckout {
  conFondo?: boolean;
  reintento?: PagoPendiente;
}

type Metodo = MetodoCulqi | 'transferencia';
type Paso = 1 | 2 | 3;
// Qué hace el botón de pago mientras está ocupado (null: libre).
type FaseDelPago = 'creando' | 'abriendo' | 'confirmando' | null;

const ESPERA_TRAS_REEMPLAZAR_MS = 1500;
const esperar = (ms: number) => new Promise((resolver) => { setTimeout(resolver, ms); });
const lineasDe = (pedido: Pedido): LineaDePedido[] => (pedido.items || []).map((i) => ({ producto_id: i.producto_id, unidad: i.unidad, cantidad: i.cantidad }));

// El pedido del comprador como lo muestra la tarjeta de "pedido sin pagar" (el mismo resumen que trae el 409 de Pedidos).
function resumenDe(pedido: Pedido): PedidoPendiente {
  return {
    id: pedido.id,
    fecha_creacion: pedido.fecha_creacion,
    costo_envio: Number(pedido.costo_envio) || 0,
    total: totalPedido(pedido),
    monto_adelanto: pedido.monto_adelanto != null ? Number(pedido.monto_adelanto) : null,
    modalidad_envio: pedido.modalidad_envio || 'local',
    items: (pedido.items || []).map((i) => ({ producto_id: i.producto_id, producto_nombre: i.producto_nombre, cantidad: i.cantidad, unidad: i.unidad })),
  };
}

// Un solo pedido sin pagar por comprador: con otro carrito, Pedidos responde 409 con ese pedido y aquí se elige pagarlo o
// reemplazarlo (se cancela y su stock vuelve en el acto). Si su pago se está verificando, ninguna de las dos: hay que esperar.
// 21st.dev "Alert with actions" (ícono, título, datos y acciones en un mismo bloque); ui-ux-pro-max: la consecuencia de
// reemplazar escrita antes del botón, el error junto a las acciones (role="alert") y botones de 44 px o más.
function PedidoSinPagar({ resumen, pago, vence, error, reemplazando, titulo, alPagar, alReemplazar, alVerPedidos }: {
  resumen: PedidoPendiente;
  pago: Pago | null;
  vence: Date | null;
  error: string | null;
  reemplazando: boolean;
  titulo: RefObject<HTMLHeadingElement | null>;
  alPagar: () => void;
  alReemplazar: () => void;
  alVerPedidos: () => void;
}) {
  const verificando = pagoSeEstaVerificando(pago?.estado);
  const monto = pago?.monto_a_pagar != null ? Number(pago.monto_a_pagar) : resumen.monto_adelanto ?? resumen.total;
  const productos = resumen.items.map((i) => `${i.producto_nombre || 'Producto'} × ${i.cantidad}`).join(' · ');
  return (
    <section className={`checkout-bloque checkout-pendiente${verificando ? ' es-verificando' : ''}`} id="checkout-pedido-pendiente" aria-labelledby="checkout-pendiente-titulo">
      <h3 id="checkout-pendiente-titulo" ref={titulo} tabIndex={-1}>
        <i className={`ti ti-${verificando ? 'hourglass' : 'receipt'}`} aria-hidden="true" /> {verificando ? 'Tu pago anterior se está verificando' : 'Tienes un pedido sin pagar'}
      </h3>
      <dl className="checkout-pendiente-datos">
        <div><dt>Pedido</dt><dd>#{resumen.id.slice(0, 8)}</dd></div>
        <div><dt>{resumen.monto_adelanto != null ? 'Adelanto por pagar' : 'Total'}</dt><dd><strong>{formatearMoneda(monto)}</strong></dd></div>
        {vence && !verificando && <div><dt>Guardado hasta</dt><dd><strong>{horaDeLima(vence)}</strong> (hora de Lima)</dd></div>}
        <div className="checkout-pendiente-productos"><dt>Productos</dt><dd>{productos}</dd></div>
      </dl>
      <p className="checkout-pendiente-nota">
        {verificando
          ? 'Cuando se confirme podrás hacer otro pedido. Míralo en Mis pedidos.'
          : 'Sus productos siguen reservados para ti. Págalo, o reemplázalo por lo que tienes ahora en el carrito: ese pedido se cancela y sus productos vuelven al catálogo.'}
      </p>
      {error && <p className="checkout-pendiente-error" role="alert"><i className="ti ti-alert-circle" aria-hidden="true" /> {error}</p>}
      <div className="checkout-pendiente-acciones">
        {verificando ? (
          <button type="button" className="btn btn-outline" onClick={alVerPedidos}>Ver mis pedidos</button>
        ) : (
          <>
            <button type="button" className="btn btn-primary btn-pagar-pendiente" disabled={reemplazando} onClick={alPagar}>Pagar ese pedido</button>
            <button type="button" className="btn btn-outline btn-reemplazar-pendiente" disabled={reemplazando} aria-busy={reemplazando} onClick={alReemplazar}>
              {reemplazando ? <><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Reemplazando…</> : 'Reemplazarlo por este carrito'}
            </button>
          </>
        )}
      </div>
    </section>
  );
}

type Final =
  | { tipo: 'resultado'; pedidoId: string; monto: number; envio: number; resultado: ResultadoCobro | null; errorMsg: string | null }
  | { tipo: 'transferencia'; pedidoId: string; monto: number }
  | { tipo: 'constancia'; pedidoId: string; numeroOperacion: string };

function Stepper({ paso }: { paso: Paso }) {
  const etiquetas = ['Resumen', 'Pago', 'Confirmación'];
  return (
    <div className="checkout-stepper">
      {etiquetas.map((etiqueta, i) => {
        const n = i + 1;
        return (
          <Fragment key={etiqueta}>
            {i > 0 && <div className="checkout-step-line" />}
            <div className={`checkout-step ${n === paso ? 'active' : ''} ${n < paso ? 'completado' : ''}`} data-step={n} aria-current={n === paso ? 'step' : undefined}>
              <span className="checkout-step-circle">{n < paso ? <i className="ti ti-check" /> : n}</span>
              <span className="checkout-step-label">{etiqueta}</span>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

function LineaDelCarrito({ linea, catalogo }: { linea: LineaCarrito; catalogo: ReturnType<typeof useCatalogo>['data'] }) {
  const { nombre, producto_id: id, unidad } = linea;
  const producto = catalogo?.find((p) => p.id === id) || { nombre, categoria: null };
  return (
    <article className="carrito-item">
      <MediaProducto producto={producto} clase="carrito-item-media" />
      <div className="carrito-item-info">
        <strong>{nombre}</strong>
        <span>{formatearMoneda(linea.precio)} / {unidadCorta(linea.unidad_medida)}</span>
      </div>
      <div className="carrito-qty" role="group" aria-label={`Cantidad de ${nombre}`}>
        <button type="button" aria-label={`Quitar una unidad de ${nombre}`} onClick={() => cambiarCantidad(id, -1, unidad)}>−</button>
        <span>{linea.cantidad} {unidadPlural(linea.unidad_medida, linea.cantidad)}</span>
        <button type="button" aria-label={`Agregar una unidad de ${nombre}`} onClick={() => cambiarCantidad(id, 1, unidad)}>+</button>
      </div>
      <strong className="carrito-item-subtotal">{formatearMoneda(linea.precio * linea.cantidad)}</strong>
      <button type="button" className="carrito-remove" aria-label={`Quitar ${nombre} del pedido`} onClick={() => quitarLinea(id, unidad)}><i className="ti ti-trash" aria-hidden="true" /></button>
    </article>
  );
}

function AyudaDestino({ destino, destinos, pidioAyacucho }: { destino: { lat: number; lng: number } | null; destinos: DestinoPublico[]; pidioAyacucho: boolean }) {
  if (!destino) {
    return <p className="mapa-ayuda" id="mapa-destino-ayuda">{pidioAyacucho ? 'Ahora toca el mapa en tu dirección exacta: el repartidor llega a tu puerta.' : 'Toca el mapa para marcar dónde quieres recibir tu pedido.'}</p>;
  }
  const { lat, lng } = destino;
  if (estaEnAyacucho(lat, lng)) {
    return <p className="mapa-ayuda confirmado" id="mapa-destino-ayuda"><i className="ti ti-map-pin" /> Destino marcado ({lat.toFixed(5)}, {lng.toFixed(5)})</p>;
  }
  const cerca = destinos.find((d) => distanciaKmAprox(d.latitud, d.longitud, lat, lng) <= d.radio_km);
  if (cerca) {
    return <p className="mapa-ayuda confirmado" id="mapa-destino-ayuda"><i className="ti ti-truck-delivery" aria-hidden="true" /> Tu punto está en <strong>{cerca.ciudad}</strong>: en el siguiente paso eliges si llega por agencia o en camión.</p>;
  }
  // Si quedó fuera, a cuánto está de la ciudad más cercana y hasta dónde se llega ahí (el mismo límite que Pedidos).
  const masCerca = destinos.map((d) => ({ d, km: distanciaKmAprox(d.latitud, d.longitud, lat, lng) })).sort((a, b) => a.km - b.km)[0];
  return (
    <p className="mapa-ayuda fuera" id="mapa-destino-ayuda">
      <i className="ti ti-map-pin-off" aria-hidden="true" /> Todavía no llegamos a ese punto. Entregamos en la región de Ayacucho{destinos.length > 0 && ` y en ${destinos.map((d) => d.ciudad).join(', ')}`}.
      {masCerca && ` Tu punto está a ${Math.round(masCerca.km)} km de ${masCerca.d.ciudad}, y ahí entregamos hasta ${Math.round(masCerca.d.radio_km)} km del centro.`}
    </p>
  );
}

function ResultadoDelPago({ final }: { final: Extract<Final, { tipo: 'resultado' }> }) {
  const { pedidoId, monto, envio, resultado, errorMsg } = final;
  const idCorto = pedidoId.slice(0, 8);
  if (resultado?.estado === 'aprobado') {
    return (
      <>
        <span className="checkout-exito-icono"><i className="ti ti-check" aria-hidden="true" /></span>
        <h3>¡Pago aprobado!</h3>
        <p>Tu pedido <strong>#{idCorto}</strong> está confirmado.</p>
        <div className="checkout-exito-total"><span>Total pagado</span><strong>{formatearMoneda(monto)}</strong>{envio > 0 && <small>Incluye {formatearMoneda(envio)} de envío</small>}</div>
        <ol className="checkout-siguientes">
          <li><i className="ti ti-receipt" aria-hidden="true" /><div><strong>Recibimos tu pedido</strong><span>Ya está registrado y pagado.</span></div></li>
          <li><i className="ti ti-truck-delivery" aria-hidden="true" /><div><strong>Un repartidor lo lleva a tu punto</strong><span>Te llamará al teléfono que indicaste para coordinar.</span></div></li>
          <li><i className="ti ti-key" aria-hidden="true" /><div><strong>Confirma la entrega con tu código</strong><span>Lo verás en Mis Pedidos cuando el pedido salga.</span></div></li>
        </ol>
      </>
    );
  }
  const pareceJson = errorMsg && (errorMsg.trim().startsWith('[') || errorMsg.trim().startsWith('{'));
  const motivo = errorMsg && !pareceJson ? errorMsg : `El pago quedó en estado "${resultado?.estado || 'pendiente'}".`;
  // Al reintentar el pago de un pedido rechazado, el servidor vuelve a reservar su stock: si ya no alcanza responde
  // "... Crea un nuevo pedido." y reintentar más tarde no serviría de nada.
  const sinReintento = /crea un nuevo pedido/i.test(errorMsg || '');
  return (
    <>
      <span className="checkout-exito-icono es-alerta"><i className="ti ti-alert-triangle" aria-hidden="true" /></span>
      <h3>No se pudo confirmar el pago</h3>
      <p>{motivo}</p>
      {sinReintento
        ? <p>Tu pedido <strong>#{idCorto}</strong> ya no se puede pagar. Vuelve al catálogo y crea uno nuevo.</p>
        : <p>Tu pedido <strong>#{idCorto}</strong> quedó registrado — puedes reintentar el pago más tarde.</p>}
    </>
  );
}

export default function CheckoutPage() {
  const { sesion } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const estadoInicial = (location.state ?? {}) as EstadoCheckout;
  const conFondo = Boolean(estadoInicial.conFondo);

  const carrito = useSyncExternalStore(suscribirCarrito, leerCarrito);
  const { data: catalogo } = useCatalogo();
  const { data: condicionesCompra } = useQuery({ queryKey: ['condiciones-compra'], queryFn: pedidos.condiciones });
  const { data: condicionesPago } = useQuery({ queryKey: ['condiciones-pago'], queryFn: pagos.condiciones });
  const { data: destinosCargados } = useQuery({ queryKey: ['destinos-lejanos'], queryFn: pedidos.destinosLejanos });
  const destinos = destinosCargados ?? [];

  const [paso, setPaso] = useState<Paso>(estadoInicial.reintento ? 2 : 1);
  const [telefono, setTelefono] = useState(leerTelefonoDeEntrega);
  const [destino, setDestino] = useState<{ lat: number; lng: number } | null>(null);
  const [mapaResaltado, setMapaResaltado] = useState(false);
  const [pidioAyacucho, setPidioAyacucho] = useState(false);
  const [buscandoUbicacion, setBuscandoUbicacion] = useState(false);
  const [cotizando, setCotizando] = useState(false);
  const [cotizacion, setCotizacion] = useState<Cotizacion | null>(null);
  const [vecesCotizado, setVecesCotizado] = useState(0);
  // Lo que se confirmó al pasar al paso 2: el carrito se vacía al crear el pedido, pero el resumen sigue a la vista.
  const [recap, setRecap] = useState<LineaCarrito[]>([]);
  const [telefonoConfirmado, setTelefonoConfirmado] = useState('');
  const [modalidad, setModalidad] = useState<Modalidad | null>(null);
  const [receptor, setReceptor] = useState<Receptor | null>(null);
  const [metodo, setMetodo] = useState<Metodo>('tarjeta');
  const [fase, setFase] = useState<FaseDelPago>(null);
  const enviandoPago = fase !== null;
  const [final, setFinal] = useState<Final | null>(null);

  const reintento = estadoInicial.reintento ?? null;
  // Pedido por pagar: el que vino de Mis Pedidos o el que acaba de crearse (o retomarse) aquí. Mientras exista, el botón
  // dice "Volver a pagar" y reabre el cobro de ESE pedido en vez de crear otro. pedidoActivo es lo mismo para pintar.
  const pedidoPorPagar = useRef<PagoPendiente | null>(reintento);
  const [pedidoActivo, setPedidoActivo] = useState<PagoPendiente | null>(reintento);
  const [vence, setVence] = useState<Date | null>(() => venceDelPedido(reintento?.venceEn, null, null));
  // El comprador cerró la ventana de Culqi sin pagar (Culqi avisó con closeCheckout).
  const [cobroInterrumpido, setCobroInterrumpido] = useState(false);
  // El pedido sin pagar que frenó a este carrito (409 de Pedidos), para pagarlo o reemplazarlo.
  const [pendiente, setPendiente] = useState<{ resumen: PedidoPendiente; pago: Pago | null } | null>(null);
  const [errorPendiente, setErrorPendiente] = useState<string | null>(null);
  const [reemplazando, setReemplazando] = useState(false);
  // Se está pagando ese otro pedido (no el del carrito): el resumen muestra ese pedido, como un reintento.
  const [pagandoOtroPedido, setPagandoOtroPedido] = useState(false);
  const pagoEnProceso = useRef(false); // bloquea el segundo clic antes de que React llegue a deshabilitar el botón
  const cotizandoRef = useRef(false);
  const mapa = useRef<MapaSeleccionableApi>(null);
  const modal = useRef<HTMLDivElement>(null);
  const cuerpo = useRef<HTMLDivElement>(null);
  const campoTelefono = useRef<HTMLInputElement>(null);
  const botonPagar = useRef<HTMLButtonElement>(null);
  const tituloPendiente = useRef<HTMLHeadingElement>(null);

  const lineas = carrito.length;
  const subtotal = subtotalCarrito(carrito);
  const montoMinimo = Number.isFinite(condicionesCompra?.monto_minimo) ? (condicionesCompra as { monto_minimo: number }).monto_minimo : null;
  const faltaParaMinimo = montoMinimo === null ? 0 : Math.round((montoMinimo - subtotal) * 100) / 100;

  useEffect(() => { void cargarCulqi(); }, []);

  // El foco entra al modal y vuelve a donde estaba al cerrarlo.
  useEffect(() => {
    const previo = document.activeElement as HTMLElement | null;
    requestAnimationFrame(() => modal.current?.focus());
    return () => { if (previo?.isConnected) previo.focus?.(); };
  }, []);

  // Cada paso arranca desde arriba.
  useEffect(() => { if (cuerpo.current) cuerpo.current.scrollTop = 0; }, [paso]);

  function cerrar() {
    if (conFondo) void navigate(-1);
    else void navigate('/', { replace: true });
  }

  function alCambiarTelefono(valor: string) {
    setTelefono(valor);
    guardarTelefonoDeEntrega(valor);
  }

  // Mismo criterio que valida Pedidos: celular de 9 dígitos que empieza con 9.
  function telefonoValido(): string | null {
    const valor = telefono.replace(/\s+/g, '');
    if (!/^9\d{8}$/.test(valor)) {
      toast('Ingresa un teléfono de contacto válido: 9 dígitos, empieza con 9 (ej. 987654321).', 'error');
      campoTelefono.current?.focus();
      return null;
    }
    return valor;
  }

  function resaltarMapa() {
    setMapaResaltado(true);
    verMapa();
  }

  function verMapa() {
    document.getElementById('mapa-destino-pedido')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function marcarDestino(lat: number, lng: number) {
    setDestino({ lat, lng });
    setMapaResaltado(false);
    setPidioAyacucho(false);
  }

  function elegirCiudad({ latitud, longitud, local }: { latitud: number; longitud: number; local: boolean }) {
    if (local) {
      mapa.current?.verPunto(latitud, longitud, 13);
      setPidioAyacucho(true);
      return;
    }
    mapa.current?.moverMarcador(latitud, longitud);
    mapa.current?.cambiarZoom(11);
    marcarDestino(latitud, longitud);
  }

  async function usarMiUbicacion() {
    setBuscandoUbicacion(true);
    try {
      const { lat, lng } = await obtenerUbicacionActual();
      mapa.current?.moverMarcador(lat, lng);
      marcarDestino(lat, lng);
    } catch (err) {
      toast((err as Error).message, 'error');
    } finally {
      setBuscandoUbicacion(false);
    }
  }

  function modalidadPorDefecto(c: CotizacionLejana, actual: Modalidad | null): Modalidad | null {
    const agenciaOk = Boolean(c.opciones.agencia) && !c.opciones.agencia?.motivo_no_disponible;
    const camionOk = Boolean(c.opciones.camion) && !c.opciones.camion?.motivo_no_disponible;
    if (actual === 'agencia' && agenciaOk) return actual;
    if (actual === 'camion' && camionOk) return actual;
    return camionOk ? 'camion' : agenciaOk ? 'agencia' : null;
  }

  async function irAPaso2() {
    if (!lineas || cotizandoRef.current) return;
    const telefonoLimpio = telefonoValido();
    if (!telefonoLimpio) return;
    if (!destino) {
      toast('Marca el punto de entrega en el mapa antes de continuar.', 'error');
      resaltarMapa();
      return;
    }
    // El envío depende de la distancia y del peso, y lo calcula el servidor: se cotiza con el carrito para que el total
    // que ve el comprador sea el mismo que se cobra.
    cotizandoRef.current = true;
    setCotizando(true);
    try {
      const c = await pedidos.cotizar({ destino_latitud: destino.lat, destino_longitud: destino.lng, items: itemsParaPedido(carrito) });
      setCotizacion(c);
      setVecesCotizado((v) => v + 1);
      if (c.tipo === 'lejano') setModalidad((actual) => modalidadPorDefecto(c, actual));
      setRecap(carrito);
      setTelefonoConfirmado(telefonoLimpio);
      // Desde el paso 1 el carrito o la entrega pudieron cambiar: el pedido por pagar lo vuelve a decidir Pedidos (el mismo
      // carrito retoma el pedido sin pagar; otro, 409 con ese pedido).
      olvidarPedido();
      setPendiente(null);
      setErrorPendiente(null);
      setPaso(2);
    } catch (err) {
      manejarError(err, 'calcular el envío');
      if (err instanceof ApiError && err.status === 400) resaltarMapa(); // destino fuera de la región de entrega
    } finally {
      cotizandoRef.current = false;
      setCotizando(false);
    }
  }

  // ---- Lo que se muestra en el paso 2 ----
  // Un pedido que no es el del carrito (vino de Mis pedidos, o es el pendiente que eligió pagar): se muestra ese pedido.
  const pedidoAjeno = reintento ?? (pagandoOtroPedido ? pedidoActivo : null);
  const cotizacionLejana = !pedidoAjeno && cotizacion?.tipo === 'lejano' ? cotizacion : null;
  const cotizacionLocal = !pedidoAjeno && cotizacion?.tipo === 'local' ? cotizacion : null;
  const opcionLejana = cotizacionLejana && modalidad ? cotizacionLejana.opciones[modalidad] : null;
  const opcionCamion = modalidad === 'camion' ? cotizacionLejana?.opciones.camion ?? null : null;
  const opcionAgencia = modalidad === 'agencia' ? cotizacionLejana?.opciones.agencia ?? null : null;

  let envioMostrado = 0;
  let subtotalMostrado = subtotalCarrito(recap);
  let etiquetaEnvio = 'Envío';
  let etiquetaTotal = 'Total a pagar';
  let totalAPagar = 0;
  let sinDesglose = false; // un pago parcial (camión) muestra solo lo que toca pagar ahora
  if (pedidoAjeno) {
    envioMostrado = pedidoAjeno.envio;
    subtotalMostrado = pedidoAjeno.monto - pedidoAjeno.envio;
    totalAPagar = pedidoAjeno.monto;
    if (pedidoAjeno.parte === 'adelanto' || pedidoAjeno.parte === 'saldo') {
      sinDesglose = true;
      etiquetaTotal = pedidoAjeno.parte === 'saldo' ? 'Saldo (30%)' : 'Adelanto (70%)';
    }
  } else if (cotizacionLejana) {
    subtotalMostrado = Number(cotizacionLejana.subtotal) || 0;
    envioMostrado = Number(opcionLejana?.costo_envio) || 0;
    totalAPagar = subtotalMostrado + envioMostrado;
    if (opcionCamion?.adelanto != null) totalAPagar = Number(opcionCamion.adelanto);
    if (modalidad === 'camion') {
      etiquetaEnvio = `Camión · ${opcionCamion?.sacos ?? '—'} sacos`;
      etiquetaTotal = 'Pagas ahora';
    } else if (modalidad === 'agencia') {
      etiquetaEnvio = 'Traslado a la agencia';
    }
  } else if (cotizacionLocal) {
    envioMostrado = Number(cotizacionLocal.costo_envio) || 0;
    totalAPagar = subtotalMostrado + envioMostrado;
    if (Number.isFinite(Number(cotizacionLocal.distancia_km))) etiquetaEnvio = `Envío · ${Number(cotizacionLocal.distancia_km).toFixed(1)} km`;
  }
  const hayOpcionEnLejano = !cotizacionLejana || Boolean(opcionLejana);
  const etiquetaDelBoton = fase === 'creando' ? 'Enviando pedido...'
    : fase === 'abriendo' ? 'Abriendo el pago...'
      : fase === 'confirmando' ? 'Confirmando tu pago...'
        : pedidoActivo ? 'Volver a pagar' : 'Confirmar y pagar';

  // Yape permite hasta S/ 500 por operación (el límite lo da Pagos, que también lo exige): con un total mayor la opción se
  // apaga y se dice por qué, en vez de dejar que la pasarela lo rechace después.
  const yapeMaximo = condicionesPago?.yape_maximo_por_operacion;
  const hayTransferencia = Boolean(condicionesPago?.transferencia);
  const yapeExcedido = yapeMaximo != null && totalAPagar > yapeMaximo;

  useEffect(() => {
    if (yapeExcedido && metodo === 'yape') setMetodo('tarjeta');
  }, [yapeExcedido, metodo]);

  // ---- Pagar ----
  function resetearBotonPago() {
    pagoEnProceso.current = false;
    setFase(null);
  }

  // El pedido que se va a pagar (creado, retomado o el pendiente que eligió pagar): desde aquí el botón dice "Volver a
  // pagar" y el aviso dice hasta qué hora se guarda (en hora de Lima).
  function activarPedido(p: PagoPendiente, fechaCreacion: string | null = null) {
    pedidoPorPagar.current = p;
    setPedidoActivo(p);
    setCobroInterrumpido(false);
    setVence(venceDelPedido(p.venceEn, fechaCreacion, condicionesPago?.plazo_para_pagar_minutos));
    if (!p.venceEn) {
      // El plazo exacto es el del pago, que Pagos crea con el aviso del pedido nuevo (unos instantes después).
      void pagos.obtener(p.id).then((pago) => {
        const exacto = venceDelPedido(pago.vence_en, null, null);
        if (exacto && pedidoPorPagar.current?.id === p.id) setVence(exacto);
      }).catch(() => { /* se queda el calculado */ });
    }
  }

  function olvidarPedido() {
    if (reintento) return; // el pedido que vino de Mis pedidos es el que se paga aquí
    pedidoPorPagar.current = null;
    setPedidoActivo(null);
    setPagandoOtroPedido(false);
    setCobroInterrumpido(false);
    setVence(null);
  }

  async function procesarPagoCulqi(tokenId: string, pago: PagoPendiente, metodoCulqi: MetodoCulqi) {
    pagoEnProceso.current = true;
    setFase('confirmando');
    setCobroInterrumpido(false);
    let resultado: ResultadoCobro | null = null;
    let errorMsg: string | null = null;
    try {
      resultado = await pagos.procesar({ pedido_id: pago.id, token_culqi: tokenId, email: sesion.email || '', metodo: metodoCulqi });
    } catch (err) {
      errorMsg = (err as Error).message;
    }
    // El carrito se vacía con el pago confirmado (no al crear el pedido), y solo si tiene lo mismo que ese pedido.
    if (resultado && ['aprobado', 'adelanto_pagado'].includes(resultado.estado || '')) vaciarCarritoSiCoincide(pago.items);
    void queryClient.invalidateQueries({ queryKey: ['mis-pedidos'] });
    setFinal({ tipo: 'resultado', pedidoId: pago.id, monto: pago.monto, envio: pago.envio, resultado, errorMsg });
    resetearBotonPago();
    setPaso(3);
  }

  async function cobrar(pago: PagoPendiente, metodoElegido: Metodo) {
    if (metodoElegido === 'transferencia') {
      resetearBotonPago();
      if (!condicionesPago?.transferencia) {
        toast('El pago por transferencia no está disponible ahora. Paga tu pedido con tarjeta desde Mis Pedidos.', 'error');
        return;
      }
      setFinal({ tipo: 'transferencia', pedidoId: pago.id, monto: pago.monto });
      setPaso(3);
      return;
    }
    setFase('abriendo');
    const abierto = await abrirCulqi({
      monto: pago.monto,
      metodo: metodoElegido,
      alToken: (tokenId) => { void procesarPagoCulqi(tokenId, pago, metodoElegido); },
      alError: (mensaje) => {
        toast(mensaje, 'error');
        resetearBotonPago();
      },
      // Cerró la ventana sin pagar: el pedido sigue guardado y el botón ya dice "Volver a pagar".
      alCerrar: () => {
        setCobroInterrumpido(true);
        resetearBotonPago();
        requestAnimationFrame(() => botonPagar.current?.focus());
      },
    });
    if (!abierto) {
      toast('No se pudo cargar la pasarela de pagos. Toca «Volver a pagar» en un momento o paga tu pedido desde Mis pedidos.', 'error');
    }
    // Culqi puede cerrar su ventana sin avisar (ver culqi.ts): el botón vuelve a servir apenas se abre, y tocarlo reabre
    // el cobro de este mismo pedido (Pagos ya impide dos cobros a la vez).
    resetearBotonPago();
  }

  // POST /pedidos. Si se acaba de reemplazar el pendiente y Pedidos todavía lo ve (lo cancela el aviso de Pagos cuando la
  // llamada directa no llegó), se espera un momento y se reintenta.
  async function crearPedido(reemplazado: string | null): Promise<Pedido> {
    const esLejano = cotizacion?.tipo === 'lejano';
    const datos = {
      comprador_nombre: sesion.nombre || 'Cliente Chakra Shop',
      comprador_telefono: telefono.replace(/\s+/g, ''),
      destino_latitud: destino!.lat,
      destino_longitud: destino!.lng,
      items: itemsParaPedido(carrito),
      ...(esLejano && modalidad && receptor ? { modalidad_envio: modalidad, receptor_dni: receptor.dni } : {}),
    };
    for (let intento = 0; ; intento++) {
      try {
        return await pedidos.crear(datos);
      } catch (err) {
        if (reemplazado && intento < 3 && pedidoPendienteDe(err)?.id === reemplazado) {
          await esperar(ESPERA_TRAS_REEMPLAZAR_MS);
          continue;
        }
        throw err;
      }
    }
  }

  async function mostrarPendiente(resumen: PedidoPendiente, pago?: Pago | null) {
    const suPago = pago !== undefined ? pago : await pagos.obtener(resumen.id).catch(() => null);
    setPendiente({ resumen, pago: suPago });
    requestAnimationFrame(() => {
      document.getElementById('checkout-pedido-pendiente')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      tituloPendiente.current?.focus({ preventScroll: true });
    });
  }

  async function pagarElPendiente() {
    if (!pendiente || pagoEnProceso.current) return;
    const { resumen, pago } = pendiente;
    const monto = pago?.monto_a_pagar != null ? Number(pago.monto_a_pagar) : resumen.monto_adelanto ?? resumen.total;
    activarPedido({ id: resumen.id, monto, envio: resumen.costo_envio, parte: pago?.parte_a_pagar, items: resumen.items, venceEn: pago?.vence_en }, resumen.fecha_creacion);
    setPagandoOtroPedido(!carritoCoincideCon(resumen.items));
    setPendiente(null);
    setErrorPendiente(null);
    pagoEnProceso.current = true;
    await cobrar(pedidoPorPagar.current!, metodo);
  }

  async function reemplazarPendiente() {
    if (!pendiente || reemplazando || pagoEnProceso.current) return;
    const { resumen } = pendiente;
    setReemplazando(true);
    setErrorPendiente(null);
    try {
      await pagos.cancelarPedido(resumen.id);
    } catch (err) {
      setReemplazando(false);
      setErrorPendiente((err as Error).message || 'No se pudo reemplazar el pedido. Intenta de nuevo.');
      // Puede que su pago se esté verificando: la tarjeta lo muestra con su estado de ahora.
      if (err instanceof ApiError && err.status === 409) void mostrarPendiente(resumen);
      return;
    }
    toast(`Cancelamos el pedido #${resumen.id.slice(0, 8)}: sus productos volvieron al catálogo.`);
    void queryClient.invalidateQueries({ queryKey: ['mis-pedidos'] });
    setPendiente(null);
    setReemplazando(false);
    await confirmarYPagar(resumen.id);
  }

  async function confirmarYPagar(reemplazado: string | null = null) {
    if (pagoEnProceso.current) return;

    // Ya hay un pedido: solo falta cobrarlo.
    if (pedidoPorPagar.current) {
      pagoEnProceso.current = true;
      setFase('abriendo');
      await cobrar(pedidoPorPagar.current, metodo);
      return;
    }

    if (!telefonoValido()) {
      setPaso(1);
      return;
    }
    if (!destino) {
      toast('Selecciona el punto de entrega en el mapa antes de confirmar tu pedido.', 'error');
      resaltarMapa();
      return;
    }
    const esLejano = cotizacion?.tipo === 'lejano';
    if (esLejano && !modalidad) {
      toast('Elige cómo llega tu pedido: por agencia o en camión.', 'error');
      document.getElementById('checkout-bloque-lejano')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (esLejano && !receptor) {
      toast('Verifica en RENIEC el DNI de quien recibe y confírmalo.', 'error');
      document.getElementById('receptor-dni')?.focus();
      return;
    }

    pagoEnProceso.current = true;
    setFase('creando');
    setPendiente(null);
    setErrorPendiente(null);
    try {
      // El carrito NO se vacía aquí: si el pago no se completa, sigue igual y volver a pagar con él retoma este pedido.
      const pedido = await crearPedido(reemplazado);
      guardarPedidoRecordado(sesion.usuarioId, pedido.id);
      vincularCarritoAlPedido(pedido.id);
      // El stock recién bajó: el menú de categorías y la búsqueda no deben seguir mostrando el anterior.
      void queryClient.invalidateQueries({ queryKey: ['catalogo'] });
      void queryClient.invalidateQueries({ queryKey: ['mis-pedidos'] });

      // El envío lo calcula el servidor; Pagos cobra subtotal + envío, así que Culqi y la confirmación deben mostrar eso.
      const envio = Number(pedido.costo_envio) || 0;
      if (pedido.reutilizado) {
        // El mismo carrito con un pedido sin pagar: es ese pedido. Si su pago se está verificando, no se abre Culqi.
        const suPago = await pagos.obtener(pedido.id).catch(() => null);
        if (suPago && pagoSeEstaVerificando(suPago.estado)) {
          resetearBotonPago();
          await mostrarPendiente(resumenDe(pedido), suPago);
          return;
        }
        toast(`Seguimos con tu pedido #${pedido.id.slice(0, 8)}, que estaba sin pagar: sus productos ya estaban reservados.`);
      } else if (Math.abs(envio - envioMostrado) > 0.005) {
        toast(`El costo de envío cambió a ${formatearMoneda(envio)}. Revisa el monto antes de pagar.`);
      }
      // En camión se cobra ahora solo el adelanto (el resto, al llegar).
      const ahora = pedido.monto_adelanto != null ? Number(pedido.monto_adelanto) : totalPedido(pedido);
      activarPedido({ id: pedido.id, monto: ahora, envio, items: lineasDe(pedido) }, pedido.fecha_creacion);
      await cobrar(pedidoPorPagar.current!, metodo);
    } catch (err) {
      resetearBotonPago();
      const otro = pedidoPendienteDe(err);
      if (otro) {
        await mostrarPendiente(otro);
        return;
      }
      manejarError(err, 'crear el pedido');
    }
  }

  function alTocarOverlay(e: MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget) cerrar();
  }

  function alTeclear(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault();
      cerrar();
      return;
    }
    atraparFoco(e);
  }

  const ciudadMarcada = pidioAyacucho ? 'Ayacucho' : destino ? ciudadDelPunto(destinos, destino.lat, destino.lng) : undefined;

  return (
    <div className="modal-overlay" id="modal-pedido" onClick={alTocarOverlay} onKeyDown={alTeclear}>
      <div ref={modal} className="modal modal-checkout" role="dialog" aria-modal="true" aria-labelledby="checkout-titulo" tabIndex={-1}>
        <div className="checkout-cabecera">
          <h2 className="mp-solo-lectores" id="checkout-titulo">Tu pedido</h2>
          <Stepper paso={paso} />
          <button type="button" className="modal-close" aria-label="Cerrar" onClick={cerrar}><i className="ti ti-x" /></button>
        </div>

        <div ref={cuerpo} className="checkout-cuerpo">
          {/* PASO 1: RESUMEN */}
          <div className={`checkout-panel ${paso === 1 ? 'active' : ''}`} id="checkout-panel-1">
            <div className="checkout-grid">
              <div className="checkout-principal">
                <section className="checkout-bloque">
                  <h3><i className="ti ti-basket" /> Tu pedido <span className="checkout-conteo" id="carrito-conteo">{lineas ? `(${lineas})` : ''}</span></h3>
                  <div id="carrito-items" className="carrito-items">
                    {carrito.map((l) => <LineaDelCarrito key={`${l.producto_id}-${l.unidad || ''}`} linea={l} catalogo={catalogo} />)}
                  </div>
                  {!lineas && (
                    <div id="carrito-vacio" className="empty-state">
                      <i className="ti ti-basket empty-state-icon" aria-hidden="true" />
                      <strong>Tu carrito está vacío</strong>
                      <p>Agrega productos desde el catálogo.</p>
                      <button type="button" className="btn btn-primary" id="btn-carrito-explorar" onClick={() => { cerrar(); abrirPanelBusqueda('', { explorar: true }); }}>Explorar el catálogo</button>
                    </div>
                  )}
                </section>

                <section className={`checkout-bloque ${lineas ? '' : 'hidden'}`} id="checkout-bloque-datos">
                  <h3><i className="ti ti-truck-delivery" /> Datos de entrega</h3>
                  <label className="checkout-campo">Teléfono de contacto (obligatorio)
                    <input ref={campoTelefono} type="tel" id="pedido-telefono" inputMode="numeric" maxLength={9} pattern="9[0-9]{8}" placeholder="Ej. 987654321" autoComplete="tel-national" required value={telefono} onChange={(e) => alCambiarTelefono(e.target.value)} />
                    <small className="muted">Lo usa el repartidor para coordinar la entrega contigo. 9 dígitos, empieza con 9.</small>
                  </label>
                  <div className="mapa-selector-bloque">
                    <div className="ciudad-selector-bloque" id="checkout-ciudades">
                      <SelectorCiudades destinos={destinos} marcada={ciudadMarcada} alElegir={elegirCiudad} />
                    </div>
                    <div className="mapa-selector-label">
                      <span><i className="ti ti-map-pin" /> Punto de entrega (obligatorio)</span>
                      <button type="button" className="btn btn-ghost btn-sm" id="btn-usar-mi-ubicacion" disabled={buscandoUbicacion} onClick={() => { void usarMiUbicacion(); }}>{buscandoUbicacion ? 'Obteniendo ubicación...' : 'Usar mi ubicación'}</button>
                    </div>
                    <Suspense fallback={<div className="mapa-seleccionable" />}>
                        <MapaSeleccionable ref={mapa} id="mapa-destino-pedido" clase={`mapa-seleccionable ${mapaResaltado ? 'destino-requerido' : ''}`} alSeleccionar={marcarDestino} />
                    </Suspense>
                    <AyudaDestino destino={destino} destinos={destinos} pidioAyacucho={pidioAyacucho} />
                  </div>
                  <div className="lejanos-info" id="checkout-lejanos-info">
                    <InfoLejanos
                      destinos={destinos}
                      alVerCiudad={(lat, lng) => {
                        mapa.current?.verPunto(lat, lng, 12);
                        verMapa();
                      }}
                    />
                  </div>
                </section>
              </div>

              <aside className="checkout-resumen" aria-label="Resumen del pedido">
                <h3>Resumen</h3>
                <div className="checkout-resumen-fila"><span>Productos</span><span id="carrito-resumen-cantidad">{lineas}</span></div>
                <div className="carrito-total">
                  <span>Subtotal</span>
                  <strong id="carrito-total">{formatearMoneda(subtotal)}</strong>
                </div>
                <p className="checkout-resumen-nota"><i className="ti ti-truck-delivery" aria-hidden="true" /><span>El envío se calcula según la distancia a tu punto de entrega. Lo verás en el siguiente paso, antes de pagar.</span></p>
                <p className={`checkout-minimo ${lineas && faltaParaMinimo > 0 ? '' : 'hidden'}`} id="checkout-minimo-aviso" role="status">
                  <i className="ti ti-info-circle" aria-hidden="true" />
                  <span id="checkout-minimo-texto">{montoMinimo !== null && faltaParaMinimo > 0 && `La compra mínima es de ${formatearMoneda(montoMinimo)}: te faltan ${formatearMoneda(faltaParaMinimo)}. Agrega más productos para continuar.`}</span>
                </p>
                <button type="button" className="btn btn-primary btn-block" id="btn-checkout-paso1-siguiente" aria-describedby="checkout-minimo-aviso" disabled={!lineas || faltaParaMinimo > 0 || cotizando} onClick={() => { void irAPaso2(); }}>{cotizando ? 'Calculando envío...' : 'Continuar al pago →'}</button>
                <ul className="checkout-garantias">
                  <li><i className="ti ti-lock" aria-hidden="true" /><span>Pago seguro con Culqi</span></li>
                  <li><i className="ti ti-truck-delivery" aria-hidden="true" /><span>Un repartidor lleva tu pedido</span></li>
                  <li><i className="ti ti-shield-check" aria-hidden="true" /><span>Origen verificado de cada producto</span></li>
                </ul>
              </aside>
            </div>
          </div>

          {/* PASO 2: MÉTODO DE PAGO */}
          <div className={`checkout-panel ${paso === 2 ? 'active' : ''}`} id="checkout-panel-2">
            <div className="checkout-grid">
              <div className="checkout-principal">
                {pendiente && (
                  <PedidoSinPagar
                    resumen={pendiente.resumen}
                    pago={pendiente.pago}
                    vence={venceDelPedido(pendiente.pago?.vence_en, pendiente.resumen.fecha_creacion, condicionesPago?.plazo_para_pagar_minutos)}
                    error={errorPendiente}
                    reemplazando={reemplazando}
                    titulo={tituloPendiente}
                    alPagar={() => { void pagarElPendiente(); }}
                    alReemplazar={() => { void reemplazarPendiente(); }}
                    alVerPedidos={() => { void navigate('/comprador/pedidos', { replace: true }); }}
                  />
                )}
                {cotizacionLejana && (
                    <BloqueLejano key={vecesCotizado} cotizacion={cotizacionLejana} modalidad={modalidad} receptor={receptor} alElegirModalidad={setModalidad} alCambiarReceptor={setReceptor} />
                )}
                <section className="checkout-bloque">
                  <h3><i className="ti ti-credit-card" /> Método de pago</h3>
                  <p className="muted">Tarjeta y Yape se cobran de forma segura con Culqi; la transferencia la confirma Chakra Shop.</p>
                  <div className="metodo-pago-grid" role="radiogroup" aria-label="Método de pago">
                    <label className={`metodo-pago-card ${metodo === 'tarjeta' ? 'seleccionado' : ''}`}>
                      <input type="radio" name="metodo-pago" value="tarjeta" checked={metodo === 'tarjeta'} onChange={() => setMetodo('tarjeta')} />
                      <span className="metodo-pago-icon"><i className="ti ti-credit-card" /></span>
                      <span className="metodo-pago-texto"><span className="metodo-pago-nombre">Tarjeta</span><small>Crédito o débito</small></span>
                      <span className="metodo-pago-check" aria-hidden="true"><i className="ti ti-check" /></span>
                    </label>
                    <label className={`metodo-pago-card ${metodo === 'yape' ? 'seleccionado' : ''} ${yapeExcedido ? 'deshabilitado' : ''}`}>
                      <input type="radio" name="metodo-pago" value="yape" checked={metodo === 'yape'} disabled={yapeExcedido} onChange={() => setMetodo('yape')} />
                      <span className="metodo-pago-icon"><i className="ti ti-device-mobile" /></span>
                      <span className="metodo-pago-texto"><span className="metodo-pago-nombre">Yape</span><small id="metodo-yape-detalle">{yapeMaximo != null ? `Desde tu celular · hasta ${formatearMoneda(yapeMaximo)}` : 'Paga desde tu celular'}</small></span>
                      <span className="metodo-pago-check" aria-hidden="true"><i className="ti ti-check" /></span>
                    </label>
                    {hayTransferencia && (
                      <label className={`metodo-pago-card ${metodo === 'transferencia' ? 'seleccionado' : ''}`} id="metodo-transferencia-card">
                        <input type="radio" name="metodo-pago" value="transferencia" checked={metodo === 'transferencia'} onChange={() => setMetodo('transferencia')} />
                        <span className="metodo-pago-icon"><i className="ti ti-building-bank" /></span>
                        <span className="metodo-pago-texto"><span className="metodo-pago-nombre">Transferencia</span><small>Cualquier monto · se confirma en horas</small></span>
                        <span className="metodo-pago-check" aria-hidden="true"><i className="ti ti-check" /></span>
                      </label>
                    )}
                  </div>
                  {yapeExcedido && yapeMaximo != null && (
                    <p className="metodo-pago-aviso" id="metodo-pago-aviso" role="status">
                      <i className="ti ti-info-circle" aria-hidden="true" />
                      <span>Yape permite pagar hasta {formatearMoneda(yapeMaximo)} por operación (y por día, entre S/ 500 y S/ 2,000 según tu banco). Para este total de {formatearMoneda(totalAPagar)}, paga con tarjeta{hayTransferencia ? ' o por transferencia bancaria' : ''}.</span>
                    </p>
                  )}
                </section>

                {!pedidoAjeno && destino && (
                  <section className="checkout-bloque" id="checkout-bloque-entrega">
                    <h3><i className="ti ti-truck-delivery" /> Entrega</h3>
                    <dl className="checkout-entrega-resumen">
                      <div><dt>Teléfono de contacto</dt><dd id="checkout-entrega-telefono">{telefonoConfirmado}</dd></div>
                      <div><dt>Punto de entrega</dt><dd id="checkout-entrega-punto"><i className="ti ti-map-pin" aria-hidden="true" /> Marcado en el mapa ({destino.lat.toFixed(5)}, {destino.lng.toFixed(5)})</dd></div>
                    </dl>
                    <button type="button" className="btn btn-outline" id="btn-checkout-editar-entrega" onClick={() => setPaso(1)}>Cambiar datos de entrega</button>
                  </section>
                )}
              </div>

              <aside className="checkout-resumen" aria-label="Resumen del pedido">
                <h3>Tu pedido</h3>
                <ul className="checkout-recap" id="checkout-recap">
                  {pedidoAjeno ? (
                    <li className="checkout-recap-reintento">
                      <i className="ti ti-receipt" aria-hidden="true" />
                      <span>Pago pendiente del pedido <strong>#{pedidoAjeno.id.slice(0, 8)}</strong></span>
                    </li>
                  ) : recap.map((l) => (
                    <li className="checkout-recap-item" key={`${l.producto_id}-${l.unidad || ''}`}>
                      <MediaProducto producto={catalogo?.find((p) => p.id === l.producto_id) || { nombre: l.nombre, categoria: null }} clase="checkout-recap-media" />
                      <span className="checkout-recap-texto">
                        <strong>{l.nombre}</strong>
                        <small>{l.cantidad} {unidadPlural(l.unidad_medida, l.cantidad)} × {formatearMoneda(l.precio)}</small>
                      </span>
                      <span className="checkout-recap-precio">{formatearMoneda(l.precio * l.cantidad)}</span>
                    </li>
                  ))}
                </ul>
                <div className="checkout-resumen-desglose">
                  <div className="checkout-resumen-fila"><span>Subtotal</span><span id="checkout-subtotal-paso2">{sinDesglose ? '—' : formatearMoneda(subtotalMostrado)}</span></div>
                  <div className="checkout-resumen-fila"><span id="checkout-envio-etiqueta">{etiquetaEnvio}</span><span id="checkout-envio-paso2">{sinDesglose || !hayOpcionEnLejano ? '—' : formatearMoneda(envioMostrado)}</span></div>
                </div>
                {cotizacionLocal && (
                  <div className={`checkout-tipo-compra ${cotizacionLocal.por_mayor ? 'es-por-mayor' : ''}`} id="checkout-tipo-compra" role="status">
                    {cotizacionLocal.por_mayor ? (
                      <>
                        <i className="ti ti-truck" aria-hidden="true" />
                        <div><strong>Compra por mayor · {formatearKg(cotizacionLocal.peso_total_kg)}</strong>{' '}
                          <span>Pasa los {formatearKg(cotizacionLocal.umbral_kg)}: va en camioneta. El envío es {formatearMoneda(cotizacionLocal.costo_envio_base)} por la distancia más {formatearMoneda(cotizacionLocal.recargo_envio_peso)} por los {formatearKg(cotizacionLocal.kg_extra)} de más ({formatearMoneda(cotizacionLocal.recargo_por_kg)} por kg).</span></div>
                      </>
                    ) : (
                      <>
                        <i className="ti ti-shopping-bag" aria-hidden="true" />
                        <div><strong>Compra por menor{cotizacionLocal.peso_total_kg != null && ` · ${formatearKg(cotizacionLocal.peso_total_kg)}`}</strong>{' '}
                          <span>El envío es solo por la distancia. Desde {formatearKg(cotizacionLocal.umbral_kg)} es compra por mayor: va en camioneta y el envío suma {formatearMoneda(cotizacionLocal.recargo_por_kg)} por cada kg de más.</span></div>
                      </>
                    )}
                  </div>
                )}
                {opcionCamion?.adelanto != null && (
                  <dl className="checkout-pago-partes" id="checkout-pago-partes">
                    <div><dt>Total del pedido</dt><dd>{formatearMoneda(subtotalMostrado + envioMostrado)}</dd></div>
                    <div className="destacada"><dt>Pagas ahora ({opcionCamion.porcentaje_adelanto}%)</dt><dd>{formatearMoneda(opcionCamion.adelanto)}</dd></div>
                    <div><dt>Al llegar el camión ({100 - opcionCamion.porcentaje_adelanto}%)</dt><dd>{formatearMoneda(opcionCamion.saldo)}</dd></div>
                  </dl>
                )}
                {opcionAgencia?.flete_destino_estimado != null && (
                  <dl className="checkout-pago-partes" id="checkout-pago-partes">
                    <div><dt>Flete de la agencia, lo pagas al recoger (aprox.)</dt><dd>{formatearMoneda(opcionAgencia.flete_destino_estimado)}</dd></div>
                  </dl>
                )}
                {envioMostrado > 0 && condicionesPago && (
                  <p className="checkout-envio-nota" id="checkout-envio-nota">
                    <i className="ti ti-truck-delivery" aria-hidden="true" />
                    <span>El envío es para el repartidor que lleva tu pedido: recibe {formatearMoneda(netoDelRepartidor(envioMostrado, condicionesPago.comision_plataforma_porcentaje))} y Chakra Shop se queda con el {condicionesPago.comision_plataforma_porcentaje.toLocaleString('es-PE')}%.</span>
                  </p>
                )}
                <div className="carrito-total">
                  <span>{etiquetaTotal}</span>
                  <strong id="checkout-total-paso2">{hayOpcionEnLejano ? formatearMoneda(totalAPagar) : '—'}</strong>
                </div>
                <div className="checkout-actions">
                  <button
                    ref={botonPagar}
                    type="button"
                    className="btn btn-primary"
                    id="btn-checkout-pagar"
                    disabled={enviandoPago || Boolean(pendiente)}
                    aria-busy={enviandoPago}
                    aria-describedby={pedidoActivo && !pendiente ? 'checkout-pedido-guardado' : undefined}
                    onClick={() => { void confirmarYPagar(); }}
                  >{etiquetaDelBoton}</button>
                  <button type="button" className="btn btn-outline" id="btn-checkout-paso2-volver" disabled={enviandoPago} onClick={() => setPaso(1)}>← Volver</button>
                </div>
                {/* El pedido ya existe y sus productos están reservados: si la ventana de Culqi se cerró sin pagar (o el token no
                    llegó), "Volver a pagar" reabre el cobro de este mismo pedido. ui-ux-pro-max: estado con role="status", sin
                    robar el foco; el botón lo cita con aria-describedby. */}
                {pedidoActivo && !pendiente && (
                  <p className={`checkout-pedido-guardado${cobroInterrumpido ? ' es-interrumpido' : ''}`} id="checkout-pedido-guardado" role="status">
                    <i className={`ti ti-${cobroInterrumpido ? 'alert-circle' : 'clock'}`} aria-hidden="true" />
                    <span>
                      {cobroInterrumpido && <strong>No se completó el pago. </strong>}
                      Tu pedido <strong>#{pedidoActivo.id.slice(0, 8)}</strong> está guardado
                      {vence && <> hasta las <strong>{horaDeLima(vence)}</strong> (hora de Lima)</>}.
                      {cobroInterrumpido ? ' Toca «Volver a pagar» para intentarlo otra vez.' : ' Si cierras la ventana de pago, puedes volver a pagar aquí o desde Mis pedidos.'}
                    </span>
                  </p>
                )}
              </aside>
            </div>
          </div>

          {/* PASO 3: CONFIRMACIÓN */}
          <div className={`checkout-panel ${paso === 3 ? 'active' : ''}`} id="checkout-panel-3">
            <div className="checkout-final">
              {final?.tipo === 'transferencia' && condicionesPago?.transferencia && (
                <PanelTransferencia
                  pedidoId={final.pedidoId}
                  monto={final.monto}
                  cuenta={condicionesPago.transferencia}
                  alEnviar={(numeroOperacion) => {
                    // Con la constancia enviada, el comprador ya pagó (falta que se revise): el carrito de ese pedido se vacía.
                    vaciarCarritoSiCoincide(pedidoPorPagar.current?.items);
                    void queryClient.invalidateQueries({ queryKey: ['mis-pedidos'] });
                    setFinal({ tipo: 'constancia', pedidoId: final.pedidoId, numeroOperacion });
                  }}
                />
              )}
              <div id="checkout-confirmacion" className={`checkout-confirmacion ${final?.tipo === 'transferencia' ? 'hidden' : ''}`} role="status" aria-live="polite">
                {final?.tipo === 'resultado' && <ResultadoDelPago final={final} />}
                {final?.tipo === 'constancia' && <ConstanciaRecibida pedidoId={final.pedidoId} numeroOperacion={final.numeroOperacion} />}
              </div>
              <button type="button" className="btn btn-primary btn-block" id="btn-checkout-finalizar" onClick={() => { void navigate('/comprador/pedidos', { replace: true }); }}>Ver mis pedidos</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
