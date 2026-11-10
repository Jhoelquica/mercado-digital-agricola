import { lazy, Suspense } from 'react';
import { Link } from 'react-router';
import type { Chacra } from '../../api/chacras';
import type { Productor, RegistroProduccion } from '../../api/productores';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearFechaServidor, formatearHectareas, formatearMoneda } from '../../lib/formato';
import { hayCoordenadas } from '../../lib/productos';
import { calcularResumenEconomico } from '../productor/comun';

const MapaSoloLectura = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaSoloLectura })));

function EstadisticasProductor({ registros }: { registros: RegistroProduccion[] }) {
  const { totalInvertido, cosechados, totalCosechadoKg, conGanancia, gananciaAcumulada, sinCalcular } = calcularResumenEconomico(registros);
  const tarjeta = (icono: string, etiqueta: string, valor: string, clase = '') => (
    <div className="perfil-stat">
      <span className="perfil-stat-icono"><i className={`ti ti-${icono}`} aria-hidden="true" /></span>
      <div><span className={`perfil-stat-valor ${clase}`}>{valor}</span><span className="perfil-stat-etiqueta">{etiqueta}</span></div>
    </div>
  );
  return (
    <section className="perfil-economia" aria-label="Gestión económica">
      <div className="perfil-stats">
        {tarjeta('coin', 'Total invertido', formatearMoneda(totalInvertido))}
        {tarjeta('scale', 'Total cosechado', cosechados.length ? `${totalCosechadoKg.toFixed(2)} kg` : '—')}
        {tarjeta('trending-up', 'Ganancia acumulada', conGanancia.length ? formatearMoneda(gananciaAcumulada) : '—', gananciaAcumulada >= 0 ? 'ganancia-positiva' : 'ganancia-negativa')}
      </div>
      {sinCalcular > 0 && (
        <p className="muted produccion-nota-resumen"><i className="ti ti-info-circle" /> {sinCalcular} registro{sinCalcular > 1 ? 's' : ''} cosechado{sinCalcular > 1 ? 's' : ''} sin datos suficientes para calcular ganancia — no se incluye{sinCalcular > 1 ? 'n' : ''} en el total.</p>
      )}
    </section>
  );
}

// Los cinco estados que puede tener un registro (productores/models.py): "planificado" y "cosechado" son los de hoy;
// "pendiente_verificacion", "aprobado" y "rechazado" quedaron de cuando existía el Verificador.
const ESTADOS_REGISTRO: Record<string, { clase: string; icono: string; etiqueta: string }> = {
  planificado: { clase: 'pill-pendiente', icono: 'seedling', etiqueta: 'Planificado' },
  cosechado: { clase: 'pill-aprobado', icono: 'circle-check', etiqueta: 'Cosechado' },
  pendiente_verificacion: { clase: 'pill-pendiente', icono: 'hourglass', etiqueta: 'Pendiente de verificación' },
  aprobado: { clase: 'pill-aprobado', icono: 'rosette-discount-check', etiqueta: 'Aprobado' },
  rechazado: { clase: 'pill-rechazado', icono: 'circle-x', etiqueta: 'Rechazado' },
};

function fechaCorta(fecha: string | null | undefined): string {
  if (!fecha) return '—';
  // Fechas de calendario que escribe el agricultor: se leen tal cual, sin pasarlas por la zona horaria.
  const [anio = 0, mes = 1, dia = 1] = fecha.slice(0, 10).split('-').map(Number);
  return new Date(anio, mes - 1, dia).toLocaleDateString('es-PE', { day: '2-digit', month: 'short', year: 'numeric' });
}

function TarjetaRegistro({ r, chacras }: { r: RegistroProduccion; chacras: Chacra[] }) {
  const estado = ESTADOS_REGISTRO[r.estado] || { clase: 'pill-default', icono: 'circle', etiqueta: r.estado };
  const chacra = chacras.find((c) => c.id === r.chacra_id);
  const dato = (etiqueta: string, valor: string) => <div className="perfil-dato"><span className="perfil-dato-label">{etiqueta}</span><span className="perfil-dato-valor">{valor}</span></div>;
  return (
    <div className="card-panel produccion-card">
      <div className="produccion-card-header">
        <h4>{r.cultivo}</h4>
        <span className="produccion-card-chips"><span className={`pill pill-estado ${estado.clase}`}><i className={`ti ti-${estado.icono}`} /> {estado.etiqueta}</span></span>
      </div>
      <div className="perfil-datos-grid">
        {dato('Chacra', chacra ? (chacra.nombre || chacra.codigo) : (r.ubicacion_cosecha || '—'))}
        {dato('Siembra', fechaCorta(r.fecha_siembra))}
        {dato(r.fecha_cosecha_real ? 'Cosecha' : 'Cosecha estimada', fechaCorta(r.fecha_cosecha_real || r.fecha_cosecha_estimada))}
        {dato('Costo total', formatearMoneda(r.costo_total))}
        {r.cantidad_cosechada != null && dato('Cosechado', `${Number(r.cantidad_cosechada).toFixed(2)} ${r.unidad_medida}`)}
      </div>
      {r.estado === 'rechazado' && (
        <p className="produccion-sin-calculo"><i className="ti ti-alert-circle" /> Motivo del rechazo: {r.motivo_rechazo || 'no se registró el motivo'}.</p>
      )}
    </div>
  );
}

function TarjetaChacra({ c, registros }: { c: Chacra; registros: RegistroProduccion[] }) {
  const propias = registros.filter((r) => r.chacra_id === c.id);
  const cultivos = [...new Set(propias.map((r) => r.cultivo))];
  return (
    <div className="chacra-card">
      <div className="chacra-card-banner">
        <span className="chacra-codigo-badge">{c.codigo}</span>
        <span className="chacra-card-banner-chips">
          {c.area_m2 != null && <span className="chacra-area-chip" title="Área del contorno dibujado"><i className="ti ti-ruler-measure" aria-hidden="true" /> {formatearHectareas(c.area_m2)} ha</span>}
          <span className="chacra-siembras">{propias.length} siembra{propias.length === 1 ? '' : 's'}</span>
        </span>
      </div>
      <div className="chacra-card-body">
        <h4>{c.nombre || c.codigo}</h4>
        {cultivos.length > 0 && <div className="chacra-card-cultivos">{cultivos.join(', ')}</div>}
        {c.contorno && (
          <div className="chacra-card-mapa-mini">
            <Suspense fallback={null}><MapaSoloLectura latitud={c.ubicacion_latitud} longitud={c.ubicacion_longitud} popup={c.nombre || c.codigo} etiqueta={`Contorno de ${c.nombre || c.codigo} sobre el mapa satelital`} contorno={c.contorno} areaM2={c.area_m2} /></Suspense>
          </div>
        )}
        <div className="chacra-card-ubicacion"><i className="ti ti-map-pin" /> {Number(c.ubicacion_latitud).toFixed(5)}, {Number(c.ubicacion_longitud).toFixed(5)}</div>
        {c.area_m2 != null && <div className="chacra-card-ubicacion"><i className="ti ti-ruler-measure" /> Contorno dibujado: {formatearHectareas(c.area_m2)} ha</div>}
      </div>
    </div>
  );
}

interface Props {
  perfil: Productor | null;
  registros: RegistroProduccion[];
  chacras: Chacra[];
}

// Datos del agricultor en Mi Perfil, en solo lectura: lo que se cambia está en su panel.
export function PerfilProductor({ perfil, registros }: Omit<Props, 'chacras'>) {
  if (!perfil) {
    return (
      <div className="perfil-cta-completar">
        <span className="empty-state-icon"><i className="ti ti-tractor" /></span>
        <p><strong>Aún no completaste tu perfil de productor</strong></p>
        <p className="muted">Créalo para empezar a publicar productos sin intermediarios.</p>
        <Link className="btn btn-primary" id="btn-perfil-ir-panel" to="/productor">Completar perfil de productor</Link>
      </div>
    );
  }
  const dato = (etiqueta: string, valor: string) => <div className="perfil-dato"><span className="perfil-dato-label">{etiqueta}</span><span className="perfil-dato-valor">{valor}</span></div>;
  return (
    <>
      <EstadisticasProductor registros={registros} />
      <section className="card-panel perfil-tarjeta">
        <h3><i className="ti ti-tractor" /> Datos de productor</h3>
        <div className="perfil-datos-grid">
          {perfil.nombre && dato('Nombre', perfil.nombre)}
          {dato('Comunidad / Región', perfil.comunidad || '—')}
          {dato('Contacto', perfil.contacto || '—')}
          {perfil.fecha_registro && dato('Registrado', formatearFechaServidor(perfil.fecha_registro))}
        </div>
        {hayCoordenadas(perfil.latitud, perfil.longitud) ? (
          <div className="mapa-bloque">
            <h4 className="mapa-titulo"><i className="ti ti-map-pin" /> Ubicación de tu chakra</h4>
            <Suspense fallback={<div className="mapa-mini" />}>
              <MapaSoloLectura latitud={perfil.latitud as string} longitud={perfil.longitud as string} popup={perfil.nombre || 'Tu chakra'} etiqueta="Mapa con la ubicación de tu chakra" />
            </Suspense>
          </div>
        ) : <div className="mapa-bloque-neutro"><span className="icon"><i className="ti ti-map-pin" /></span> No has marcado la ubicación de tu chakra todavía.</div>}
      </section>
    </>
  );
}

// "Mis siembras" y "Mis chacras" van a todo el ancho, debajo del perfil.
export function SiembrasYChacrasDelPerfil({ registros, chacras }: Omit<Props, 'perfil'>) {
  return (
    <>
      <section className="perfil-siembras-acceso card-panel">
        <span className="perfil-siembras-icono" aria-hidden="true"><i className="ti ti-plant-2" /></span>
        <div><strong>Tus siembras y producciones están en tu panel</strong>
          <p>Ahí las registras con un botón grande en "Inicio" y anotas sus gastos en "Mis siembras".</p></div>
        <Link className="btn btn-primary" id="btn-perfil-ir-siembras" to="/productor/siembras"><i className="ti ti-arrow-right" aria-hidden="true" /> Ir a <Texto clave="tab.siembras">Mis siembras</Texto></Link>
      </section>
      <section className="perfil-produccion" aria-labelledby="perfil-siembras-titulo">
        <h3 id="perfil-siembras-titulo"><i className="ti ti-plant-2" /> <Texto clave="seccion.siembras">Mis siembras</Texto></h3>
        {registros.length
          ? <div className="produccion-lista">{registros.map((r) => <TarjetaRegistro key={r.id} r={r} chacras={chacras} />)}</div>
          : <p className="muted">Aún no registraste ninguna siembra.</p>}
      </section>
      <section className="perfil-produccion" aria-labelledby="perfil-chacras-titulo">
        <h3 id="perfil-chacras-titulo"><i className="ti ti-map-pin" /> <Texto clave="seccion.chacras">Mis chacras</Texto></h3>
        {chacras.length
          ? <div className="grid-chacras">{chacras.map((c) => <TarjetaChacra key={c.id} c={c} registros={registros} />)}</div>
          : <p className="muted">Aún no registraste ninguna chacra.</p>}
      </section>
    </>
  );
}
