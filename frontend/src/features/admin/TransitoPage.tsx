// /admin/transito: productos publicados que todavía no llegan al almacén. Cómo llega cada uno (lo trae el agricultor, un
// repartidor o un transporte contratado por el admin cuando ningún vehículo registrado la carga) y la confirmación de la
// llegada con lo que pesó el almacén: desde ahí se puede comprar.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type FormEvent } from 'react';
import { productores } from '../../api/productores';
import { productos, type ProductoCatalogo } from '../../api/productos';
import { recojos, type Recojo } from '../../api/recojos';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { formatearMoneda } from '../../lib/formato';
import { etiquetaDeGeocercaDelAlmacen } from '../../lib/geocerca';
import { CODIGOS_UNIDADES, infoUnidad, nombreCategoria } from '../../lib/productos';
import { datosTransporteContratado, evaluarCantidadLlegada, fechaHoraLegible, type CamposTransporteContratado } from '../../lib/reglas';
import { formatearTelefono } from '../pedidos/estadoPedido';
import { formatearFechaCorta } from '../productor/comun';
import { EncabezadoSeccion, useProductosEnTransito, useRefrescarAdmin } from './comun';

// H8 (validación con usuarios del 2026-10-13: 1600 kg de papa y solo una moto registrada): los datos del vehículo, el
// teléfono y el día en un solo formulario; cada error va junto a su campo y el botón dice lo que pasa al tocarlo.
const CAMPOS_TRANSPORTE: { campo: keyof CamposTransporteContratado; etiqueta: string; tipo: string; extra: Record<string, string | number> }[] = [
  { campo: 'nombre', etiqueta: 'Conductor', tipo: 'text', extra: { maxLength: 80, autoComplete: 'off', placeholder: 'Nombre y apellido' } },
  { campo: 'telefono', etiqueta: 'Celular del conductor', tipo: 'tel', extra: { inputMode: 'tel', maxLength: 11, placeholder: '987 654 321' } },
  { campo: 'placa', etiqueta: 'Placa', tipo: 'text', extra: { maxLength: 10, autoComplete: 'off', placeholder: 'ABC-123' } },
  { campo: 'capacidadKg', etiqueta: 'Carga hasta (kg)', tipo: 'number', extra: { inputMode: 'decimal', min: '1', step: 'any' } },
  { campo: 'capacidadSacos', etiqueta: 'Sacos que entran (opcional)', tipo: 'number', extra: { inputMode: 'numeric', min: '1', step: '1' } },
  { campo: 'fecha', etiqueta: 'Día y hora del recojo', tipo: 'datetime-local', extra: {} },
  { campo: 'flete', etiqueta: 'Flete acordado (S/)', tipo: 'number', extra: { inputMode: 'decimal', min: '1', step: 'any' } },
];
const VACIO_TRANSPORTE: CamposTransporteContratado = { nombre: '', telefono: '', placa: '', capacidadKg: '', capacidadSacos: '', fecha: '', flete: '' };

function FormTransporteContratado({ r }: { r: Recojo }) {
  const refrescar = useRefrescarAdmin();
  const [abierto, setAbierto] = useState(false);
  const [valores, setValores] = useState(VACIO_TRANSPORTE);
  const [error, setError] = useState<{ campo: keyof CamposTransporteContratado; texto: string } | null>(null);
  const [enviando, setEnviando] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const botonAbrir = useRef<HTMLButtonElement>(null);
  const enfocar = (campo: keyof CamposTransporteContratado) => setTimeout(() => form.current?.querySelector<HTMLInputElement>(`input[name="${campo}"]`)?.focus());
  function mostrarError(campo: keyof CamposTransporteContratado, texto: string) {
    setError({ campo, texto });
    enfocar(campo);
  }
  async function enviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const resultado = datosTransporteContratado(valores, { pesoKg: Number(r.peso_kg) || null, sacos: Number(r.sacos) || null });
    if (resultado.datos === null) { mostrarError(resultado.campo, resultado.error); return; }
    setEnviando(true);
    try {
      await recojos.contratarTransporte(r.id, resultado.datos);
      toast('Transporte asignado: le avisamos al agricultor quién va, cuándo y el flete 🚚');
      await refrescar('transito');
    } catch (err) {
      setEnviando(false);
      // El servidor dice qué no alcanza o qué falta (capacidad, sacos, fecha...): se muestra en el campo que corresponde.
      const texto = (err as Error).message || 'No se pudo asignar el transporte.';
      const campo = /sacos/i.test(texto) ? 'capacidadSacos' : /kg/.test(texto) ? 'capacidadKg' : /celular/i.test(texto) ? 'telefono'
        : /placa/i.test(texto) ? 'placa' : /día/i.test(texto) ? 'fecha' : 'nombre';
      mostrarError(campo, texto);
    }
  }
  const id = r.id;
  return (
    <>
      <button ref={botonAbrir} type="button" className={`btn ${r.sin_vehiculo_registrado ? 'btn-primary' : 'btn-outline'} btn-sm btn-abrir-transporte ${abierto ? 'hidden' : ''}`} data-recojo-id={id} aria-expanded={abierto} aria-controls={`ft-${id}`}
        onClick={() => { setAbierto(true); enfocar('nombre'); }}>
        <i className="ti ti-truck-delivery" aria-hidden="true" /> Asignar transporte contratado</button>
      <form ref={form} className={`form-transporte ${abierto ? '' : 'hidden'}`} id={`ft-${id}`} data-recojo-id={id} noValidate onSubmit={(e) => { void enviar(e); }}>
        <p className="ft-titulo"><strong>Transporte contratado</strong>
          <small>El conductor no necesita la app: tú marcas cuándo la recogió y le pagas el flete por fuera. Al agricultor le avisamos quién va, cuándo y cuánto cuesta (se le descuenta al vender).</small></p>
        <div className="ft-campos">
          {CAMPOS_TRANSPORTE.map((c) => {
            const conError = error?.campo === c.campo;
            return (
              <label key={c.campo} className={`ft-campo ft-${c.campo}`} htmlFor={`ft-${c.campo}-${id}`}>{c.etiqueta}
                <input id={`ft-${c.campo}-${id}`} name={c.campo} type={c.tipo} {...c.extra} aria-describedby={`ft-${c.campo}-${id}-error`} aria-invalid={conError || undefined} disabled={enviando}
                  value={valores[c.campo]} onChange={(e) => setValores({ ...valores, [c.campo]: e.target.value })} />
                <small className={`ft-error ${conError ? '' : 'hidden'}`} id={`ft-${c.campo}-${id}-error`} role="alert">{conError ? error.texto : ''}</small>
              </label>
            );
          })}
        </div>
        <div className="ft-acciones">
          <button type="submit" className="btn btn-primary btn-sm" disabled={enviando}><i className="ti ti-send" aria-hidden="true" /> Asignar y avisar al agricultor</button>
          <button type="button" className="btn btn-ghost btn-sm btn-cerrar-transporte" disabled={enviando} onClick={() => { setAbierto(false); setTimeout(() => botonAbrir.current?.focus()); }}>Cancelar</button>
        </div>
      </form>
    </>
  );
}

function BotonRecogidoContratado({ r }: { r: Recojo }) {
  const refrescar = useRefrescarAdmin();
  const [enviando, setEnviando] = useState(false);
  async function marcar() {
    setEnviando(true);
    try {
      await recojos.recogidoContratado(r.id);
      toast('Listo: le avisamos al agricultor que ya recogieron su cosecha.');
      await refrescar('transito');
    } catch (err) {
      setEnviando(false);
      manejarError(err, 'marcar el recojo');
    }
  }
  return <button type="button" className="btn btn-outline btn-sm btn-recogido-contratado" data-recojo-id={r.id} disabled={enviando} onClick={() => { void marcar(); }}><i className="ti ti-package-import" aria-hidden="true" /> Ya la recogió</button>;
}

// Cómo llega al almacén la cosecha que se espera (lo que eligió el agricultor; ver transporte/logica_recojos.py).
function LlegadaEsperada({ r }: { r: Recojo | null }) {
  if (!r) return <p className="alm-admin-llega sin-coordinar"><i className="ti ti-help-circle" aria-hidden="true" /> El agricultor todavía no dijo cómo la trae.</p>;
  if (r.modalidad === 'productor') {
    return <p className="alm-admin-llega"><i className="ti ti-walk" aria-hidden="true" /> La trae el agricultor{r.fecha_estimada ? ` el ${formatearFechaCorta(r.fecha_estimada)}` : ''}.</p>;
  }
  const t = r.transporte_contratado;
  if (t) {
    // H8: el camión que contrató el admin (el conductor no tiene la app: el admin marca cuándo la recogió).
    return (
      <>
        <p className={`alm-admin-llega${r.estado === 'recogido' ? ' lista' : ''}`}><i className="ti ti-truck" aria-hidden="true" />{' '}
          <span>{r.estado === 'asignado'
            ? <>{t.nombre} la recoge el {fechaHoraLegible(t.fecha_programada)} (vehículo {t.placa}, <a href={`tel:${t.telefono ?? ''}`}>{formatearTelefono(t.telefono)}</a>).</>
            : `${t.nombre} (vehículo ${t.placa}) ya la recogió y la trae: pésala y confirma la llegada.`}</span>
          <small>Transporte contratado. Flete de {formatearMoneda(r.costo)}: se lo pagas tú al conductor y se le descuenta al agricultor al vender.</small></p>
        {r.estado === 'asignado' && <BotonRecogidoContratado r={r} />}
      </>
    );
  }
  const quien = r.repartidor?.nombre || 'El repartidor';
  const kilos = Number(r.peso_kg || 0).toLocaleString('es-PE', { maximumFractionDigits: 1 });
  const carga = `${kilos} kg${r.sacos ? ` (${r.sacos} sacos)` : ''}`;
  const texto = ({
    pendiente_asignacion: r.sin_vehiculo_registrado
      ? `Ningún vehículo registrado carga ${carga}: contrata un transporte y asígnalo aquí.`
      : 'Recojo pedido: todavía sin repartidor con espacio.',
    propuesto: 'Recojo ofrecido a un repartidor: falta que acepte.',
    asignado: `${quien} va a recogerla en la chacra.`,
    recogido: `${quien} ya la recogió y la trae.`,
    en_almacen: `${quien} la dejó en el almacén: pésala y confirma la llegada.`,
  } as Record<string, string>)[r.estado] || r.estado;
  const sinVehiculo = r.estado === 'pendiente_asignacion' && r.sin_vehiculo_registrado;
  return (
    <>
      <p className={`alm-admin-llega${r.estado === 'en_almacen' ? ' lista' : ''}${sinVehiculo ? ' sin-coordinar' : ''}`}><i className={`ti ti-${sinVehiculo ? 'truck-off' : 'truck'}`} aria-hidden="true" /> {texto}
        <small>{sinVehiculo
          ? `Costo estimado con la tarifa de la app: ${formatearMoneda(r.costo)}. Si contratas un camión, el flete que acuerdes lo reemplaza.`
          : `Recojo de ${formatearMoneda(r.costo)}: se le paga al repartidor y se le descuenta al agricultor al vender.`}</small></p>
      {['pendiente_asignacion', 'propuesto'].includes(r.estado) && <FormTransporteContratado r={r} />}
    </>
  );
}

function FormLlegada({ p, declarada, unidad }: { p: ProductoCatalogo; declarada: number; unidad: string }) {
  const queryClient = useQueryClient();
  const [cantidad, setCantidad] = useState(String(declarada));
  const [observaciones, setObservaciones] = useState('');
  const [enviando, setEnviando] = useState(false);
  const campo = useRef<HTMLInputElement>(null);
  const evaluacion = evaluarCantidadLlegada(cantidad, declarada, unidad);
  const idCantidad = `llegada-cantidad-${p.id}`;
  async function confirmar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (evaluacion.cantidad === null) { campo.current?.focus(); return; }
    setEnviando(true);
    try {
      await productos.confirmarLlegadaAlmacen(p.id, evaluacion.cantidad, observaciones.trim());
      toast(`Llegada confirmada: ${evaluacion.cantidad} ${unidad} ya se pueden comprar y se avisó a quienes lo reservaron 📦`);
      // Sale de la lista sin recargar toda la vista (el resumen de Inicio lee la misma lista).
      queryClient.setQueryData<{ p: ProductoCatalogo }[]>(['admin', 'transito'], (lista) => lista?.filter((item) => String(item.p.id) !== String(p.id)));
      void queryClient.invalidateQueries({ queryKey: ['catalogo'] });
    } catch (err) {
      manejarError(err, 'confirmar la llegada al almacén');
      setEnviando(false);
    }
  }
  return (
    <form className="form-llegada" data-producto-id={p.id} data-declarada={declarada} data-unidad={unidad} noValidate onSubmit={(e) => { void confirmar(e); }}>
      <div className="form-llegada-campos">
        <label htmlFor={idCantidad}>Cantidad recibida ({unidad})
          <input ref={campo} id={idCantidad} type="number" name="cantidad" inputMode="numeric" min="1" max={declarada} step="1" required aria-describedby={`${idCantidad}-ayuda`}
            aria-invalid={evaluacion.estado === 'error'} disabled={enviando} value={cantidad} onChange={(e) => setCantidad(e.target.value)} />
          <span className={`form-llegada-ayuda ${evaluacion.estado}`} id={`${idCantidad}-ayuda`} role="status" aria-live="polite">{evaluacion.mensaje}</span>
        </label>
        <label>Observaciones (opcional)
          <input type="text" name="observaciones" maxLength={300} placeholder="Ej.: 2 cajas golpeadas" autoComplete="off" disabled={enviando} value={observaciones} onChange={(e) => setObservaciones(e.target.value)} />
        </label>
      </div>
      <div className="form-llegada-acciones">
        <button type="submit" className="btn btn-primary btn-sm btn-confirmar-llegada" disabled={enviando || evaluacion.cantidad === null}><i className="ti ti-building-warehouse" /> Confirmar llegada al almacén</button>
      </div>
    </form>
  );
}

// Dónde estaba el agricultor respecto de su chacra al registrar esta cosecha: una sola etiqueta, con ícono y texto. El
// servidor manda el resultado de la geocerca, nunca coordenadas. Si falla o el registro no tiene nada, no se muestra.
function EtiquetaGeocerca({ registroId }: { registroId: string }) {
  const { data } = useQuery({
    queryKey: ['admin', 'geocerca', registroId],
    queryFn: () => productores.produccion.geocerca(registroId).catch(() => null),
    staleTime: 5 * 60_000,
  });
  const etiqueta = data ? etiquetaDeGeocercaDelAlmacen(data.cosecha, data.radio_km) : null;
  if (!etiqueta) return null;
  return (
    <p className={`alm-admin-geocerca${etiqueta.advertencia ? ' es-advertencia' : ''}`} data-resultado={etiqueta.resultado}>
      <i className={`ti ti-${etiqueta.icono}`} aria-hidden="true" /> <span>{etiqueta.texto}</span>
    </p>
  );
}

function TarjetaProductoTransito({ p, reservas, recojo, indice }: { p: ProductoCatalogo; reservas: number; recojo: Recojo | null; indice: number }) {
  const unidad = p.unidad_medida && CODIGOS_UNIDADES.includes(p.unidad_medida) ? infoUnidad(p.unidad_medida).etiqueta : (p.unidad_medida || 'kg');
  const declarada = Math.max(Number(p.stock) || 0, 0);
  const dato = (etiqueta: string, valor: string | number) => <div className="perfil-dato"><span className="perfil-dato-label">{etiqueta}</span><span className="perfil-dato-valor">{valor}</span></div>;
  return (
    <div className="pedido-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-producto-id={p.id}>
      <div className="pedido-card-header">
        <h4>{p.nombre}</h4>
        <span className="pill pill-estado pill-en_transito"><i className="ti ti-truck" /> En tránsito</span>
      </div>
      <div className="perfil-datos-grid">
        {dato('Productor', p.productor_nombre || '—')}
        {dato('Categoría', nombreCategoria(p.categoria))}
        {dato('Precio', formatearMoneda(p.precio))}
        {dato('Declarado', `${declarada} ${unidad}`)}
        {dato('Reservas', reservas)}
      </div>
      <LlegadaEsperada r={recojo} />
      {p.registro_produccion_id && <EtiquetaGeocerca registroId={p.registro_produccion_id} />}
      <FormLlegada p={p} declarada={declarada} unidad={unidad} />
    </div>
  );
}

export default function TransitoPage() {
  const consulta = useProductosEnTransito();
  const refrescar = useRefrescarAdmin();
  const lista = consulta.data ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="transito">
      <EncabezadoSeccion icono="building-warehouse" titulo="Productos en tránsito" alRefrescar={() => { void refrescar('transito'); }} />
      <div id="productos-transito-lista" className="grid-admin">
        {consulta.isFetching && !consulta.data ? <SkeletonFilas cantidad={2} />
          : lista.map((item, i) => <TarjetaProductoTransito key={`${item.p.id}-${item.recojo?.estado ?? ''}-${item.recojo?.transporte_contratado ? 'c' : ''}`} p={item.p} reservas={item.reservas} recojo={item.recojo} indice={i} />)}
      </div>
      {consulta.data && !lista.length && (
        <div id="productos-transito-empty" className="empty-state">
          <span className="empty-state-icon"><i className="ti ti-package-off" /></span>
          <p><strong>No hay productos pendientes de confirmar</strong></p>
        </div>
      )}
    </div>
  );
}

