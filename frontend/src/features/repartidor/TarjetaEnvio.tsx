// Tarjetas de "Mis envíos" del repartidor: propuestas por responder y envíos a su cargo (a domicilio, por agencia de
// carga o en camión compartido), con los pasos, el botón con el estado que sigue y la entrega con código y foto.
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ApiError } from '../../api/errors';
import { pedidos, type Pedido } from '../../api/pedidos';
import { transporte, type Envio, type Propuesta } from '../../api/transporte';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor, formatearMoneda } from '../../lib/formato';
import { siguienteEstadoEnvio } from '../../lib/reglas';
import { BloqueRuta } from '../pedidos/TarjetaPedido';
import {
  BadgeEstadoEnvio, enlaceMapaOsm, ESTADOS_ENVIO_CUMPLIDOS, ESTADOS_ENVIO_EN_CURSO, Metrica, montoQueGana, PlazoPropuesta, useAgenciaDespacho,
  useCondicionesPago, useRefrescarPanel,
} from './comun';
import { SelectorFoto } from './SelectorFoto';

const corto = (id: string) => String(id).slice(0, 8);

function Cabecera({ envio }: { envio: Envio }) {
  return (
    <div className="pedido-card-header">
      <div>
        <div className="pedido-id">Envío #{corto(envio.id)} · Pedido #{corto(envio.pedido_id)}</div>
        <div className="pedido-fecha">{formatearFechaServidor(envio.fecha_creacion)}</div>
      </div>
      <BadgeEstadoEnvio estado={envio.estado || 'propuesto'} />
    </div>
  );
}

// Lo que necesita para decidir si acepta: cuánto recorrer, cuánto pesa y cuánto gana (sin datos del comprador).
function MetricasPropuesta({ resumen }: { resumen: Propuesta['resumen'] }) {
  const { data: condiciones } = useCondicionesPago();
  if (!resumen) return null;
  const numero = (valor: number) => Number(valor).toLocaleString('es-PE', { maximumFractionDigits: 1 });
  const porcentaje = condiciones?.comision_plataforma_porcentaje;
  const reparto = resumen.costo_envio != null ? montoQueGana(resumen.costo_envio, porcentaje) : null;
  return (
    <>
      <div className="propuesta-metricas" role="group" aria-label="Datos del envío">
        <Metrica icono="route" valor={resumen.distancia_km != null ? `${numero(resumen.distancia_km)} km` : '—'} etiqueta="Desde el almacén" />
        <Metrica icono="weight" valor={resumen.peso_total_kg != null ? `${numero(resumen.peso_total_kg)} kg` : 'Sin dato'} etiqueta="Peso" />
        <Metrica icono="cash" valor={resumen.costo_envio != null ? formatearMoneda(reparto ? reparto.neto : resumen.costo_envio) : '—'} etiqueta={reparto ? 'Ganas' : 'Tarifa'} />
      </div>
      {reparto && porcentaje != null && (
        <p className="propuesta-reparto"><i className="ti ti-info-circle" aria-hidden="true" /> El comprador paga {formatearMoneda(reparto.bruto)} de envío; {formatearMoneda(reparto.comision)} ({porcentaje.toLocaleString('es-PE')}%) es la comisión de Chakra Shop. Se registra a tu favor al entregar el pedido.</p>
      )}
      {resumen.requiere_vehiculo_grande && <p className="propuesta-aviso"><i className="ti ti-truck" aria-hidden="true" /> Pedido grande: pide un vehículo con más capacidad.</p>}
    </>
  );
}

export function TarjetaPropuesta({ envio, indice }: { envio: Propuesta; indice: number }) {
  const refrescar = useRefrescarPanel();
  const [respondiendo, setRespondiendo] = useState(false);
  async function responder(accion: 'aceptar' | 'rechazar') {
    setRespondiendo(true);
    try {
      if (accion === 'aceptar') await transporte.aceptar(envio.id);
      else await transporte.rechazar(envio.id);
      toast(accion === 'aceptar' ? 'Envío aceptado, ¡buen viaje! 🚚' : 'Envío rechazado.');
      await refrescar();
    } catch (err) {
      manejarError(err, `${accion} el envío`);
      setRespondiendo(false);
    }
  }
  return (
    <div className="propuesta-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }}>
      <Cabecera envio={envio} />
      <MetricasPropuesta resumen={envio.resumen} />
      <PlazoPropuesta hasta={envio.responder_antes_de} />
      <div className="propuesta-actions">
        <button type="button" className="btn btn-outline btn-rechazar-propuesta" disabled={respondiendo} onClick={() => { void responder('rechazar'); }}><i className="ti ti-x" /> Rechazar</button>
        <button type="button" className="btn btn-primary btn-aceptar-propuesta" disabled={respondiendo} onClick={() => { void responder('aceptar'); }}><i className="ti ti-check" /> Aceptar envío</button>
      </div>
    </div>
  );
}

// El teléfono del comprador (obligatorio al pedir): los pedidos anteriores pueden no tenerlo.
function ContactoComprador({ pedido }: { pedido: Pedido | undefined }) {
  const telefono = String(pedido?.comprador_telefono || '').replace(/\D/g, '');
  if (!pedido || !telefono) return null;
  return (
    <div className="envio-contacto">
      <span className="envio-contacto-nombre"><i className="ti ti-user" /> {pedido.comprador_nombre || 'Comprador'}</span>
      <a className="btn btn-outline btn-sm envio-contacto-llamar" href={`tel:${telefono}`}><i className="ti ti-phone" /> {telefono}</a>
    </div>
  );
}

// Quien recibe un pedido lejano (DNI confirmado en RENIEC): a su nombre va la guía y es quien lo muestra al descargar.
function QuienRecibe({ pedido }: { pedido: Pedido | undefined }) {
  if (!pedido?.receptor_nombre && !pedido?.receptor_dni) return null;
  return (
    <div className="envio-receptor">
      <span className="envio-receptor-icono"><i className="ti ti-id" aria-hidden="true" /></span>
      <div>
        <span className="envio-receptor-etiqueta">Recibe</span>
        <strong>{pedido.receptor_nombre || 'Sin nombre registrado'}</strong>
        <small>DNI {pedido.receptor_dni || '—'} · confirmado en RENIEC</small>
      </div>
    </div>
  );
}

const ETIQUETAS_CAMBIO_ESTADO: Record<string, string> = { asignado: 'Asignado', en_camino: 'En camino', entregado: 'Entregado', en_agencia: 'En la agencia' };

// H11 (validación con usuarios del 2026-10-13, R03): los pasos a la vista, qué hacer ahora y UN botón con el estado que
// sigue. "Entregado" abre el formulario del código + foto; "En la agencia" es el despacho con la guía (`extra`).
const GUIA_SIGUIENTE_ESTADO: Record<string, { boton?: string; icono?: string; guia: string }> = {
  en_camino: { boton: 'Ya salí: marcar «En camino»', icono: 'truck-delivery', guia: 'Cuando salgas del almacén con el pedido, toca «Ya salí»: el comprador verá que va en camino.' },
  entregado: { boton: 'Ya llegué: entregar con código y foto', icono: 'package', guia: 'Al llegar, toca «Ya llegué», pídele al comprador su código de 4 dígitos y toma una foto de la entrega.' },
  en_agencia: { guia: 'Cuando lo dejes en la agencia, toca «Dejé el pedido en la agencia» y registra la guía.' },
};

function CambioEstadoEnvio({ envio, estados, extra, formularioAbierto, alAbrirFormulario }: { envio: Envio; estados: string[]; extra?: ReactNode; formularioAbierto: boolean; alAbrirFormulario: () => void }) {
  const refrescar = useRefrescarPanel();
  const [moviendo, setMoviendo] = useState(false);
  const actual = estados.indexOf(envio.estado || '');
  const siguiente = siguienteEstadoEnvio(envio.estado, estados);
  const paso = siguiente ? GUIA_SIGUIENTE_ESTADO[siguiente] : undefined;
  async function mover() {
    if (!siguiente) return;
    setMoviendo(true);
    try {
      await transporte.actualizarEstado(envio.id, siguiente);
      toast(`Listo: el envío está «${ETIQUETAS_CAMBIO_ESTADO[siguiente] || siguiente}» ✅`);
      await refrescar();
    } catch (err) {
      manejarError(err, 'actualizar el estado del envío');
    }
    setMoviendo(false);
  }
  let accion: ReactNode = null;
  if (siguiente === 'entregado') {
    accion = <button type="button" className={`btn btn-primary btn-abrir-entrega ${formularioAbierto ? 'hidden' : ''}`} aria-expanded={formularioAbierto} onClick={alAbrirFormulario}><i className={`ti ti-${paso?.icono}`} aria-hidden="true" /> {paso?.boton}</button>;
  } else if (siguiente === 'en_agencia') {
    accion = extra;
  } else if (paso) {
    accion = <button type="button" className="btn btn-primary btn-actualizar-envio" data-estado={siguiente} disabled={moviendo} onClick={() => { void mover(); }}><i className={`ti ti-${paso.icono}`} aria-hidden="true" /> {paso.boton}</button>;
  }
  return (
    <div className="envio-estado">
      <ol className="envio-estado-pasos" aria-label="Pasos del envío">
        {estados.map((e, i) => (
          <li key={e} className={i < actual ? 'hecho' : i === actual ? 'actual' : ''} aria-current={i === actual ? 'step' : undefined}>
            {i < actual ? <i className="ti ti-check" aria-hidden="true" /> : <span aria-hidden="true">{i + 1}</span>}{ETIQUETAS_CAMBIO_ESTADO[e]}
          </li>
        ))}
      </ol>
      {paso && <p className="envio-guia"><i className="ti ti-info-circle" aria-hidden="true" /><span><strong>Ahora:</strong> {paso.guia}</span></p>}
      {accion && <div className="envio-actualizar">{accion}</div>}
    </div>
  );
}

type MensajeForm = { texto: string; tipo: 'error' | 'bloqueado' } | null;

function MensajeEntrega({ mensaje }: { mensaje: MensajeForm }) {
  return mensaje
    ? <p className={`form-entrega-mensaje ${mensaje.tipo}`} role="alert"><i className={`ti ${mensaje.tipo === 'bloqueado' ? 'ti-lock' : 'ti-alert-circle'}`} /><span>{mensaje.texto}</span></p>
    : <p className="form-entrega-mensaje hidden" role="alert" />;
}

// "Entregado" necesita el código que el comprador le dicta al repartidor y una foto (POST /envios/{id}/entregar).
function FormEntrega({ envio, ayuda = 'Pídele al comprador el código de 4 dígitos de su pedido.', abierto, alCancelar }: { envio: Envio; ayuda?: string; abierto: boolean; alCancelar: () => void }) {
  const refrescar = useRefrescarPanel();
  const [pin, setPin] = useState('');
  const [foto, setFoto] = useState<File | null>(null);
  const [mensaje, setMensaje] = useState<MensajeForm>(null);
  const [estado, setEstado] = useState<'libre' | 'enviando' | 'bloqueado'>('libre');
  const campoPin = useRef<HTMLInputElement>(null);
  const campoFoto = useRef<HTMLInputElement>(null);
  // Al abrirlo, el foco va al código (el botón que lo abrió se esconde y vuelve con "Cancelar").
  useEffect(() => { if (abierto) campoPin.current?.focus(); }, [abierto]);

  async function confirmar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!foto) {
      setMensaje({ texto: 'Agrega la foto de la entrega antes de confirmar.', tipo: 'error' });
      campoFoto.current?.focus();
      return;
    }
    setMensaje(null);
    setEstado('enviando');
    try {
      await transporte.confirmarEntrega(envio.id, pin.trim(), foto);
      toast('Entrega confirmada ✅');
      await refrescar();
    } catch (err) {
      manejarError(err, 'confirmar la entrega');
      const codigo = err instanceof ApiError ? err.status : 0;
      setMensaje({ texto: (err as Error).message, tipo: codigo === 429 ? 'bloqueado' : 'error' });
      if (codigo === 429) {
        // Bloqueado por demasiados códigos incorrectos: queda deshabilitado (con el mensaje a la vista) hasta que pase.
        setEstado('bloqueado');
        return;
      }
      setEstado('libre');
      // Solo un código incorrecto (400) se borra; si falló la foto, el código sigue bueno y el foco va a la foto.
      if (codigo === 400) { setPin(''); setTimeout(() => campoPin.current?.focus()); } else setTimeout(() => campoFoto.current?.focus());
    }
  }

  const deshabilitado = estado !== 'libre';
  return (
    <form className={`form-confirmar-entrega ${abierto ? '' : 'hidden'}`} data-envio-id={envio.id} onSubmit={(e) => { void confirmar(e); }}>
      <p className="muted"><i className="ti ti-shield-check" /> {ayuda}</p>
      <div className="form-confirmar-entrega-campos">
        <label>Código del comprador
          <input ref={campoPin} type="text" name="pin" inputMode="numeric" maxLength={4} pattern="[0-9]{4}" required placeholder="0000" value={pin} disabled={deshabilitado} onChange={(e) => setPin(e.target.value)} />
        </label>
        <SelectorFoto id={envio.id} titulo="Foto de la entrega" detalle="Del paquete entregado · JPG, PNG o WEBP · máx. 5 MB" textoAlternativo="Vista previa de la foto de la entrega" disabled={deshabilitado} alCambiar={setFoto} inputRef={campoFoto} />
      </div>
      <MensajeEntrega mensaje={mensaje} />
      <div className="form-confirmar-entrega-acciones">
        <button type="button" className="btn btn-ghost btn-sm btn-cancelar-entrega" disabled={estado === 'enviando'} onClick={alCancelar}>Cancelar</button>
        <button type="submit" className="btn btn-primary btn-sm" disabled={deshabilitado}>{estado === 'enviando' ? 'Confirmando...' : estado === 'bloqueado' ? 'Bloqueado temporalmente' : 'Confirmar entrega'}</button>
      </div>
    </form>
  );
}

function FormDespachoAgencia({ envio, abierto, alCancelar }: { envio: Envio; abierto: boolean; alCancelar: () => void }) {
  const refrescar = useRefrescarPanel();
  const [agencia, setAgencia] = useState('');
  const [guia, setGuia] = useState('');
  const [foto, setFoto] = useState<File | null>(null);
  const [mensaje, setMensaje] = useState<MensajeForm>(null);
  const [enviando, setEnviando] = useState(false);
  const formulario = useRef<HTMLFormElement>(null);
  const campoFoto = useRef<HTMLInputElement>(null);
  useEffect(() => { if (abierto) formulario.current?.querySelector<HTMLInputElement>('input[name="agencia"]')?.focus(); }, [abierto]);

  async function registrar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const nombre = agencia.trim().replace(/\s+/g, ' ');
    const numero = guia.trim();
    let problema: [string, () => void] | null = null;
    const foco = (n: string) => () => formulario.current?.querySelector<HTMLInputElement>(`[name="${n}"]`)?.focus();
    if (nombre.length < 2) problema = ['Escribe el nombre de la agencia (por ejemplo, Shalom).', foco('agencia')];
    else if (!/^[A-Za-z0-9-]{3,40}$/.test(numero)) problema = ['El número de guía tiene de 3 a 40 letras, números o guiones: cópialo tal como está en el comprobante.', foco('numero_guia')];
    else if (!foto) problema = ['Agrega la foto de la guía antes de registrar el despacho.', () => campoFoto.current?.focus()];
    if (problema) {
      setMensaje({ texto: problema[0], tipo: 'error' });
      problema[1]();
      return;
    }
    setEnviando(true);
    try {
      await transporte.despacharAgencia(envio.id, nombre, numero, foto as File);
      toast('Despacho registrado: el comprador ya tiene la guía para seguir su pedido 📦');
      await refrescar();
    } catch (err) {
      manejarError(err, 'registrar el despacho en la agencia');
      setMensaje({ texto: (err as Error).message, tipo: 'error' });
      setEnviando(false);
    }
  }

  return (
    <form ref={formulario} className={`form-despacho-agencia ${abierto ? '' : 'hidden'}`} data-envio-id={envio.id} noValidate onSubmit={(e) => { void registrar(e); }} onInput={() => setMensaje(null)}>
      <div className="form-despacho-campos">
        <label>Agencia
          <input type="text" name="agencia" list="agencias-sugeridas" maxLength={60} autoComplete="off" placeholder="Ej. Shalom" required value={agencia} disabled={enviando} onChange={(e) => setAgencia(e.target.value)} />
        </label>
        <label>N.º de guía
          <input type="text" name="numero_guia" maxLength={40} autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="Está en el comprobante" required value={guia} disabled={enviando} onChange={(e) => setGuia(e.target.value)} />
        </label>
      </div>
      <SelectorFoto id={`guia-${envio.id}`} titulo="Foto de la guía" detalle="El comprobante que te dio la agencia · JPG, PNG o WEBP · máx. 5 MB" textoAlternativo="Vista previa de la foto de la guía" disabled={enviando} alCambiar={setFoto} inputRef={campoFoto} />
      <MensajeEntrega mensaje={mensaje} />
      <div className="form-confirmar-entrega-acciones">
        <button type="button" className="btn btn-ghost btn-sm btn-cancelar-entrega" disabled={enviando} onClick={alCancelar}>Cancelar</button>
        <button type="submit" className="btn btn-primary btn-sm" disabled={enviando}>{enviando ? 'Registrando...' : 'Registrar despacho'}</button>
      </div>
    </form>
  );
}

// Por agencia: recoge el pedido en el almacén, lo deja en la agencia de carga de Huamanga a nombre de quien recibe (flete
// pagado en destino) y registra la guía; con eso su parte termina.
function EnvioPorAgencia({ envio, pedido, activo }: { envio: Envio; pedido: Pedido | undefined; activo: boolean }) {
  const { data: agencia } = useAgenciaDespacho(activo);
  const [abierto, setAbierto] = useState(false);
  if (ESTADOS_ENVIO_CUMPLIDOS.includes(envio.estado || '')) {
    return (
      <div className="envio-aviso ok">
        <i className="ti ti-circle-check" aria-hidden="true" />
        <div>
          <strong>Lo dejaste en {envio.agencia_nombre || 'la agencia'}{envio.fecha_despacho ? ` el ${formatearFechaServidor(envio.fecha_despacho)}` : ''}</strong>
          <span>Guía N.º {envio.numero_guia || '—'}. {envio.estado === 'entregado' ? 'El comprador ya lo recogió.' : 'El comprador lo recoge en la agencia de su ciudad.'}</span>
        </div>
      </div>
    );
  }
  if (!activo) return null;
  const ciudad = envio.destino_ciudad || pedido?.destino_ciudad || 'su ciudad';
  const receptor = pedido?.receptor_nombre || 'quien recibe';
  const botonDespacho = <button type="button" className={`btn btn-primary btn-abrir-despacho ${abierto ? 'hidden' : ''}`} aria-expanded={abierto} onClick={() => setAbierto(true)}><i className="ti ti-building-warehouse" aria-hidden="true" /> Dejé el pedido en la agencia</button>;
  return (
    <>
      <ContactoComprador pedido={pedido} />
      <QuienRecibe pedido={pedido} />
      <ol className="envio-pasos-agencia">
        <li><strong>Recógelo en el almacén y llévalo a la agencia de carga en Huamanga</strong> que viaje a {ciudad}.
          {agencia?.latitud != null && <> <a href={enlaceMapaOsm(agencia.latitud, agencia.longitud as number)} target="_blank" rel="noopener">Ver el punto en el mapa <i className="ti ti-external-link" aria-hidden="true" /></a></>}</li>
        <li><strong>Mándalo a nombre de {receptor}</strong> con destino {ciudad} y el flete <b>pagado en destino</b>: lo paga el comprador al recoger.</li>
        <li><strong>Registra aquí la guía</strong>: la agencia, el número y una foto del comprobante, para que el comprador lo siga.</li>
      </ol>
      <CambioEstadoEnvio envio={envio} estados={['asignado', 'en_camino', 'en_agencia']} extra={botonDespacho} formularioAbierto={abierto} alAbrirFormulario={() => setAbierto(true)} />
      <FormDespachoAgencia envio={envio} abierto={abierto} alCancelar={() => setAbierto(false)} />
    </>
  );
}

// En camión: el envío llega al transportista cuando su viaje sale (Mis viajes). En la ciudad se descarga solo con el saldo
// pagado (30%) y con el código del comprador.
function EnvioEnCamion({ envio, pedido, ruta }: { envio: Envio; pedido: Pedido | undefined; ruta: ReactNode }) {
  const refrescar = useRefrescarPanel();
  const [abierto, setAbierto] = useState(false);
  const ciudad = envio.destino_ciudad || pedido?.destino_ciudad || 'la ciudad de destino';
  if (envio.estado === 'en_camino') {
    return (
      <>
        <p className="envio-aviso info"><i className="ti ti-road" aria-hidden="true" />
          <span>Va en tu viaje a {ciudad}. Al llegar, marca <b>"Llegué a {ciudad}"</b> en Mis viajes: recién ahí cada comprador paga su saldo.</span></p>
        {ruta}
      </>
    );
  }
  if (envio.estado !== 'en_destino') return null;
  return (
    <>
      <ContactoComprador pedido={pedido} />
      <QuienRecibe pedido={pedido} />
      {envio.saldo_pagado
        ? <div className="envio-aviso ok" role="status"><i className="ti ti-circle-check" aria-hidden="true" /><div><strong>Saldo pagado: ya puedes descargar</strong><span>El sistema confirmó el 30% restante del comprador.</span></div></div>
        : (
          <div className="envio-aviso espera" role="status"><i className="ti ti-hourglass" aria-hidden="true" />
            <div><strong>Esperando el saldo (30%)</strong><span>No descargues todavía. El comprador lo paga desde su cuenta y aquí verás cuando el sistema lo confirme.</span></div>
            <button type="button" className="btn btn-outline btn-sm btn-consultar-saldo" onClick={() => { void refrescar(); }}><i className="ti ti-refresh" aria-hidden="true" /> Volver a consultar</button></div>
        )}
      {ruta}
      {envio.saldo_pagado && (
        <>
          <div className="envio-actualizar">
            <button type="button" className={`btn btn-primary btn-abrir-entrega ${abierto ? 'hidden' : ''}`} aria-expanded={abierto} onClick={() => setAbierto(true)}><i className="ti ti-package" aria-hidden="true" /> Descargar y entregar</button>
          </div>
          <FormEntrega envio={envio} ayuda="Revisa el DNI de quien recibe y pídele el código de 4 dígitos del pedido." abierto={abierto} alCancelar={() => setAbierto(false)} />
        </>
      )}
    </>
  );
}

export function TarjetaEnvio({ envio, indice }: { envio: Envio; indice: number }) {
  const activo = ESTADOS_ENVIO_EN_CURSO.includes(envio.estado || '');
  const finalizado = ['entregado', 'cancelado', 'rechazado', 'en_agencia'].includes(envio.estado || '');
  const [abierto, setAbierto] = useState(false);
  // Ruta (mapa) y pedido (contacto del comprador): opcionales; si alguna falla, la tarjeta sale sin ese bloque. Un pedido
  // por agencia no lleva mapa (el repartidor va a la agencia, no a la ciudad del comprador).
  const ruta = useQuery({ queryKey: ['repartidor', 'ruta', envio.id], queryFn: () => transporte.ruta(envio.id), enabled: activo && envio.modalidad_envio !== 'agencia', retry: false });
  const pedido = useQuery({ queryKey: ['repartidor', 'pedido', envio.pedido_id], queryFn: () => pedidos.obtener(envio.pedido_id), enabled: activo, retry: false });
  const bloqueRuta = activo && !ruta.isLoading ? <BloqueRuta key={ruta.dataUpdatedAt} envio={envio} ruta={ruta.data ?? null} /> : null;
  let cuerpo: ReactNode;
  if (envio.modalidad_envio === 'agencia') cuerpo = <EnvioPorAgencia envio={envio} pedido={pedido.data} activo={activo} />;
  else if (envio.modalidad_envio === 'camion') cuerpo = <EnvioEnCamion envio={envio} pedido={pedido.data} ruta={bloqueRuta} />;
  else {
    cuerpo = (
      <>
        {activo && <ContactoComprador pedido={pedido.data} />}
        {bloqueRuta}
        {!finalizado && (
          <>
            <CambioEstadoEnvio envio={envio} estados={['asignado', 'en_camino', 'entregado']} formularioAbierto={abierto} alAbrirFormulario={() => setAbierto(true)} />
            <FormEntrega envio={envio} abierto={abierto} alCancelar={() => setAbierto(false)} />
          </>
        )}
      </>
    );
  }
  return (
    <div className={`pedido-card ${activo ? 'pedido-card-activo' : ''}`} style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }}>
      <Cabecera envio={envio} />
      {envio.modalidad_envio === 'camion'
        ? <p className="envio-lejano-ruta camion"><i className="ti ti-truck" aria-hidden="true" /> Camión compartido a <b>{envio.destino_ciudad || 'otra ciudad'}</b>{envio.sacos ? ` · ${envio.sacos} sacos` : ''}</p>
        : envio.modalidad_envio === 'agencia'
          ? <p className="envio-lejano-ruta agencia"><i className="ti ti-building-warehouse" aria-hidden="true" /> Por agencia de carga a <b>{envio.destino_ciudad || 'otra ciudad'}</b>{envio.sacos ? ` · ${envio.sacos} sacos` : ''}</p>
          : null}
      {cuerpo}
    </div>
  );
}
