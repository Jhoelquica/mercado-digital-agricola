import { useQuery } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { certificacion, type CertificadoPedido } from '../../api/certificacion';
import { ApiError } from '../../api/errors';
import type { DestinoPublico, Pedido } from '../../api/pedidos';
import type { ProductoCatalogo } from '../../api/productos';
import { productos } from '../../api/productos';
import { transporte, type EnvioDetalle, type RutaEnvio } from '../../api/transporte';
import { useAuth } from '../../auth/AuthContext';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor, formatearMoneda, hashCorto, parsearFechaServidor } from '../../lib/formato';
import { unidadCorta, unidadPlural } from '../../lib/productos';
import { MediaProducto } from '../catalogo/TarjetasProducto';
import type { EstadoDetalle } from '../catalogo/useCatalogo';
import type { EstadoCheckout } from '../checkout/CheckoutPage';
import { formatearKg } from '../checkout/lejanos';
import { abrirModalCertificacion } from '../producto/ModalCertificacion';
import {
  estadoGeneralPedido, esPedidoEntregado, formatearTelefono, pagoRechazadoPedido, pasosPedido, subtotalPedido, textoEstadoPago, totalPedido, type DatosPedido, type PasoPedido,
} from './estadoPedido';
import { useSondeoVisible } from './useSondeoVisible';

const MapaRuta = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaRuta })));

const INTERVALO_RUTA_MS = 15000;

// El estado no puede depender solo del color: cada hito lleva un prefijo solo para lectores de pantalla.
const PREFIJO_PASO: Record<PasoPedido['estado'], string> = { hecho: 'Completado: ', actual: 'Paso actual: ', error: 'Con problema: ', pendiente: 'Pendiente: ' };

function ProgresoPedido({ pasos }: { pasos: PasoPedido[] }) {
  return (
    <ol className="mp-progreso" aria-label="Progreso del pedido">
      {pasos.map((p) => (
        <li key={p.titulo} className={`mp-paso mp-paso-${p.estado}`} aria-current={p.estado === 'actual' || p.estado === 'error' ? 'step' : undefined}>
          <span className="mp-paso-nodo" aria-hidden="true"><i className={`ti ti-${p.estado === 'hecho' ? 'check' : p.estado === 'error' ? 'alert-triangle' : p.icono}`} /></span>
          <span className="mp-paso-texto">
            <strong><span className="mp-solo-lectores">{PREFIJO_PASO[p.estado]}</span>{p.titulo}</strong>
            <small>{p.detalle}</small>
          </span>
        </li>
      ))}
    </ol>
  );
}

// API moderna del portapapeles y, si el navegador la rechaza o no existe (contexto no seguro, permiso denegado), el método
// clásico con un <textarea> temporal.
async function copiarTexto(texto: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch { /* seguimos con el plan B */ }
  const area = document.createElement('textarea');
  area.value = texto;
  area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  area.remove();
  return ok;
}

function PinEntrega({ pin }: { pin: string }) {
  const digitos = String(pin).split('');
  const [copiado, setCopiado] = useState(false);
  const temporizador = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(temporizador.current), []);

  async function copiar() {
    if (!(await copiarTexto(pin))) {
      toast('No se pudo copiar el código. Anótalo manualmente.', 'error');
      return;
    }
    setCopiado(true);
    temporizador.current = setTimeout(() => setCopiado(false), 1800);
  }

  return (
    <div className="mp-pin">
      <div className="mp-pin-cabecera">
        <i className="ti ti-key" />
        <div><strong>Código para el repartidor</strong><span>Dáselo cuando llegue: confirma que recibiste tu pedido.</span></div>
      </div>
      <div className="mp-pin-fila">
        <div className="mp-pin-digitos" role="group" aria-label={`Código de entrega: ${digitos.join(' ')}`}>
          {digitos.map((c, i) => <span key={i} className="mp-pin-digito" aria-hidden="true">{c}</span>)}
        </div>
        <button type="button" className="btn btn-outline btn-copiar-pin" disabled={copiado} onClick={() => { void copiar(); }}>
          {copiado ? <><i className="ti ti-check" /> Copiado</> : <><i className="ti ti-copy" /> Copiar</>}
        </button>
      </div>
    </div>
  );
}

function BloqueRuta({ envio, ruta }: { envio: EnvioDetalle; ruta: RutaEnvio | null }) {
  const [actual, setActual] = useState(ruta);
  const disponible = actual?.disponible === true;

  // Solo mientras la ruta se puede seguir: el repartidor mueve el origen. El intervalo muere al salir de la pantalla o al
  // ocultar la pestaña (useSondeoVisible). Una respuesta sin datos no borra el último punto conocido.
  useSondeoVisible(async () => {
    try {
      const nueva = await transporte.ruta(envio.id);
      if (nueva.disponible) setActual(nueva);
    } catch { /* se reintenta en el siguiente ciclo */ }
  }, INTERVALO_RUTA_MS, disponible);

  if (!actual || !actual.disponible) {
    const mensaje = (actual && !actual.disponible && actual.mensaje) || 'El seguimiento en mapa aún no está disponible para este envío.';
    return <div className="mapa-bloque-neutro"><span className="icon"><i className="ti ti-map" /></span> {mensaje}</div>;
  }
  return (
    <div className="mapa-bloque">
      <Suspense fallback={<div className="mapa-mini" />}>
        <MapaRuta id={`mapa-ruta-${envio.id}`} origen={actual.origen} destino={actual.destino} />
      </Suspense>
      <div className="ruta-stats" id={`ruta-datos-${envio.id}`}>
        <div className="ruta-stat"><span className="ruta-stat-icon"><i className="ti ti-route" /></span><div><strong>{actual.distancia_km} km</strong><span>distancia</span></div></div>
        <div className="ruta-stat"><span className="ruta-stat-icon"><i className="ti ti-clock" /></span><div><strong>~{actual.tiempo_estimado_min} min</strong><span>tiempo estimado</span></div></div>
      </div>
    </div>
  );
}

function BotonYaLoRecogi({ envioId, alConfirmar }: { envioId: string; alConfirmar: () => void }) {
  const [enviando, setEnviando] = useState(false);
  async function confirmar() {
    setEnviando(true);
    try {
      await transporte.confirmarRecibido(envioId);
      toast('¡Listo! Marcamos tu pedido como recibido.');
      alConfirmar();
    } catch (err) {
      manejarError(err, 'confirmar que recogiste tu pedido');
      setEnviando(false);
    }
  }
  return <button type="button" className="btn btn-primary btn-ya-lo-recogi" disabled={enviando} onClick={() => { void confirmar(); }}><i className="ti ti-package" aria-hidden="true" /> Ya lo recogí</button>;
}

// Pedido a otra ciudad: cómo va (agencia o camión) y qué tiene que hacer el comprador.
function BloqueLejano({ d, envio, destinos, alCambiar }: { d: DatosPedido; envio: EnvioDetalle; destinos: DestinoPublico[]; alCambiar: () => void }) {
  const { pedido, pago } = d;
  const ciudad = envio.destino_ciudad || pedido.destino_ciudad || 'tu ciudad';
  if (envio.modalidad_envio === 'agencia') {
    const flete = pedido.flete_destino_estimado != null ? <> Al recoger pagas el flete de la agencia: aprox. <b>{formatearMoneda(pedido.flete_destino_estimado)}</b>.</> : null;
    // La agencia entrega solo con el DNI de quien recibe (verificado en RENIEC al pedir) y la guía; el comprador lo
    // confirma con "Ya lo recogí".
    const oficina = destinos.find((x) => x.ciudad === (envio.destino_ciudad || pedido.destino_ciudad))?.agencia?.direccion;
    let cuerpo: ReactNode;
    if (envio.estado === 'en_agencia') {
      cuerpo = (
        <>
          <p>Tu pedido está en <b>{envio.agencia_nombre || 'la agencia'}</b>. Guía N.º <b>{envio.numero_guia || '—'}</b>.{flete}</p>
          <ol className="mp-recojo-pasos">
            <li>Ve a su oficina de {ciudad}{oficina && <>: <b>{oficina}</b></>}.</li>
            <li>Lleva el DNI de <b>{pedido.receptor_nombre || 'quien recibe'}</b> y el número de guía: la agencia lo entrega solo a esa persona.</li>
            <li>Cuando lo tengas, toca <b>Ya lo recogí</b> para cerrar tu pedido.</li>
          </ol>
          <BotonYaLoRecogi envioId={envio.id} alConfirmar={alCambiar} />
        </>
      );
    } else if (envio.estado === 'entregado') {
      cuerpo = <p>Lo recogiste en la agencia de {ciudad}.</p>;
    } else {
      cuerpo = <p>Un repartidor lo lleva a la agencia de carga en Huamanga; te avisamos con el número de guía para que lo sigas.{flete}</p>;
    }
    return <section className="mp-bloque mp-lejano"><h4 className="mp-bloque-titulo"><i className="ti ti-building-warehouse" /> Por agencia a {ciudad}</h4>{cuerpo}</section>;
  }
  const v = envio.viaje;
  let cuerpo: ReactNode;
  if (v && ['abierto', 'listo'].includes(v.estado)) {
    const porcentaje = Math.min(100, Math.round((v.sacos_cargados / v.sacos_para_salir) * 100));
    cuerpo = (
      <>
        <p>{v.estado === 'listo' ? 'El camión ya juntó su carga: sale pronto.' : <>El camión a {ciudad} lleva <b>{v.sacos_cargados} de {v.sacos_para_salir} sacos</b> para salir (carga hasta {v.capacidad_sacos}).</>}</p>
        <div className="mp-viaje-barra" role="progressbar" aria-valuemin={0} aria-valuemax={v.sacos_para_salir} aria-valuenow={Math.min(v.sacos_cargados, v.sacos_para_salir)} aria-label="Sacos juntados para que salga el camión">
          <span style={{ width: `${porcentaje}%` }} />
        </div>
        <small className="muted">Tus {envio.sacos ?? '—'} sacos van en este camión. Te avisamos cuando salga.</small>
      </>
    );
  } else if (v?.estado === 'en_ruta' || envio.estado === 'en_camino') {
    cuerpo = <p>El camión salió{v?.fecha_salida ? ` el ${formatearFechaServidor(v.fecha_salida)}` : ''} rumbo a {ciudad} con tus {envio.sacos ?? '—'} sacos.</p>;
  } else if (envio.estado === 'en_destino') {
    cuerpo = envio.saldo_pagado
      ? <p>El camión llegó y tu saldo está pagado: el chofer ya puede descargar. Dale tu código de entrega.</p>
      : <p>El camión llegó a tu ciudad. Paga el 30% para que descarguen tu pedido.</p>;
  } else if (envio.estado === 'entregado') {
    cuerpo = <p>Tu pedido llegó en el camión.</p>;
  } else {
    cuerpo = <p>Estamos subiendo tu pedido a un camión a tu ciudad.</p>;
  }
  return (
    <section className="mp-bloque mp-lejano">
      <h4 className="mp-bloque-titulo"><i className="ti ti-truck" /> En camión compartido a {ciudad}</h4>
      {cuerpo}
      {pago?.estado === 'adelanto_pagado' && envio.estado !== 'en_destino' && (
        <p className="mp-lejano-pago"><i className="ti ti-wallet" aria-hidden="true" /> Pagaste el adelanto (70%). Falta {formatearMoneda(pago.monto_a_pagar)} (30%) cuando llegue el camión.</p>
      )}
    </section>
  );
}

function tieneAlertaDePago(d: DatosPedido): boolean {
  const { pedido, envio, pago } = d;
  return pagoRechazadoPedido(d)
    || pago?.estado === 'en_revision'
    || (pago?.estado === 'pendiente' && pedido.estado === 'pendiente')
    || pago?.estado === 'reembolsado'
    || (pago?.estado === 'adelanto_pagado' && envio?.estado === 'en_destino');
}

function AlertaPago({ d, total, costoEnvio }: { d: DatosPedido; total: number; costoEnvio: number }) {
  const navigate = useNavigate();
  const { pedido, envio, pago } = d;
  const montoAhora = pago?.monto_a_pagar != null ? Number(pago.monto_a_pagar) : total;

  const botonPagar = (texto: string) => (
    <button
      type="button"
      className="btn btn-primary btn-reintentar-pago"
      onClick={() => {
        void navigate('/comprador/checkout', {
          state: { conFondo: true, reintento: { id: pedido.id, monto: montoAhora, envio: costoEnvio, parte: pago?.parte_a_pagar } } satisfies EstadoCheckout,
          preventScrollReset: true,
        });
      }}
    >{texto}</button>
  );

  if (pagoRechazadoPedido(d)) {
    const motivo = pago?.motivo_rechazo ? `Motivo: ${pago.motivo_rechazo.replace(/[.\s]+$/, '')}. ` : '';
    return pago?.vencido_en ? (
      <div className="mp-alerta" role="alert">
        <i className="ti ti-clock-x" />
        <div><strong>Venció el plazo para pagar</strong><span>Los productos volvieron al catálogo. Si siguen disponibles, puedes pagar este pedido ahora.</span></div>
        {botonPagar('Pagar ahora')}
      </div>
    ) : (
      <div className="mp-alerta" role="alert">
        <i className="ti ti-alert-triangle" />
        <div><strong>{pago?.metodo === 'transferencia' ? 'Tu transferencia no se aprobó' : 'Tu pago fue rechazado'}</strong><span>{motivo}Puedes volver a pagar sin perder tu pedido.</span></div>
        {botonPagar('Reintentar pago')}
      </div>
    );
  }
  if (pago?.estado === 'en_revision') {
    return (
      <div className="mp-alerta mp-alerta-info" role="status">
        <i className="ti ti-building-bank" />
        <div><strong>Estamos revisando tu transferencia</strong><span>Operación {pago.numero_operacion || '—'}. Cuando confirmemos que llegó el dinero, tu pedido sale a reparto.</span></div>
      </div>
    );
  }
  if (pago?.estado === 'pendiente' && pedido.estado === 'pendiente') {
    const esAdelanto = pago.parte_a_pagar === 'adelanto';
    const vence = pago.vence_en ? parsearFechaServidor(pago.vence_en) : null;
    const plazo = vence && !isNaN(vence.getTime())
      ? ` Lo guardamos hasta las ${vence.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' })}; si no se paga, los productos vuelven al catálogo.`
      : '';
    return (
      <div className="mp-alerta mp-alerta-info" role="status">
        <i className="ti ti-wallet" />
        <div><strong>{esAdelanto ? `Falta pagar el adelanto (70%): ${formatearMoneda(montoAhora)}` : 'Falta pagar este pedido'}</strong>{' '}
          <span>{esAdelanto ? 'Con el adelanto reservas tu lugar en el camión; el 30% se paga cuando llegue.' : 'Está reservado: págalo con tarjeta, Yape o transferencia.'}{plazo}</span></div>
        {botonPagar(esAdelanto ? 'Pagar el adelanto' : 'Pagar ahora')}
      </div>
    );
  }
  if (pago?.estado === 'reembolsado') {
    // El admin canceló el pedido ya pagado: por qué y cuánto se devolvió.
    const devuelto = Number(pago.monto_reembolsado) || 0;
    const aMano = Number(pago.reembolso_manual) || 0;
    const motivo = (pedido.motivo_cancelacion || '').replace(/[.\s]+$/, '');
    return (
      <div className="mp-alerta mp-alerta-info" role="status">
        <i className="ti ti-receipt-refund" />
        <div><strong>Cancelamos este pedido{motivo && `: ${motivo}`}</strong>{' '}
          <span>{devuelto > 0 && `Te devolvimos ${formatearMoneda(devuelto)} al medio con que pagaste; puede tardar unos días en verse en tu cuenta. `}{aMano > 0 && `Los ${formatearMoneda(aMano)} que pagaste por transferencia te los devolvemos a la misma cuenta.`}</span></div>
      </div>
    );
  }
  if (pago?.estado === 'adelanto_pagado' && envio?.estado === 'en_destino') {
    return (
      <div className="mp-alerta" role="alert">
        <i className="ti ti-truck" />
        <div><strong>El camión llegó: paga el 30% ({formatearMoneda(montoAhora)})</strong><span>El chofer descarga tu pedido cuando se confirme este pago. Después dale tu código de entrega.</span></div>
        {botonPagar('Pagar el saldo')}
      </div>
    );
  }
  return null;
}

function CertificadoDePedido({ cert, catalogo }: { cert: CertificadoPedido; catalogo: ProductoCatalogo[] }) {
  const detalle = Array.isArray(cert.detalle) ? cert.detalle : [];
  return (
    <div className="certificado-pedido">
      <p className="certificado-pedido-hash">Certificado: <code>{hashCorto(cert.merkle_root)}</code></p>
      {detalle.map((item) => {
        const nombre = catalogo.find((p) => p.id === item.producto_id)?.nombre || `Producto ${String(item.producto_id).slice(0, 8)}`;
        return (
          <div className="certificado-producto-linea" key={item.producto_id}>
            <span>{nombre} — <code>{hashCorto(item.hash)}</code></span>
            <button type="button" className="btn-link certificado-link-cadena btn-ver-certificacion" onClick={() => abrirModalCertificacion(item.producto_id)}>Ver certificación</button>
            <a href={`/verificar?producto_id=${encodeURIComponent(item.producto_id)}`} target="_blank" rel="noopener" className="certificado-link-cadena">Ver cadena completa →</a>
          </div>
        );
      })}
      <p className="muted certificado-pedido-nota"><i className="ti ti-lock" /> Verificado mediante blockchain</p>
    </div>
  );
}

type EstadoCertificado = { tipo: 'cargando' } | { tipo: 'mensaje'; texto: string } | { tipo: 'listo'; cert: CertificadoPedido };

function CertificadoAcordeon({ pedidoId, catalogo }: { pedidoId: string; catalogo: ProductoCatalogo[] }) {
  const [abierto, setAbierto] = useState(false);
  const [estado, setEstado] = useState<EstadoCertificado>({ tipo: 'cargando' });

  async function alternar() {
    if (abierto) { setAbierto(false); return; }
    setAbierto(true);
    if (estado.tipo === 'listo') return;
    setEstado({ tipo: 'cargando' });
    try {
      setEstado({ tipo: 'listo', cert: await certificacion.certificadoPedido(pedidoId) });
    } catch (err) {
      // Sin marcarlo como cargado: al volver a abrir el acordeón se reintenta.
      setEstado({ tipo: 'mensaje', texto: err instanceof ApiError && err.status === 404 ? 'Certificado en proceso, vuelve a intentar en un momento.' : 'No se pudo cargar el certificado por ahora.' });
    }
  }

  return (
    <div className="pedido-certificado-cta">
      <button type="button" className="btn btn-outline btn-ver-certificado-pedido" aria-expanded={abierto} onClick={() => { void alternar(); }}><i className="ti ti-certificate" /> Ver certificado de compra</button>
      <div className={`panel-expandible ${abierto ? '' : 'hidden'}`} data-panel="certificado-pedido">
        {estado.tipo === 'cargando' && <p className="muted">Cargando certificado...</p>}
        {estado.tipo === 'mensaje' && <p className="muted">{estado.texto}</p>}
        {estado.tipo === 'listo' && <CertificadoDePedido cert={estado.cert} catalogo={catalogo} />}
      </div>
    </div>
  );
}

// Un botón por cada producto entregado que el comprador todavía no calificó.
function CalificarProductos({ pedido, catalogo }: { pedido: Pedido; catalogo: ProductoCatalogo[] }) {
  const { sesion } = useAuth();
  const navigate = useNavigate();
  const items = pedido.items || [];
  const { data: pendientes = [] } = useQuery({
    queryKey: ['resenas-pendientes', pedido.id, sesion.usuarioId],
    staleTime: 0,
    queryFn: async () => {
      const faltan: string[] = [];
      for (const item of items) {
        try {
          const resenas = await productos.listarResenas(item.producto_id);
          if (!resenas.some((r) => String(r.usuario_id) === String(sesion.usuarioId))) faltan.push(item.producto_id);
        } catch { /* si falla la consulta, no se muestra el botón para ese producto */ }
      }
      return faltan;
    },
  });
  return (
    <div className="resena-cta">
      {pendientes.map((id) => {
        const nombre = items.find((i) => i.producto_id === id)?.producto_nombre || catalogo.find((p) => p.id === id)?.nombre || 'este producto';
        return (
          <button
            key={id}
            type="button"
            className="btn btn-outline btn-sm btn-calificar-producto"
            onClick={() => { void navigate(`/producto/${id}`, { state: { volverA: '/comprador/pedidos', seccion: 'detalle-seccion-resenas' } satisfies EstadoDetalle }); }}
          ><i className="ti ti-star" /> Califica {nombre}</button>
        );
      })}
    </div>
  );
}

function tituloPedido(nombres: string[]): string {
  if (!nombres.length) return 'Pedido sin productos';
  if (nombres.length <= 2) return nombres.join(' y ');
  return `${nombres[0]}, ${nombres[1]} y ${nombres.length - 2} más`;
}

interface Props {
  datos: DatosPedido;
  indice?: number;
  catalogo: ProductoCatalogo[];
  destinos: DestinoPublico[];
  alCambiar: () => void;
}

export function TarjetaPedido({ datos: d, indice = 0, catalogo, destinos, alCambiar }: Props) {
  const { pedido, envio, pago, ruta } = d;
  const retraso = Math.min(indice, 8) * 60;
  const items = (pedido.items || []).map((i) => ({ ...i, producto: catalogo.find((p) => p.id === i.producto_id) }));
  const nombreDeItem = (i: (typeof items)[number]) => i.producto?.nombre || `Producto ${String(i.producto_id).slice(0, 8)}`;
  const total = totalPedido(pedido);
  const costoEnvio = Number(pedido.costo_envio) || 0;
  const recargoPeso = Number(pedido.recargo_envio_peso) || 0;
  const general = estadoGeneralPedido(d);
  const activo = Boolean(envio) && ['asignado', 'en_camino'].includes(envio?.estado || '');
  const entregado = esPedidoEntregado(d);

  // En camión el código se da al llegar (con el saldo pagado); por agencia no hay código (se recoge con el DNI).
  const conPin = envio?.modalidad_envio === 'camion' ? envio.estado === 'en_destino' : envio?.modalidad_envio !== 'agencia' && activo;
  const esLejano = envio && ['agencia', 'camion'].includes(envio.modalidad_envio || '');
  const hayAlerta = tieneAlertaDePago(d);
  const hayLateral = hayAlerta || Boolean(esLejano) || Boolean(conPin && envio?.pin_entrega) || activo || entregado;
  const sinProducto = { categoria: null, nombre: '' };

  return (
    <article className="pedido-card mp-card" style={{ animationDelay: `${retraso}ms` }} data-pedido-id={pedido.id} data-estado={general.clave}>
      <header className="mp-cabecera">
        <div className="mp-thumbs" aria-hidden="true">
          {items.slice(0, 3).map((i) => <MediaProducto key={i.id} producto={i.producto || sinProducto} clase="mp-thumb" />)}
          {items.length > 3 && <div className="mp-thumb mp-thumb-mas">+{items.length - 3}</div>}
        </div>
        <div className="mp-cabecera-info">
          <h3 className="mp-titulo">{tituloPedido(items.map(nombreDeItem))}</h3>
          <div className="mp-meta">
            <span className="mp-chip mp-chip-id" title="Número de pedido">#{pedido.id.slice(0, 8)}</span>
            <span className="mp-chip"><i className="ti ti-calendar-event" /> {formatearFechaServidor(pedido.fecha_creacion)}</span>
            <span className="mp-chip"><i className="ti ti-shopping-bag" /> {items.length} producto{items.length === 1 ? '' : 's'}</span>
            {pedido.requiere_vehiculo_grande
              ? <span className="mp-chip mp-chip-por-mayor"><i className="ti ti-truck" /> Por mayor{pedido.peso_total_kg != null && ` · ${formatearKg(pedido.peso_total_kg)}`}</span>
              : <span className="mp-chip"><i className="ti ti-basket" /> Por menor</span>}
          </div>
        </div>
        <div className="mp-total">
          <span className="mp-total-label">Total</span>
          <strong>{formatearMoneda(total)}</strong>
          <span className={`pill pill-estado pill-${general.pill}`}><i className={`ti ti-${general.icono}`} /> {general.etiqueta}</span>
        </div>
      </header>

      <ProgresoPedido pasos={pasosPedido(d)} />

      <div className={`mp-cuerpo ${hayLateral ? 'mp-cuerpo-lateral' : ''}`}>
        <section className="mp-col-items">
          <h4 className="mp-bloque-titulo"><i className="ti ti-basket" /> Productos</h4>
          <ul className="mp-items">
            {items.map((i) => {
              const unidad = i.unidad || i.producto?.unidad_medida;
              return (
                <li className="mp-item" key={i.id}>
                  <div className="mp-item-media"><MediaProducto producto={i.producto || sinProducto} clase="mp-item-thumb" /></div>
                  <div className="mp-item-info">
                    <strong>{nombreDeItem(i)}</strong>
                    <small>{unidad ? `${i.cantidad} ${unidadPlural(unidad, i.cantidad)}` : `${i.cantidad} ×`} · {unidad ? `${formatearMoneda(i.precio_unitario)} / ${unidadCorta(unidad)}` : formatearMoneda(i.precio_unitario)}</small>
                  </div>
                  <span className="mp-item-total">{formatearMoneda(i.precio_unitario * i.cantidad)}</span>
                </li>
              );
            })}
          </ul>
          <dl className="mp-cobro" aria-label="Detalle del cobro">
            <div><dt>Subtotal</dt><dd>{formatearMoneda(subtotalPedido(pedido))}</dd></div>
            <div><dt>Envío{recargoPeso > 0 && <> <small className="mp-cobro-nota">incluye {formatearMoneda(recargoPeso)} por el peso</small></>}</dt><dd>{formatearMoneda(costoEnvio)}</dd></div>
            <div className="mp-cobro-total"><dt>Total</dt><dd>{formatearMoneda(total)}</dd></div>
          </dl>
          <dl className="mp-kv">
            {pedido.comprador_nombre && <div><dt><i className="ti ti-user" /> Recibe</dt><dd>{pedido.comprador_nombre}</dd></div>}
            {pedido.comprador_telefono && <div><dt><i className="ti ti-phone" /> Teléfono</dt><dd>{formatearTelefono(pedido.comprador_telefono)}</dd></div>}
            <div><dt><i className="ti ti-credit-card" /> Pago</dt><dd>{textoEstadoPago(pago)}</dd></div>
          </dl>
        </section>
        {hayLateral && (
          <aside className="mp-col-lateral">
            {hayAlerta && <AlertaPago d={d} total={total} costoEnvio={costoEnvio} />}
            {esLejano && envio && <BloqueLejano d={d} envio={envio} destinos={destinos} alCambiar={alCambiar} />}
            {conPin && envio?.pin_entrega && <PinEntrega pin={envio.pin_entrega} />}
            {activo && envio && (
              <section className="mp-bloque">
                <h4 className="mp-bloque-titulo"><span className="mp-en-vivo" aria-hidden="true" /> Seguimiento en vivo</h4>
                <BloqueRuta envio={envio} ruta={ruta} />
              </section>
            )}
            {entregado && (
              <section className="mp-bloque">
                <h4 className="mp-bloque-titulo"><i className="ti ti-shield-check" /> Tu compra verificada</h4>
                <CalificarProductos pedido={pedido} catalogo={catalogo} />
                <CertificadoAcordeon pedidoId={pedido.id} catalogo={catalogo} />
              </section>
            )}
          </aside>
        )}
      </div>
    </article>
  );
}
