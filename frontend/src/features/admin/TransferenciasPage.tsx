// /admin/transferencias: las constancias que subieron los compradores. Al aprobar, el pedido se confirma y sale a reparto; al
// rechazar, se devuelve el stock y el comprador ve el motivo. Arriba, solo si hay, los cobros y devoluciones de Culqi que no
// se resolvieron solos (con la referencia para buscarlos en el panel de Culqi).
import { useState } from 'react';
import { ApiError } from '../../api/errors';
import { pagos, type PagoPorRevisar, type TransferenciaEnRevision } from '../../api/pagos';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor, formatearMoneda } from '../../lib/formato';
import { copiarAlPortapapeles, EncabezadoSeccion, PasosDeRevision, usePagosPorRevisar, useRefrescarAdmin, useTransferencias, Vacio, VisorDocumento } from './comun';

function TarjetaTransferencia({ t, indice }: { t: TransferenciaEnRevision; indice: number }) {
  const refrescar = useRefrescarAdmin();
  const [verConstancia, setVerConstancia] = useState(false);
  const [cargada, setCargada] = useState(false);
  const corto = String(t.pedido_id).slice(0, 8);
  async function decidir(accion: 'aprobar' | 'rechazar', motivo = '') {
    try {
      if (accion === 'aprobar') {
        await pagos.admin.aprobar(t.pedido_id);
        toast('Transferencia aprobada: el pedido sale a reparto 🚚');
      } else {
        await pagos.admin.rechazar(t.pedido_id, motivo);
        toast('Transferencia rechazada: se devolvió el stock y el comprador verá el motivo.');
      }
    } catch (err) {
      manejarError(err, accion === 'aprobar' ? 'aprobar la transferencia' : 'rechazar la transferencia');
      if (!(err instanceof ApiError && err.status === 409)) return; // 409: otro admin ya la revisó → se recarga la lista igual
    }
    await refrescar('transferencias', 'sin-entregar');
  }
  return (
    <div className="pedido-card transferencia-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-pedido-id={t.pedido_id}>
      <div className="pedido-card-header">
        <h4>Pedido #{corto}</h4>
        <span className="pill pill-estado pill-pendiente"><i className="ti ti-hourglass" /> En revisión</span>
      </div>
      <div className="transferencia-card-monto"><span>Monto del pedido</span><strong>{formatearMoneda(t.monto)}</strong></div>
      <div className="perfil-datos-grid">
        <div className="perfil-dato"><span className="perfil-dato-label">N.º de operación</span><span className="perfil-dato-valor perfil-dato-mono">{t.numero_operacion || '—'}</span></div>
        <div className="perfil-dato"><span className="perfil-dato-label">Enviada</span><span className="perfil-dato-valor">{formatearFechaServidor(t.fecha_constancia)}</span></div>
      </div>
      <button type="button" className="btn btn-outline btn-ver-constancia" aria-expanded={verConstancia} onClick={() => { setVerConstancia(!verConstancia); setCargada(true); }}><i className="ti ti-receipt" aria-hidden="true" /> Ver constancia</button>
      {/* Se pide una sola vez: al cerrarla y volver a abrirla se ve la misma. */}
      <div className={`transferencia-constancia ${verConstancia ? '' : 'hidden'}`}>
        {cargada && <VisorDocumento cargar={() => pagos.admin.constancia(t.pedido_id)} titulo="la constancia" descripcion={`Constancia de la transferencia del pedido #${corto}`} soloImagen />}
      </div>
      <PasosDeRevision
        textoRechazar={<><i className="ti ti-x" aria-hidden="true" /> Rechazar</>} claseRechazar="btn-rechazar-transferencia"
        textoAprobar={<><i className="ti ti-check" aria-hidden="true" /> Aprobar</>} claseAprobar="btn-aprobar-transferencia"
        etiquetaConfirmar="Confirmar aprobación"
        pregunta={`¿Ya verificaste en el banco que llegaron ${formatearMoneda(t.monto)} con la operación ${t.numero_operacion || '—'}? Al aprobar, el pedido sale a reparto.`}
        confirmar="Sí, aprobar"
        etiquetaMotivo="Motivo del rechazo (lo verá el comprador)" placeholderMotivo="Ej. El monto transferido no coincide con el total del pedido."
        enviarRechazo="Rechazar transferencia"
        validarMotivo={(m) => (m.length < 3 ? 'Escribe el motivo: el comprador lo va a leer para corregir su pago.' : null)}
        alAprobar={() => decidir('aprobar')} alRechazar={(m) => decidir('rechazar', m)}
      />
    </div>
  );
}

const QUE_ES = { cobro: 'Cobro', devolucion: 'Devolución' } as const;
const DE_LA_PARTE: Record<string, string> = { total: '', adelanto: ' del adelanto', saldo: ' del saldo' };

function TarjetaPorRevisar({ p }: { p: PagoPorRevisar }) {
  const corto = String(p.pedido_id).slice(0, 8);
  return (
    <div className="pedido-card" data-pedido-id={p.pedido_id}>
      <div className="pedido-card-header">
        <h4>{QUE_ES[p.tipo]}{DE_LA_PARTE[p.parte ?? ''] ?? ''} · pedido #{corto}</h4>
        {p.revision_manual
          ? <span className="pill pill-estado pill-rechazado"><i className="ti ti-user-search" aria-hidden="true" /> Revisión manual</span>
          : <span className="pill pill-estado pill-pendiente"><i className="ti ti-loader" aria-hidden="true" /> Verificando</span>}
      </div>
      <div className="transferencia-card-monto"><span>Monto</span><strong>{formatearMoneda(p.monto)}</strong></div>
      {p.referencia && (
        <div className="pph-medio">
          <i className="ti ti-fingerprint" aria-hidden="true" />
          <div><span>Referencia (búscala en el panel de Culqi)</span><strong className="perfil-dato-mono">{p.referencia}</strong></div>
          <button type="button" className="btn btn-ghost btn-sm btn-copiar" aria-label={`Copiar la referencia del pedido ${corto}`}
            onClick={() => { void copiarAlPortapapeles(p.referencia!); }}><i className="ti ti-copy" aria-hidden="true" /></button>
        </div>
      )}
      <div className="perfil-datos-grid">
        <div className="perfil-dato"><span className="perfil-dato-label">En proceso desde</span><span className="perfil-dato-valor">{formatearFechaServidor(p.desde)}</span></div>
        <div className="perfil-dato"><span className="perfil-dato-label">Búsquedas en Culqi</span><span className="perfil-dato-valor">{p.pasadas}</span></div>
        {p.cargo_id && <div className="perfil-dato"><span className="perfil-dato-label">Cargo en Culqi</span><span className="perfil-dato-valor perfil-dato-mono">{p.cargo_id}</span></div>}
      </div>
      {p.mensaje && <p className="muted admin-nota">{p.mensaje}</p>}
    </div>
  );
}

function CobrosPorRevisar() {
  const consulta = usePagosPorRevisar();
  const refrescar = useRefrescarAdmin();
  const lista = consulta.data ?? [];
  if (!lista.length) return null;
  return (
    <section aria-labelledby="titulo-por-revisar">
      <EncabezadoSeccion icono="alert-triangle" titulo={<span id="titulo-por-revisar">Cobros por revisar en Culqi ({lista.length})</span>}
        alRefrescar={() => { void refrescar('por-revisar'); }} />
      <p className="muted admin-nota">Estos cobros o devoluciones no se pudieron confirmar solos con la pasarela. Busca cada referencia en el panel de Culqi: si el cargo (o la devolución) está ahí, ya se hizo; si no está, no se hizo. Mientras tanto, el pedido no se puede volver a cobrar.</p>
      <div className="grid-admin">{lista.map((p) => <TarjetaPorRevisar key={`${p.pedido_id}-${p.tipo}`} p={p} />)}</div>
    </section>
  );
}

export default function TransferenciasPage() {
  const consulta = useTransferencias();
  const refrescar = useRefrescarAdmin();
  const lista = consulta.data ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="transferencias">
      <CobrosPorRevisar />
      <EncabezadoSeccion icono="building-bank" titulo="Transferencias por revisar" alRefrescar={() => { void refrescar('transferencias'); }} />
      <p className="muted admin-nota">Antes de aprobar, revisa en el banco que el dinero llegó, con el número de operación y el monto. Al aprobar, el pedido se confirma y sale a reparto; al rechazar, se devuelve el stock y el comprador ve el motivo.</p>
      <div id="transferencias-lista" className="grid-admin">{consulta.isLoading ? <SkeletonFilas cantidad={1} /> : lista.map((t, i) => <TarjetaTransferencia key={t.pedido_id} t={t} indice={i} />)}</div>
      {consulta.data && !lista.length && <Vacio icono="building-bank" titulo="No hay transferencias por revisar" detalle="Cuando un comprador suba la constancia de su transferencia, aparece acá." />}
    </div>
  );
}
