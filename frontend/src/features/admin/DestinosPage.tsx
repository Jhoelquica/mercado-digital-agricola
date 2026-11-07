// /admin/destinos: las ciudades fuera de Ayacucho a las que se vende, con sus condiciones de agencia de carga y de camión
// compartido (el radio de entrega se ve como un círculo en el mapa), y el punto de Huamanga donde el repartidor deja los
// pedidos que van por agencia.
import { useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { pedidos, type DestinoLejano, type UbicacionConfigurada } from '../../api/pedidos';
import type { MapaSeleccionableApi } from '../../components/Mapa';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { toast } from '../../components/Toast';
import { formatearMoneda } from '../../lib/formato';
import { hayCoordenadas } from '../../lib/productos';
import { EncabezadoSeccion, MensajeError, useDestinosAdmin, useRefrescarAdmin, Vacio } from './comun';

const MapaSeleccionable = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaSeleccionable })));

const sacosParaSalir = (capacidad: number | string | null | undefined, porcentaje: number | string | null | undefined) => Math.ceil((Number(capacidad) || 0) * (Number(porcentaje) || 0) / 100);
const numero = (n: number | string) => Number(n).toLocaleString('es-PE', { maximumFractionDigits: 2 });

function Dato({ etiqueta, children }: { etiqueta: string; children: ReactNode }) {
  return <div><dt>{etiqueta}</dt><dd>{children}</dd></div>;
}

function TarjetaDestino({ d, indice, alEditar }: { d: DestinoLejano; indice: number; alEditar: () => void }) {
  const salen = sacosParaSalir(d.camion_capacidad_sacos, d.camion_porcentaje_salida);
  return (
    <article className={`destino-card${d.activo ? '' : ' destino-oculto'}`} style={{ animationDelay: `${Math.min(indice, 8) * 40}ms` }} data-destino-id={d.id}>
      <header className="destino-cabecera">
        <div>
          <h4><i className="ti ti-map-pin" aria-hidden="true" /> {d.ciudad}</h4>
          <small>{d.departamento || 'Sin departamento'} · radio de {numero(d.radio_km)} km</small>
        </div>
        <span className={`pill pill-estado ${d.activo ? 'pill-aprobado' : 'pill-default'}`}><i className={`ti ti-${d.activo ? 'eye' : 'eye-off'}`} aria-hidden="true" /> {d.activo ? 'Visible' : 'Oculta'}</span>
      </header>
      <div className="destino-modalidades">
        <section className={`destino-modalidad${d.agencia_disponible ? '' : ' apagada'}`}>
          <h5><i className="ti ti-building-warehouse" aria-hidden="true" /> Agencia de carga</h5>
          {d.agencia_disponible
            ? <dl>
              <Dato etiqueta="Agencia">{d.agencia_nombre || 'Sin nombre'}</Dato>
              <Dato etiqueta="Oficina de recojo">{d.agencia_direccion || 'Sin dirección: se confirma con la guía'}</Dato>
              <Dato etiqueta="Flete aprox.">{d.agencia_flete_kg != null ? `${formatearMoneda(d.agencia_flete_kg)} / kg` : '—'}</Dato>
              <Dato etiqueta="Tarda">{d.agencia_dias != null ? `${d.agencia_dias} días` : '—'}</Dato>
            </dl>
            : <p className="destino-apagado">No se ofrece</p>}
        </section>
        <section className={`destino-modalidad${d.camion_disponible ? '' : ' apagada'}`}>
          <h5><i className="ti ti-truck" aria-hidden="true" /> Camión compartido</h5>
          {d.camion_disponible
            ? <>
              <dl>
                <Dato etiqueta="Precio por saco">{d.camion_precio_saco != null ? formatearMoneda(d.camion_precio_saco) : '—'}</Dato>
                <Dato etiqueta="Mínimo por comprador">{d.camion_minimo_sacos} sacos</Dato>
                <Dato etiqueta="Sale con">{salen} de {d.camion_capacidad_sacos} sacos ({d.camion_porcentaje_salida}%)</Dato>
                <Dato etiqueta="Tarda">{d.camion_dias != null ? `${d.camion_dias} días` : '—'}</Dato>
              </dl>
              {d.camion_precio_saco != null && <p className="destino-ingreso">Flete al salir: <b>{formatearMoneda(salen * d.camion_precio_saco)}</b></p>}
            </>
            : <p className="destino-apagado">No se ofrece</p>}
        </section>
      </div>
      {d.nota && <p className="destino-nota"><i className="ti ti-message" aria-hidden="true" /> {d.nota}</p>}
      <div className="tarjeta-acciones">
        <button type="button" className="btn btn-outline btn-editar-destino" onClick={alEditar}><i className="ti ti-pencil" aria-hidden="true" /> Editar {d.ciudad}</button>
      </div>
    </article>
  );
}

type CampoTexto = 'ciudad' | 'departamento' | 'latitud' | 'longitud' | 'radio_km' | 'agencia_nombre' | 'agencia_direccion' | 'agencia_flete_kg' | 'agencia_dias'
  | 'camion_precio_saco' | 'camion_minimo_sacos' | 'camion_capacidad_sacos' | 'camion_porcentaje_salida' | 'camion_dias' | 'nota';
type Interruptor = 'activo' | 'agencia_disponible' | 'camion_disponible';

function valoresIniciales(d: DestinoLejano | null): Record<CampoTexto, string> {
  const v = (valor: unknown, porDefecto = '') => (valor ?? porDefecto) === '' ? porDefecto : String(valor ?? porDefecto);
  return {
    ciudad: v(d?.ciudad), departamento: v(d?.departamento), latitud: v(d?.latitud), longitud: v(d?.longitud), radio_km: v(d?.radio_km, '40'),
    agencia_nombre: v(d?.agencia_nombre), agencia_direccion: v(d?.agencia_direccion), agencia_flete_kg: v(d?.agencia_flete_kg), agencia_dias: v(d?.agencia_dias),
    camion_precio_saco: v(d?.camion_precio_saco), camion_minimo_sacos: v(d?.camion_minimo_sacos, '50'), camion_capacidad_sacos: v(d?.camion_capacidad_sacos, '100'),
    camion_porcentaje_salida: v(d?.camion_porcentaje_salida, '80'), camion_dias: v(d?.camion_dias), nota: v(d?.nota),
  };
}

function FormDestino({ d, alCerrar }: { d: DestinoLejano | null; alCerrar: () => void }) {
  const refrescar = useRefrescarAdmin();
  const [valores, setValores] = useState(() => valoresIniciales(d));
  const [switches, setSwitches] = useState<Record<Interruptor, boolean>>({ activo: d ? d.activo : true, agencia_disponible: d ? d.agencia_disponible : true, camion_disponible: d ? d.camion_disponible : true });
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const mapa = useRef<MapaSeleccionableApi>(null);
  const radioMetros = (texto: string) => (Number(texto) || 40) * 1000;

  useEffect(() => {
    form.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    form.current?.querySelector<HTMLInputElement>('input[name="ciudad"]')?.focus({ preventScroll: true });
  }, []);

  function cambiar(nombre: CampoTexto, valor: string) {
    setError(null);
    const nuevos = { ...valores, [nombre]: valor };
    setValores(nuevos);
    if (nombre === 'radio_km' && hayCoordenadas(nuevos.latitud, nuevos.longitud)) mapa.current?.dibujarCirculo(Number(nuevos.latitud), Number(nuevos.longitud), radioMetros(valor), true);
    if ((nombre === 'latitud' || nombre === 'longitud') && hayCoordenadas(nuevos.latitud, nuevos.longitud)) {
      mapa.current?.moverMarcador(Number(nuevos.latitud), Number(nuevos.longitud));
      mapa.current?.dibujarCirculo(Number(nuevos.latitud), Number(nuevos.longitud), radioMetros(nuevos.radio_km), true);
    }
  }
  const campo = (etiqueta: string, nombre: CampoTexto, atributos: Record<string, string | number | boolean>, ayuda = '', deshabilitado = false) => (
    <label className="perfil-campo">{etiqueta}
      <input name={nombre} {...atributos} disabled={deshabilitado} value={valores[nombre]} onChange={(e) => cambiar(nombre, e.target.value)} />
      {ayuda && <small className="muted">{ayuda}</small>}
    </label>
  );
  const interruptor = (nombre: Interruptor, titulo: string, descripcion: string) => (
    <label className="interruptor">
      <span><strong>{titulo}</strong><small>{descripcion}</small></span>
      <input type="checkbox" role="switch" name={nombre} checked={switches[nombre]} onChange={(e) => { setError(null); setSwitches({ ...switches, [nombre]: e.target.checked }); }} />
    </label>
  );

  // Lo que resulta del camión según su capacidad, el porcentaje con que sale y el precio por saco.
  const capacidad = Number(valores.camion_capacidad_sacos);
  const porcentaje = Number(valores.camion_porcentaje_salida);
  const precio = Number(valores.camion_precio_saco);
  const salen = sacosParaSalir(capacidad, porcentaje);
  const calculo = switches.camion_disponible && capacidad && porcentaje
    ? <>El camión sale con <b>{salen} sacos</b> ({porcentaje}% de {capacidad}).{precio ? <> Flete al salir: <b>{formatearMoneda(salen * precio)}</b>; lleno: <b>{formatearMoneda(capacidad * precio)}</b>.</> : null}</>
    : null;

  async function guardar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const num = (nombre: CampoTexto) => (valores[nombre] === '' ? null : Number(valores[nombre]));
    const texto = (nombre: CampoTexto) => valores[nombre].trim() || null;
    const datos = {
      ciudad: valores.ciudad.trim(),
      departamento: texto('departamento'),
      latitud: num('latitud'),
      longitud: num('longitud'),
      radio_km: num('radio_km') ?? 40,
      activo: switches.activo,
      agencia_disponible: switches.agencia_disponible,
      agencia_nombre: texto('agencia_nombre'),
      agencia_direccion: texto('agencia_direccion'),
      agencia_flete_kg: num('agencia_flete_kg'),
      agencia_dias: num('agencia_dias'),
      camion_disponible: switches.camion_disponible,
      camion_precio_saco: num('camion_precio_saco'),
      camion_minimo_sacos: num('camion_minimo_sacos') ?? 50,
      camion_capacidad_sacos: num('camion_capacidad_sacos') ?? 100,
      camion_porcentaje_salida: num('camion_porcentaje_salida') ?? 80,
      camion_dias: num('camion_dias'),
      nota: texto('nota'),
    };
    const problema: [string, CampoTexto] | null = datos.ciudad.length < 2 ? ['Escribe el nombre de la ciudad.', 'ciudad']
      : datos.latitud == null || datos.longitud == null ? ['Ubica la ciudad: toca el mapa o escribe su latitud y longitud.', 'latitud'] : null;
    if (problema) {
      setError(problema[0]);
      form.current?.querySelector<HTMLInputElement>(`input[name="${problema[1]}"]`)?.focus();
      return;
    }
    setGuardando(true);
    try {
      const cuerpo = datos as Parameters<typeof pedidos.admin.crearDestino>[0];
      const guardado = d ? await pedidos.admin.actualizarDestino(d.id, cuerpo) : await pedidos.admin.crearDestino(cuerpo);
      toast(guardado.activo ? `${guardado.ciudad} guardada: los compradores ya ven estas condiciones.` : `${guardado.ciudad} guardada (oculta: los compradores no la ven).`);
      alCerrar();
      await refrescar('destinos');
    } catch (err) {
      setGuardando(false);
      setError((err as Error).message);
    }
  }

  return (
    <form ref={form} id="form-destino" className="card-panel form-destino" noValidate data-destino-id={d?.id ?? ''} onSubmit={(e) => { void guardar(e); }}>
      <h4><i className={`ti ti-${d ? 'pencil' : 'map-pin-plus'}`} aria-hidden="true" /> {d ? `Editar ${d.ciudad}` : 'Agregar una ciudad'}</h4>
      <div className="form-destino-cuerpo">
        <div className="form-destino-campos">
          <fieldset className="form-destino-grupo">
            <legend>Ciudad</legend>
            <div className="form-destino-grid">
              {campo('Ciudad *', 'ciudad', { type: 'text', maxLength: 80, required: true, placeholder: 'Ej. Lima' })}
              {campo('Departamento', 'departamento', { type: 'text', maxLength: 80, placeholder: 'Ej. Lima' })}
              {campo('Latitud *', 'latitud', { type: 'number', step: 'any', min: '-18.5', max: '0.2', required: true })}
              {campo('Longitud *', 'longitud', { type: 'number', step: 'any', min: '-81.5', max: '-68.5', required: true })}
              {campo('Radio de entrega (km)', 'radio_km', { type: 'number', step: '1', min: '1', max: '200' }, 'El comprador puede pedir si entrega dentro de este radio.')}
            </div>
            {interruptor('activo', 'Visible para los compradores', 'Si la apagas, la ciudad deja de aparecer al comprar (los pedidos ya hechos siguen).')}
          </fieldset>
          <fieldset className={`form-destino-grupo${switches.agencia_disponible ? '' : ' apagado'}`} data-grupo="agencia">
            <legend>Agencia de carga</legend>
            {interruptor('agencia_disponible', 'Ofrecer envío por agencia', 'El repartidor lo deja en una agencia de Huamanga; el comprador paga el flete al recoger.')}
            <div className="form-destino-grid form-destino-grupo-campos">
              {campo('Agencia', 'agencia_nombre', { type: 'text', maxLength: 80, list: 'agencias-sugeridas', placeholder: 'Ej. Shalom' }, '', !switches.agencia_disponible)}
              {campo('Oficina de recojo en esa ciudad', 'agencia_direccion', { type: 'text', maxLength: 160, placeholder: 'Dirección donde el comprador recoge' }, '', !switches.agencia_disponible)}
              {campo('Flete aprox. (S/ por kg)', 'agencia_flete_kg', { type: 'number', step: '0.01', min: '0', max: '100' }, '', !switches.agencia_disponible)}
              {campo('Días de viaje', 'agencia_dias', { type: 'number', step: '1', min: '0', max: '60' }, '', !switches.agencia_disponible)}
            </div>
          </fieldset>
          <fieldset className={`form-destino-grupo${switches.camion_disponible ? '' : ' apagado'}`} data-grupo="camion">
            <legend>Camión compartido</legend>
            {interruptor('camion_disponible', 'Ofrecer camión compartido', 'Para 50 sacos o más: 70% al pedir y 30% al llegar, antes de descargar.')}
            <div className="form-destino-grid form-destino-grupo-campos">
              {campo('Precio por saco (S/)', 'camion_precio_saco', { type: 'number', step: '0.01', min: '0' }, '', !switches.camion_disponible)}
              {campo('Mínimo por comprador (sacos)', 'camion_minimo_sacos', { type: 'number', step: '1', min: '1' }, '', !switches.camion_disponible)}
              {campo('Capacidad del camión (sacos)', 'camion_capacidad_sacos', { type: 'number', step: '1', min: '1' }, '', !switches.camion_disponible)}
              {campo('Sale al llegar al (%)', 'camion_porcentaje_salida', { type: 'number', step: '1', min: '1', max: '100' }, '', !switches.camion_disponible)}
              {campo('Días de viaje', 'camion_dias', { type: 'number', step: '1', min: '0', max: '60' }, '', !switches.camion_disponible)}
            </div>
            <p className="form-destino-calculo" aria-live="polite">{calculo}</p>
          </fieldset>
          <label className="perfil-campo">Nota para el comprador
            <textarea name="nota" rows={2} maxLength={300} placeholder="Ej. Entregas en el mercado mayorista de Santa Anita." value={valores.nota} onChange={(e) => cambiar('nota', e.target.value)} />
          </label>
        </div>
        <div className="form-destino-mapa">
          <span className="form-destino-mapa-titulo"><i className="ti ti-click" aria-hidden="true" /> Toca el mapa para ubicar la ciudad</span>
          <Suspense fallback={<div className="mapa-form-destino" />}>
            <MapaSeleccionable ref={mapa} id="mapa-form-destino" clase="mapa-form-destino" color="#2D3B2A"
              inicial={d ? { lat: d.latitud, lng: d.longitud } : null} radioInicialMetros={d ? radioMetros(String(d.radio_km)) : null}
              alSeleccionar={(lat, lng) => {
                setError(null);
                setValores((v) => ({ ...v, latitud: lat.toFixed(5), longitud: lng.toFixed(5) }));
                mapa.current?.dibujarCirculo(lat, lng, radioMetros(valores.radio_km));
              }} />
          </Suspense>
          <small className="muted">El círculo es el radio de entrega.</small>
        </div>
      </div>
      <MensajeError texto={error} />
      <div className="tarjeta-acciones form-destino-acciones">
        <button type="button" className="btn btn-ghost btn-cancelar-destino" onClick={alCerrar}>Cancelar</button>
        <button type="submit" className="btn btn-primary" disabled={guardando}><i className="ti ti-device-floppy" aria-hidden="true" /> Guardar</button>
      </div>
    </form>
  );
}

// Dónde deja el repartidor los pedidos que van por agencia de carga (el terminal de las agencias en Huamanga).
function AgenciaDespacho({ agencia }: { agencia: UbicacionConfigurada | null }) {
  const queryClient = useQueryClient();
  const [punto] = useState(() => (agencia && hayCoordenadas(agencia.latitud, agencia.longitud) ? { lat: Number(agencia.latitud), lng: Number(agencia.longitud) } : null));
  const [mostrado, setMostrado] = useState(punto);
  const [elegida, setElegida] = useState<{ latitud: number; longitud: number } | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function guardar() {
    if (!elegida) return;
    setGuardando(true);
    try {
      await pedidos.admin.actualizarAgenciaDespacho(elegida.latitud, elegida.longitud);
      setElegida(null);
      // El panel del repartidor lo vuelve a pedir.
      void queryClient.invalidateQueries({ queryKey: ['configuracion', 'agencia-despacho'] });
      toast('Punto de despacho guardado: los repartidores ya lo ven en el mapa.');
    } catch (err) {
      setError((err as Error).message);
    }
    setGuardando(false);
  }
  return (
    <div className="agencia-despacho-admin-cuerpo">
      <div className="agencia-despacho-admin-texto">
        <h4><i className="ti ti-building-warehouse" aria-hidden="true" /> Punto de despacho a agencias</h4>
        <p className="muted">Dónde deja el repartidor los pedidos que van por agencia de carga, en Huamanga (el terminal de las agencias). Toca el mapa para moverlo y guarda.</p>
        <dl className="carga-datos agencia-despacho-coordenadas">
          <div><dt>Latitud</dt><dd id="agencia-despacho-lat">{mostrado ? mostrado.lat.toFixed(5) : '—'}</dd></div>
          <div><dt>Longitud</dt><dd id="agencia-despacho-lng">{mostrado ? mostrado.lng.toFixed(5) : '—'}</dd></div>
        </dl>
        <MensajeError texto={error} />
        <button type="button" className="btn btn-primary" id="btn-guardar-agencia-despacho" disabled={!elegida || guardando} onClick={() => { void guardar(); }}><i className="ti ti-device-floppy" aria-hidden="true" /> Guardar el punto</button>
      </div>
      <Suspense fallback={<div className="mapa-agencia-despacho" />}>
        <MapaSeleccionable id="mapa-agencia-despacho" clase="mapa-agencia-despacho" color="#A16207" icono="building-warehouse" inicial={punto} zoomInicial={16}
          alSeleccionar={(lat, lng) => {
            setElegida({ latitud: Number(lat.toFixed(6)), longitud: Number(lng.toFixed(6)) });
            setMostrado({ lat, lng });
            setError(null);
          }} />
      </Suspense>
    </div>
  );
}

export default function DestinosPage() {
  const consulta = useDestinosAdmin();
  // null: cerrado; 'nuevo': agregar una ciudad; si no, la que se edita.
  const [editando, setEditando] = useState<DestinoLejano | 'nuevo' | null>(null);
  const destinos = consulta.data?.destinos ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="destinos">
      <EncabezadoSeccion icono="map-2" titulo="Destinos lejanos">
        <button type="button" className="btn btn-primary" id="btn-nuevo-destino" onClick={() => setEditando('nuevo')}><i className="ti ti-plus" aria-hidden="true" /> Agregar ciudad</button>
      </EncabezadoSeccion>
      <p className="muted admin-nota">Las ciudades fuera de Ayacucho a las que se vende. El comprador ve esta lista y sus condiciones al comprar: pon solo ciudades donde el viaje sea rentable.</p>
      <div id="destino-form-wrap" className={editando ? '' : 'hidden'}>
        {editando && <FormDestino key={editando === 'nuevo' ? 'nuevo' : editando.id} d={editando === 'nuevo' ? null : editando} alCerrar={() => setEditando(null)} />}
      </div>
      <datalist id="agencias-sugeridas">
        <option value="Shalom" />
        <option value="Marvisur" />
        <option value="Olva Courier" />
      </datalist>
      <div id="destinos-admin-lista" className="destinos-admin-grid">
        {consulta.isLoading ? <SkeletonFilas cantidad={1} /> : destinos.map((d, i) => <TarjetaDestino key={d.id} d={d} indice={i} alEditar={() => setEditando(d)} />)}
      </div>
      {consulta.data && !destinos.length && <Vacio icono="map-2" titulo="Todavía no hay ciudades lejanas" detalle="Agrega una ciudad para que los compradores de allá puedan pedir por agencia o por camión." />}
      <div id="agencia-despacho-admin" className="card-panel agencia-despacho-admin">
        {consulta.data && <AgenciaDespacho agencia={consulta.data.agencia} />}
      </div>
    </div>
  );
}

