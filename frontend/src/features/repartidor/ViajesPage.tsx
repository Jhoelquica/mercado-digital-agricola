// /repartidor/viajes: viajes largos en camión. Los documentos del transportista (licencia, tarjeta de propiedad, SOAT)
// que revisa el admin, y sus viajes compartidos a otras ciudades (salir cuando juntó la carga, llegar).
import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type FormEvent } from 'react';
import { repartidores, type Repartidor } from '../../api/repartidores';
import { transporte, type Viaje } from '../../api/transporte';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { FOTO_TAMANO_MAXIMO, formatearTamanoArchivo } from '../../lib/fotos';
import { formatearFechaServidor } from '../../lib/formato';
import { BotonViaje, EnviosDelViaje, EtapasViaje, MedidorViaje } from '../viajes/piezas';
import { useMiRepartidor, useRefrescarPanel, useViajes } from './comun';

const KG_MINIMOS_CAMION = 2500; // 50 sacos de 50 kg: lo mínimo que pide un comprador lejano (mismo tope que Transporte)
const DOCUMENTOS_CARGA = [
  { campo: 'licencia', icono: 'id', titulo: 'Licencia de conducir', detalle: 'Foto o PDF, hasta 5 MB' },
  { campo: 'tarjeta_propiedad', icono: 'file-certificate', titulo: 'Tarjeta de propiedad', detalle: 'Del vehículo · foto o PDF, hasta 5 MB' },
  { campo: 'soat', icono: 'shield-check', titulo: 'SOAT vigente', detalle: 'Del mismo vehículo · foto o PDF, hasta 5 MB' },
] as const;
type CampoDocumento = (typeof DOCUMENTOS_CARGA)[number]['campo'];
const DOCUMENTO_TIPOS_VALIDOS = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf'];
const ESTADO_CARGA_INFO: Record<string, { texto: string; icono: string }> = {
  pendiente: { texto: 'En revisión', icono: 'hourglass' },
  rechazado: { texto: 'Observado', icono: 'alert-circle' },
  verificado: { texto: 'Verificado', icono: 'circle-check' },
};
const numero = (n: number | string) => Number(n).toLocaleString('es-PE', { maximumFractionDigits: 0 });

function ArchivoDocumento({ d, alElegir }: { d: (typeof DOCUMENTOS_CARGA)[number]; alElegir: (archivo: File | null) => void }) {
  const [estado, setEstado] = useState<{ archivo: File | null; error: string }>({ archivo: null, error: '' });
  return (
    <label className={`reclamo-archivo${estado.archivo ? ' con-archivo' : ''}${estado.error ? ' con-error' : ''}`}>
      <input type="file" name={d.campo} accept="image/jpeg,image/png,image/webp,application/pdf" required onChange={(e) => {
        const archivo = e.target.files?.[0] ?? null;
        let error = '';
        if (archivo && !DOCUMENTO_TIPOS_VALIDOS.includes(archivo.type)) error = 'Tiene que ser una foto (JPG, PNG o WEBP) o un PDF.';
        else if (archivo && archivo.size > FOTO_TAMANO_MAXIMO) error = `Pesa ${formatearTamanoArchivo(archivo.size)} y el máximo es 5 MB.`;
        if (error) e.target.value = '';
        setEstado({ archivo: error ? null : archivo, error });
        alElegir(error ? null : archivo);
      }} />
      <span className="reclamo-archivo-caja">
        <i className={`ti ti-${d.icono}`} aria-hidden="true" />
        <strong>{d.titulo}</strong>
        <small className="reclamo-archivo-nombre" data-vacio={d.detalle}>{estado.error || (estado.archivo ? `${estado.archivo.name} · ${formatearTamanoArchivo(estado.archivo.size)}` : d.detalle)}</small>
      </span>
    </label>
  );
}

function FormCarga({ perfil, oculto }: { perfil: Repartidor; oculto: boolean }) {
  const queryClient = useQueryClient();
  const [placa, setPlaca] = useState(perfil.placa || '');
  const [licencia, setLicencia] = useState(perfil.licencia_numero || '');
  const archivos = useRef<Partial<Record<CampoDocumento, File | null>>>({});
  const [mensaje, setMensaje] = useState('');
  const [enviando, setEnviando] = useState(false);
  const formulario = useRef<HTMLFormElement>(null);

  async function enviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const placaLimpia = placa.toUpperCase().replace(/\s+/g, '');
    const licenciaLimpia = licencia.toUpperCase().replace(/\s+/g, '');
    const falta = DOCUMENTOS_CARGA.find((d) => !archivos.current[d.campo]);
    let problema: [string, string] | null = null;
    if (!/^[A-Z0-9]{3}-?[A-Z0-9]{3}$/.test(placaLimpia)) problema = ['La placa son 6 letras o números, por ejemplo ABC-123.', 'placa'];
    else if (!/^[A-Z0-9-]{6,15}$/.test(licenciaLimpia)) problema = ['Escribe el número de tu licencia de conducir (6 a 15 letras o números).', 'licencia_numero'];
    else if (falta) problema = [`Falta subir: ${falta.titulo}.`, falta.campo];
    if (problema) {
      setMensaje(problema[0]);
      formulario.current?.querySelector<HTMLInputElement>(`[name="${problema[1]}"]`)?.focus();
      return;
    }
    setEnviando(true);
    try {
      await repartidores.enviarDocumentosCarga({
        placa: placaLimpia, licenciaNumero: licenciaLimpia, licencia: archivos.current.licencia as File,
        tarjetaPropiedad: archivos.current.tarjeta_propiedad as File, soat: archivos.current.soat as File,
      });
      toast('Documentos enviados: el admin los revisa y el resultado aparece aquí 🚛');
      await queryClient.invalidateQueries({ queryKey: ['repartidor', 'perfil'] });
    } catch (err) {
      manejarError(err, 'enviar tus documentos');
      setMensaje((err as Error).message);
      setEnviando(false);
    }
  }

  return (
    <form ref={formulario} id="form-carga" className={`form-carga${oculto ? ' hidden' : ''}`} noValidate onSubmit={(e) => { void enviar(e); }} onInput={() => setMensaje('')}>
      <div className="form-carga-grid">
        <label className="perfil-campo">Placa del vehículo
          <input type="text" name="placa" maxLength={7} autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="ABC-123" value={placa} disabled={enviando} onChange={(e) => setPlaca(e.target.value)} required />
        </label>
        <label className="perfil-campo">N.º de licencia de conducir
          <input type="text" name="licencia_numero" maxLength={15} autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="Ej. Q12345678" value={licencia} disabled={enviando} onChange={(e) => setLicencia(e.target.value)} required />
        </label>
      </div>
      <div className="reclamo-archivos carga-archivos">{DOCUMENTOS_CARGA.map((d) => <ArchivoDocumento key={d.campo} d={d} alElegir={(f) => { archivos.current[d.campo] = f; }} />)}</div>
      <p className="carga-privacidad"><i className="ti ti-lock" aria-hidden="true" /> Solo el admin ve estos documentos, para comprobar que el vehículo y quien maneja están en regla.</p>
      {mensaje ? <p className="form-entrega-mensaje error" role="alert"><i className="ti ti-alert-circle" /><span>{mensaje}</span></p> : <p className="form-entrega-mensaje hidden" role="alert" />}
      <div className="form-carga-acciones">
        <button type="submit" className="btn btn-primary" disabled={enviando}>{enviando ? 'Enviando...' : <><i className="ti ti-send" aria-hidden="true" /> Enviar documentos</>}</button>
      </div>
    </form>
  );
}

function SeccionCarga({ perfil }: { perfil: Repartidor }) {
  const [abierto, setAbierto] = useState(false);
  const capacidad = Number(perfil.capacidad_maxima_kg) || 0;
  const estado = perfil.transportista_carga ? perfil.verificacion_estado : null;
  const info = estado ? ESTADO_CARGA_INFO[estado] : undefined;
  const intro = (
    <p className="carga-intro">ChakraShop lleva pedidos de <b>50 sacos o más</b> a Lima y otras ciudades en <b>camiones compartidos</b>.
      {' '}Cada viaje sale cuando junta el 80% de su carga; cada comprador paga el 70% al pedir y el 30% al llegar, <b>antes de descargar</b>.</p>
  );
  const datos = (
    <dl className="carga-datos">
      <div><dt>Placa</dt><dd>{perfil.placa || '—'}</dd></div>
      <div><dt>Licencia</dt><dd>{perfil.licencia_numero || '—'}</dd></div>
      <div><dt>Carga hasta</dt><dd>{numero(capacidad)} kg · {Math.floor(capacidad / 50)} sacos</dd></div>
    </dl>
  );
  let cuerpo;
  if (!estado && capacidad < KG_MINIMOS_CAMION) {
    cuerpo = (
      <>
        {intro}
        <p className="envio-aviso info"><i className="ti ti-info-circle" aria-hidden="true" />
          <span>Para estos viajes el vehículo debe cargar al menos <b>{numero(KG_MINIMOS_CAMION)} kg</b> (50 sacos). El tuyo está registrado con {numero(capacidad)} kg.</span></p>
      </>
    );
  } else if (!estado) {
    cuerpo = (
      <>
        {intro}
        <ul className="carga-requisitos" aria-label="Lo que vas a necesitar">{DOCUMENTOS_CARGA.map((d) => <li key={d.campo}><i className={`ti ti-${d.icono}`} aria-hidden="true" /> {d.titulo}</li>)}</ul>
        <button type="button" className="btn btn-primary btn-abrir-carga" aria-expanded={abierto} aria-controls="form-carga" onClick={() => { setAbierto(!abierto); if (!abierto) setTimeout(() => document.querySelector<HTMLInputElement>('#form-carga input[name="placa"]')?.focus()); }}><i className="ti ti-upload" aria-hidden="true" /> Enviar mis documentos</button>
        <FormCarga perfil={perfil} oculto={!abierto} />
      </>
    );
  } else if (estado === 'pendiente') {
    cuerpo = (
      <>
        <div className="envio-aviso espera"><i className="ti ti-hourglass" aria-hidden="true" />
          <div><strong>Tus documentos están en revisión</strong>
            <span>{perfil.fecha_documentos ? `Los enviaste el ${formatearFechaServidor(perfil.fecha_documentos)} ` : ''}El admin revisa que la licencia, la tarjeta de propiedad y el SOAT estén vigentes y correspondan a este vehículo; el resultado aparece aquí.</span></div></div>
        {datos}
      </>
    );
  } else if (estado === 'rechazado') {
    cuerpo = (
      <>
        <div className="envio-aviso error"><i className="ti ti-alert-circle" aria-hidden="true" />
          <div><strong>El admin observó tus documentos</strong>
            <span>{perfil.verificacion_motivo || 'Revisa que se lean bien y estén vigentes.'} Corrige lo que te indica y vuelve a enviarlos.</span></div></div>
        <FormCarga perfil={perfil} oculto={false} />
      </>
    );
  } else {
    cuerpo = (
      <>
        <div className="envio-aviso ok"><i className="ti ti-circle-check" aria-hidden="true" />
          <div><strong>Ya puedes hacer viajes largos</strong>
            <span>Cuando el admin te asigne un viaje, aparece en «Mis viajes en camión», arriba de tus envíos.</span></div></div>
        {datos}
      </>
    );
  }
  return (
    <div className="card-panel carga-panel">
      <div className="carga-cabecera">
        <span className="carga-cabecera-icono"><i className="ti ti-truck" aria-hidden="true" /></span>
        <div>
          <h3 id="carga-titulo">Viajes largos en camión</h3>
          <p>Lima y otras ciudades, con documentos verificados</p>
        </div>
        {info && <span className={`carga-estado ${estado}`}><i className={`ti ti-${info.icono}`} aria-hidden="true" /> {info.texto}</span>}
      </div>
      {cuerpo}
    </div>
  );
}

// Salir o llegar: el transportista mueve su viaje.
function useMoverViaje(v: Viaje, accion: 'salir' | 'llegar') {
  const refrescar = useRefrescarPanel();
  return async () => {
    try {
      const viaje = accion === 'salir' ? await transporte.salirViaje(v.id) : await transporte.llegarViaje(v.id);
      toast(accion === 'salir'
        ? `¡Buen viaje! Los compradores ya ven que su pedido va a ${viaje.destino_ciudad} 🚛`
        : `Llegaste a ${viaje.destino_ciudad}: descarga cada pedido cuando su saldo figure pagado.`);
      await refrescar();
      return true;
    } catch (err) {
      manejarError(err, accion === 'salir' ? 'marcar la salida del viaje' : 'marcar la llegada del viaje');
      return false;
    }
  };
}

function TarjetaViaje({ v, indice }: { v: Viaje; indice: number }) {
  const ciudad = v.destino_ciudad || 'su destino';
  const cargados = Number(v.sacos_cargados) || 0;
  const paraSalir = Number(v.sacos_para_salir) || 0;
  const salir = useMoverViaje(v, 'salir');
  const llegar = useMoverViaje(v, 'llegar');
  const acciones = {
    abierto: <p className="viaje-nota"><i className="ti ti-info-circle" aria-hidden="true" /> Sale al juntar {paraSalir} sacos: faltan {Math.max(0, paraSalir - cargados)}. Si hace falta salir antes, lo decide el admin.</p>,
    listo: <BotonViaje key="salir" accion="salir" icono="truck-delivery" texto={`Salir hacia ${ciudad}`} pregunta={`¿Ya cargaste los ${cargados} sacos? Al salir, cada comprador ve que su pedido va en camino a ${ciudad}.`} confirmar="Sí, salir ahora" alConfirmar={salir} />,
    en_ruta: <BotonViaje key="llegar" accion="llegar" icono="map-pin" texto={`Llegué a ${ciudad}`} pregunta={`¿Ya estás en ${ciudad}? Cada comprador recibe el aviso para pagar su saldo (30%) antes de que descargues.`} confirmar="Sí, ya llegué" alConfirmar={llegar} />,
    llegado: <p className="viaje-nota"><i className="ti ti-wallet" aria-hidden="true" /> Cobra antes de descargar: entrega cada pedido en «Mis envíos» cuando su saldo figure pagado, con el código del comprador.</p>,
  }[v.estado] ?? null;
  return (
    <article className="viaje-card" style={{ animationDelay: `${Math.min(indice, 8) * 50}ms` }}>
      <div className="viaje-cabecera">
        <div>
          <h4><i className="ti ti-route" aria-hidden="true" /> Huamanga → {ciudad}</h4>
          <small>Viaje #{String(v.id).slice(0, 8)}{v.fecha_salida ? ` · salió el ${formatearFechaServidor(v.fecha_salida)}` : ''}</small>
        </div>
      </div>
      <EtapasViaje v={v} />
      <MedidorViaje v={v} />
      <EnviosDelViaje v={v} />
      {acciones}
    </article>
  );
}

export default function ViajesPage() {
  const perfil = useMiRepartidor();
  const p = perfil.data;
  const viajes = useViajes(Boolean(p?.transportista_carga));
  const lista = viajes.data ?? [];
  return (
    <div className="repartidor-shell">
      {p?.transportista_carga && (
        <div id="viajes-section" className="viajes-section">
          <div className="section-header inner">
            <h3><i className="ti ti-truck" /> Mis viajes en camión</h3>
          </div>
          <div id="viajes-list" className="viajes-grid">{viajes.isLoading ? <SkeletonFilas cantidad={1} /> : lista.map((v, i) => <TarjetaViaje key={v.id} v={v} indice={i} />)}</div>
          {!viajes.isLoading && !lista.length && <p className="muted">Todavía no tienes viajes asignados: cuando el admin te asigne uno, aparece aquí.</p>}
        </div>
      )}
      {p && <section id="carga-section" className="carga-section" aria-labelledby="carga-titulo"><SeccionCarga key={`${p.verificacion_estado}-${p.capacidad_maxima_kg}`} perfil={p} /></section>}
    </div>
  );
}
