// /admin/sin-entregar: pedidos ya pagados que todavía no llegan al comprador. Si uno no puede salir, se cancela con su
// motivo: el dinero se devuelve (Culqi, o el admin a mano si fue por transferencia), los productos vuelven al catálogo y el
// comprador recibe un aviso. Un pedido que ya salió no se puede cancelar.
import { useRef, useState, type FormEvent } from 'react';
import { pedidos } from '../../api/pedidos';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { toast } from '../../components/Toast';
import { ETIQUETAS_ESTADO_ENVIO, tiempoRelativo } from '../../lib/envios';
import { formatearFechaServidor, formatearMoneda } from '../../lib/formato';
import { textoEstadoPago } from '../pedidos/estadoPedido';
import { EncabezadoSeccion, ENVIOS_QUE_SE_PUEDEN_CANCELAR, MensajeError, textoEnvioSinEntregar, usePedidosSinEntregar, useRefrescarAdmin, Vacio, type PedidoSinEntregar } from './comun';

function CancelarConDevolucion({ d, monto, comoSeDevuelve }: { d: PedidoSinEntregar; monto: string; comoSeDevuelve: string }) {
  const refrescar = useRefrescarAdmin();
  const [abierto, setAbierto] = useState(false);
  const [motivo, setMotivo] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const campo = useRef<HTMLTextAreaElement>(null);
  const botonAbrir = useRef<HTMLButtonElement>(null);
  const liberaRepartidor = d.envio?.estado === 'propuesto' || d.envio?.estado === 'asignado';
  async function cancelar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const texto = motivo.trim();
    if (texto.length < 3) {
      setError('Escribe el motivo: el comprador lo va a leer en su aviso.');
      campo.current?.focus();
      return;
    }
    setEnviando(true);
    try {
      const pedido = await pedidos.admin.cancelar(d.pedido.id, texto);
      const dev = (pedido.devolucion || {}) as { reembolso_manual?: number | string | null; monto_reembolsado?: number | string | null };
      const aMano = Number(dev.reembolso_manual) || 0;
      toast(aMano > 0
        ? `Pedido cancelado. Falta que devuelvas ${formatearMoneda(aMano)} por transferencia al comprador.`
        : `Pedido cancelado: se devolvieron ${formatearMoneda(dev.monto_reembolsado)} y el comprador ya tiene el aviso.`);
      await refrescar('sin-entregar', 'capacidad');
    } catch (err) {
      setEnviando(false);
      setError((err as Error).message || 'No se pudo cancelar el pedido.');
    }
  }
  if (!abierto) {
    return (
      <div className="viaje-acciones">
        <button ref={botonAbrir} type="button" className="btn btn-danger btn-sin-entregar-cancelar" onClick={() => { setAbierto(true); setTimeout(() => campo.current?.focus()); }}><i className="ti ti-receipt-refund" aria-hidden="true" /> Cancelar y devolver {monto}</button>
      </div>
    );
  }
  return (
    <form className="sin-entregar-confirmar" data-pedido-id={d.pedido.id} noValidate onSubmit={(e) => { void cancelar(e); }}>
      <p><i className="ti ti-alert-triangle" aria-hidden="true" /><span>No se puede deshacer. Se devuelven {monto}. {comoSeDevuelve} Los productos vuelven al catálogo{liberaRepartidor ? ' y el repartidor queda libre' : ''}.</span></p>
      <label className="perfil-campo">
        <span>Motivo <small>(lo lee el comprador en su aviso)</small></span>
        <textarea ref={campo} name="motivo" rows={2} maxLength={300} required placeholder="Ej.: no hubo repartidor para tu zona en estos días" disabled={enviando} value={motivo} onChange={(e) => { setMotivo(e.target.value); setError(null); }} />
      </label>
      <MensajeError texto={error} />
      <div className="tarjeta-acciones">
        <button type="button" className="btn btn-ghost btn-sm btn-sin-entregar-volver" disabled={enviando} onClick={() => { setAbierto(false); setError(null); setTimeout(() => botonAbrir.current?.focus()); }}>Todavía no</button>
        <button type="submit" className="btn btn-danger" disabled={enviando}>{enviando ? <><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Devolviendo…</> : `Sí, cancelar y devolver ${monto}`}</button>
      </div>
    </form>
  );
}

function TarjetaSinEntregar({ d, indice }: { d: PedidoSinEntregar; indice: number }) {
  const { pedido, envio, pago } = d;
  const estadoEnvio = textoEnvioSinEntregar(envio);
  const pagado = pago?.estado === 'adelanto_pagado' ? Number(pago.monto_adelanto) : Number(pago?.monto);
  const metodos = [pago?.metodo, pago?.estado === 'aprobado' ? pago?.metodo_saldo : null].filter(Boolean) as string[];
  const porTransferencia = metodos.includes('transferencia');
  const destino = pedido.destino_ciudad ? `${pedido.destino_ciudad} · ${pedido.modalidad_envio === 'camion' ? 'camión' : 'agencia'}` : 'Ayacucho · reparto local';
  const tel = String(pedido.comprador_telefono || '').replace(/\D/g, '');
  const lineas = (pedido.items || []).length;

  let acciones;
  if (!pago || !['aprobado', 'adelanto_pagado'].includes(pago.estado || '')) {
    acciones = <p className="viaje-nota"><i className="ti ti-info-circle" aria-hidden="true" /> Su pago figura como "{textoEstadoPago(pago)}": no hay dinero confirmado que devolver.</p>;
  } else if (envio && !ENVIOS_QUE_SE_PUEDEN_CANCELAR.includes(envio.estado || '')) {
    acciones = <p className="viaje-nota"><i className="ti ti-truck" aria-hidden="true" /> Ya salió ({ETIQUETAS_ESTADO_ENVIO[envio.estado || ''] || envio.estado}): no se puede cancelar.</p>;
  } else {
    const comoSeDevuelve = metodos.every((m) => m === 'transferencia')
      ? 'Pagó por transferencia: Culqi no puede devolverlo, lo devuelves tú a la misma cuenta del comprador (queda anotado).'
      : porTransferencia
        ? 'Lo pagado por transferencia lo devuelves tú a la misma cuenta del comprador (queda anotado); lo pagado con tarjeta o Yape lo devuelve Culqi.'
        : 'Culqi lo devuelve a la tarjeta o Yape con que pagó (puede tardar unos días en verse).';
    acciones = <CancelarConDevolucion d={d} monto={formatearMoneda(pagado)} comoSeDevuelve={comoSeDevuelve} />;
  }
  return (
    <article className={`viaje-card sin-entregar-card${estadoEnvio.alerta ? ' con-alerta' : ''}`} style={{ animationDelay: `${Math.min(indice, 8) * 50}ms` }} data-pedido-id={pedido.id}>
      <div className="viaje-cabecera sin-entregar-cabecera">
        <div>
          <h4><i className="ti ti-package" aria-hidden="true" /> Pedido #{String(pedido.id).slice(0, 8)}</h4>
          <small>Hecho {tiempoRelativo(pedido.fecha_creacion)} · {formatearFechaServidor(pedido.fecha_creacion)}</small>
        </div>
        <span className={`pill pill-estado ${estadoEnvio.alerta ? 'pill-pendiente' : `pill-${envio?.estado || 'pendiente'}`}`}>{ETIQUETAS_ESTADO_ENVIO[envio?.estado || ''] || 'Sin envío'}</span>
      </div>
      <p className={`sin-entregar-envio${estadoEnvio.alerta ? ' alerta' : ''}`}><i className={`ti ti-${estadoEnvio.alerta ? 'user-question' : 'truck'}`} aria-hidden="true" /> {estadoEnvio.texto}</p>
      <dl className="sin-entregar-datos">
        <div><dt>Comprador</dt><dd>{pedido.comprador_nombre || '—'}{tel && <> · <a href={`tel:${tel}`}>{pedido.comprador_telefono}</a></>}</dd></div>
        <div><dt>Pagado</dt><dd><strong>{formatearMoneda(pagado)}</strong> · {textoEstadoPago(pago)}</dd></div>
        <div><dt>Destino</dt><dd>{destino}</dd></div>
        <div><dt>Productos</dt><dd>{lineas} línea{lineas === 1 ? '' : 's'}{pedido.peso_total_kg ? ` · ${Number(pedido.peso_total_kg).toLocaleString('es-PE')} kg` : ''}</dd></div>
      </dl>
      {acciones}
    </article>
  );
}

export default function SinEntregarPage() {
  const consulta = usePedidosSinEntregar();
  const refrescar = useRefrescarAdmin();
  const lista = consulta.data ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="sin-entregar">
      <EncabezadoSeccion icono="package" titulo="Pedidos pagados sin entregar" alRefrescar={() => { void refrescar('sin-entregar'); }} />
      <p className="muted admin-nota">Pedidos ya pagados que todavía no llegan al comprador. Si uno no puede salir (no hay repartidor, falta el producto o el comprador lo pide), cancélalo: el dinero se devuelve por Culqi si pagó con tarjeta o Yape (lo pagado por transferencia lo devuelves tú a la misma cuenta), los productos vuelven al catálogo y el comprador recibe un aviso con el motivo. Un pedido que ya salió no se puede cancelar.</p>
      <div id="sin-entregar-lista" className="viajes-grid">{consulta.isLoading ? <SkeletonFilas cantidad={1} /> : lista.map((d, i) => <TarjetaSinEntregar key={`${d.pedido.id}-${d.envio?.estado ?? ''}-${d.pago?.estado ?? ''}`} d={d} indice={i} />)}</div>
      {consulta.data && !lista.length && <Vacio icono="package" titulo="Todos los pedidos pagados ya se entregaron" detalle="Cuando un comprador pague un pedido, aparece acá hasta que lo reciba." />}
    </div>
  );
}
