// /admin/transportistas: repartidores con vehículo de carga que quieren hacer viajes largos. El admin revisa la licencia,
// la tarjeta de propiedad y el SOAT; solo a un transportista verificado se le asignan viajes.
import { useState } from 'react';
import { transporte, type Transportista } from '../../api/transporte';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { formatearFechaServidor } from '../../lib/formato';
import { DocumentosPrivados, EncabezadoSeccion, kgYSacos, PasosDeRevision, useRefrescarAdmin, useTransportistas, Vacio } from './comun';

const DOCUMENTOS_TRANSPORTISTA = [
  { cual: 'licencia', icono: 'id', titulo: 'Licencia' },
  { cual: 'tarjeta', icono: 'file-certificate', titulo: 'Tarjeta de propiedad' },
  { cual: 'soat', icono: 'shield-check', titulo: 'SOAT' },
];
const ESTADO_TRANSPORTISTA: Record<string, { texto: string; pill: string; icono: string; vacio: string }> = {
  pendiente: { texto: 'Por revisar', pill: 'pill-pendiente', icono: 'hourglass', vacio: 'No hay documentos por revisar' },
  verificado: { texto: 'Verificado', pill: 'pill-aprobado', icono: 'circle-check', vacio: 'Todavía no hay transportistas verificados' },
  rechazado: { texto: 'Observado', pill: 'pill-rechazado', icono: 'alert-circle', vacio: 'No hay documentos observados' },
};
const FILTROS = [
  { estado: 'pendiente', icono: 'hourglass', texto: 'Por revisar' },
  { estado: 'verificado', icono: 'circle-check', texto: 'Verificados' },
  { estado: 'rechazado', icono: 'alert-circle', texto: 'Observados' },
];

function TarjetaTransportista({ t, indice }: { t: Transportista; indice: number }) {
  const refrescar = useRefrescarAdmin();
  const estado = ESTADO_TRANSPORTISTA[t.verificacion_estado || ''] ?? ESTADO_TRANSPORTISTA.pendiente!;
  const placa = t.placa || '—';
  const dato = (etiqueta: string, valor: string, mono = false) => (
    <div className="perfil-dato"><span className="perfil-dato-label">{etiqueta}</span><span className={`perfil-dato-valor${mono ? ' perfil-dato-mono' : ''}`}>{valor}</span></div>
  );
  async function decidir(accion: 'aprobar' | 'rechazar', motivo = '') {
    try {
      if (accion === 'aprobar') {
        await transporte.admin.verificarTransportista(t.id);
        toast('Transportista verificado: ya se le pueden asignar viajes 🚛');
      } else {
        await transporte.admin.rechazarTransportista(t.id, motivo);
        toast('Documentos observados: el transportista verá qué corregir.');
      }
    } catch (err) {
      manejarError(err, accion === 'aprobar' ? 'verificar al transportista' : 'observar los documentos');
      return;
    }
    await refrescar('transportistas', 'viajes');
  }
  return (
    <div className="pedido-card transportista-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-transportista-id={t.id}>
      <div className="pedido-card-header">
        <h4>{t.nombre || 'Sin nombre'}</h4>
        <span className={`pill pill-estado ${estado.pill}`}><i className={`ti ti-${estado.icono}`} /> {estado.texto}</span>
      </div>
      <div className="perfil-datos-grid">
        {dato('DNI', t.dni || '—', true)}
        {dato('Placa', placa, true)}
        {dato('Licencia', t.licencia_numero || '—', true)}
        {dato('Vehículo', `${t.tipo_vehiculo || '—'} · ${kgYSacos(t.capacidad_maxima_kg)}`)}
        {dato('Documentos enviados', t.fecha_documentos ? formatearFechaServidor(t.fecha_documentos) : '—')}
      </div>
      {t.verificacion_estado === 'rechazado' && t.verificacion_motivo && <p className="reclamo-pista alerta"><i className="ti ti-alert-circle" aria-hidden="true" /> Le pediste: {t.verificacion_motivo}</p>}
      <DocumentosPrivados documentos={DOCUMENTOS_TRANSPORTISTA} cargar={(cual) => transporte.admin.documentoTransportista(t.id, cual)} quien="el transportista" />
      <PasosDeRevision
        mostrarRechazar={t.verificacion_estado !== 'rechazado'} textoRechazar={<><i className="ti ti-alert-circle" aria-hidden="true" /> Observar</>} claseRechazar="btn-rechazar-transportista"
        mostrarAprobar={t.verificacion_estado !== 'verificado'} textoAprobar={<><i className="ti ti-check" aria-hidden="true" /> Verificar</>} claseAprobar="btn-aprobar-transportista"
        etiquetaConfirmar="Confirmar verificación"
        pregunta={`¿La licencia, la tarjeta de propiedad y el SOAT están vigentes, se leen bien y corresponden a la placa ${placa}? Al verificar, ya se le pueden asignar viajes.`}
        confirmar="Sí, verificar"
        etiquetaMotivo="Qué tiene que corregir (lo verá el transportista)" placeholderMotivo="Ej. El SOAT está vencido: sube el vigente."
        enviarRechazo="Observar documentos"
        validarMotivo={(m) => (m.length < 3 ? 'Escribe qué tiene que corregir: el transportista lo va a leer.' : null)}
        alAprobar={() => decidir('aprobar')} alRechazar={(m) => decidir('rechazar', m)}
      />
    </div>
  );
}

export default function TransportistasPage() {
  const [filtro, setFiltro] = useState('pendiente');
  const consulta = useTransportistas(filtro);
  const refrescar = useRefrescarAdmin();
  const lista = consulta.data ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="transportistas">
      <EncabezadoSeccion icono="id" titulo="Transportistas de carga" alRefrescar={() => { void refrescar('transportistas'); }} />
      <p className="muted admin-nota">Repartidores con vehículo de carga (2 500 kg o más) que quieren hacer viajes largos. Revisa que la licencia, la tarjeta de propiedad y el SOAT estén vigentes, se lean bien y correspondan a la placa. Solo a un transportista verificado se le asignan viajes.</p>
      <div className="admin-filtros" role="group" aria-label="Filtrar transportistas por estado">
        {FILTROS.map((f) => (
          <button key={f.estado} type="button" className={`admin-filtro${filtro === f.estado ? ' active' : ''}`} data-estado={f.estado} aria-pressed={filtro === f.estado} onClick={() => setFiltro(f.estado)}>
            <i className={`ti ti-${f.icono}`} aria-hidden="true" /> {f.texto}
          </button>
        ))}
      </div>
      <div id="transportistas-lista" className="grid-admin">{consulta.isLoading ? <SkeletonFilas cantidad={1} /> : lista.map((t, i) => <TarjetaTransportista key={`${t.id}-${t.verificacion_estado}`} t={t} indice={i} />)}</div>
      {consulta.data && !lista.length && <Vacio icono="id" titulo={<span id="transportistas-empty-titulo">{ESTADO_TRANSPORTISTA[filtro]!.vacio}</span>} detalle="Cuando un repartidor con vehículo de carga envíe sus documentos, aparece acá." />}
    </div>
  );
}
