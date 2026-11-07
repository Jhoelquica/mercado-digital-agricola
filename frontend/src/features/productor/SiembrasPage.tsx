import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { normalizarUrlImagen } from '../../api/imagenes';
import { productores, type CatalogoPlantillas, type DetalleGastos, type RegistroProduccion } from '../../api/productores';
import { manejarError, toast } from '../../components/Toast';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearMoneda } from '../../lib/formato';
import { manoDeObra } from '../../lib/reglas';
import { abrirAsistenteCosecha, abrirAsistenteSiembra, puedeHablar } from './asistente/abrir';
import { textoTotalJornales } from './asistente/piezas';
import {
  badgeDeEstado, ESTADOS_POR_COMPLETAR, etapasDePlantilla, fechaIso, formatearDistancia, formatearFechaCorta, nombreRubro, palabrasDe,
  RADIO_CHACRA_KM, textoPeonesDias, useChacras, usePlantillas, useRegistros,
} from './comun';

const recargarSiembras = (queryClient: ReturnType<typeof useQueryClient>) => queryClient.invalidateQueries({ queryKey: ['productor', 'registros'] });

function MensajeDeFormulario({ mensaje }: { mensaje: string }) {
  return mensaje
    ? <p className="form-entrega-mensaje error" role="alert"><i className="ti ti-alert-circle" /><span>{mensaje}</span></p>
    : <p className="form-entrega-mensaje hidden" role="alert" />;
}

function textoDistancia(km: number | null, radio: number) {
  if (km == null) return { texto: 'sin ubicación', advertencia: true };
  return km <= radio ? { texto: `a ${formatearDistancia(km)} de tu chacra`, advertencia: false } : { texto: `a ${formatearDistancia(km)} de tu chacra (lejos)`, advertencia: true };
}

// Lo que quedó sellado en la cadena al completar la cosecha: la foto y dónde estaba el agricultor.
function EvidenciaRegistro({ r }: { r: RegistroProduccion }) {
  if (r.estado !== 'cosechado') return null;
  const radio = r.radio_chacra_km || RADIO_CHACRA_KM;
  const foto = r.foto_cosecha_url ? normalizarUrlImagen(r.foto_cosecha_url) : null;
  const linea = (momento: string, km: number | null) => {
    const d = textoDistancia(km, radio);
    return (
      <li className={d.advertencia ? 'es-advertencia' : ''}><i className={`ti ti-${d.advertencia ? 'alert-triangle' : 'map-pin'}`} aria-hidden="true" />
        <span><strong>{momento}:</strong> {d.texto}{d.advertencia ? ' — quedó como advertencia' : ''}</span></li>
    );
  };
  return (
    <div className="evidencia-resumen">
      {foto && <a className="evidencia-resumen-foto" href={foto} target="_blank" rel="noopener"><img src={foto} alt={`Foto de la cosecha de ${r.cultivo}`} loading="lazy" /></a>}
      <ul className="evidencia-resumen-lista">{linea('Siembra', r.siembra_distancia_km)}{linea('Cosecha', r.cosecha_distancia_km)}</ul>
    </div>
  );
}

function FormGasto({ registroId, etapaClave, catalogo, plantilla, alCancelar, alGuardar }: { registroId: string; etapaClave: string; catalogo: CatalogoPlantillas | null; plantilla: string | null; alCancelar: () => void; alGuardar: () => void }) {
  const queryClient = useQueryClient();
  const etapa = etapasDePlantilla(catalogo, plantilla).find((e) => e.clave === etapaClave) || { clave: etapaClave, nombre: etapaClave, rubros: [] as string[] };
  const otros = Object.keys(catalogo?.rubros || {}).filter((r) => !etapa.rubros.includes(r));
  const hoy = fechaIso(new Date());
  const [rubro, setRubro] = useState(etapa.rubros[0] || otros[0] || '');
  const [monto, setMonto] = useState('');
  const [peones, setPeones] = useState('');
  const [dias, setDias] = useState('');
  const [precio, setPrecio] = useState('');
  const [descripcion, setDescripcion] = useState('');
  const [fecha, setFecha] = useState(hoy);
  const [mensaje, setMensaje] = useState('');
  const [guardando, setGuardando] = useState(false);
  const formulario = useRef<HTMLFormElement>(null);
  const jornales = rubro === 'mano_obra';

  useEffect(() => { formulario.current?.querySelector<HTMLSelectElement>('select[name="rubro"]')?.focus(); }, []);

  async function guardar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const mano = jornales ? manoDeObra({ peones, dias, pago: precio }) : null;
    const campoDe = { peones: 'peones', dias: 'dias', pago: 'precio' } as const;
    let problema: [string, string] | null = null;
    if (mano) {
      if (mano.error) problema = [mano.error, campoDe[mano.campo]];
      else if (!mano.datos) problema = ['Escribe cuántos peones trabajaron, cuántos días y cuánto le pagaste a cada uno por día.', 'peones'];
    } else if (!(Number(monto) > 0)) problema = ['Escribe cuánto gastaste.', 'monto'];
    if (problema) {
      setMensaje(problema[0]);
      formulario.current?.querySelector<HTMLInputElement>(`[name="${problema[1]}"]`)?.focus();
      return;
    }
    setGuardando(true);
    try {
      await productores.produccion.anotarGasto(registroId, {
        etapa: etapaClave, rubro, descripcion: descripcion.trim() || null, fecha: fecha ? `${fecha}T12:00:00` : null,
        ...(mano?.datos ? mano.datos : { monto: Number(monto) || null }),
      });
      toast('Gasto anotado 🧾');
      await recargarSiembras(queryClient);
      alGuardar();
    } catch (err) {
      setMensaje((err as Error).message || 'No se pudo anotar el gasto.');
      setGuardando(false);
    }
  }

  return (
    <form ref={formulario} className="form-gasto" data-etapa={etapaClave} noValidate onSubmit={(e) => { void guardar(e); }}>
      <h6><i className="ti ti-coin" aria-hidden="true" /> Anotar un gasto · {etapa.nombre}</h6>
      <div className="fg-grid">
        <label className="perfil-campo">¿En qué gastaste?
          <select name="rubro" value={rubro} onChange={(e) => setRubro(e.target.value)}>
            <optgroup label="Lo más común en esta etapa">{etapa.rubros.map((r) => <option key={r} value={r}>{nombreRubro(catalogo, r)}</option>)}</optgroup>
            {otros.length > 0 && <optgroup label="Otros">{otros.map((r) => <option key={r} value={r}>{nombreRubro(catalogo, r)}</option>)}</optgroup>}
          </select>
        </label>
        <label className={`perfil-campo fg-monto ${jornales ? 'hidden' : ''}`}>Monto (S/)<input name="monto" type="number" inputMode="decimal" min="0" step="0.01" value={monto} onChange={(e) => setMonto(e.target.value)} /></label>
        <label className={`perfil-campo fg-jornal ${jornales ? '' : 'hidden'}`}>¿Cuántos peones?<input name="peones" type="number" inputMode="numeric" min="1" step="1" placeholder="Ej.: 3" value={peones} onChange={(e) => setPeones(e.target.value)} /></label>
        <label className={`perfil-campo fg-jornal ${jornales ? '' : 'hidden'}`}>¿Cuántos días?<input name="dias" type="text" inputMode="decimal" autoComplete="off" placeholder="Ej.: 2 o 0,5" value={dias} onChange={(e) => setDias(e.target.value)} /></label>
        <label className={`perfil-campo fg-jornal ${jornales ? '' : 'hidden'}`}>Pago por peón al día (S/)<input name="precio" type="number" inputMode="decimal" min="0" step="any" placeholder="Ej.: 50" value={precio} onChange={(e) => setPrecio(e.target.value)} /></label>
        <label className="perfil-campo">Detalle <small>(opcional)</small><input name="descripcion" type="text" maxLength={120} placeholder="Ej.: 2 sacos de urea" value={descripcion} onChange={(e) => setDescripcion(e.target.value)} /></label>
        <label className="perfil-campo">Fecha<input name="fecha" type="date" max={hoy} value={fecha} onChange={(e) => setFecha(e.target.value)} /></label>
      </div>
      <p className="fg-total" aria-live="polite">{jornales ? textoTotalJornales(peones, dias, precio) : ''}</p>
      <MensajeDeFormulario mensaje={mensaje} />
      <div className="tarjeta-acciones">
        <button type="button" className="btn btn-ghost btn-cancelar-gasto" onClick={alCancelar}>Cancelar</button>
        <button type="submit" className="btn btn-primary" disabled={guardando}><i className="ti ti-device-floppy" aria-hidden="true" /> Guardar gasto</button>
      </div>
    </form>
  );
}

function BotonBorrarGasto({ registroId, gastoId }: { registroId: string; gastoId: string }) {
  const queryClient = useQueryClient();
  // Primer toque: pide confirmar en el mismo botón; segundo toque: borra.
  const [confirmar, setConfirmar] = useState(false);
  const [borrando, setBorrando] = useState(false);
  async function borrar() {
    if (!confirmar) { setConfirmar(true); return; }
    setBorrando(true);
    try {
      await productores.produccion.borrarGasto(registroId, gastoId);
      toast('Gasto borrado.');
      await recargarSiembras(queryClient);
    } catch (err) {
      setBorrando(false);
      manejarError(err, 'borrar el gasto');
    }
  }
  return (
    <button type="button" className={`btn btn-ghost btn-sm btn-borrar-gasto ${confirmar ? 'btn-danger' : ''}`} disabled={borrando} aria-label="Borrar este gasto" onClick={() => { void borrar(); }}>
      {confirmar ? '¿Borrar?' : <i className="ti ti-trash" aria-hidden="true" />}
    </button>
  );
}

function unidadDeGasto(unidad: string | null, cantidad: number | null) {
  if (!unidad) return '';
  return Number(cantidad) === 1 ? unidad : unidad === 'jornal' ? 'jornales' : unidad;
}

function DetalleDeGastos({ registroId, editable, catalogo }: { registroId: string; editable: boolean; catalogo: CatalogoPlantillas | null }) {
  // Bajo ['productor', 'registros']: al anotar o borrar un gasto se recarga junto con la lista.
  const consulta = useQuery({ queryKey: ['productor', 'registros', registroId, 'gastos'], queryFn: () => productores.produccion.gastos(registroId) });
  const estado = consulta.isLoading ? { tipo: 'cargando' as const }
    : consulta.isError ? { tipo: 'error' as const, mensaje: (consulta.error as Error).message || 'No se pudieron cargar los gastos.' }
      : { tipo: 'listo' as const, d: consulta.data as DetalleGastos };
  if (estado.tipo === 'cargando') return <p className="muted">Cargando…</p>;
  if (estado.tipo === 'error') return <p className="muted">{estado.mensaje}</p>;
  const { d } = estado;
  if (!d.gastos.length) return <p className="muted">Todavía no anotaste gastos.</p>;
  const nombreEtapa = Object.fromEntries(d.etapas.map((e) => [e.clave, e.nombre]));
  return (
    <ul>
      {d.gastos.map((g) => {
        const detalle = [g.descripcion || '', g.peones && g.dias
          ? `${textoPeonesDias(g.peones, g.dias)} × ${formatearMoneda(g.precio_unitario)}`
          : g.cantidad ? `${String(g.cantidad).replace('.', ',')} ${unidadDeGasto(g.unidad, g.cantidad)} × ${formatearMoneda(g.precio_unitario)}` : ''].filter(Boolean).join(' · ');
        return (
          <li key={g.id}>
            <div><strong>{nombreRubro(catalogo, g.rubro)}</strong> <span className="muted">· {nombreEtapa[g.etapa] || 'Otra etapa'} · {formatearFechaCorta(String(g.fecha).slice(0, 10))}</span>
              {(g.descripcion || g.cantidad) && <small>{detalle}</small>}</div>
            <span className="eg-lista-monto">{formatearMoneda(g.monto)}</span>
            {editable && <BotonBorrarGasto registroId={registroId} gastoId={g.id} />}
          </li>
        );
      })}
    </ul>
  );
}

// 21st.dev "Process Timeline": etapas con gasto marcadas y la siguiente resaltada.
function GastosPorEtapa({ r, editable, catalogo }: { r: RegistroProduccion; editable: boolean; catalogo: CatalogoPlantillas | null }) {
  const [etapaAbierta, setEtapaAbierta] = useState<string | null>(null);
  const [verDetalle, setVerDetalle] = useState(false);
  const etapas = r.gastos_por_etapa || [];
  const ultimaConGasto = etapas.map((e) => e.total > 0).lastIndexOf(true);
  const detalles = Object.fromEntries(etapasDePlantilla(catalogo, r.plantilla).map((e) => [e.clave, e]));
  const total = etapas.reduce((s, e) => s + e.total, 0);
  return (
    <section className="etapas-gastos" aria-label={`Gastos por etapa de ${r.cultivo}`}>
      <div className="eg-cabeza"><h5><i className="ti ti-list-check" aria-hidden="true" /> Gastos por etapa</h5><strong>{formatearMoneda(total)}</strong></div>
      <p className="eg-ayuda">{editable ? 'Anota cada gasto en su etapa cuando ocurra: al final sabrás cuánto te costó de verdad.' : 'Lo que gastaste en cada etapa.'}</p>
      <ol className="eg-linea">
        {etapas.map((e, i) => (
          <li key={e.clave} className={`eg-etapa ${e.total > 0 ? 'hecha' : i === ultimaConGasto + 1 ? 'siguiente' : 'pendiente'}`}>
            <span className="eg-marca" aria-hidden="true">{e.total > 0 ? <i className="ti ti-check" /> : i + 1}</span>
            <div className="eg-texto"><strong>{e.nombre}</strong>{detalles[e.clave] && <small>{detalles[e.clave]?.que_se_hace}</small>}</div>
            <span className="eg-monto">{e.total > 0 ? formatearMoneda(e.total) : <><span className="sr-only">sin gastos</span>—</>}{e.gastos > 1 && <small>{e.gastos} gastos</small>}</span>
            {editable && e.clave !== 'otras' && <button type="button" className="btn btn-outline btn-sm btn-anotar-gasto" aria-label={`Anotar un gasto en ${e.nombre}`} onClick={() => setEtapaAbierta(e.clave)}><i className="ti ti-plus" aria-hidden="true" /> Anotar</button>}
          </li>
        ))}
      </ol>
      <div className="eg-form-wrap">{etapaAbierta && <FormGasto key={etapaAbierta} registroId={r.id} etapaClave={etapaAbierta} catalogo={catalogo} plantilla={r.plantilla} alCancelar={() => setEtapaAbierta(null)} alGuardar={() => setEtapaAbierta(null)} />}</div>
      <details className="eg-detalle" onToggle={(e) => setVerDetalle((e.target as HTMLDetailsElement).open)}>
        <summary>Ver cada gasto</summary>
        <div className="eg-lista">{verDetalle && <DetalleDeGastos registroId={r.id} editable={editable} catalogo={catalogo} />}</div>
      </details>
    </section>
  );
}

// Corregir una siembra todavía planificada: lo que se escribió. La chacra y el lugar donde se marcó la siembra no: son la
// evidencia de origen.
function FormCorregir({ r, alCancelar, alGuardar }: { r: RegistroProduccion; alCancelar: () => void; alGuardar: () => void }) {
  const queryClient = useQueryClient();
  const fecha = (valor: string | null) => (valor ? String(valor).slice(0, 10) : '');
  const [v, setV] = useState({
    cultivo: r.cultivo, numero_parcelas: String(r.numero_parcelas), fecha_siembra: fecha(r.fecha_siembra), fecha_cosecha_estimada: fecha(r.fecha_cosecha_estimada),
    costo_semillas: String(Number(r.costo_semillas) || 0), costo_insumos: String(Number(r.costo_insumos) || 0),
  });
  const [mensaje, setMensaje] = useState('');
  const [guardando, setGuardando] = useState(false);
  const formulario = useRef<HTMLFormElement>(null);
  useEffect(() => { formulario.current?.querySelector('input')?.focus(); }, []);
  const campo = (etiqueta: string, nombre: keyof typeof v, props: Record<string, unknown>) => (
    <label className="perfil-campo">{etiqueta}<input name={nombre} value={v[nombre]} onChange={(e) => setV({ ...v, [nombre]: e.target.value })} {...props} /></label>
  );

  async function guardar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const datos = {
      cultivo: v.cultivo.trim(), numero_parcelas: Number(v.numero_parcelas.trim()),
      fecha_siembra: v.fecha_siembra ? `${v.fecha_siembra}T00:00:00` : '', fecha_cosecha_estimada: v.fecha_cosecha_estimada ? `${v.fecha_cosecha_estimada}T00:00:00` : '',
      costo_semillas: r.plantilla ? 0 : Number(v.costo_semillas || 0), costo_insumos: r.plantilla ? 0 : Number(v.costo_insumos || 0),
      unidad_medida: r.unidad_medida || 'kg', equivalencia_kg: r.equivalencia_kg ?? null,
    };
    let problema: [string, string] | null = null;
    if (!datos.cultivo) problema = ['Escribe el cultivo.', 'cultivo'];
    else if (!Number.isInteger(datos.numero_parcelas) || datos.numero_parcelas < 1) problema = ['Las parcelas son un número entero desde 1.', 'numero_parcelas'];
    else if (!datos.fecha_siembra || !datos.fecha_cosecha_estimada) problema = ['Pon la fecha de siembra y la de cosecha estimada.', datos.fecha_siembra ? 'fecha_cosecha_estimada' : 'fecha_siembra'];
    else if (datos.fecha_cosecha_estimada < datos.fecha_siembra) problema = ['La cosecha estimada no puede ser antes de la siembra.', 'fecha_cosecha_estimada'];
    if (problema) {
      setMensaje(problema[0]);
      formulario.current?.querySelector<HTMLInputElement>(`[name="${problema[1]}"]`)?.focus();
      return;
    }
    setGuardando(true);
    try {
      await productores.produccion.corregir(r.id, datos);
      toast('Siembra corregida ✅');
      await recargarSiembras(queryClient);
      alGuardar();
    } catch (err) {
      setGuardando(false);
      setMensaje((err as Error).message);
    }
  }

  return (
    <form ref={formulario} className="form-corregir-siembra" noValidate onSubmit={(e) => { void guardar(e); }}>
      <h5><i className="ti ti-pencil" aria-hidden="true" /> Corregir la siembra</h5>
      <div className="form-corregir-grid">
        {campo('Cultivo', 'cultivo', { type: 'text', maxLength: 80, required: true })}
        {campo('Parcelas', 'numero_parcelas', { type: 'number', min: 1, step: 1, required: true })}
        {campo('Fecha de siembra', 'fecha_siembra', { type: 'date', required: true })}
        {campo('Cosecha estimada', 'fecha_cosecha_estimada', { type: 'date', required: true })}
        {!r.plantilla && campo('Semillas (S/)', 'costo_semillas', { type: 'number', min: 0, step: 0.01 })}
        {!r.plantilla && campo('Insumos (S/)', 'costo_insumos', { type: 'number', min: 0, step: 0.01 })}
      </div>
      <p className="form-corregir-nota"><i className="ti ti-info-circle" aria-hidden="true" /> La chacra y el lugar donde marcaste la siembra no se cambian: son la prueba de origen de tu cosecha.{r.plantilla ? ' Los gastos se corrigen en "Gastos por etapa".' : ''}</p>
      <MensajeDeFormulario mensaje={mensaje} />
      <div className="tarjeta-acciones">
        <button type="button" className="btn btn-ghost btn-cancelar-corregir" onClick={alCancelar}>Cancelar</button>
        <button type="submit" className="btn btn-primary" disabled={guardando}><i className="ti ti-device-floppy" aria-hidden="true" /> Guardar cambios</button>
      </div>
    </form>
  );
}

function TarjetaRegistro({ r, indice, catalogo }: { r: RegistroProduccion; indice: number; catalogo: CatalogoPlantillas | null }) {
  const queryClient = useQueryClient();
  const [paso, setPaso] = useState<'acciones' | 'corregir' | 'anular'>('acciones');
  const [anulando, setAnulando] = useState(false);
  const cancelarAnular = useRef<HTMLButtonElement>(null);
  const porCompletar = ESTADOS_POR_COMPLETAR.includes(r.estado);
  const palabras = palabrasDe(r.tipo_produccion);
  const badge = badgeDeEstado(r.estado);
  const enCurso = r.estado === 'planificado' && ['crianza', 'elaboracion'].includes(r.tipo_produccion || '');
  const etiquetaEstado = ['cosechado', 'aprobado'].includes(r.estado) ? palabras.listo : enCurso ? 'En curso' : badge.etiqueta;
  useEffect(() => { if (paso === 'anular') cancelarAnular.current?.focus(); }, [paso]);

  async function anular() {
    setAnulando(true);
    try {
      await productores.produccion.anular(r.id);
      toast('Siembra anulada.');
      await recargarSiembras(queryClient);
    } catch (err) {
      setAnulando(false);
      manejarError(err, 'anular la siembra');
    }
  }

  const dato = (etiqueta: string, valor: string | number) => <div className="perfil-dato"><span className="perfil-dato-label">{etiqueta}</span><span className="perfil-dato-valor">{valor}</span></div>;
  const completar = <button type="button" className="btn btn-primary btn-completar-cosecha" onClick={() => abrirAsistenteCosecha(r.id)}><i className="ti ti-basket-check" aria-hidden="true" /> {palabras.completar}</button>;

  let bloqueEstado;
  if (porCompletar) {
    bloqueEstado = r.estado !== 'planificado' ? (
      <>
        <p className="produccion-sin-calculo"><i className="ti ti-camera" /> Esta cosecha quedó pendiente de revisión. Ya no hace falta: complétala con la foto de tu cosecha.</p>
        {completar}
      </>
    ) : (
      <>
        <div className={`produccion-acciones ${paso === 'acciones' ? '' : 'hidden'}`}>
          {completar}
          <button type="button" className="btn btn-outline btn-corregir-siembra" aria-expanded={paso === 'corregir'} onClick={() => setPaso('corregir')}><i className="ti ti-pencil" aria-hidden="true" /> Corregir datos</button>
          <button type="button" className="btn btn-ghost btn-anular-siembra" onClick={() => setPaso('anular')}><i className="ti ti-trash" aria-hidden="true" /> Anular</button>
        </div>
        {paso === 'corregir' && <FormCorregir r={r} alCancelar={() => setPaso('acciones')} alGuardar={() => setPaso('acciones')} />}
        <div className={`anular-siembra-confirmar ${paso === 'anular' ? '' : 'hidden'}`} role="group" aria-label="Confirmar anulación">
          <p><i className="ti ti-alert-triangle" aria-hidden="true" /> ¿Anular el registro de {r.cultivo}? Deja de aparecer en tu lista y ya no se podrá completar. No se puede deshacer.</p>
          <div className="tarjeta-acciones">
            <button ref={cancelarAnular} type="button" className="btn btn-ghost btn-cancelar-anular" onClick={() => setPaso('acciones')}>No, dejarla</button>
            <button type="button" className="btn btn-danger btn-confirmar-anular" disabled={anulando} onClick={() => { void anular(); }}>Sí, anular</button>
          </div>
        </div>
      </>
    );
  } else if (r.ingreso_estimado != null) {
    bloqueEstado = (
      <>
        <div className="perfil-datos-grid">
          {dato(palabras.listo, `${Number(r.cantidad_cosechada).toFixed(2)} ${r.unidad_medida}`)}
          {dato('Precio de referencia', `${formatearMoneda(r.precio_referencia_kg)}/kg`)}
          {dato('Ingreso estimado', formatearMoneda(r.ingreso_estimado))}
          <div className="perfil-dato"><span className="perfil-dato-label">Ganancia</span><span className={`perfil-dato-valor ${Number(r.ganancia_estimada) >= 0 ? 'ganancia-positiva' : 'ganancia-negativa'}`}>{formatearMoneda(r.ganancia_estimada)} ({r.margen_porcentaje}%)</span></div>
        </div>
        <EvidenciaRegistro r={r} />
      </>
    );
  } else {
    bloqueEstado = (
      <>
        <div className="perfil-datos-grid">{dato('Cosechado', `${Number(r.cantidad_cosechada).toFixed(2)} ${r.unidad_medida}`)}</div>
        <p className="produccion-sin-calculo"><i className="ti ti-info-circle" /> No se pudo calcular la ganancia: {r.motivo_sin_calculo || 'motivo desconocido'}.</p>
        <EvidenciaRegistro r={r} />
      </>
    );
  }

  return (
    <div className="card-panel produccion-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }}>
      <div className="produccion-card-header">
        <h4>{r.cultivo}</h4>
        <span className="produccion-card-chips">
          {r.tipo_produccion && r.tipo_produccion !== 'temporada' && <span className="pill pill-tipo-produccion">{palabras.nombre}{r.alimento ? ` · ${r.alimento}` : ''}</span>}
          <span className={`pill pill-estado ${badge.clase}`}><i className={`ti ti-${badge.icono}`} /> {etiquetaEstado}</span>
        </span>
      </div>
      <div className="perfil-datos-grid">
        {palabras.cantidad && dato(palabras.cantidad, r.numero_parcelas)}
        {dato('Ubicación', r.ubicacion_cosecha || '—')}
        {dato(palabras.inicio, formatearFechaCorta(r.fecha_siembra))}
        {dato(palabras.fin, formatearFechaCorta(r.fecha_cosecha_estimada))}
        {dato('Costo total', formatearMoneda(r.costo_total))}
      </div>
      {r.gastos_por_etapa && <GastosPorEtapa r={r} editable={r.estado === 'planificado'} catalogo={catalogo} />}
      {bloqueEstado}
    </div>
  );
}

export function TarjetaAbrirSiembra({ hayChacras }: { hayChacras: boolean }) {
  return (
    <div className="card-panel siembra-cta">
      <h4><i className="ti ti-seedling" /> Registrar siembra o producción</h4>
      <p>Te haremos unas preguntas, una por una: dónde, qué (un cultivo, frutas, huevos, leche, queso…), cuándo y cuánto gastaste.</p>
      {puedeHablar && <span className="siembra-cta-voz"><i className="ti ti-volume" aria-hidden="true" /> Puedes escuchar las preguntas y responder hablando.</span>}
      {hayChacras
        ? <button type="button" className="btn btn-primary btn-abrir-asistente-siembra" onClick={abrirAsistenteSiembra}><i className="ti ti-player-play" aria-hidden="true" /> Empezar</button>
        : (
          <>
            <p className="mapa-ayuda" id="produccion-sin-chacras-aviso"><i className="ti ti-alert-circle" /> Antes necesitas registrar tu chacra.</p>
            <Link className="btn btn-primary btn-abrir-asistente-siembra-chacra" id="btn-ir-a-chacras" to="/productor/chacras"><i className="ti ti-map-pin" aria-hidden="true" /> Registrar mi chacra</Link>
          </>
        )}
    </div>
  );
}

export default function SiembrasPage() {
  const registros = useRegistros();
  const chacras = useChacras();
  const { data: catalogo = null } = usePlantillas();
  const lista = registros.data ?? [];
  return (
    <div className="productor-tab-panel active" data-tab-panel="siembras">
      <div className="section-header inner"><h3><i className="ti ti-plant-2" /> <Texto clave="seccion.siembras">Mis siembras y producciones</Texto></h3></div>
      <div id="productor-siembras">
        <div className="produccion-layout">
          <TarjetaAbrirSiembra hayChacras={(chacras.data ?? []).length > 0} />
          <div id="produccion-lista" className="produccion-lista">
            {registros.isLoading && <div className="skeleton-row-card"><div className="skeleton skeleton-line w-40" /><div className="skeleton skeleton-line tall w-70" /></div>}
            {!registros.isLoading && (lista.length
              ? lista.map((r, i) => <TarjetaRegistro key={r.id} r={r} indice={i} catalogo={catalogo} />)
              : <p className="muted" style={{ textAlign: 'center', padding: '16px 0' }}>Aún no registraste ninguna siembra.</p>)}
          </div>
        </div>
      </div>
    </div>
  );
}
