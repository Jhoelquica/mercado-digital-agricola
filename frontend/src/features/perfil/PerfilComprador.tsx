import { useEffect, useRef, useState, type FormEvent } from 'react';
import { usuarios, type MisReclamosRuc, type Perfil } from '../../api/usuarios';
import { actualizarSesion } from '../../auth/sesion';
import { toast } from '../../components/Toast';
import { datosDeComprador, rucOcupadoDe, type TipoComprador } from '../../lib/comprador';
import { formatearFechaServidor } from '../../lib/formato';
import { rucValido } from '../../lib/reglas';

const ESTADOS_RECLAMO_RUC: Record<string, { texto: string; icono: string }> = {
  pendiente: { texto: 'En revisión del admin', icono: 'hourglass' },
  aprobado: { texto: 'Aprobado: el RUC ya está en tu cuenta', icono: 'circle-check' },
  rechazado: { texto: 'Rechazado', icono: 'circle-x' },
};

const TAMANO_MAXIMO_DOCUMENTO = 5 * 1024 * 1024;

function ArchivoReclamo({ id, icono, titulo, vacio, archivo, alElegir }: { id: string; icono: string; titulo: string; vacio: string; archivo: File | null; alElegir: (f: File | null) => void }) {
  const tamano = !archivo ? '' : archivo.size < 1024 * 1024
    ? `${Math.max(1, Math.round(archivo.size / 1024))} KB`
    : `${(archivo.size / 1024 / 1024).toLocaleString('es-PE', { maximumFractionDigits: 1 })} MB`;
  return (
    <label className={`reclamo-archivo ${archivo ? 'con-archivo' : ''}`}>
      <input type="file" id={id} accept="image/jpeg,image/png,image/webp,application/pdf" required onChange={(e) => alElegir(e.target.files?.[0] ?? null)} />
      <span className="reclamo-archivo-caja">
        <i className={`ti ti-${icono}`} aria-hidden="true" />
        <strong>{titulo}</strong>
        <small className="reclamo-archivo-nombre">{archivo ? `${archivo.name} · ${tamano}` : vacio}</small>
      </span>
    </label>
  );
}

// Reclamar un RUC que está en otra cuenta: el dueño sube su Ficha RUC y el DNI del dueño o representante; el admin compara
// los nombres con SUNAT y decide.
function FormReclamoRuc({ rucInicial, alCancelar, alEnviar }: { rucInicial: string; alCancelar: () => void; alEnviar: () => void }) {
  const [ruc, setRuc] = useState(rucInicial);
  const [dni, setDni] = useState('');
  const [nombreTitular, setNombreTitular] = useState('');
  const [mensaje, setMensaje] = useState('');
  const [fichaRuc, setFichaRuc] = useState<File | null>(null);
  const [fotoDni, setFotoDni] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [enviando, setEnviando] = useState(false);
  const seccion = useRef<HTMLElement>(null);

  useEffect(() => {
    seccion.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    seccion.current?.querySelector<HTMLInputElement>(rucInicial ? '#reclamo-dni' : '#reclamo-ruc')?.focus({ preventScroll: true });
  }, [rucInicial]);

  async function enviar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const rucLimpio = ruc.replace(/\s+/g, '');
    const problema = !rucValido(rucLimpio) ? 'El RUC no es válido: son 11 dígitos y empieza con 10 o 20.'
      : !/^\d{8}$/.test(dni.trim()) ? 'El DNI son 8 dígitos.'
        : nombreTitular.trim().length < 3 ? 'Escribe el nombre completo, como figura en el DNI.'
          : !fichaRuc ? 'Sube la Ficha RUC (foto o PDF).'
            : !fotoDni ? 'Sube la foto del DNI.'
              : [fichaRuc, fotoDni].some((f) => f.size > TAMANO_MAXIMO_DOCUMENTO) ? 'Cada documento puede pesar hasta 5 MB.' : '';
    setError(problema);
    if (problema || !fichaRuc || !fotoDni) return;
    setEnviando(true);
    try {
      await usuarios.reclamarRuc({ ruc: rucLimpio, nombreTitular: nombreTitular.trim(), dni: dni.trim(), mensaje, fichaRuc, fotoDni });
      toast('Reclamo enviado: el admin revisará tus documentos y te responderá en tu perfil.');
      alEnviar();
    } catch (err) {
      setError((err as Error).message || 'No se pudo enviar el reclamo. Intenta de nuevo.');
      setEnviando(false);
    }
  }

  return (
    <section ref={seccion} className="reclamo-ruc" id="perfil-reclamo-ruc" aria-labelledby="reclamo-ruc-titulo">
      <h4 id="reclamo-ruc-titulo"><i className="ti ti-file-certificate" aria-hidden="true" /> Reclamar un RUC</h4>
      <p className="reclamo-ruc-intro">Si otra cuenta registró el RUC de tu negocio, demuéstranos que es tuyo. El admin compara el nombre
        de tu DNI con el de la Ficha RUC y con lo que dice SUNAT, y te responde aquí mismo.</p>
      <form id="perfil-form-reclamo" noValidate onSubmit={(e) => { void enviar(e); }}>
        <div className="reclamo-ruc-grid">
          <label className="perfil-campo"><span>RUC del negocio</span>
            <input type="text" id="reclamo-ruc" inputMode="numeric" maxLength={11} placeholder="11 dígitos" required value={ruc} onChange={(e) => setRuc(e.target.value)} /></label>
          <label className="perfil-campo"><span>DNI del dueño o representante</span>
            <input type="text" id="reclamo-dni" inputMode="numeric" maxLength={8} placeholder="8 dígitos" required value={dni} onChange={(e) => setDni(e.target.value)} /></label>
          <label className="perfil-campo reclamo-ruc-ancho"><span>Nombre completo, como figura en el DNI</span>
            <input type="text" id="reclamo-nombre" maxLength={120} autoComplete="name" placeholder="Ej. Rosa Quispe Huamán" required value={nombreTitular} onChange={(e) => setNombreTitular(e.target.value)} /></label>
        </div>
        <div className="reclamo-archivos">
          <ArchivoReclamo id="reclamo-ficha" icono="file-certificate" titulo="Ficha RUC" vacio="Foto o PDF, hasta 5 MB. La descargas en SUNAT Operaciones en Línea." archivo={fichaRuc} alElegir={setFichaRuc} />
          <ArchivoReclamo id="reclamo-foto-dni" icono="id" titulo="Foto del DNI" vacio="Del dueño o representante legal, que se lea bien. Hasta 5 MB." archivo={fotoDni} alElegir={setFotoDni} />
        </div>
        <label className="perfil-campo"><span>Mensaje para el admin <span className="auth-opcional">(opcional)</span></span>
          <textarea id="reclamo-mensaje" maxLength={500} rows={2} placeholder="Ej. Soy la dueña del restaurante; no conozco la cuenta que registró mi RUC." value={mensaje} onChange={(e) => setMensaje(e.target.value)} /></label>
        <p className={`perfil-form-error ${error ? '' : 'hidden'}`} id="reclamo-error" role="alert">{error}</p>
        <div className="tarjeta-acciones">
          <button type="button" className="btn btn-ghost btn-cancelar-reclamo" onClick={alCancelar}>Cancelar</button>
          <button type="submit" className="btn btn-primary" disabled={enviando}>
            {enviando ? <><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Enviando…</> : <><i className="ti ti-send" aria-hidden="true" /> Enviar reclamo</>}
          </button>
        </div>
      </form>
    </section>
  );
}

interface Props {
  usuario: Perfil;
  reclamos: MisReclamosRuc | null;
  // RUC con el que abrir el reclamo al cargar (null: cerrado). Lo usa el registro "para mi casa y reclamar el RUC".
  reclamoInicial: string | null;
  alCambiar: () => void;
}

// Persona o negocio: cambia lo que la tienda muestra primero (por kilo o por saco).
export function PerfilComoCompras({ usuario, reclamos, reclamoInicial, alCambiar }: Props) {
  const [tipo, setTipo] = useState<TipoComprador>(usuario.tipo_comprador === 'negocio' ? 'negocio' : 'persona');
  const [nombreComercial, setNombreComercial] = useState(usuario.nombre_comercial || '');
  const [ruc, setRuc] = useState(usuario.ruc || '');
  const [error, setError] = useState('');
  const [rucOcupado, setRucOcupado] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [reclamo, setReclamo] = useState<string | null>(reclamoInicial);
  const campoRuc = useRef<HTMLInputElement>(null);
  const negocio = usuario.tipo_comprador === 'negocio';

  async function guardar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setRucOcupado(null);
    const { datos, error: mensaje } = datosDeComprador(tipo, nombreComercial, ruc);
    setError(mensaje || '');
    if (mensaje || !datos) {
      campoRuc.current?.focus();
      return;
    }
    setGuardando(true);
    try {
      const perfil = await usuarios.actualizarComprador(datos);
      actualizarSesion({ tipoComprador: perfil.tipo_comprador ?? null });
      toast(perfil.tipo_comprador === 'negocio'
        ? `Negocio verificado en SUNAT: ${perfil.razon_social}. Te mostraremos primero el precio por mayor.`
        : 'Listo: te mostraremos primero el precio por kilo.');
      alCambiar();
    } catch (err) {
      setError((err as Error).message || 'No se pudo guardar. Intenta de nuevo.');
      setRucOcupado(rucOcupadoDe(err));
      setGuardando(false);
    }
  }

  const opcion = (valor: TipoComprador, icono: string, titulo: string, detalle: string) => (
    <label className="auth-rol">
      <input type="radio" name="perfil-tipo-comprador" value={valor} checked={tipo === valor} onChange={() => setTipo(valor)} />
      <span className="auth-rol-caja"><i className={`ti ti-${icono}`} aria-hidden="true" /><strong>{titulo}</strong><small>{detalle}</small></span>
    </label>
  );

  return (
    <section className="card-panel perfil-tarjeta perfil-como-compras">
      <h3><i className="ti ti-building-store" /> ¿Cómo compras?</h3>
      <form id="perfil-form-comprador" noValidate onSubmit={(e) => { void guardar(e); }}>
        <fieldset className="auth-roles">
          <legend className="sr-only">Compro para</legend>
          <div className="auth-roles-grid">
            {opcion('persona', 'home', 'Para mi casa', 'Te mostramos primero el precio por kilo')}
            {opcion('negocio', 'building-store', 'Para mi negocio', 'Te mostramos primero el precio por mayor (por saco)')}
          </div>
        </fieldset>
        <div className={`perfil-datos-negocio ${tipo === 'negocio' ? '' : 'hidden'}`} id="perfil-datos-negocio">
          <label className="perfil-campo"><span>Nombre del negocio <span className="auth-opcional">(opcional)</span></span>
            <input type="text" id="perfil-nombre-comercial" maxLength={120} value={nombreComercial} placeholder="Ej. Restaurante El Ayacuchano" onChange={(e) => setNombreComercial(e.target.value)} />
          </label>
          <label className="perfil-campo"><span>RUC del negocio</span>
            <input ref={campoRuc} type="text" id="perfil-ruc" inputMode="numeric" maxLength={11} value={ruc} placeholder="11 dígitos" aria-describedby="perfil-ruc-ayuda" onChange={(e) => setRuc(e.target.value)} />
            <small className="auth-ayuda" id="perfil-ruc-ayuda"><i className="ti ti-shield-check" aria-hidden="true" /> Lo verificamos en SUNAT: tiene que estar activo y habido.</small>
          </label>
          {negocio && usuario.razon_social && (
            <p className="perfil-ruc-verificado"><i className="ti ti-rosette-discount-check" aria-hidden="true" />
              <span>Verificado en SUNAT: <strong>{usuario.razon_social}</strong> · RUC {usuario.ruc}</span></p>
          )}
        </div>
        <div className={`perfil-form-error ${error ? '' : 'hidden'}`} id="perfil-comprador-error" role="alert">
          {error}
          {rucOcupado !== null && (
            <>{' '}<button type="button" className="btn btn-primary btn-sm" onClick={() => setReclamo(rucOcupado)}>
              <i className="ti ti-file-certificate" aria-hidden="true" /> Reclamar este RUC</button></>
          )}
        </div>
        <button type="submit" className="btn btn-primary" disabled={guardando}>
          {guardando && tipo === 'negocio' ? <><i className="ti ti-loader-2" aria-hidden="true" /> Verificando en SUNAT…</> : <><i className="ti ti-device-floppy" aria-hidden="true" /> Guardar</>}
        </button>
      </form>
      {(reclamos?.rucs_que_perdi || []).map((p) => (
        <p className="reclamo-ruc-aviso" role="status" key={p.ruc}><i className="ti ti-info-circle" aria-hidden="true" />
          <span>El admin revisó los documentos de otra cuenta y le asignó el RUC <strong>{p.ruc}</strong>{' '}
            {p.razon_social && `(${p.razon_social})`} el {formatearFechaServidor(p.fecha_revision)}; por eso tu cuenta quedó
            {' '}"Para mi casa". Si crees que es un error, escríbenos.</span></p>
      ))}
      {(reclamos?.mis_reclamos || []).length > 0 && (
        <div className="reclamos-ruc-lista">
          <h4>Tus reclamos de RUC</h4>
          <ul>
            {(reclamos?.mis_reclamos || []).map((r) => {
              const estado = ESTADOS_RECLAMO_RUC[r.estado] || { texto: r.estado, icono: 'file' };
              return (
                <li className={`reclamo-ruc-item estado-${r.estado}`} key={r.id}>
                  <i className={`ti ti-${estado.icono}`} aria-hidden="true" />
                  <div>
                    <strong>RUC {r.ruc}{r.razon_social && ` · ${r.razon_social}`}</strong>
                    <small>Enviado el {formatearFechaServidor(r.fecha_creacion)} · {estado.texto}</small>
                    {r.estado === 'rechazado' && r.motivo_rechazo && <small className="reclamo-ruc-motivo">Motivo: {r.motivo_rechazo}</small>}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <p className="reclamo-ruc-invitacion">¿Otra cuenta registró el RUC de tu negocio?{' '}
        <button type="button" className="btn-link" onClick={() => setReclamo('')}>Reclámalo</button></p>
      {reclamo !== null && <FormReclamoRuc key={reclamo} rucInicial={reclamo} alCancelar={() => setReclamo(null)} alEnviar={alCambiar} />}
    </section>
  );
}
