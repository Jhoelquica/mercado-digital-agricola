// /admin/viajes: los viajes compartidos en camión. El admin le asigna a cada uno un transportista verificado que cargue todos
// los sacos y, si hace falta, adelanta la salida; el transportista marca la salida y la llegada.
import { useState, type FormEvent } from 'react';
import { transporte, type Transportista, type Viaje } from '../../api/transporte';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor } from '../../lib/formato';
import { BotonViaje, EnviosDelViaje, ETAPAS_VIAJE, EtapasViaje, MedidorViaje } from '../viajes/piezas';
import { EncabezadoSeccion, kgYSacos, useRefrescarAdmin, useViajesAdmin, Vacio } from './comun';

function AsignacionViaje({ v, verificados }: { v: Viaje; verificados: Transportista[] }) {
  const refrescar = useRefrescarAdmin();
  const actual = v.transportista?.id;
  const [elegido, setElegido] = useState(actual ?? '');
  const [enviando, setEnviando] = useState(false);
  if (!['abierto', 'listo'].includes(v.estado)) return null;
  const sacos = Number(v.sacos_cargados) || 0;
  const aptos = verificados.filter((t) => Number(t.capacidad_maxima_kg) >= sacos * 50);
  if (!aptos.length) {
    return (
      <p className="viaje-nota viaje-nota-alerta"><i className="ti ti-alert-triangle" aria-hidden="true" />{' '}
        No hay transportistas verificados que carguen {sacos} sacos ({(sacos * 50).toLocaleString('es-PE')} kg). Revisa la pestaña Transportistas.</p>
    );
  }
  if (actual && !aptos.some((t) => t.id !== actual)) return null; // no hay otro a quien pasárselo
  async function asignar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!elegido) { (e.currentTarget.elements.namedItem('transportista') as HTMLSelectElement).focus(); return; }
    setEnviando(true);
    try {
      const viaje = await transporte.admin.asignarViaje(v.id, elegido);
      toast(`Viaje a ${viaje.destino_ciudad} asignado a ${viaje.transportista?.nombre || 'el transportista'} 🚛`);
      await refrescar('viajes');
    } catch (err) {
      manejarError(err, 'asignar el transportista');
    }
    setEnviando(false);
  }
  return (
    <form className="form-asignar-viaje" data-viaje-id={v.id} onSubmit={(e) => { void asignar(e); }}>
      <label>{actual ? 'Cambiar transportista' : 'Transportista'}
        <select name="transportista" required value={elegido} disabled={enviando} onChange={(e) => setElegido(e.target.value)}>
          {!actual && <option value="">Elige uno verificado…</option>}
          {aptos.map((t) => <option key={t.id} value={t.id}>{t.nombre} · {t.placa || 'sin placa'} · {kgYSacos(t.capacidad_maxima_kg)}</option>)}
        </select>
      </label>
      <button type="submit" className="btn btn-outline" disabled={enviando}>{actual ? 'Cambiar' : 'Asignar'}</button>
    </form>
  );
}

function TarjetaViajeAdmin({ v, verificados, indice }: { v: Viaje; verificados: Transportista[]; indice: number }) {
  const refrescar = useRefrescarAdmin();
  const ciudad = v.destino_ciudad || 'su destino';
  const cargados = Number(v.sacos_cargados) || 0;
  const paraSalir = Number(v.sacos_para_salir) || 0;
  const etapa = ETAPAS_VIAJE.find((e) => e.estado === v.estado);
  const t = v.transportista;
  async function salir(forzar: boolean) {
    try {
      const viaje = await transporte.admin.salirViaje(v.id, forzar);
      toast(`El viaje a ${viaje.destino_ciudad} salió: los compradores ya lo ven en camino 🚛`);
      await refrescar('viajes');
      return true;
    } catch (err) {
      manejarError(err, 'hacer salir el viaje');
      return false;
    }
  }
  let acciones = null;
  if (t && v.estado === 'listo') {
    acciones = <BotonViaje key="salir" accion="salir" icono="truck-delivery" texto={`Salir hacia ${ciudad}`}
      pregunta={`Ya juntó ${cargados} sacos. ¿Sale ahora con ${t.nombre}? (El transportista también puede marcar la salida.)`} confirmar="Sí, salir ahora" alConfirmar={() => salir(false)} />;
  } else if (t && v.estado === 'abierto') {
    acciones = <BotonViaje key="forzar" accion="forzar" icono="clock-bolt" texto="Adelantar la salida" claseBoton="btn-outline"
      pregunta={`Lleva ${cargados} de los ${paraSalir} sacos con que sale: el flete juntado puede no cubrir el viaje. ¿Salir igual?`} confirmar="Sí, salir con menos carga" alConfirmar={() => salir(true)} />;
  } else if (v.estado === 'en_ruta') {
    acciones = <p className="viaje-nota"><i className="ti ti-road" aria-hidden="true" /> El transportista marca la llegada.{v.fecha_salida ? ` Salió el ${formatearFechaServidor(v.fecha_salida)}` : ''}</p>;
  } else if (v.estado === 'llegado') {
    acciones = <p className="viaje-nota"><i className="ti ti-wallet" aria-hidden="true" /> Cada pedido se descarga con su saldo pagado y el código del comprador.{v.fecha_llegada ? ` Llegó el ${formatearFechaServidor(v.fecha_llegada)}` : ''}</p>;
  }
  return (
    <article className="viaje-card viaje-card-admin" style={{ animationDelay: `${Math.min(indice, 8) * 50}ms` }} data-viaje-id={v.id}>
      <div className="viaje-cabecera">
        <div>
          <h4><i className="ti ti-route" aria-hidden="true" /> Huamanga → {ciudad}</h4>
          <small>Viaje #{String(v.id).slice(0, 8)}</small>
        </div>
        <span className={`pill pill-estado ${v.estado === 'listo' ? 'pill-pendiente' : 'pill-en_camino'}`}>{etapa?.texto || v.estado}</span>
      </div>
      <EtapasViaje v={v} />
      <MedidorViaje v={v} />
      <EnviosDelViaje v={v} />
      {t
        ? <div className="viaje-transportista"><i className="ti ti-steering-wheel" aria-hidden="true" /><div><strong>{t.nombre}</strong><small>Placa {t.placa || '—'} · carga hasta {kgYSacos(t.capacidad_maxima_kg)}</small></div></div>
        : <p className="viaje-nota viaje-nota-alerta"><i className="ti ti-user-question" aria-hidden="true" /> Sin transportista asignado.</p>}
      <AsignacionViaje key={`${t?.id ?? ''}-${v.estado}`} v={v} verificados={verificados} />
      {acciones}
    </article>
  );
}

export default function ViajesPage() {
  const consulta = useViajesAdmin();
  const refrescar = useRefrescarAdmin();
  const viajes = consulta.data?.viajes ?? [];
  const verificados = consulta.data?.verificados ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="viajes">
      <EncabezadoSeccion icono="route" titulo="Viajes en camión" alRefrescar={() => { void refrescar('viajes'); }} />
      <p className="muted admin-nota">Los pedidos por camión de una misma ciudad se juntan en un viaje compartido. El viaje queda "Listo para salir" al juntar el porcentaje de carga de esa ciudad. Asígnale un transportista verificado que cargue todos los sacos; él marca la salida y la llegada. Si hace falta, puedes adelantar la salida.</p>
      <div id="viajes-admin-lista" className="viajes-grid">{consulta.isLoading ? <SkeletonFilas cantidad={1} /> : viajes.map((v, i) => <TarjetaViajeAdmin key={`${v.id}-${v.estado}`} v={v} verificados={verificados} indice={i} />)}</div>
      {consulta.data && !viajes.length && <Vacio icono="route" titulo="No hay viajes en curso" detalle="Cuando un comprador de otra ciudad pida por camión, su pedido abre un viaje o se suma al que ya junta carga." />}
    </div>
  );
}
