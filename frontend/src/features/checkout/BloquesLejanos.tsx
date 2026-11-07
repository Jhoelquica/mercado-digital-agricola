import { useState, type ReactNode } from 'react';
import { pedidos, type Cotizacion, type DestinoPublico } from '../../api/pedidos';
import { formatearMoneda } from '../../lib/formato';
import { HUAMANGA_CENTRO, formatearKg } from './lejanos';

export type CotizacionLejana = Extract<Cotizacion, { tipo: 'lejano' }>;
export type Modalidad = 'agencia' | 'camion';
export interface Receptor {
  dni: string;
  nombre: string;
}

interface PropsCiudades {
  destinos: DestinoPublico[];
  marcada: string | undefined;
  alElegir: (ciudad: { ciudad: string; latitud: number; longitud: number; local: boolean }) => void;
}

// Las ciudades a las que se llega y su límite (el radio que pone el admin).
export function SelectorCiudades({ destinos, marcada, alElegir }: PropsCiudades) {
  if (!destinos.length) return null;
  const chip = (ciudad: string, latitud: number, longitud: number, detalle: string, local: boolean) => (
    <button key={ciudad} type="button" className="ciudad-chip" aria-pressed={marcada === ciudad} onClick={() => alElegir({ ciudad, latitud, longitud, local })}>
      <strong>{ciudad}</strong><small>{detalle}</small>
    </button>
  );
  return (
    <>
      <p className="ciudad-selector-titulo" id="ciudad-selector-titulo"><i className="ti ti-building-community" aria-hidden="true" /> ¿A qué ciudad va tu pedido?</p>
      <div className="ciudad-selector" role="group" aria-labelledby="ciudad-selector-titulo">
        {chip('Ayacucho', HUAMANGA_CENTRO.latitud, HUAMANGA_CENTRO.longitud, 'A tu puerta, en la región', true)}
        {destinos.map((d) => chip(d.ciudad, d.latitud, d.longitud, `Hasta ${Math.round(d.radio_km)} km del centro`, false))}
      </div>
    </>
  );
}

// A qué ciudades se llega y cómo funciona comprar desde lejos.
export function InfoLejanos({ destinos, alVerCiudad }: { destinos: DestinoPublico[]; alVerCiudad: (lat: number, lng: number) => void }) {
  if (!destinos.length) return null;
  const nombres = destinos.map((d) => d.ciudad).join(', ');
  return (
    <>
      <p className="lejanos-cobertura"><i className="ti ti-map-2" aria-hidden="true" />
        <span>Entregamos en la región de Ayacucho y en <strong>{nombres}</strong>.</span></p>
      <details className="lejanos-reglas">
        <summary><i className="ti ti-help-circle" aria-hidden="true" /> ¿Compras desde otra ciudad? Así funciona</summary>
        <div className="lejanos-reglas-grid">
          <div className="lejanos-regla"><i className="ti ti-building-warehouse" aria-hidden="true" /><div><strong>Por agencia de carga</strong>
            <span>Dejamos tu pedido en la agencia en Huamanga y lo recoges en su oficina de tu ciudad. Ahora pagas el producto y el traslado a la agencia; <b>el flete de la agencia lo pagas al recoger</b> (te mostramos cuánto sería, aproximado).</span></div></div>
          <div className="lejanos-regla"><i className="ti ti-truck" aria-hidden="true" /><div><strong>En camión compartido</strong>
            <span>Para compras grandes: <b>desde 50 sacos de 50 kg por comprador</b>. El camión sale cuando junta el 80% de su carga con otros compradores de tu ciudad; mientras, tu pedido espera y ves cuánto falta.</span></div></div>
          <div className="lejanos-regla"><i className="ti ti-wallet" aria-hidden="true" /><div><strong>Pago con camión: 70% y 30%</strong>
            <span>Pagas el <b>70% al pedir</b> para reservar tu lugar y el <b>30% cuando el camión llega</b>, antes de descargar. Luego le das al chofer tu código de entrega.</span></div></div>
          <div className="lejanos-regla"><i className="ti ti-id" aria-hidden="true" /><div><strong>DNI de quien recibe</strong>
            <span>La agencia o el chofer entregan solo a quien muestra su DNI. Lo verificamos en RENIEC y solo te mostramos sus iniciales para que confirmes.</span></div></div>
          <div className="lejanos-regla"><i className="ti ti-plant-2" aria-hidden="true" /><div><strong>Qué conviene pedir desde lejos</strong>
            <span>Lo de más valor por kilo: quinua, kiwicha, papas nativas, papa amarilla. La papa blanca común suele estar más barata en el mercado de tu ciudad.</span></div></div>
        </div>
        <div className="lejanos-tabla-envoltura">
          <table className="lejanos-tabla">
            <caption>Precios por ciudad (los actualiza Chakra Shop)</caption>
            <thead><tr><th scope="col">Ciudad</th><th scope="col">Agencia</th><th scope="col">Camión compartido</th></tr></thead>
            <tbody>
              {destinos.map((d) => (
                <tr key={d.id}>
                  <th scope="row">{d.ciudad}{' '}
                    <button type="button" className="btn-link lejanos-ver-ciudad" onClick={() => alVerCiudad(d.latitud, d.longitud)}>ver en el mapa</button>{' '}
                    <small>Hasta {Math.round(d.radio_km)} km del centro</small></th>
                  <td>{d.agencia
                    ? <>{d.agencia.nombre || 'Agencia'}{d.agencia.flete_kg != null && ` · flete aprox. ${formatearMoneda(d.agencia.flete_kg)}/kg`}{d.agencia.dias ? ` · ~${d.agencia.dias} días` : ''}{d.agencia.direccion && <><br /><small>Recoges en: {d.agencia.direccion}</small></>}</>
                    : '—'}</td>
                  <td>{d.camion ? `${formatearMoneda(d.camion.precio_saco)} por saco · desde ${d.camion.minimo_sacos} sacos · sale con ${d.camion.sacos_para_salir} de ${d.camion.capacidad_sacos}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

interface PropsOpcion {
  valor: Modalidad;
  titulo: string;
  icono: string;
  disponible: boolean;
  motivo: string | null | undefined;
  precio: string;
  detalle: ReactNode;
  chips?: string[];
  elegida: Modalidad | null;
  alElegir: (m: Modalidad) => void;
}

function OpcionLejana({ valor, titulo, icono, disponible, motivo, precio, detalle, chips = [], elegida, alElegir }: PropsOpcion) {
  return (
    <label className={`opcion-lejana ${disponible ? '' : 'no-disponible'}`}>
      <input type="radio" name="modalidad-envio" value={valor} disabled={!disponible} checked={elegida === valor} onChange={() => alElegir(valor)} />
      <span className="opcion-lejana-caja">
        <span className="opcion-lejana-cabecera">
          <span className="opcion-lejana-icono"><i className={`ti ti-${icono}`} aria-hidden="true" /></span>
          <strong>{titulo}</strong>
          {precio && <span className="opcion-lejana-precio">{precio}</span>}
        </span>
        <span className="opcion-lejana-detalle">{detalle}</span>
        {chips.length > 0 && <span className="opcion-lejana-chips">{chips.map((c) => <span key={c}>{c}</span>)}</span>}
        {!disponible && <span className="opcion-lejana-motivo"><i className="ti ti-info-circle" aria-hidden="true" /> {motivo || 'No disponible para este destino.'}</span>}
      </span>
    </label>
  );
}

function ReceptorConfirmado({ receptor }: { receptor: Receptor }) {
  return <p className="lejano-receptor-ok"><i className="ti ti-circle-check" aria-hidden="true" /> Recibe <strong>{receptor.nombre}</strong> (DNI {receptor.dni}). ¿No es esa persona? Cambia el DNI.</p>;
}

type ResultadoDni =
  | { tipo: 'nada' }
  | { tipo: 'consultando' }
  | { tipo: 'error'; mensaje: string }
  | { tipo: 'confirmar'; dni: string; nombre: string };

function ReceptorLejano({ ciudad, receptor, alCambiar }: { ciudad: string; receptor: Receptor | null; alCambiar: (r: Receptor | null) => void }) {
  const [dni, setDni] = useState(receptor?.dni || '');
  const [resultado, setResultado] = useState<ResultadoDni>({ tipo: 'nada' });

  async function verificar() {
    const limpio = dni.trim();
    alCambiar(null);
    if (!/^\d{8}$/.test(limpio)) {
      setResultado({ tipo: 'error', mensaje: 'El DNI son 8 dígitos.' });
      return;
    }
    setResultado({ tipo: 'consultando' });
    try {
      const r = await pedidos.consultarDni(limpio);
      setResultado({ tipo: 'confirmar', dni: r.dni, nombre: r.nombre_para_confirmar });
    } catch (err) {
      setResultado({ tipo: 'error', mensaje: (err as Error).message || 'No se pudo consultar el DNI.' });
    }
  }

  return (
    <div className="lejano-receptor">
      <label className="checkout-campo" htmlFor="receptor-dni">DNI de quien recibe en {ciudad}
        <span className="lejano-receptor-fila">
          <input
            type="text"
            id="receptor-dni"
            inputMode="numeric"
            maxLength={8}
            placeholder="8 dígitos"
            autoComplete="off"
            value={dni}
            onChange={(e) => {
              setDni(e.target.value);
              if (receptor && e.target.value.trim() !== receptor.dni) {
                alCambiar(null);
                setResultado({ tipo: 'nada' });
              }
            }}
          />
          <button type="button" className="btn btn-outline" id="btn-verificar-dni" disabled={resultado.tipo === 'consultando'} onClick={() => { void verificar(); }}><i className="ti ti-shield-check" aria-hidden="true" /> Verificar en RENIEC</button>
        </span>
        <small className="muted">La agencia o el chofer entregan solo a quien muestra este DNI. Te mostramos solo sus iniciales.</small>
      </label>
      <div id="receptor-resultado" aria-live="polite">
        {receptor ? <ReceptorConfirmado receptor={receptor} /> : (
          <>
            {resultado.tipo === 'consultando' && <p className="muted"><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Consultando RENIEC…</p>}
            {resultado.tipo === 'error' && <p className="lejano-receptor-error" role="alert">{resultado.mensaje}</p>}
            {resultado.tipo === 'confirmar' && (
              <div className="lejano-receptor-confirmar">
                <p>RENIEC dice: <strong>{resultado.nombre}</strong></p>
                <button type="button" className="btn btn-primary btn-sm" id="btn-confirmar-receptor" onClick={() => alCambiar({ dni: resultado.dni, nombre: resultado.nombre })}>Sí, es quien va a recibir</button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

interface PropsBloque {
  cotizacion: CotizacionLejana;
  modalidad: Modalidad | null;
  receptor: Receptor | null;
  alElegirModalidad: (m: Modalidad) => void;
  alCambiarReceptor: (r: Receptor | null) => void;
}

// Fuera de Ayacucho: agencia o camión, y el DNI de quien recibe.
export function BloqueLejano({ cotizacion: c, modalidad, receptor, alElegirModalidad, alCambiarReceptor }: PropsBloque) {
  const { agencia, camion } = c.opciones;
  const ciudad = c.destino.ciudad;
  const agenciaOk = Boolean(agencia) && !agencia?.motivo_no_disponible;
  const camionOk = Boolean(camion) && !camion?.motivo_no_disponible;

  return (
    <section className="checkout-bloque" id="checkout-bloque-lejano" aria-labelledby="checkout-lejano-titulo">
      <h3 id="checkout-lejano-titulo"><i className="ti ti-truck-delivery" aria-hidden="true" /> Tu pedido va a {ciudad}</h3>
      {c.destino.nota && <p className="lejano-nota"><i className="ti ti-info-circle" aria-hidden="true" /> {c.destino.nota}</p>}
      <div className="opciones-lejanas" role="radiogroup" aria-label="Cómo llega tu pedido">
        <OpcionLejana
          valor="camion"
          titulo="En camión compartido"
          icono="truck"
          disponible={camionOk}
          motivo={camion ? camion.motivo_no_disponible : `A ${ciudad} no va el camión.`}
          precio={camionOk && camion ? formatearMoneda(camion.costo_envio) : ''}
          detalle={!camion ? '' : !camionOk
            ? `${formatearMoneda(camion.precio_saco)} por saco de ${c.kg_por_saco} kg, desde ${camion.minimo_sacos} sacos por comprador. Sale al juntar ${camion.sacos_para_salir} sacos con otros compradores; ${camion.porcentaje_adelanto}% al pedir y el resto al llegar.`
            : (
              <>
                {camion.sacos != null && `${camion.sacos} sacos de ${c.kg_por_saco} kg × ${formatearMoneda(camion.precio_saco)}. `}
                El camión sale cuando junta <b>{camion.sacos_para_salir} sacos</b> (el {camion.porcentaje_salida}% de {camion.capacidad_sacos}) con otros compradores de {ciudad}; mientras, tu pedido espera y ves cuánto falta.{' '}
                {camion.adelanto != null && <>Pagas <b>{formatearMoneda(camion.adelanto)} ahora ({camion.porcentaje_adelanto}%)</b> y <b>{formatearMoneda(camion.saldo)} cuando llegue</b>, antes de descargar.</>}
              </>
            )}
          chips={camion ? [`Desde ${camion.minimo_sacos} sacos por comprador`, `${camion.porcentaje_adelanto}% al pedir · resto al llegar`, ...(camion.dias ? [`~${camion.dias} días de viaje`] : [])] : []}
          elegida={modalidad}
          alElegir={alElegirModalidad}
        />
        <OpcionLejana
          valor="agencia"
          titulo={`Por agencia de carga${agencia?.agencia ? ` (${agencia.agencia})` : ''}`}
          icono="building-warehouse"
          disponible={agenciaOk}
          motivo={agencia ? agencia.motivo_no_disponible : `A ${ciudad} no enviamos por agencia.`}
          precio={agencia ? formatearMoneda(agencia.costo_envio) : ''}
          detalle={agencia ? (
            <>
              Lo dejamos en la agencia en Huamanga y lo recoges en su oficina de {ciudad}{agencia.dias ? ` en unos ${agencia.dias} días` : ''}:{' '}
              {agencia.direccion ? <b>{agencia.direccion}</b> : 'la agencia te confirma la oficina con la guía'}.{' '}
              Ahora pagas el traslado a la agencia. <b>El flete de la agencia lo pagas al recoger</b>{agencia.flete_destino_estimado != null && <>: aprox. <b>{formatearMoneda(agencia.flete_destino_estimado)}</b> ({formatearMoneda(agencia.flete_kg)} por kg × {formatearKg(c.peso_total_kg)}; lo confirma la agencia)</>}.
            </>
          ) : ''}
          elegida={modalidad}
          alElegir={alElegirModalidad}
        />
      </div>
      {!agenciaOk && !camionOk && <p className="lejano-sin-opcion" role="alert">Con este carrito no hay forma de llegar a esa ciudad: revisa el motivo de cada opción.</p>}
      <ReceptorLejano ciudad={ciudad} receptor={receptor} alCambiar={alCambiarReceptor} />
      <p className="lejano-consejo"><i className="ti ti-plant-2" aria-hidden="true" /> Desde lejos conviene lo de más valor por kilo (quinua, kiwicha, papas nativas, papa amarilla): la papa blanca común suele estar más barata en tu ciudad.</p>
    </section>
  );
}
