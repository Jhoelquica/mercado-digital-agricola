// ---- Cómo llega una cosecha publicada al almacén (recojo en la chacra, ver transporte/logica_recojos.py) ----
// Referencias: 21st.dev "Radio Group in Card" (opciones con su precio), "AI Approval" (la elegida se despliega) y
// "Task Steps" (pasos del recojo con su estado). ui-ux-pro-max: el costo a la vista ANTES de pedir, botones de 48px,
// etiquetas visibles y aviso de éxito.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useRef, useState, type FormEvent, type ReactNode } from 'react';
import type { Chacra } from '../../../api/chacras';
import type { RegistroProduccion } from '../../../api/productores';
import type { ProductoMio } from '../../../api/productos';
import { recojos, type CotizacionRecojo, type Recojo } from '../../../api/recojos';
import { manejarError, toast } from '../../../components/Toast';
import { Texto } from '../../../idioma/IdiomaContext';
import { formatearMoneda } from '../../../lib/formato';
import { unidadPlural } from '../../../lib/productos';
import { fechaHoraLegible } from '../../../lib/reglas';
import { MediaProducto } from '../../catalogo/TarjetasProducto';
import { formatearTelefono } from '../../pedidos/estadoPedido';
import { formatearDistancia, formatearFechaCorta } from '../comun';

const MapaSeleccionable = lazy(() => import('../../../components/Mapa').then((m) => ({ default: m.MapaSeleccionable })));

export const ESTADOS_RECOJO_EN_CURSO = ['pendiente_asignacion', 'propuesto', 'asignado', 'recogido', 'en_almacen', 'en_camino'];
const PASOS_RECOJO = [
  { titulo: 'Buscando vehículo', estados: ['pendiente_asignacion', 'propuesto'] },
  { titulo: 'Vehículo en camino a tu chacra', estados: ['asignado'] },
  { titulo: 'Recogida', estados: ['recogido'] },
  { titulo: 'En el almacén, por pesar', estados: ['en_almacen'] },
  { titulo: 'Pesada y a la venta', estados: ['completado'] },
];

export const useRecojos = (activo = true) => useQuery({
  queryKey: ['productor', 'recojos'],
  queryFn: () => recojos.mios().catch((err) => { manejarError(err, 'cargar cómo llegan tus cosechas al almacén'); return [] as Recojo[]; }),
  enabled: activo,
});

export const recojoVigenteDe = (lista: Recojo[], productoId: string) =>
  lista.find((r) => String(r.producto_id) === String(productoId) && ESTADOS_RECOJO_EN_CURSO.includes(r.estado)) || null;

const fechaLocalIso = (f: Date) => `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, '0')}-${String(f.getDate()).padStart(2, '0')}`;

// Por defecto se recoge en la chacra de la siembra de esta cosecha.
function chacraDeProducto(p: ProductoMio, registros: RegistroProduccion[], chacras: Chacra[]) {
  const registro = registros.find((r) => String(r.id) === String(p.registro_produccion_id));
  return chacras.find((c) => String(c.id) === String(registro?.chacra_id)) || chacras[0] || null;
}

function Mensaje({ texto }: { texto: string }) {
  return texto
    ? <p className="form-entrega-mensaje error" role="alert"><i className="ti ti-alert-circle" /><span>{texto}</span></p>
    : <p className="form-entrega-mensaje hidden" role="alert" />;
}

function OpcionLlegada({ productoId, valor, icono, titulo, detalle, elegida, alElegir }: { productoId: string; valor: 'productor' | 'repartidor'; icono: string; titulo: string; detalle: string; elegida: string; alElegir: (v: 'productor' | 'repartidor') => void }) {
  return (
    <label className="opcion-lejana">
      <input type="radio" name={`llegada-${productoId}`} value={valor} checked={elegida === valor} onChange={() => alElegir(valor)} />
      <span className="opcion-lejana-caja">
        <span className="opcion-lejana-cabecera">
          <span className="opcion-lejana-icono"><i className={`ti ti-${icono}`} aria-hidden="true" /></span>
          <strong>{titulo}</strong>
        </span>
        <span className="opcion-lejana-detalle">{detalle}</span>
      </span>
    </label>
  );
}

function Cotizacion({ c }: { c: CotizacionRecojo }) {
  const peso = Number(c.peso_kg).toLocaleString('es-PE', { maximumFractionDigits: 1 });
  return (
    <>
      <dl className="alm-cotizacion-datos">
        <div><dt>Distancia al almacén</dt><dd>{formatearDistancia(c.distancia_km)}</dd></div>
        <div><dt>Peso</dt><dd>{peso} kg</dd></div>
        <div><dt>Tarifa</dt><dd>{formatearMoneda(c.tarifa_distancia)}{c.recargo_peso ? ` + ${formatearMoneda(c.recargo_peso)} por el peso` : ''}</dd></div>
      </dl>
      <p className="alm-cotizacion-total">{c.hay_vehiculo === false ? 'Costo estimado' : 'Costo del recojo'}: <b>{formatearMoneda(c.total)}</b></p>
      {c.hay_vehiculo === false && <p className="alm-cotizacion-aviso" role="status"><i className="ti ti-truck-off" aria-hidden="true" /><span>Ningún vehículo registrado carga {peso} kg{c.sacos ? ` (${c.sacos} sacos)` : ''}. Si lo pides, avisamos al administrador para que consiga un camión: te dirá quién va, cuándo y el flete antes de ir.</span></p>}
      <p className="alm-cotizacion-nota"><i className="ti ti-info-circle" aria-hidden="true" /> No pagas nada ahora: se descuenta de lo que ganes cuando se venda tu cosecha.</p>
    </>
  );
}

function EleccionLlegada({ p, chacra }: { p: ProductoMio; chacra: Chacra | null }) {
  const queryClient = useQueryClient();
  const hoy = new Date();
  const limite = new Date();
  limite.setDate(limite.getDate() + 60);
  const [modalidad, setModalidad] = useState<'' | 'productor' | 'repartidor'>('');
  const [mapaListo, setMapaListo] = useState(false);
  const [fecha, setFecha] = useState(fechaLocalIso(hoy));
  const [referencia, setReferencia] = useState('');
  const [telefono, setTelefono] = useState('');
  const [punto, setPunto] = useState<{ lat: number; lng: number } | null>(chacra ? { lat: Number(chacra.ubicacion_latitud), lng: Number(chacra.ubicacion_longitud) } : null);
  const [cotizacion, setCotizacion] = useState<{ estado: 'sin-punto' } | { estado: 'calculando' } | { estado: 'lista'; c: CotizacionRecojo } | { estado: 'error'; mensaje: string }>({ estado: 'sin-punto' });
  const [mensaje, setMensaje] = useState('');
  const [enviando, setEnviando] = useState(false);
  const turno = useRef(0);
  const formulario = useRef<HTMLFormElement>(null);

  async function cotizar(nuevo: { lat: number; lng: number } | null) {
    if (!nuevo) { setCotizacion({ estado: 'sin-punto' }); return; }
    const mio = ++turno.current;
    setCotizacion({ estado: 'calculando' });
    try {
      const c = await recojos.cotizar(p.id, nuevo.lat, nuevo.lng);
      if (mio === turno.current) setCotizacion({ estado: 'lista', c }); // si no, ya se movió el punto otra vez
    } catch (err) {
      if (mio === turno.current) setCotizacion({ estado: 'error', mensaje: (err as Error).message });
    }
  }

  function elegir(valor: 'productor' | 'repartidor') {
    setModalidad(valor);
    setMensaje('');
    if (valor === 'repartidor' && !mapaListo) {
      setMapaListo(true);
      void cotizar(punto);
    }
  }

  async function enviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!modalidad) { setMensaje('Elige cómo llega tu cosecha al almacén.'); return; }
    let datos: Parameters<typeof recojos.pedir>[0] = { producto_id: p.id, modalidad };
    if (modalidad === 'productor') {
      if (!fecha) {
        setMensaje('Elige qué día la llevas.');
        formulario.current?.querySelector<HTMLInputElement>('input[name="fecha"]')?.focus();
        return;
      }
      datos.fecha_estimada = `${fecha}T12:00:00`;
    } else {
      const digitos = telefono.replace(/\D/g, '');
      if (!punto) { setMensaje('Marca en el mapa dónde recoger tu cosecha.'); return; }
      if (!/^9\d{8}$/.test(digitos)) {
        setMensaje('Escribe tu celular de 9 dígitos: el repartidor te llama al llegar.');
        formulario.current?.querySelector<HTMLInputElement>('input[name="telefono"]')?.focus();
        return;
      }
      datos = { ...datos, latitud: punto.lat, longitud: punto.lng, referencia: referencia.trim() || null, telefono: digitos };
    }
    setEnviando(true);
    try {
      const recojo = await recojos.pedir(datos);
      toast(modalidad === 'productor'
        ? 'Listo: el almacén te espera ese día.'
        : recojo.estado === 'propuesto' ? 'Recojo pedido: ya se lo ofrecimos a un repartidor 🚚'
          : recojo.sin_vehiculo_registrado ? 'Recojo pedido: avisamos al administrador para que consiga un camión 🚚'
            : 'Recojo pedido: estamos buscando un repartidor con espacio.');
      await queryClient.invalidateQueries({ queryKey: ['productor', 'recojos'] });
    } catch (err) {
      setEnviando(false);
      setMensaje((err as Error).message);
    }
  }

  const listaParaPedir = cotizacion.estado === 'lista' && !enviando;
  return (
    <form ref={formulario} className="alm-form" data-producto-id={p.id} noValidate onSubmit={(e) => { void enviar(e); }} onInput={() => setMensaje('')}>
      <fieldset className="alm-opciones">
        <legend>¿Cómo llega al almacén?</legend>
        <OpcionLlegada productoId={p.id} valor="productor" icono="walk" titulo="La llevo yo" detalle="Sin costo. Solo dinos qué día la llevas." elegida={modalidad} alElegir={elegir} />
        <OpcionLlegada productoId={p.id} valor="repartidor" icono="truck" titulo="Pedir recojo en mi chacra" detalle="Un repartidor la recoge y la deja en el almacén. No pagas ahora: el costo se descuenta de lo que ganes cuando se venda." elegida={modalidad} alElegir={elegir} />
      </fieldset>
      <div className={`alm-detalle ${modalidad === 'productor' ? '' : 'hidden'}`} data-detalle="productor">
        <label className="perfil-campo">¿Qué día la llevas?
          <input type="date" name="fecha" min={fechaLocalIso(hoy)} max={fechaLocalIso(limite)} value={fecha} onChange={(e) => setFecha(e.target.value)} />
        </label>
        <button type="submit" className="btn btn-primary" disabled={enviando}><i className="ti ti-send" aria-hidden="true" /> Avisar al almacén</button>
      </div>
      <div className={`alm-detalle ${modalidad === 'repartidor' ? '' : 'hidden'}`} data-detalle="repartidor">
        <div className="alm-recojo">
          <div className="alm-mapa-bloque">
            <span className="alm-mapa-titulo"><i className="ti ti-map-pin" aria-hidden="true" /> Dónde recoger: toca el mapa para mover el punto</span>
            {mapaListo
              ? <Suspense fallback={<div className="alm-mapa" />}><MapaSeleccionable id={`mapa-recojo-${p.id}`} clase="alm-mapa" color="#2D3B2A" inicial={punto} alSeleccionar={(lat, lng) => { setPunto({ lat, lng }); void cotizar({ lat, lng }); }} /></Suspense>
              : <div className="alm-mapa" id={`mapa-recojo-${p.id}`} />}
          </div>
          <div className="alm-recojo-campos">
            <label className="perfil-campo">Cómo llegar (referencia)
              <input type="text" name="referencia" maxLength={200} placeholder="Ej. Pasando el puente, casa de adobe" autoComplete="off" value={referencia} onChange={(e) => setReferencia(e.target.value)} />
            </label>
            <label className="perfil-campo">Tu celular
              <input type="tel" name="telefono" inputMode="tel" maxLength={11} placeholder="987 654 321" autoComplete="tel" value={telefono} onChange={(e) => setTelefono(e.target.value)} />
              <small className="muted">El repartidor te llama al llegar.</small>
            </label>
            <div className="alm-cotizacion" aria-live="polite">
              {cotizacion.estado === 'sin-punto' && <p className="muted">Toca el mapa para marcar dónde recoger tu cosecha.</p>}
              {cotizacion.estado === 'calculando' && <p className="muted"><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Calculando el costo…</p>}
              {cotizacion.estado === 'error' && <p className="form-entrega-mensaje error"><i className="ti ti-alert-circle" aria-hidden="true" /><span>{cotizacion.mensaje}</span></p>}
              {cotizacion.estado === 'lista' && <Cotizacion c={cotizacion.c} />}
            </div>
            <button type="submit" className="btn btn-primary btn-pedir-recojo" disabled={!listaParaPedir}>
              <i className="ti ti-truck" aria-hidden="true" /> {cotizacion.estado === 'lista' ? `Pedir recojo por ${formatearMoneda(cotizacion.c.total)}` : 'Pedir recojo'}
            </button>
          </div>
        </div>
      </div>
      <Mensaje texto={mensaje} />
    </form>
  );
}

function AnularRecojo({ r, texto }: { r: Recojo; texto: string }) {
  const queryClient = useQueryClient();
  const [confirmando, setConfirmando] = useState(false);
  const [anulando, setAnulando] = useState(false);
  const no = useRef<HTMLButtonElement>(null);
  async function anular() {
    setAnulando(true);
    try {
      await recojos.cancelar(r.id);
      toast('Listo: ya puedes elegir otra vez cómo llega tu cosecha.');
      await queryClient.invalidateQueries({ queryKey: ['productor', 'recojos'] });
    } catch (err) {
      setAnulando(false);
      manejarError(err, 'anular el recojo');
    }
  }
  return (
    <div className="alm-anular">
      <button type="button" className={`btn btn-ghost btn-sm btn-anular-recojo ${confirmando ? 'hidden' : ''}`} onClick={() => { setConfirmando(true); setTimeout(() => no.current?.focus()); }}><i className="ti ti-x" aria-hidden="true" /> {texto}</button>
      <div className={`alm-anular-confirmar ${confirmando ? '' : 'hidden'}`} role="group" aria-label={texto}>
        <span>{r.estado !== 'asignado' ? '¿Seguro?' : r.transporte_contratado
          ? 'Ya hay un camión contratado para ir: avisaremos al administrador para que no vaya. ¿Anular igual?'
          : 'El repartidor ya aceptó: avísale que no vaya. ¿Anular igual?'}</span>
        <button ref={no} type="button" className="btn btn-ghost btn-sm btn-anular-no" onClick={() => setConfirmando(false)}>No</button>
        <button type="button" className="btn btn-danger btn-sm btn-anular-si" disabled={anulando} onClick={() => { void anular(); }}>Sí, anular</button>
      </div>
    </div>
  );
}

function EstadoRecojo({ r }: { r: Recojo }) {
  if (r.modalidad === 'productor') {
    return (
      <div className="alm-estado">
        <div className="envio-aviso info"><i className="ti ti-walk" aria-hidden="true" />
          <div><strong>La llevas tú{r.fecha_estimada ? ` el ${formatearFechaCorta(r.fecha_estimada)}` : ''}</strong>
            <span>Cuando llegue, el almacén la pesa y sale a la venta.</span></div></div>
        <AnularRecojo r={r} texto="Cambiar cómo llega" />
      </div>
    );
  }
  const actual = Math.max(0, PASOS_RECOJO.findIndex((p) => p.estados.includes(r.estado)));
  const peso = Number(r.peso_kg || 0).toLocaleString('es-PE', { maximumFractionDigits: 1 });
  const repartidor = r.repartidor ? `${r.repartidor.nombre} (${r.repartidor.tipo_vehiculo || 'vehículo'})` : 'El repartidor';
  const t = r.transporte_contratado; // H8: el camión que contrató el administrador
  const detalle = ({
    pendiente_asignacion: r.sin_vehiculo_registrado
      ? `Ningún vehículo registrado carga ${peso} kg: avisamos al administrador para que consiga un camión. Te diremos quién va, cuándo y el flete.`
      : `Todavía no hay un repartidor libre con espacio para ${peso} kg. Seguimos buscando; aquí verás cuando uno acepte.`,
    propuesto: 'Se lo ofrecimos a un repartidor: falta que acepte.',
    asignado: t
      ? <>{t.nombre} pasa a recogerla el {fechaHoraLegible(t.fecha_programada)} en el vehículo {t.placa}. Su celular: <a href={`tel:${t.telefono}`}>{formatearTelefono(t.telefono)}</a>.</>
      : `${repartidor} va a recogerla. Te llamará al ${formatearTelefono(r.telefono || '')}.`,
    recogido: t ? `${t.nombre} ya la recogió y la lleva al almacén.` : 'El repartidor ya la recogió y la lleva al almacén.',
    en_almacen: 'Ya está en el almacén: falta que la pesen. Ahí sale a la venta.',
  } as Record<string, ReactNode>)[r.estado] || '';
  return (
    <div className="alm-estado">
      <ol className="alm-pasos" aria-label="Cómo va el recojo">
        {PASOS_RECOJO.map((p, i) => (
          <li key={p.titulo} className={i < actual ? 'hecho' : i === actual ? 'actual' : ''} aria-current={i === actual ? 'step' : undefined}>
            <span className="alm-paso-punto" aria-hidden="true">{i < actual && <i className="ti ti-check" />}</span>
            <div><strong>{p.titulo}</strong>{i === actual && detalle ? <small>{detalle}</small> : null}</div>
          </li>
        ))}
      </ol>
      <p className="alm-costo"><i className="ti ti-receipt" aria-hidden="true" /><span>{t ? 'Flete acordado' : r.sin_vehiculo_registrado ? 'Costo estimado' : 'Recojo'}: <b>{formatearMoneda(r.costo)}</b>, se descuenta de lo que ganes cuando se venda.{t ? ' Si no estás de acuerdo, anúlalo antes de que pase.' : ''}</span></p>
      {['pendiente_asignacion', 'propuesto', 'asignado'].includes(r.estado) && <AnularRecojo r={r} texto="Anular el recojo" />}
    </div>
  );
}

export function LlegadasAlmacen({ enTransito, registros, chacras }: { enTransito: ProductoMio[]; registros: RegistroProduccion[]; chacras: Chacra[] }) {
  const consulta = useRecojos(enTransito.length > 0);
  if (!enTransito.length || consulta.isLoading) return null;
  const lista = consulta.data ?? [];
  return (
    <section id="productor-llegadas" className="alm-seccion" aria-labelledby="productor-llegadas-titulo">
      <div className="section-header inner">
        <h3 id="productor-llegadas-titulo"><i className="ti ti-building-warehouse" /> <Texto clave="seccion.almacen">Cosechas por llevar al almacén</Texto></h3>
      </div>
      <p className="muted alm-intro">Tu cosecha sale a la venta cuando el almacén la recibe y la pesa. Elige cómo llega: la llevas tú o un repartidor la recoge en tu chacra.</p>
      <div id="productor-llegadas-lista" className="alm-lista">
        {enTransito.map((p, i) => {
          const recojo = recojoVigenteDe(lista, p.id);
          const stock = Number(p.stock ?? 0);
          return (
            <article className="alm-card" data-producto-id={p.id} key={p.id} style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}>
              <div className="alm-producto">
                <MediaProducto producto={p} clase="alm-foto" />
                <div>
                  <h4>{p.nombre}</h4>
                  <p>Declaraste <b>{p.stock} {unidadPlural(p.unidad_medida, stock)}</b></p>
                  <span className="pill pill-estado pill-en_transito"><i className="ti ti-truck" aria-hidden="true" /> Por llegar al almacén</span>
                </div>
              </div>
              <div className="alm-cuerpo">{recojo ? <EstadoRecojo r={recojo} /> : <EleccionLlegada key={`${p.id}-libre`} p={p} chacra={chacraDeProducto(p, registros, chacras)} />}</div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
