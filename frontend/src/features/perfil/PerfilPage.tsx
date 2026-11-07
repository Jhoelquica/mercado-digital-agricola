import { useQuery } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router';
import { ApiError } from '../../api/errors';
import { chacras } from '../../api/chacras';
import { productores } from '../../api/productores';
import { repartidores, type Repartidor } from '../../api/repartidores';
import { usuarios, type Perfil } from '../../api/usuarios';
import { useAuth } from '../../auth/AuthContext';
import { rutaInicialPara } from '../../auth/rutasPorRol';
import { manejarError } from '../../components/Toast';
import { abrirPanelBusqueda } from '../catalogo/menus';
import { PerfilComoCompras } from './PerfilComprador';
import { PerfilProductor, SiembrasYChacrasDelPerfil } from './PerfilProductor';

const ROL_INFO: Record<string, { icono: string; texto: string; descripcion: string }> = {
  comprador: { icono: 'shopping-cart', texto: 'Comprador', descripcion: 'Explora el catálogo y haz seguimiento de tus compras.' },
  productor: { icono: 'tractor', texto: 'Productor', descripcion: 'Publica tus productos sin intermediarios y lleva tu gestión económica.' },
  repartidor: { icono: 'truck', texto: 'Repartidor', descripcion: 'Recibe propuestas de envío y entrega los pedidos.' },
  admin: { icono: 'settings', texto: 'Administrador', descripcion: 'Administra la plataforma.' },
};

const ACCESO_PANEL: Record<string, { icono: string; etiqueta: string }> = {
  productor: { icono: 'tractor', etiqueta: 'Panel de productor' },
  repartidor: { icono: 'truck', etiqueta: 'Gestionar envíos' },
  admin: { icono: 'settings', etiqueta: 'Panel de administración' },
};

const DESCONECTADO = { texto: 'Desconectado', clase: 'disponibilidad-gris', detalle: 'No recibirás nuevas propuestas hasta que te conectes.' };
const DISPONIBILIDAD: Record<string, typeof DESCONECTADO> = {
  disponible: { texto: 'Disponible', clase: 'disponibilidad-verde', detalle: 'Puedes recibir nuevas propuestas de envío.' },
  ocupado: { texto: 'Ocupado', clase: 'disponibilidad-ambar', detalle: 'Estás atendiendo un envío en este momento.' },
  desconectado: DESCONECTADO,
};

const VEHICULOS: Record<string, string> = { moto: 'Moto', mototaxi: 'Mototaxi', auto: 'Auto', camioneta: 'Camioneta', camion: 'Camión', bicicleta: 'Bicicleta' };

export interface EstadoPerfil {
  reclamoRucPorAbrir?: string;
}

function ocultarDni(dni: string | null | undefined): string {
  if (!dni) return '—';
  if (dni.length < 6) return dni;
  return `${dni.slice(0, 4)}${'*'.repeat(Math.max(0, dni.length - 6))}${dni.slice(-2)}`;
}

function SkeletonPerfil() {
  return (
    <>
      <div className="perfil-hero panel-header-hero" aria-hidden="true">
        <div className="skeleton" style={{ width: 104, height: 104, borderRadius: '50%', flexShrink: 0 }} />
        <div style={{ flex: 1 }}>
          <div className="skeleton skeleton-line w-40" style={{ marginBottom: 12 }} />
          <div className="skeleton skeleton-line tall w-60" />
        </div>
      </div>
      <div className="perfil-layout" aria-hidden="true">
        <div className="card-panel">
          <div className="skeleton skeleton-line w-40" style={{ marginBottom: 14 }} />
          <div className="skeleton skeleton-line tall w-70" style={{ marginBottom: 10 }} />
          <div className="skeleton skeleton-line w-60" />
        </div>
        <div className="card-panel">
          <div className="skeleton skeleton-line w-40" style={{ marginBottom: 14 }} />
          <div className="skeleton skeleton-line tall w-70" />
        </div>
      </div>
    </>
  );
}

function PerfilRepartidor({ perfil }: { perfil: Repartidor | null }) {
  if (!perfil) {
    return (
      <div className="perfil-cta-completar">
        <span className="empty-state-icon"><i className="ti ti-truck" /></span>
        <p><strong>Aún no completaste tu perfil de repartidor</strong></p>
        <p className="muted">Créalo para empezar a recibir propuestas de envío.</p>
        <Link className="btn btn-primary" id="btn-perfil-ir-envios" to="/repartidor">Completar perfil de repartidor</Link>
      </div>
    );
  }
  const clave = (perfil.estado_disponibilidad || 'desconectado').toLowerCase();
  const info = DISPONIBILIDAD[clave] ?? DESCONECTADO;
  const dato = (etiqueta: string, valor: string, mono = false) => <div className="perfil-dato"><span className="perfil-dato-label">{etiqueta}</span><span className={`perfil-dato-valor ${mono ? 'perfil-dato-mono' : ''}`}>{valor}</span></div>;
  return (
    <section className="card-panel perfil-tarjeta">
      <h3><i className="ti ti-truck" /> Datos de repartidor</h3>
      <div className="perfil-datos-grid">
        {dato('DNI', ocultarDni(perfil.dni), true)}
        {dato('Vehículo', perfil.tipo_vehiculo ? (VEHICULOS[perfil.tipo_vehiculo] || perfil.tipo_vehiculo) : '—')}
        {dato('Capacidad', perfil.capacidad_maxima_kg != null ? `${Number(perfil.capacidad_maxima_kg).toLocaleString('es-PE')} kg` : '—')}
        {perfil.placa && dato('Placa', perfil.placa, true)}
      </div>
      <div className={`disponibilidad-banner ${info.clase}`}>
        <span className="disponibilidad-icono"><span className="disponibilidad-punto" /></span>
        <div><strong>{info.texto}</strong><span>{info.detalle}</span></div>
      </div>
    </section>
  );
}

function Accesos({ rol }: { rol: string }) {
  const panel = ACCESO_PANEL[rol];
  const acceso = (icono: string, etiqueta: string) => (
    <>
      <span className="perfil-acceso-icono"><i className={`ti ti-${icono}`} aria-hidden="true" /></span>
      <span>{etiqueta}</span>
      <i className="ti ti-chevron-right" aria-hidden="true" />
    </>
  );
  return (
    <nav className="card-panel perfil-accesos" aria-label="Accesos rápidos">
      <h3>Accesos rápidos</h3>
      {panel && <Link className="perfil-acceso" to={rutaInicialPara(rol)}>{acceso(panel.icono, panel.etiqueta)}</Link>}
      {rol === 'comprador' && <Link className="perfil-acceso" to="/comprador/pedidos">{acceso('package', 'Mis pedidos')}</Link>}
      <Link className="perfil-acceso" to="/notificaciones">{acceso('bell', 'Notificaciones')}</Link>
      <button type="button" className="perfil-acceso" onClick={() => abrirPanelBusqueda('', { explorar: true })}>{acceso('search', 'Explorar el catálogo')}</button>
    </nav>
  );
}

// Lo que cada rol tiene además de su cuenta. Un 404 de "mi perfil" es que todavía no lo creó: no es un error.
async function cargarPerfil(usuarioId: string) {
  const usuario = await usuarios.obtener(usuarioId);
  const sinPerfil = <T,>(promesa: Promise<T>) => promesa.catch((err) => { if (err instanceof ApiError && err.status === 404) return null; throw err; });
  if (usuario.rol === 'productor') {
    const perfil = await sinPerfil(productores.miPerfil());
    if (!perfil) return { usuario, rol: 'productor' as const, perfil, registros: [], chacras: [] };
    const [registros, misChacras] = await Promise.all([
      productores.produccion.listarMe().catch((err) => { manejarError(err, 'cargar tu gestión económica'); return []; }),
      chacras.misChacras().catch((err) => { manejarError(err, 'cargar tus chacras'); return []; }),
    ]);
    return { usuario, rol: 'productor' as const, perfil, registros, chacras: misChacras };
  }
  if (usuario.rol === 'repartidor') return { usuario, rol: 'repartidor' as const, perfil: await sinPerfil(repartidores.miPerfil()) };
  if (usuario.rol === 'comprador') return { usuario, rol: 'comprador' as const, reclamos: await usuarios.misReclamosRuc().catch(() => null) };
  return { usuario, rol: 'otro' as const };
}

function CuentaSinDatosDeRol({ usuario }: { usuario: Perfil }) {
  const info = ROL_INFO[usuario.rol] || { icono: 'user', texto: usuario.rol || 'Usuario', descripcion: '' };
  return (
    <section className="card-panel perfil-tarjeta">
      <h3><i className="ti ti-id-badge-2" /> Tu cuenta</h3>
      <div className="perfil-datos-grid">
        <div className="perfil-dato"><span className="perfil-dato-label">Nombre</span><span className="perfil-dato-valor">{usuario.nombre}</span></div>
        <div className="perfil-dato"><span className="perfil-dato-label">Correo</span><span className="perfil-dato-valor">{usuario.email}</span></div>
        <div className="perfil-dato"><span className="perfil-dato-label">Tipo de cuenta</span><span className="perfil-dato-valor">{info.texto}</span></div>
      </div>
      <p className="perfil-cuenta-descripcion">{info.descripcion}</p>
    </section>
  );
}

export default function PerfilPage() {
  const { sesion, cerrarSesion } = useAuth();
  const { state } = useLocation();
  const reclamoInicial = (state as EstadoPerfil | null)?.reclamoRucPorAbrir ?? null;
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['perfil', sesion.usuarioId],
    queryFn: () => cargarPerfil(sesion.usuarioId as string),
    enabled: Boolean(sesion.usuarioId),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });

  let contenido;
  if (isLoading) {
    contenido = <SkeletonPerfil />;
  } else if (isError || !data) {
    contenido = <p className="muted">No se pudo cargar tu perfil.</p>;
  } else {
    const { usuario } = data;
    const info = ROL_INFO[usuario.rol] || { icono: 'user', texto: usuario.rol || 'Usuario', descripcion: '' };
    let principal;
    if (data.rol === 'productor') principal = <PerfilProductor perfil={data.perfil} registros={data.registros} />;
    else if (data.rol === 'repartidor') principal = <PerfilRepartidor perfil={data.perfil} />;
    else {
      principal = (
        <>
          <CuentaSinDatosDeRol usuario={usuario} />
          {data.rol === 'comprador' && <PerfilComoCompras usuario={usuario} reclamos={data.reclamos} reclamoInicial={reclamoInicial} alCambiar={() => { void refetch(); }} />}
        </>
      );
    }
    contenido = (
      <>
        <header className="perfil-hero panel-header-hero">
          <div className="perfil-avatar" aria-hidden="true">{(usuario.nombre || '?').charAt(0).toUpperCase()}</div>
          <div className="perfil-hero-info">
            <span className="perfil-hero-etiqueta">Mi perfil</span>
            <h2>{usuario.nombre}</h2>
            <div className="perfil-hero-meta">
              <span className="perfil-email"><i className="ti ti-mail" aria-hidden="true" /> {usuario.email}</span>
              <span className="perfil-rol-badge"><i className={`ti ti-${info.icono}`} /> {info.texto}</span>
            </div>
          </div>
          <button type="button" className="btn btn-outline" id="btn-perfil-logout" onClick={() => cerrarSesion()}><i className="ti ti-logout" /> Cerrar sesión</button>
        </header>
        <div className="perfil-layout">
          <div className="perfil-principal">{principal}</div>
          <aside className="perfil-lateral"><Accesos rol={usuario.rol} /></aside>
        </div>
        {data.rol === 'productor' && data.perfil && <SiembrasYChacrasDelPerfil registros={data.registros} chacras={data.chacras} />}
      </>
    );
  }

  return (
    <section id="view-perfil" className="view active" aria-label="Mi perfil">
      <div id="perfil-contenido">{contenido}</div>
    </section>
  );
}
