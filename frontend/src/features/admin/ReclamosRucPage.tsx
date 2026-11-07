// /admin/reclamos-ruc: alguien dice que el RUC que tiene otra cuenta es de su negocio. El admin compara el DNI con la Ficha
// RUC y con SUNAT; al aprobar, el RUC pasa a quien reclamó; al rechazar, quien reclamó ve el motivo.
import { ApiError } from '../../api/errors';
import { usuarios, type DatosDeCuenta, type ReclamoRucAdmin } from '../../api/usuarios';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor } from '../../lib/formato';
import { DocumentosPrivados, EncabezadoSeccion, PasosDeRevision, useRefrescarAdmin, useReclamosRuc, Vacio } from './comun';

const DOCUMENTOS_RECLAMO = [
  { cual: 'ficha', icono: 'file-certificate', titulo: 'Ficha RUC', boton: 'Ver Ficha RUC' },
  { cual: 'dni', icono: 'id', titulo: 'DNI', boton: 'Ver DNI' },
];

function CuentaDelReclamo({ cuenta, titulo, declara }: { cuenta: DatosDeCuenta | null; titulo: string; declara?: { nombre: string; dni: string } }) {
  if (!cuenta) return <div className="reclamo-cuenta"><h5>{titulo}</h5><p className="muted">Ninguna: la cuenta que lo tenía ya lo soltó.</p></div>;
  return (
    <div className="reclamo-cuenta">
      <h5>{titulo}</h5>
      <dl>
        <div><dt>Cuenta</dt><dd>{cuenta.nombre}<br /><small>{cuenta.email}</small></dd></div>
        {declara
          ? <div><dt>Dice ser</dt><dd>{declara.nombre}<br /><small>DNI {declara.dni}</small></dd></div>
          : <>
            <div><dt>Negocio</dt><dd>{cuenta.nombre_comercial || '—'}</dd></div>
            <div><dt>RUC verificado en SUNAT</dt><dd>{cuenta.ruc_verificado_en ? formatearFechaServidor(cuenta.ruc_verificado_en) : '—'}</dd></div>
          </>}
        <div><dt>Cuenta creada</dt><dd>{formatearFechaServidor(cuenta.fecha_registro)}</dd></div>
      </dl>
    </div>
  );
}

// En un RUC 10 (persona con negocio) los dígitos 3 a 10 son el DNI: se le dice al admin si coincide con el declarado.
function PistaDniEnRuc({ r }: { r: ReclamoRucAdmin }) {
  if (!String(r.ruc).startsWith('10')) return null;
  const dniDelRuc = String(r.ruc).slice(2, 10);
  const coincide = dniDelRuc === r.dni;
  return (
    <p className={`reclamo-pista ${coincide ? 'ok' : 'alerta'}`}><i className={`ti ti-${coincide ? 'circle-check' : 'alert-triangle'}`} aria-hidden="true" />{' '}
      En un RUC 10 el DNI va dentro del RUC: {dniDelRuc}. {coincide ? 'Coincide con el DNI declarado.' : 'NO coincide con el DNI declarado.'}</p>
  );
}

function TarjetaReclamo({ r, indice }: { r: ReclamoRucAdmin; indice: number }) {
  const refrescar = useRefrescarAdmin();
  async function decidir(accion: 'aprobar' | 'rechazar', motivo = '') {
    try {
      if (accion === 'aprobar') {
        await usuarios.admin.aprobarReclamo(r.id);
        toast('Reclamo aprobado: el RUC pasó a la cuenta que lo reclamó.');
      } else {
        await usuarios.admin.rechazarReclamo(r.id, motivo);
        toast('Reclamo rechazado: quien reclamó verá el motivo en su perfil.');
      }
    } catch (err) {
      manejarError(err, accion === 'aprobar' ? 'aprobar el reclamo' : 'rechazar el reclamo');
      if (!(err instanceof ApiError && err.status === 409)) return; // 409: otro admin ya lo revisó → se recarga la lista igual
    }
    await refrescar('reclamos-ruc');
  }
  return (
    <div className="pedido-card reclamo-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-reclamo-id={r.id}>
      <div className="pedido-card-header">
        <h4>RUC {r.ruc}</h4>
        <span className="pill pill-estado pill-pendiente"><i className="ti ti-hourglass" /> Por revisar</span>
      </div>
      <p className="reclamo-sunat"><i className="ti ti-building-bank" aria-hidden="true" /> SUNAT: <strong>{r.razon_social || '—'}</strong></p>
      <PistaDniEnRuc r={r} />
      <div className="reclamo-cuentas">
        <CuentaDelReclamo cuenta={r.reclamante} titulo="Quien reclama" declara={{ nombre: r.nombre_titular, dni: r.dni }} />
        <CuentaDelReclamo cuenta={r.titular_actual} titulo="Cuenta que tiene el RUC" />
      </div>
      {r.mensaje && <p className="reclamo-mensaje"><i className="ti ti-message" aria-hidden="true" /> "{r.mensaje}"</p>}
      <DocumentosPrivados documentos={DOCUMENTOS_RECLAMO} cargar={(cual) => usuarios.admin.documentoReclamo(r.id, cual as 'ficha' | 'dni')} quien="quien reclama" />
      <PasosDeRevision
        textoRechazar={<><i className="ti ti-x" aria-hidden="true" /> Rechazar</>} claseRechazar="btn-rechazar-reclamo"
        textoAprobar={<><i className="ti ti-check" aria-hidden="true" /> Aprobar</>} claseAprobar="btn-aprobar-reclamo"
        etiquetaConfirmar="Confirmar aprobación"
        pregunta={<>¿El nombre del DNI coincide con el de la Ficha RUC y con SUNAT? Al aprobar, el RUC {r.ruc} pasa a {r.reclamante?.nombre || 'quien reclama'}{r.titular_actual ? ` y ${r.titular_actual.nombre} queda "Para mi casa"` : ''}.</>}
        confirmar="Sí, aprobar"
        etiquetaMotivo="Motivo del rechazo (lo verá quien reclamó)" placeholderMotivo="Ej. El nombre del DNI no coincide con el de la Ficha RUC."
        enviarRechazo="Rechazar reclamo"
        validarMotivo={(m) => (m ? null : 'Escribe el motivo: quien reclamó lo va a ver.')}
        alAprobar={() => decidir('aprobar')} alRechazar={(m) => decidir('rechazar', m)}
      />
    </div>
  );
}

export default function ReclamosRucPage() {
  const consulta = useReclamosRuc();
  const refrescar = useRefrescarAdmin();
  const lista = consulta.data ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="reclamos-ruc">
      <EncabezadoSeccion icono="file-certificate" titulo="Reclamos de RUC por revisar" alRefrescar={() => { void refrescar('reclamos-ruc'); }} />
      <p className="muted admin-nota">Alguien dice que el RUC que tiene otra cuenta es de su negocio. Compara el nombre de su DNI con el de la Ficha RUC y con la razón social de SUNAT. Al aprobar, el RUC pasa a la cuenta que reclamó y la otra queda "Para mi casa"; al rechazar, quien reclamó ve el motivo.</p>
      <div id="reclamos-ruc-lista" className="grid-admin">{consulta.isLoading ? <SkeletonFilas cantidad={1} /> : lista.map((r, i) => <TarjetaReclamo key={r.id} r={r} indice={i} />)}</div>
      {consulta.data && !lista.length && <Vacio icono="file-certificate" titulo="No hay reclamos de RUC por revisar" detalle="Cuando alguien reclame un RUC que está en otra cuenta, aparece acá." />}
    </div>
  );
}
