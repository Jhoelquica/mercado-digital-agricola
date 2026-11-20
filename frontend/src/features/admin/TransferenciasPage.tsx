// /admin/transferencias: las constancias que subieron los compradores. Al aprobar, el pedido se confirma y sale a reparto; al
// rechazar, se devuelve el stock y el comprador ve el motivo. Arriba, solo si hay, los cobros y devoluciones de Culqi que no
// se resolvieron solos (con la referencia para buscarlos en el panel de Culqi).
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../../api/errors';
import { pagos, type PagoPorRevisar, type ResultadoDelCaso, type TransferenciaEnRevision } from '../../api/pagos';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor, formatearMoneda } from '../../lib/formato';
import { copiarAlPortapapeles, EncabezadoSeccion, MensajeError, PasosDeRevision, usePagosPorRevisar, useRefrescarAdmin, useTransferencias, Vacio, VisorDocumento } from './comun';

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
const ID_DE_CULQI = /^[A-Za-z0-9_]{6,64}$/;

// Los textos de cada caso: un cobro se resuelve como cobrado o no; una devolución, como devuelta o no.
function textosDelCaso(p: PagoPorRevisar) {
  const monto = formatearMoneda(p.monto);
  return p.tipo === 'cobro'
    ? {
        si: 'Sí se cobró', no: 'No se cobró', resultadoSi: 'cobrado' as ResultadoDelCaso, resultadoNo: 'no_cobrado' as ResultadoDelCaso,
        etiquetaId: 'Id del cargo en Culqi', placeholderId: 'chr_…',
        ayudaId: `Lo validamos con Culqi: tiene que ser el cargo de este cobro, por ${monto}.`,
        advertenciaNo: 'Se libera el stock y el comprador podrá volver a pagar. Hazlo solo si el cargo NO aparece en el panel de Culqi.',
        confirmarSi: 'Confirmar: sí se cobró', confirmarNo: 'Confirmar: no se cobró',
        listoSi: 'Cobro confirmado: el pago quedó aprobado y el pedido sigue su curso.',
        listoNo: 'Marcado como no cobrado: se liberó el stock y el comprador puede volver a pagar.',
      }
    : {
        si: 'Sí se devolvió', no: 'No se devolvió', resultadoSi: 'devuelto' as ResultadoDelCaso, resultadoNo: 'no_devuelto' as ResultadoDelCaso,
        etiquetaId: 'Id de la devolución en Culqi', placeholderId: 'ref_…',
        ayudaId: `La validamos con Culqi: tiene que ser una devolución completa de este cargo, por ${monto}.`,
        advertenciaNo: 'La devolución se vuelve a intentar sola. Hazlo solo si el cargo no tiene esa devolución en el panel de Culqi.',
        confirmarSi: 'Confirmar: sí se devolvió', confirmarNo: 'Confirmar: no se devolvió',
        listoSi: 'Devolución confirmada y registrada.',
        listoNo: 'Marcada como no devuelta: la devolución se vuelve a intentar sola.',
      };
}

// Resolver a mano un caso que la conciliación no pudo: lo que se ve en el panel de Culqi, con el motivo obligatorio (queda
// registrado con quién y cuándo). El backend valida el id contra Culqi y explica si no cuadra.
function ResolverCaso({ p }: { p: PagoPorRevisar }) {
  const refrescar = useRefrescarAdmin();
  const t = textosDelCaso(p);
  const id = useId();
  const [paso, setPaso] = useState<'acciones' | 'si' | 'no'>('acciones');
  const [idCulqi, setIdCulqi] = useState('');
  const [motivo, setMotivo] = useState('');
  const [errores, setErrores] = useState<{ id?: string; motivo?: string; general?: string }>({});
  const [trabajando, setTrabajando] = useState(false);
  const campoId = useRef<HTMLInputElement>(null);
  const campoMotivo = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (paso === 'si') campoId.current?.focus();
    if (paso === 'no') campoMotivo.current?.focus();
  }, [paso]);

  function volver() {
    setPaso('acciones');
    setErrores({});
  }
  async function enviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const valorId = idCulqi.trim();
    const texto = motivo.replace(/\s+/g, ' ').trim();
    const problemas: typeof errores = {};
    if (paso === 'si' && !ID_DE_CULQI.test(valorId)) problemas.id = `Pega el ${t.etiquetaId.toLowerCase()} tal como aparece en el panel de Culqi.`;
    if (texto.length < 10) problemas.motivo = 'Escribe el motivo (al menos 10 caracteres): queda registrado con tu nombre.';
    setErrores(problemas);
    if (problemas.id) { campoId.current?.focus(); return; }
    if (problemas.motivo) { campoMotivo.current?.focus(); return; }
    setTrabajando(true);
    try {
      const si = paso === 'si';
      await pagos.admin.resolver(p.referencia!, {
        resultado: si ? t.resultadoSi : t.resultadoNo,
        motivo: texto,
        ...(si ? (p.tipo === 'cobro' ? { cargo_id: valorId } : { devolucion_id: valorId }) : {}),
      });
      toast(si ? t.listoSi : t.listoNo);
      await refrescar('por-revisar', 'sin-entregar', 'transferencias');
    } catch (err) {
      if (err instanceof ApiError && (err.status === 422 || err.status === 409)) {
        // 422: lo que dijo Culqi no cuadra (junto al campo del id si es eso); 409: el caso cambió o Culqi dice otra cosa.
        setErrores(paso === 'si' && err.status === 422 ? { id: err.message } : { general: err.message });
        if (err.status === 409) void refrescar('por-revisar');
      } else {
        manejarError(err, 'resolver el caso');
      }
    } finally {
      setTrabajando(false);
    }
  }

  if (paso === 'acciones') {
    return (
      <div className="tarjeta-acciones transferencia-acciones">
        <button type="button" className="btn btn-outline btn-resolver-no" onClick={() => setPaso('no')}>
          <i className="ti ti-circle-x" aria-hidden="true" /> {t.no}
        </button>
        <button type="button" className="btn btn-primary btn-resolver-si" onClick={() => setPaso('si')}>
          <i className="ti ti-circle-check" aria-hidden="true" /> {t.si}
        </button>
      </div>
    );
  }
  const errorId = `${id}-error-id`;
  const errorMotivo = `${id}-error-motivo`;
  const ayuda = `${id}-ayuda`;
  return (
    <form className="form-rechazar-transferencia form-resolver-caso" noValidate onSubmit={enviar} aria-label={paso === 'si' ? t.si : t.no}>
      {paso === 'si' ? (
        <label>{t.etiquetaId}
          <input ref={campoId} name="id_culqi" className="perfil-dato-mono" autoComplete="off" spellCheck={false} maxLength={64}
            placeholder={t.placeholderId} value={idCulqi} onChange={(e) => setIdCulqi(e.target.value)}
            aria-invalid={Boolean(errores.id)} aria-describedby={errores.id ? `${ayuda} ${errorId}` : ayuda} />
          <span id={ayuda} className="muted admin-nota">{t.ayudaId}</span>
          {errores.id && <span id={errorId} className="form-entrega-mensaje error" role="alert"><i className="ti ti-alert-circle" aria-hidden="true" /> {errores.id}</span>}
        </label>
      ) : (
        <div className="transferencia-confirmar"><p><i className="ti ti-alert-triangle" aria-hidden="true" /> {t.advertenciaNo}</p></div>
      )}
      <label>Motivo (queda registrado con tu nombre)
        <textarea ref={campoMotivo} name="motivo" rows={3} maxLength={500} required
          placeholder={paso === 'si' ? 'Ej. Lo encontré en el panel de Culqi buscando la referencia.' : 'Ej. No aparece en el panel de Culqi ni en el estado de cuenta.'}
          value={motivo} onChange={(e) => setMotivo(e.target.value)}
          aria-invalid={Boolean(errores.motivo)} aria-describedby={errores.motivo ? errorMotivo : undefined} />
        {errores.motivo && <span id={errorMotivo} className="form-entrega-mensaje error" role="alert"><i className="ti ti-alert-circle" aria-hidden="true" /> {errores.motivo}</span>}
      </label>
      <MensajeError texto={errores.general ?? null} />
      <div className="tarjeta-acciones">
        <button type="button" className="btn btn-ghost btn-cancelar-revision" disabled={trabajando} onClick={volver}>Cancelar</button>
        <button type="submit" className={`btn ${paso === 'si' ? 'btn-primary' : 'btn-danger'} btn-confirmar-resolucion`} disabled={trabajando}>
          {trabajando ? 'Validando con Culqi…' : paso === 'si' ? t.confirmarSi : t.confirmarNo}
        </button>
      </div>
    </form>
  );
}

function TarjetaPorRevisar({ p }: { p: PagoPorRevisar }) {
  const corto = String(p.pedido_id).slice(0, 8);
  // Un cobro se busca en Culqi por la referencia de su metadata; una devolución, por su cargo (Culqi no guarda la
  // referencia de las devoluciones).
  const clave = p.tipo === 'cobro'
    ? { etiqueta: 'Referencia (búscala en el panel de Culqi)', valor: p.referencia, copiar: 'la referencia' }
    : { etiqueta: 'Cargo a devolver (busca sus devoluciones en el panel de Culqi)', valor: p.cargo_id, copiar: 'el cargo' };
  return (
    <div className="pedido-card" data-pedido-id={p.pedido_id}>
      <div className="pedido-card-header">
        <h4>{QUE_ES[p.tipo]}{DE_LA_PARTE[p.parte ?? ''] ?? ''} · pedido #{corto}</h4>
        {p.revision_manual
          ? <span className="pill pill-estado pill-rechazado"><i className="ti ti-user-search" aria-hidden="true" /> Revisión manual</span>
          : <span className="pill pill-estado pill-pendiente"><i className="ti ti-loader" aria-hidden="true" /> Verificando</span>}
      </div>
      <div className="transferencia-card-monto"><span>Monto</span><strong>{formatearMoneda(p.monto)}</strong></div>
      {clave.valor && (
        <div className="pph-medio">
          <i className="ti ti-fingerprint" aria-hidden="true" />
          <div><span>{clave.etiqueta}</span><strong className="perfil-dato-mono">{clave.valor}</strong></div>
          <button type="button" className="btn btn-ghost btn-sm btn-copiar" aria-label={`Copiar ${clave.copiar} del pedido ${corto}`}
            onClick={() => { void copiarAlPortapapeles(clave.valor!); }}><i className="ti ti-copy" aria-hidden="true" /></button>
        </div>
      )}
      <div className="perfil-datos-grid">
        <div className="perfil-dato"><span className="perfil-dato-label">En proceso desde</span><span className="perfil-dato-valor">{formatearFechaServidor(p.desde)}</span></div>
        <div className="perfil-dato"><span className="perfil-dato-label">Búsquedas en Culqi</span><span className="perfil-dato-valor">{p.pasadas}</span></div>
        {p.tipo === 'cobro' && p.cargo_id && <div className="perfil-dato"><span className="perfil-dato-label">Cargo en Culqi</span><span className="perfil-dato-valor perfil-dato-mono">{p.cargo_id}</span></div>}
        {p.tipo === 'devolucion' && p.referencia && <div className="perfil-dato"><span className="perfil-dato-label">Referencia interna</span><span className="perfil-dato-valor perfil-dato-mono">{p.referencia}</span></div>}
      </div>
      {p.mensaje && <p className="muted admin-nota">{p.mensaje}</p>}
      {p.puede_resolver && p.referencia
        ? <ResolverCaso p={p} />
        : <p className="muted admin-nota" role="status"><i className="ti ti-loader" aria-hidden="true" /> Hay un intento en curso con Culqi: en unos segundos podrás resolverlo aquí.</p>}
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
      <p className="muted admin-nota">Estos cobros o devoluciones no se pudieron confirmar solos con la pasarela. Busca cada cobro por su referencia en el panel de Culqi (y cada devolución, por su cargo) y resuélvelo aquí con lo que veas: el id que pegues se valida con Culqi y tu motivo queda registrado. Mientras tanto, el pedido no se puede volver a cobrar.</p>
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
