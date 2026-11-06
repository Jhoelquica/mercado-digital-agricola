// ============ MIS GANANCIAS (agricultor y repartidor) ============
// ChakraShop cobra con Culqi y les paga a mano cada lunes lo cumplido hasta el domingo (ver pagos/logica_cobros.py).
// 21st.dev "Wallet Card" (lo por cobrar arriba, grande) + "Invoice History Table" (movimientos con su estado y el total
// pendiente al pie). ui-ux-pro-max: etiquetas visibles, teclado numérico para celular y CCI, error junto al campo y aviso
// al guardar.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { ganancias, type MisGanancias as Ganancias } from '../../api/ganancias';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { toast } from '../../components/Toast';
import { formatearFechaCortaServidor, formatearFechaServidor, formatearMoneda, parsearFechaServidor } from '../../lib/formato';
import { etiquetaMes } from '../ventas/ventas';

const ESTADOS_COBRO: Record<string, { texto: string; pill: string }> = {
  esperando_entrega: { texto: 'Esperando la entrega', pill: 'pendiente_asignacion' },
  por_cobrar: { texto: 'Por cobrar', pill: 'pendiente' },
  pagado: { texto: 'Pagado', pill: 'entregado' },
  anulada: { texto: 'Anulado', pill: 'cancelado' },
};
const CONCEPTOS_COBRO: Record<string, string> = { venta: 'Venta', envio: 'Envío', recojo: 'Recojo en chacra' };

const hoyIso = () => new Date().toLocaleDateString('sv-SE');

// "2026-10-12" -> "lunes 12 oct." (la fecha viene sin hora: se lee como fecha local, no UTC)
export function fechaDePago(fechaIso: string) {
  return new Date(`${fechaIso}T12:00:00`).toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'short' }).replace(',', '');
}

// Cuándo se paga, dicho para quien cobra: un lunes que ya pasó sin pago queda "pendiente desde" ese lunes.
function textoDiaDePago(fechaIso: string, conVerbo = false) {
  const hoy = hoyIso();
  if (fechaIso < hoy) return `Pendiente desde el ${fechaDePago(fechaIso)}`;
  if (fechaIso === hoy) return conVerbo ? 'Se paga hoy' : 'Hoy';
  const dia = fechaDePago(fechaIso);
  return conVerbo ? `Se paga el ${dia}` : dia.charAt(0).toUpperCase() + dia.slice(1);
}

function Tarjeta({ icono, etiqueta, valor, pie, clase = '' }: { icono: string; etiqueta: string; valor: string; pie: string; clase?: string }) {
  return (
    <div className={`economia-stat ${clase}`}>
      <div className="economia-stat-cabeza">
        <span className="economia-stat-icono"><i className={`ti ti-${icono}`} aria-hidden="true" /></span>
        <span className="economia-stat-label">{etiqueta}</span>
      </div>
      <span className="economia-stat-valor">{valor}</span>
      <span className="economia-stat-sub">{pie}</span>
    </div>
  );
}

// Lo ganado (neto, sin lo anulado) en cada uno de los últimos 6 meses, con barras de un solo color y el monto escrito.
function GananciasPorMes({ g }: { g: Ganancias }) {
  const hoy = new Date();
  const meses = Array.from({ length: 6 }, (_, i) => {
    const f = new Date(hoy.getFullYear(), hoy.getMonth() - 5 + i, 1);
    return `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, '0')}`;
  });
  const suma: Record<string, number> = Object.fromEntries(meses.map((m) => [m, 0]));
  g.movimientos.filter((m) => m.estado !== 'anulada').forEach((m) => {
    const f = parsearFechaServidor(m.fecha);
    const clave = `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, '0')}`;
    if (clave in suma) suma[clave] = (suma[clave] ?? 0) + m.monto_neto;
  });
  const maximo = Math.max(...Object.values(suma));
  if (!maximo) return null;
  return (
    <section className="card-panel ganancias-por-mes" aria-label="Lo que ganaste por mes">
      <h4><i className="ti ti-chart-bar" aria-hidden="true" /> Lo que ganaste por mes</h4>
      <p className="ventas-card-sub">Neto (ya sin la comisión ni los descuentos), por la fecha de cada {g.beneficiario.tipo === 'productor' ? 'venta' : 'envío o recojo'}. Lo anulado no cuenta.</p>
      <ul>
        {meses.map((m) => (
          <li key={m} className={m === meses[5] ? 'actual' : ''}>
            <span className="gm-mes">{etiquetaMes(m)}</span>
            <span className="gm-barra" aria-hidden="true"><span style={{ width: `${suma[m] ? Math.max(2, ((suma[m] ?? 0) / maximo) * 100) : 0}%` }} /></span>
            <span className="gm-valor">{formatearMoneda(suma[m])}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

type DatosCuenta = { yape_celular: string | null; yape_titular: string | null; banco: string | null; cci: string | null; cuenta_titular: string | null };
const CAMPOS_CUENTA = ['yape_celular', 'yape_titular', 'banco', 'cci', 'cuenta_titular'] as const;

function validarCuentaCobro(datos: DatosCuenta): [string, keyof DatosCuenta] | null {
  const celular = (datos.yape_celular || '').replace(/\D/g, '');
  const cci = (datos.cci || '').replace(/\D/g, '');
  const conYape = Boolean(celular || datos.yape_titular);
  const conBanco = Boolean(cci || datos.banco || datos.cuenta_titular);
  if (!conYape && !conBanco) return ['Llena tu Yape, tu cuenta bancaria o los dos.', 'yape_celular'];
  if (conYape && !/^9\d{8}$/.test(celular)) return ['El celular de Yape tiene 9 dígitos y empieza con 9.', 'yape_celular'];
  if (conYape && (datos.yape_titular || '').length < 3) return ['Escribe el nombre que sale en tu Yape.', 'yape_titular'];
  if (conBanco && !datos.banco) return ['Elige tu banco o caja.', 'banco'];
  if (conBanco && cci.length !== 20) return [`El CCI tiene 20 dígitos (escribiste ${cci.length}).`, 'cci'];
  if (conBanco && (datos.cuenta_titular || '').length < 3) return ['Escribe el nombre del titular de la cuenta.', 'cuenta_titular'];
  return null;
}

function FormCuentaCobro({ g, alCancelar }: { g: Ganancias; alCancelar: () => void }) {
  const queryClient = useQueryClient();
  const c = g.cuenta;
  const [valores, setValores] = useState<Record<keyof DatosCuenta, string>>({
    yape_celular: c?.yape_celular || '', yape_titular: c?.yape_titular || '', banco: c?.banco || '', cci: c?.cci || '', cuenta_titular: c?.cuenta_titular || '',
  });
  // El mensaje y el campo que lo causa (null si es un error del servidor: no se marca ningún campo).
  const [error, setError] = useState<[string, keyof DatosCuenta | null] | null>(null);
  const [guardando, setGuardando] = useState(false);
  const formulario = useRef<HTMLFormElement>(null);
  useEffect(() => { if (c) formulario.current?.querySelector('input')?.focus(); }, [c]);

  async function guardar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const datos = Object.fromEntries(CAMPOS_CUENTA.map((k) => [k, valores[k].trim() || null])) as DatosCuenta;
    const problema = validarCuentaCobro(datos);
    setError(problema);
    if (problema) {
      formulario.current?.querySelector<HTMLElement>(`[name="${problema[1]}"]`)?.focus();
      return;
    }
    setGuardando(true);
    try {
      await ganancias.guardarCuenta(datos);
      toast('Listo: ya sabemos cómo pagarte 💸');
      await queryClient.invalidateQueries({ queryKey: ['ganancias', 'me'] });
    } catch (err) {
      setGuardando(false);
      setError([(err as Error).message || 'No se pudo guardar.', null]);
    }
  }

  const campo = (nombre: keyof DatosCuenta, props: Record<string, unknown>) => (
    <input name={nombre} value={valores[nombre]} disabled={guardando} aria-invalid={error?.[1] === nombre ? true : undefined} onChange={(e) => setValores({ ...valores, [nombre]: e.target.value })} {...props} />
  );
  return (
    <section className={`card-panel ganancias-cuenta${c ? '' : ' sin-cuenta'}`} aria-labelledby="ganancias-cuenta-titulo">
      <h4 id="ganancias-cuenta-titulo"><i className="ti ti-cash" aria-hidden="true" /> {c ? 'Cambia cómo cobras' : 'Dinos cómo quieres cobrar'}</h4>
      {!c && <p className="ganancias-nota alerta"><i className="ti ti-alert-triangle" aria-hidden="true" /> Sin esto no te podemos pagar. Llena tu Yape, tu cuenta bancaria o los dos.</p>}
      <form ref={formulario} className="form-cuenta-cobro" noValidate onSubmit={(e) => { void guardar(e); }}>
        <fieldset>
          <legend><i className="ti ti-device-mobile" aria-hidden="true" /> Yape <small>hasta S/ 500 por pago</small></legend>
          <label className="perfil-campo"><span>Celular de Yape</span>
            {campo('yape_celular', { type: 'tel', inputMode: 'numeric', maxLength: 11, autoComplete: 'tel-national', placeholder: '9__ ___ ___' })}</label>
          <label className="perfil-campo"><span>Nombre que sale en tu Yape</span>
            {campo('yape_titular', { type: 'text', maxLength: 100, autoComplete: 'name' })}</label>
        </fieldset>
        <fieldset>
          <legend><i className="ti ti-building-bank" aria-hidden="true" /> Cuenta bancaria <small>para montos grandes</small></legend>
          <label className="perfil-campo"><span>Banco o caja</span>
            <select name="banco" value={valores.banco} disabled={guardando} aria-invalid={error?.[1] === 'banco' ? true : undefined} onChange={(e) => setValores({ ...valores, banco: e.target.value })}>
              <option value="">Elige tu banco…</option>
              {g.bancos.map((b) => <option key={b} value={b}>{b}</option>)}
            </select></label>
          <label className="perfil-campo"><span>CCI (20 dígitos)</span>
            {campo('cci', { type: 'text', inputMode: 'numeric', maxLength: 26, autoComplete: 'off', placeholder: 'Está en la app de tu banco' })}</label>
          <label className="perfil-campo"><span>Titular de la cuenta</span>
            {campo('cuenta_titular', { type: 'text', maxLength: 100, autoComplete: 'name' })}</label>
        </fieldset>
        <div className={`form-entrega-mensaje${error ? ' error' : ''}`} aria-live="polite">{error && <><i className="ti ti-alert-circle" /><span>{error[0]}</span></>}</div>
        <div className="tarjeta-acciones">
          {c && <button type="button" className="btn btn-ghost btn-cancelar-cuenta-cobro" disabled={guardando} onClick={alCancelar}>Cancelar</button>}
          <button type="submit" className="btn btn-primary" disabled={guardando}><i className="ti ti-device-floppy" aria-hidden="true" /> Guardar</button>
        </div>
      </form>
    </section>
  );
}

function CuentaCobro({ g }: { g: Ganancias }) {
  const [editando, setEditando] = useState(false);
  const cambiar = useRef<HTMLButtonElement>(null);
  const c = g.cuenta;
  // Al guardar llega la cuenta nueva: se vuelve a mostrar (sin el formulario).
  useEffect(() => { setEditando(false); }, [c]);
  if (!c || editando) return <FormCuentaCobro g={g} alCancelar={() => { setEditando(false); setTimeout(() => cambiar.current?.focus()); }} />;
  return (
    <section className="card-panel ganancias-cuenta" aria-labelledby="ganancias-cuenta-titulo">
      <div className="ganancias-cuenta-cabeza">
        <h4 id="ganancias-cuenta-titulo"><i className="ti ti-cash" aria-hidden="true" /> Cómo cobras</h4>
        <button ref={cambiar} type="button" className="btn btn-ghost btn-sm btn-cambiar-cuenta-cobro" onClick={() => setEditando(true)}><i className="ti ti-pencil" aria-hidden="true" /> Cambiar</button>
      </div>
      <ul className="ganancias-medios">
        {c.yape_celular && <li><i className="ti ti-device-mobile" aria-hidden="true" /><div><strong>Yape {c.yape_celular.replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3')}</strong><span>{c.yape_titular}</span></div></li>}
        {c.cci && <li><i className="ti ti-building-bank" aria-hidden="true" /><div><strong>{c.banco} · CCI {c.cci}</strong><span>{c.cuenta_titular}</span></div></li>}
      </ul>
      {!c.cci && <p className="ganancias-nota"><i className="ti ti-info-circle" aria-hidden="true" /> Sin cuenta bancaria, un pago de más de S/ 500 te llega en varios Yape.</p>}
    </section>
  );
}

function ProximosPagos({ g }: { g: Ganancias }) {
  return (
    <section className="card-panel ganancias-proximos" aria-labelledby="ganancias-proximos-titulo">
      <h4 id="ganancias-proximos-titulo"><i className="ti ti-calendar-dollar" aria-hidden="true" /> Próximos pagos</h4>
      {g.proximos_pagos.length
        ? <ul>{g.proximos_pagos.map((p, i) => <li key={p.fecha} className={i === 0 ? 'siguiente' : ''}><span><i className="ti ti-calendar" aria-hidden="true" /> {textoDiaDePago(p.fecha)}</span><strong>{formatearMoneda(p.monto)}</strong></li>)}</ul>
        : <p className="muted">Cuando {g.beneficiario.tipo === 'productor' ? 'se entregue un pedido con tus productos' : 'entregues un pedido'}, aquí ves qué lunes se te paga.</p>}
    </section>
  );
}

function Movimientos({ g }: { g: Ganancias }) {
  if (!g.movimientos.length) {
    return (
      <div className="card-panel ganancias-movimientos"><h4><i className="ti ti-list-details" aria-hidden="true" /> Movimientos</h4>
        <p className="muted">Todavía no tienes {g.beneficiario.tipo === 'productor' ? 'ventas' : 'envíos ni recojos'} cobrados por la plataforma.</p></div>
    );
  }
  return (
    <div className="card-panel ganancias-movimientos">
      <h4><i className="ti ti-list-details" aria-hidden="true" /> Movimientos</h4>
      <div className="tabla-wrap">
        <table className="tabla-simple tabla-ganancias">
          <thead><tr><th>Fecha</th><th>Detalle</th><th className="num">Recibes</th><th>Estado</th></tr></thead>
          <tbody>
            {g.movimientos.map((m) => {
              const estado = ESTADOS_COBRO[m.estado] || { texto: m.estado, pill: 'pendiente' };
              const cuando = m.estado === 'por_cobrar' && m.se_paga_el ? ` · ${fechaDePago(m.se_paga_el)}`
                : m.estado === 'pagado' && m.pagado_el ? ` · ${formatearFechaCortaServidor(m.pagado_el)}` : '';
              const descuentos = [formatearMoneda(m.monto_bruto), `− ${formatearMoneda(m.comision)} comisión`, m.descuento_recojo ? `− ${formatearMoneda(m.descuento_recojo)} recojo` : ''].filter(Boolean).join(' ');
              return (
                <tr key={m.id}>
                  <td data-etiqueta="Fecha">{formatearFechaCortaServidor(m.fecha)}</td>
                  <td data-etiqueta="Detalle"><strong>{CONCEPTOS_COBRO[m.concepto] || m.concepto}</strong> <span className="muted">#{m.pedido_id.slice(0, 8)}</span><small className="ganancias-desglose">{descuentos}</small></td>
                  <td data-etiqueta="Recibes" className="num">{formatearMoneda(m.monto_neto)}</td>
                  <td data-etiqueta="Estado"><span className={`pill pill-estado pill-${estado.pill}`}>{estado.texto}{cuando}</span></td>
                </tr>
              );
            })}
          </tbody>
          <tfoot><tr><td colSpan={2}>Por cobrar</td><td className="num">{formatearMoneda(g.resumen.por_cobrar)}</td><td /></tr></tfoot>
        </table>
      </div>
    </div>
  );
}

function PagosRecibidos({ g }: { g: Ganancias }) {
  if (!g.pagos.length) return null;
  return (
    <div className="card-panel ganancias-pagos">
      <h4><i className="ti ti-receipt" aria-hidden="true" /> Pagos que recibiste</h4>
      <ul>
        {g.pagos.map((p) => (
          <li key={p.id}>
            <span className="ganancias-pago-icono"><i className={`ti ti-${p.metodo === 'yape' ? 'device-mobile' : 'building-bank'}`} aria-hidden="true" /></span>
            <div><strong>{formatearMoneda(p.monto)} por {p.metodo === 'yape' ? 'Yape' : 'transferencia'}</strong>
              <span>{formatearFechaServidor(p.fecha)} · operación {p.numero_operacion}</span></div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MisGanancias({ id }: { id: string }) {
  const consulta = useQuery({ queryKey: ['ganancias', 'me'], queryFn: ganancias.mias });
  const g = consulta.data;
  let contenido;
  if (consulta.isLoading) contenido = <SkeletonFilas cantidad={1} />;
  else if (consulta.isError || !g) contenido = <p className="muted">{(consulta.error as Error | null)?.message || 'No se pudieron cargar tus ganancias.'}</p>;
  else {
    const r = g.resumen;
    const esProductor = g.beneficiario.tipo === 'productor';
    const proximo = g.proximos_pagos[0];
    const stats = [
      <Tarjeta key="por" icono="wallet" etiqueta="Por cobrar" valor={formatearMoneda(r.por_cobrar)} pie={proximo ? textoDiaDePago(proximo.fecha, true) : 'Nada pendiente por ahora'} clase="ganancias-destacada" />,
      esProductor && <Tarjeta key="esp" icono="truck" etiqueta="Esperando la entrega" valor={formatearMoneda(r.esperando_entrega)} pie="Se cobra cuando el comprador lo recibe" />,
      <Tarjeta key="cob" icono="circle-check" etiqueta="Ya cobrado" valor={formatearMoneda(r.cobrado)} pie={g.pagos.length ? `Último pago: ${formatearFechaCortaServidor(g.pagos[0]!.fecha)}` : 'Todavía sin pagos'} />,
      esProductor && r.deuda_recojos > 0 && <Tarjeta key="deu" icono="plant-2" etiqueta="Debes por recojos" valor={formatearMoneda(r.deuda_recojos)} pie="Se descuenta de tus próximas ventas" />,
    ].filter(Boolean);
    contenido = (
      <>
        <p className="ganancias-intro"><i className="ti ti-calendar-dollar" aria-hidden="true" />
          <span>Te pagamos <strong>cada lunes</strong> lo {esProductor ? 'que vendiste y ya se entregó' : 'que entregaste'} hasta el domingo: por Yape hasta S/ 500, o por transferencia a tu cuenta.</span></p>
        <div className="economia-stats ganancias-stats" style={{ '--columnas': stats.length } as CSSProperties}>{stats}</div>
        <div className="ganancias-cuerpo">
          <div className="ganancias-cuenta-wrap"><CuentaCobro g={g} /></div>
          <ProximosPagos g={g} />
        </div>
        <GananciasPorMes g={g} />
        <Movimientos g={g} />
        <PagosRecibidos g={g} />
      </>
    );
  }
  return <div id={id} className="ganancias">{contenido}</div>;
}
