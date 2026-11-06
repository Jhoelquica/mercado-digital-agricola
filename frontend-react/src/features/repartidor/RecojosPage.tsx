// /repartidor/recojos: recojos de cosechas en chacras (se recoge en la chacra y se deja en el almacén).
import { useState } from 'react';
import { recojos, type Recojo } from '../../api/recojos';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor, formatearMoneda } from '../../lib/formato';
import { BadgeEstadoEnvio, enlaceMapaOsm, Metrica, montoQueGana, numeroCorto, PlazoPropuesta, useAlmacen, useCondicionesPago, useMiRepartidor, useRecojosRepartidor, useRefrescarPanel } from './comun';

type Accion = 'aceptar' | 'rechazar' | 'recogido' | 'en-almacen';
const LLAMADAS: Record<Accion, (id: string) => Promise<unknown>> = { aceptar: recojos.aceptar, rechazar: recojos.rechazar, recogido: recojos.recogido, 'en-almacen': recojos.enAlmacen };
const MENSAJES: Record<Accion, string> = {
  aceptar: 'Recojo aceptado: llama al agricultor antes de ir 🚚',
  rechazar: 'Recojo rechazado.',
  recogido: 'Listo: ahora llévala al almacén.',
  'en-almacen': 'Gracias: lo que ganas se registra cuando el almacén la pese.',
};

function TarjetaRecojo({ r, indice }: { r: Recojo; indice: number }) {
  const refrescar = useRefrescarPanel();
  const { data: condiciones } = useCondicionesPago();
  const { data: almacen } = useAlmacen(true);
  const [moviendo, setMoviendo] = useState(false);
  const reparto = montoQueGana(r.costo, condiciones?.comision_plataforma_porcentaje);
  const telefono = String(r.telefono || '').replace(/\D/g, '');

  async function mover(accion: Accion) {
    setMoviendo(true);
    try {
      await LLAMADAS[accion](r.id);
      toast(MENSAJES[accion]);
      await refrescar();
    } catch (err) {
      manejarError(err, 'actualizar el recojo');
    }
    // La tarjeta sigue (con el estado nuevo y sus botones): se vuelven a habilitar.
    setMoviendo(false);
  }
  const boton = (accion: Accion, clase: string, icono: string, texto: string) => (
    <button type="button" className={`btn ${clase} btn-recojo-accion`} data-accion={accion} disabled={moviendo} onClick={() => { void mover(accion); }}><i className={`ti ti-${icono}`} aria-hidden="true" /> {texto}</button>
  );
  const enlaceChacra = r.latitud != null ? <a href={enlaceMapaOsm(r.latitud, r.longitud as number)} target="_blank" rel="noopener">Ver la chacra en el mapa <i className="ti ti-external-link" aria-hidden="true" /></a> : null;

  let cuerpo;
  if (r.estado === 'propuesto') {
    cuerpo = (
      <>
        {r.referencia && <p className="recojo-referencia"><i className="ti ti-map-2" aria-hidden="true" /> {r.referencia}</p>}
        <PlazoPropuesta hasta={r.responder_antes_de} />
        <div className="propuesta-actions">
          {boton('rechazar', 'btn-outline', 'x', 'Rechazar')}
          {boton('aceptar', 'btn-primary', 'check', 'Aceptar recojo')}
        </div>
      </>
    );
  } else if (r.estado === 'asignado') {
    cuerpo = (
      <>
        <ol className="envio-pasos-agencia">
          <li><strong>Ve a la chacra y recoge la cosecha.</strong> {enlaceChacra}{r.referencia && <><br />{r.referencia}</>}</li>
          <li><strong>Revisa que sean unos {numeroCorto(r.peso_kg)} kg</strong> antes de cargarla.</li>
        </ol>
        <div className="envio-actualizar">
          {telefono && <a className="btn btn-outline" href={`tel:${telefono}`}><i className="ti ti-phone" aria-hidden="true" /> Llamar al agricultor</a>}
          {boton('recogido', 'btn-primary', 'package', 'Ya la recogí')}
        </div>
      </>
    );
  } else if (r.estado === 'recogido') {
    cuerpo = (
      <>
        <p className="envio-aviso info"><i className="ti ti-road" aria-hidden="true" /><span>Llévala al almacén. {almacen?.latitud != null && <a href={enlaceMapaOsm(almacen.latitud, almacen.longitud as number)} target="_blank" rel="noopener">Ver el almacén en el mapa <i className="ti ti-external-link" aria-hidden="true" /></a>}</span></p>
        <div className="envio-actualizar">{boton('en-almacen', 'btn-primary', 'building-warehouse', 'La dejé en el almacén')}</div>
      </>
    );
  } else {
    cuerpo = <div className="envio-aviso ok"><i className="ti ti-circle-check" aria-hidden="true" /><div><strong>La dejaste en el almacén</strong><span>Lo que ganas se registra cuando el almacén la pese.</span></div></div>;
  }
  return (
    <div className={`pedido-card recojo-card ${r.estado === 'propuesto' ? 'propuesta-card' : 'pedido-card-activo'}`} data-recojo-id={r.id} style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }}>
      <div className="pedido-card-header">
        <div>
          <div className="pedido-id">Recojo · {r.producto_nombre || 'Cosecha'}</div>
          <div className="pedido-fecha">{formatearFechaServidor(r.fecha_creacion)}</div>
        </div>
        <BadgeEstadoEnvio estado={r.estado} />
      </div>
      <div className="propuesta-metricas" role="group" aria-label="Datos del recojo">
        <Metrica icono="route" valor={r.distancia_km != null ? `${numeroCorto(r.distancia_km)} km` : '—'} etiqueta="Del almacén" />
        <Metrica icono="weight" valor={`${numeroCorto(r.peso_kg)} kg`} etiqueta="Peso" />
        <Metrica icono="cash" valor={formatearMoneda(reparto ? reparto.neto : r.costo)} etiqueta={reparto ? 'Ganas' : 'Tarifa'} />
      </div>
      {cuerpo}
    </div>
  );
}

export default function RecojosPage() {
  const perfil = useMiRepartidor();
  const consulta = useRecojosRepartidor(Boolean(perfil.data));
  const lista = consulta.data ?? [];
  return (
    <div className="repartidor-shell">
      <div id="recojos-section" className="recojos-section">
        <div className="section-header inner">
          <h3><i className="ti ti-plant-2" /> Recojos en chacras</h3>
        </div>
        <p className="muted recojos-intro">Recoges la cosecha en la chacra y la dejas en el almacén. Lo que ganas se registra cuando el almacén la pesa.</p>
        <div id="recojos-list" className="envios-grid">
          {consulta.isLoading ? <SkeletonFilas cantidad={2} /> : lista.map((r, i) => <TarjetaRecojo key={r.id} r={r} indice={i} />)}
        </div>
        {/* En el frontend actual la sección se esconde vacía; aquí es su propia pantalla y dice que no hay. */}
        {!consulta.isLoading && !lista.length && (
          <div className="empty-state">
            <span className="empty-state-icon"><i className="ti ti-plant-2" /></span>
            <p><strong>No tienes recojos por ahora</strong></p>
            <p className="muted">Cuando un agricultor pida recoger su cosecha y tu vehículo pueda cargarla, te llega aquí.</p>
          </div>
        )}
      </div>
    </div>
  );
}
