// /admin/pagos: cada lunes se les paga a agricultores y repartidores lo que se cumplió hasta el domingo. Lo que se debe a
// cada uno, el medio para pagarle (con botón de copiar) y el registro del pago con su n.º de operación; abajo, los pagos
// ya hechos.
import { useState, type FormEvent } from 'react';
import { ganancias, type BeneficiarioPorPagar, type PagoHecho, type PorPagar } from '../../api/ganancias';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { toast } from '../../components/Toast';
import { formatearFechaServidor, formatearMoneda } from '../../lib/formato';
import { fechaDePago } from '../ganancias/MisGanancias';
import { EncabezadoSeccion, MensajeError, usePagosPorHacer, useRefrescarAdmin, Vacio } from './comun';

const redondear2 = (n: number | string) => Math.round(Number(n) * 100) / 100;

async function copiarAlPortapapeles(valor: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(valor);
    toast(`Copiado: ${valor}`);
  } catch {
    toast('No se pudo copiar. Selecciónalo y cópialo a mano.', 'error');
  }
}

function Cabecera({ d }: { d: PorPagar }) {
  const domingo = new Date(`${d.dia_de_pago}T12:00:00`);
  domingo.setDate(domingo.getDate() - 1);
  const personas = d.beneficiarios.filter((b) => b.a_pagar > 0).length;
  return (
    <div className="pph-cabecera">
      <div><span className="pph-cabecera-etiqueta">Pago del {fechaDePago(d.dia_de_pago)}</span>
        <strong>{formatearMoneda(d.total_a_pagar)}</strong>
        <small>{personas ? `a ${personas} persona${personas === 1 ? '' : 's'}` : 'nadie por pagar todavía'} · lo cumplido hasta el {domingo.toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'short' })}</small></div>
      <i className="ti ti-calendar-dollar" aria-hidden="true" />
    </div>
  );
}

function MedioDePago({ icono, etiqueta, valor, copiar }: { icono: string; etiqueta: string; valor: string; copiar: string }) {
  return (
    <div className="pph-medio">
      <i className={`ti ti-${icono}`} aria-hidden="true" />
      <div><span>{etiqueta}</span><strong>{valor}</strong></div>
      <button type="button" className="btn btn-ghost btn-sm btn-copiar" data-copiar={copiar} aria-label={`Copiar ${copiar}`} onClick={() => { void copiarAlPortapapeles(copiar); }}><i className="ti ti-copy" aria-hidden="true" /></button>
    </div>
  );
}

function FormPago({ b, nombre, yapeMaximo, alCerrar }: { b: BeneficiarioPorPagar; nombre: string; yapeMaximo: number; alCerrar: () => void }) {
  const refrescar = useRefrescarAdmin();
  const c = b.cuenta!;
  const montoInicial = b.a_pagar > 0 ? b.a_pagar : redondear2(b.a_pagar + b.esta_semana);
  const [metodo, setMetodo] = useState<'yape' | 'transferencia'>(c.yape_celular && (montoInicial <= yapeMaximo || !c.cci) ? 'yape' : 'transferencia');
  // Sin nada pendiente de antes, se paga lo de esta semana (casilla marcada y fija).
  const [incluir, setIncluir] = useState(!(b.a_pagar > 0));
  const [operacion, setOperacion] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const monto = redondear2(Number(b.a_pagar) + (b.esta_semana > 0 && incluir ? Number(b.esta_semana) : 0));
  const operaciones = Math.ceil(monto / yapeMaximo);
  const ayuda = metodo === 'yape' && operaciones > 1
    ? `Por Yape se manda hasta ${formatearMoneda(yapeMaximo)} por operación: para ${formatearMoneda(monto)} haz ${operaciones} y anota los ${operaciones} números separados por comas.`
    : metodo === 'transferencia' ? 'Anota el número de operación de la transferencia (sale en la constancia).' : '';
  async function registrar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const texto = operacion.trim();
    if (!metodo) { setError('Elige cómo le pagaste.'); return; }
    if (texto.length < 4) {
      setError('Escribe el número de operación que te dio Yape o tu banco.');
      e.currentTarget.querySelector<HTMLInputElement>('input[name="numero_operacion"]')?.focus();
      return;
    }
    setEnviando(true);
    try {
      await ganancias.admin.pagar(b.beneficiario_tipo, b.beneficiario_id, { metodo, numero_operacion: texto, monto, incluir_semana_actual: b.esta_semana > 0 && incluir });
      toast(`Pago de ${formatearMoneda(monto)} registrado: ${nombre} ya tiene el aviso.`);
      await refrescar('pagos');
    } catch (err) {
      setEnviando(false);
      setError((err as Error).message || 'No se pudo registrar el pago.');
    }
  }
  const opcion = (valor: 'yape' | 'transferencia', icono: string, texto: string, habilitada: boolean) => (
    <label className={`pph-opcion${habilitada ? '' : ' deshabilitada'}`}>
      <input type="radio" name="metodo" value={valor} disabled={!habilitada || enviando} checked={metodo === valor} onChange={() => { setMetodo(valor); setError(null); }} />
      <i className={`ti ti-${icono}`} aria-hidden="true" /> {texto}{!habilitada && <> <small>(no registró)</small></>}
    </label>
  );
  return (
    <form className="pph-form" noValidate onSubmit={(e) => { void registrar(e); }}>
      <fieldset className="pph-metodo"><legend>¿Cómo le pagaste?</legend>
        {opcion('yape', 'device-mobile', 'Yape', Boolean(c.yape_celular))}
        {opcion('transferencia', 'building-bank', 'Transferencia', Boolean(c.cci))}
      </fieldset>
      {b.esta_semana > 0 && (
        <label className="pph-adelantar"><input type="checkbox" name="incluir_semana_actual" checked={incluir} disabled={!(b.a_pagar > 0) || enviando} onChange={(e) => setIncluir(e.target.checked)} /> Incluir también lo de esta semana ({formatearMoneda(b.esta_semana)})</label>
      )}
      <label className="perfil-campo"><span>N.º de operación</span>
        <input name="numero_operacion" type="text" maxLength={200} autoComplete="off" placeholder="Ej.: 12345678" autoFocus disabled={enviando} value={operacion} onChange={(e) => { setOperacion(e.target.value); setError(null); }} /></label>
      <p className="pph-ayuda" aria-live="polite">{ayuda}</p>
      <MensajeError texto={error} />
      <div className="tarjeta-acciones">
        <button type="button" className="btn btn-ghost btn-sm btn-pph-cerrar" disabled={enviando} onClick={alCerrar}>Todavía no</button>
        <button type="submit" className="btn btn-primary" disabled={enviando}>Ya le pagué <span className="pph-monto">{formatearMoneda(monto)}</span></button>
      </div>
    </form>
  );
}

function TarjetaPorPagar({ b, yapeMaximo, indice }: { b: BeneficiarioPorPagar; yapeMaximo: number; indice: number }) {
  const [abierto, setAbierto] = useState(false);
  const esProductor = b.beneficiario_tipo === 'productor';
  const tipo = esProductor ? { texto: 'Agricultor', icono: 'plant-2' } : { texto: 'Repartidor', icono: 'truck' };
  const nombre = b.nombre || `${tipo.texto} #${b.beneficiario_id.slice(0, 8)}`;
  const c = b.cuenta;
  const cuantas = `${b.cantidad_a_pagar} ${esProductor ? 'venta' : 'entrega'}${b.cantidad_a_pagar === 1 ? '' : 's'}`;
  const montoBoton = b.a_pagar > 0 ? b.a_pagar : redondear2(b.a_pagar + b.esta_semana);
  return (
    <article className={`viaje-card pph-card${b.a_pagar > 0 ? ' con-pago' : ''}`} style={{ animationDelay: `${Math.min(indice, 8) * 50}ms` }} data-tipo={b.beneficiario_tipo} data-id={b.beneficiario_id} data-nombre={nombre}>
      <div className="viaje-cabecera">
        <div><h4><i className={`ti ti-${tipo.icono}`} aria-hidden="true" /> {nombre}</h4><small>{tipo.texto}</small></div>
        <span className={`pill pill-estado ${b.a_pagar > 0 ? 'pill-pendiente' : 'pill-pendiente_asignacion'}`}>{b.a_pagar > 0 ? 'Pagar este lunes' : b.esta_semana > 0 ? 'Para el próximo lunes' : 'Esperando entregas'}</span>
      </div>
      <dl className="pph-montos">
        <div className="principal"><dt>A pagar ahora</dt><dd>{formatearMoneda(b.a_pagar)}</dd>{b.a_pagar > 0 && <small>{cuantas}</small>}</div>
        <div><dt>De esta semana</dt><dd>{formatearMoneda(b.esta_semana)}</dd></div>
        {esProductor && <div><dt>Esperando la entrega</dt><dd>{formatearMoneda(b.esperando_entrega)}</dd></div>}
      </dl>
      <div className="pph-medios">
        {c
          ? <>
            {c.yape_celular && <MedioDePago icono="device-mobile" etiqueta={`Yape · ${c.yape_titular ?? ''}`} valor={c.yape_celular} copiar={c.yape_celular} />}
            {c.cci && <MedioDePago icono="building-bank" etiqueta={`${c.banco ?? ''} · ${c.cuenta_titular ?? ''}`} valor={`CCI ${c.cci}`} copiar={c.cci} />}
          </>
          : <p className="pph-sin-cuenta"><i className="ti ti-alert-triangle" aria-hidden="true" /> Todavía no registró cómo cobrar: pídele que lo haga en "Mis ganancias".</p>}
      </div>
      {c && (b.a_pagar > 0 || b.esta_semana > 0) && (abierto
        ? <FormPago b={b} nombre={nombre} yapeMaximo={yapeMaximo} alCerrar={() => setAbierto(false)} />
        : <div className="viaje-acciones"><button type="button" className="btn btn-primary btn-pph-abrir" onClick={() => setAbierto(true)}><i className="ti ti-cash" aria-hidden="true" /> Registrar pago de {formatearMoneda(montoBoton)}</button></div>)}
    </article>
  );
}

function PagosHechos({ pagos }: { pagos: PagoHecho[] }) {
  if (!pagos.length) return <p className="muted">Todavía no se registró ningún pago.</p>;
  return (
    <table className="tabla-simple tabla-ganancias">
      <thead><tr><th>Fecha</th><th>A quién</th><th className="num">Monto</th><th>Medio</th><th>Operación</th></tr></thead>
      <tbody>
        {pagos.map((p) => (
          <tr key={p.id}>
            <td data-etiqueta="Fecha">{formatearFechaServidor(p.fecha)}</td>
            <td data-etiqueta="A quién"><strong>{p.nombre || p.beneficiario_id.slice(0, 8)}</strong> <span className="muted">{p.beneficiario_tipo === 'productor' ? 'Agricultor' : 'Repartidor'}</span></td>
            <td data-etiqueta="Monto" className="num">{formatearMoneda(p.monto)}</td>
            <td data-etiqueta="Medio">{p.destino}</td>
            <td data-etiqueta="Operación">{p.numero_operacion}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function PagosPage() {
  const consulta = usePagosPorHacer();
  const refrescar = useRefrescarAdmin();
  const d = consulta.data;
  const beneficiarios = d?.porPagar.beneficiarios ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="pagos-por-hacer">
      <EncabezadoSeccion icono="cash" titulo="Pagos por hacer" alRefrescar={() => { void refrescar('pagos'); }} />
      <p className="muted admin-nota">Cada lunes se les paga a los agricultores y repartidores lo que se cumplió hasta el domingo: lo vendido y ya entregado al comprador, y los envíos y recojos hechos. Págales por Yape (hasta S/ 500 por operación) o por transferencia a su cuenta y registra aquí el número de operación: el pago queda anotado y la persona recibe un aviso.</p>
      <div id="pagos-por-hacer-cabecera">{d && <Cabecera d={d.porPagar} />}</div>
      <div id="pagos-por-hacer-lista" className="viajes-grid">
        {consulta.isLoading ? <SkeletonFilas cantidad={1} /> : beneficiarios.map((b, i) => <TarjetaPorPagar key={`${b.beneficiario_tipo}-${b.beneficiario_id}-${b.a_pagar}-${b.esta_semana}`} b={b} yapeMaximo={d!.porPagar.yape_maximo_por_operacion} indice={i} />)}
      </div>
      {d && !beneficiarios.length && <Vacio icono="cash" titulo="No hay nada por pagar" detalle="Cuando se entregue un pedido o se haga un recojo, lo que gana cada uno aparece aquí." />}
      <div className="card-panel pph-historial">
        <h4><i className="ti ti-history" aria-hidden="true" /> Pagos ya hechos</h4>
        <div id="pagos-hechos-lista" className="tabla-wrap">{d && <PagosHechos pagos={d.hechos} />}</div>
      </div>
    </div>
  );
}
