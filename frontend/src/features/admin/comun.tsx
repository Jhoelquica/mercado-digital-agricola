// Lo que comparten las pantallas del panel admin: sus datos en la caché de TanStack Query bajo ['admin', …] (los contadores
// de las pestañas y el resumen de Inicio leen las mismas consultas que cada pantalla), el visor de documentos privados y
// los pasos de una revisión (aprobar con confirmación o rechazar con motivo).
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ganancias } from '../../api/ganancias';
import { pagos, type Pago } from '../../api/pagos';
import { pedidos, type Pedido } from '../../api/pedidos';
import { productores } from '../../api/productores';
import { productos } from '../../api/productos';
import { recojos } from '../../api/recojos';
import { transporte, type EnvioDetalle } from '../../api/transporte';
import { usuarios } from '../../api/usuarios';
import { manejarError } from '../../components/Toast';
import { ETIQUETAS_ESTADO_ENVIO } from '../../lib/envios';

// Una carga que falla avisa con el mismo texto que el frontend anterior y deja la pantalla vacía.
const conAviso = <T,>(cargar: () => Promise<T>, accion: string) => () => cargar().catch((err: unknown) => { manejarError(err, accion); throw err; });

// ---- datos
export const useTransferencias = () => useQuery({ queryKey: ['admin', 'transferencias'], queryFn: conAviso(pagos.admin.transferencias, 'cargar las transferencias') });
export const useReclamosRuc = () => useQuery({ queryKey: ['admin', 'reclamos-ruc'], queryFn: conAviso(() => usuarios.admin.reclamosRuc(), 'cargar los reclamos de RUC') });
export const useTransportistas = (estado: string) => useQuery({
  queryKey: ['admin', 'transportistas', estado],
  queryFn: conAviso(() => transporte.admin.transportistas(estado), 'cargar los transportistas'),
});

// Viajes en curso y los transportistas verificados (a quién se le puede asignar cada uno).
export const useViajesAdmin = () => useQuery({
  queryKey: ['admin', 'viajes'],
  queryFn: conAviso(async () => {
    const [viajes, verificados] = await Promise.all([transporte.admin.viajes(), transporte.admin.transportistas('verificado')]);
    return { viajes, verificados };
  }, 'cargar los viajes en camión'),
});

export const useDestinosAdmin = () => useQuery({
  queryKey: ['admin', 'destinos'],
  queryFn: conAviso(async () => {
    const [destinos, agencia] = await Promise.all([pedidos.admin.destinosLejanos(), pedidos.agenciaDespacho().catch(() => null)]);
    return { destinos, agencia };
  }, 'cargar los destinos lejanos'),
});

// Productores (con contacto y coordenadas exactas) y cuántos productos publicados tiene cada uno: más productos primero; a
// igualdad, alfabético. No hay "productos por productor" para el admin: se agrupa el catálogo público.
export const useProductoresAdmin = () => useQuery({
  queryKey: ['admin', 'productores'],
  queryFn: conAviso(async () => {
    const [lista, catalogo] = await Promise.all([productores.listarAdmin(), productos.listar().catch(() => [])]);
    const totalPorProductor: Record<string, number> = {};
    catalogo.forEach((pr) => { totalPorProductor[pr.productor_id] = (totalPorProductor[pr.productor_id] || 0) + 1; });
    return lista
      .map((p) => ({ p, total: totalPorProductor[p.id] || 0 }))
      .sort((a, b) => b.total - a.total || String(a.p.nombre).localeCompare(String(b.p.nombre), 'es'));
  }, 'cargar los productores'),
});

// Productos en tránsito (el catálogo público trae en_transito y disponible juntos), cuántas reservas tiene cada uno y el
// recojo con que llega al almacén.
export const useProductosEnTransito = () => useQuery({
  queryKey: ['admin', 'transito'],
  queryFn: conAviso(async () => {
    const enTransito = (await productos.listar()).filter((p) => p.estado === 'en_transito');
    if (!enTransito.length) return [];
    const [reservas, enCurso] = await Promise.all([
      Promise.all(enTransito.map((p) => productos.reservas(p.id).catch(() => []))),
      recojos.enCurso().catch(() => []),
    ]);
    return enTransito.map((p, i) => ({ p, reservas: reservas[i]?.length ?? 0, recojo: enCurso.find((r) => String(r.producto_id) === String(p.id)) ?? null }));
  }, 'cargar los productos en tránsito'),
});

// Sección discreta y poco frecuente: si falla, queda vacía sin avisar (como en el frontend anterior).
export const usePendientesPorCapacidad = () => useQuery({ queryKey: ['admin', 'capacidad'], queryFn: () => transporte.pendientesPorCapacidad().catch(() => []) });

// Lo que se le paga a cada uno este lunes y los pagos ya hechos. Quien todavía no registró su cuenta de cobro no trae
// nombre: el del agricultor es público; el de los repartidores se pide de una vez a Transporte.
export const usePagosPorHacer = () => useQuery({
  queryKey: ['admin', 'pagos'],
  queryFn: conAviso(async () => {
    const [porPagar, hechos] = await Promise.all([ganancias.admin.porPagar(), ganancias.admin.pagosHechos()]);
    const sinNombre = porPagar.beneficiarios.filter((b) => !b.nombre);
    const idsRepartidores = sinNombre.filter((b) => b.beneficiario_tipo === 'repartidor').map((b) => b.beneficiario_id);
    const nombresRepartidores = idsRepartidores.length ? await ganancias.admin.nombresRepartidores(idsRepartidores).catch(() => ({} as Record<string, string>)) : {};
    const beneficiarios = await Promise.all(porPagar.beneficiarios.map(async (b) => {
      if (b.nombre) return b;
      if (b.beneficiario_tipo === 'repartidor') return { ...b, nombre: nombresRepartidores[b.beneficiario_id] || null };
      try { return { ...b, nombre: (await productores.obtener(b.beneficiario_id)).nombre }; } catch { return b; }
    }));
    return { porPagar: { ...porPagar, beneficiarios }, hechos };
  }, 'cargar los pagos por hacer'),
});

// ---- pedidos pagados sin entregar
export const ENVIOS_QUE_SE_PUEDEN_CANCELAR = ['pendiente', 'pendiente_asignacion', 'propuesto', 'asignado', 'esperando_camion', 'pendiente_entrega_directa'];

export function textoEnvioSinEntregar(envio: EnvioDetalle | null): { texto: string; alerta?: boolean } {
  if (!envio) return { texto: 'Todavía sin envío', alerta: true };
  if (envio.estado === 'pendiente_asignacion' && envio.motivo_pendiente === 'sin_capacidad_suficiente') {
    return { texto: 'Ningún repartidor tiene un vehículo con capacidad para llevarlo', alerta: true };
  }
  if (envio.estado === 'pendiente_asignacion') return { texto: 'Sin repartidor: nadie lo tomó todavía', alerta: true };
  if (envio.estado === 'propuesto') return { texto: `Ofrecido a ${envio.transportista || 'un repartidor'}: falta que acepte` };
  if (envio.estado === 'asignado') return { texto: `${envio.transportista || 'Un repartidor'} lo va a llevar` };
  return { texto: ETIQUETAS_ESTADO_ENVIO[envio.estado || ''] || envio.estado || '' };
}

export interface PedidoSinEntregar { pedido: Pedido; envio: EnvioDetalle | null; pago: Pago | null }

// El envío y el pago de cada uno (sin envío o sin pago la tarjeta igual se muestra). Primero los que esperan sin repartidor
// (los que el admin tiene que resolver), después por antigüedad.
export const usePedidosSinEntregar = () => useQuery({
  queryKey: ['admin', 'sin-entregar'],
  queryFn: conAviso(async () => {
    const lista = await pedidos.admin.sinEntregar();
    const datos: PedidoSinEntregar[] = await Promise.all(lista.map(async (pedido) => {
      const [envio, pago] = await Promise.all([transporte.obtenerEnvio(pedido.id).catch(() => null), pagos.obtener(pedido.id).catch(() => null)]);
      return { pedido, envio, pago };
    }));
    const urgente = (d: PedidoSinEntregar) => (textoEnvioSinEntregar(d.envio).alerta ? 0 : 1);
    return datos.sort((a, b) => urgente(a) - urgente(b));
  }, 'cargar los pedidos sin entregar'),
});

export function useRefrescarAdmin() {
  const queryClient = useQueryClient();
  return (...claves: string[]) => Promise.all(claves.map((c) => queryClient.invalidateQueries({ queryKey: ['admin', c] })));
}

// ---- piezas de las pantallas
export function EncabezadoSeccion({ icono, titulo, alRefrescar, children }: { icono: string; titulo: ReactNode; alRefrescar?: () => void; children?: ReactNode }) {
  return (
    <div className="section-header inner">
      <h3><i className={`ti ti-${icono}`} /> {titulo}</h3>
      {alRefrescar && <button type="button" className="btn btn-outline" onClick={alRefrescar}>↻ Refrescar</button>}
      {children}
    </div>
  );
}

export function Vacio({ icono, titulo, detalle }: { icono: string; titulo: ReactNode; detalle?: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon"><i className={`ti ti-${icono}`} /></span>
      <p><strong>{titulo}</strong></p>
      {detalle && <p className="muted">{detalle}</p>}
    </div>
  );
}

export function MensajeError({ texto }: { texto: string | null }) {
  return texto
    ? <p className="form-entrega-mensaje error" role="alert"><i className="ti ti-alert-circle" /><span>{texto}</span></p>
    : <p className="form-entrega-mensaje hidden" role="alert" />;
}

// Un documento privado (constancia, Ficha RUC, DNI, licencia…): se pide con el token y se muestra como imagen o PDF. Si el
// servidor lo niega (403) o no está, se ve su detalle en lugar del documento.
type EstadoDocumento = { estado: 'cargando' } | { estado: 'listo'; url: string; esPdf: boolean } | { estado: 'error'; mensaje: string };

export function VisorDocumento({ cargar, titulo, descripcion, soloImagen = false }: { cargar: () => Promise<Blob>; titulo: string; descripcion: string; soloImagen?: boolean }) {
  const [doc, setDoc] = useState<EstadoDocumento>({ estado: 'cargando' });
  const cargarRef = useRef(cargar);
  useEffect(() => {
    let vigente = true;
    let url: string | null = null;
    cargarRef.current().then((blob) => {
      if (!vigente) return;
      url = URL.createObjectURL(blob);
      setDoc({ estado: 'listo', url, esPdf: !soloImagen && blob.type === 'application/pdf' });
    }, (err: unknown) => {
      if (vigente) setDoc({ estado: 'error', mensaje: (err as Error)?.message || `No se pudo cargar ${soloImagen ? 'la constancia' : 'el documento'}.` });
    });
    return () => {
      vigente = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [soloImagen]);
  if (doc.estado === 'cargando') return <p className="muted"><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Cargando {titulo}…</p>;
  if (doc.estado === 'error') return <p className="form-entrega-mensaje error"><i className="ti ti-alert-circle" /><span>{doc.mensaje}</span></p>;
  return doc.esPdf
    ? <><iframe src={doc.url} title={descripcion} className="reclamo-pdf" /><a href={doc.url} target="_blank" rel="noopener" className="btn btn-ghost btn-sm"><i className="ti ti-external-link" /> Abrir el PDF en otra pestaña</a></>
    : <a href={doc.url} target="_blank" rel="noopener"><img src={doc.url} alt={descripcion} /></a>;
}

// Botones de documentos (uno abierto a la vez; tocar el abierto lo cierra) y su visor debajo.
export function DocumentosPrivados({ documentos, cargar, quien }: { documentos: { cual: string; icono: string; titulo: string; boton?: string }[]; cargar: (cual: string) => Promise<Blob>; quien: string }) {
  const [abierto, setAbierto] = useState<string | null>(null);
  const actual = documentos.find((d) => d.cual === abierto);
  return (
    <>
      <div className="reclamo-documentos">
        {documentos.map((d) => (
          <button key={d.cual} type="button" className="btn btn-outline btn-ver-documento" data-cual={d.cual} aria-expanded={abierto === d.cual} onClick={() => setAbierto(abierto === d.cual ? null : d.cual)}>
            <i className={`ti ti-${d.icono}`} aria-hidden="true" /> {d.boton ?? d.titulo}
          </button>
        ))}
      </div>
      <div className={`transferencia-constancia reclamo-documento ${actual ? '' : 'hidden'}`} data-cual={actual?.cual}>
        {actual && <VisorDocumento key={actual.cual} cargar={() => cargar(actual.cual)} titulo={actual.titulo} descripcion={`${actual.titulo} que subió ${quien}`} />}
      </div>
    </>
  );
}

// Los pasos de una revisión (transferencia, reclamo de RUC, documentos del transportista): las dos acciones; aprobar pide
// confirmar; rechazar pide un motivo que lee la otra persona.
interface PropsRevision {
  textoRechazar: ReactNode;
  textoAprobar: ReactNode;
  claseRechazar: string;
  claseAprobar: string;
  mostrarRechazar?: boolean;
  mostrarAprobar?: boolean;
  etiquetaConfirmar: string;
  pregunta: ReactNode;
  confirmar: string;
  etiquetaMotivo: string;
  placeholderMotivo: string;
  enviarRechazo: string;
  validarMotivo: (motivo: string) => string | null;
  alAprobar: () => Promise<void>;
  alRechazar: (motivo: string) => Promise<void>;
}

export function PasosDeRevision(p: PropsRevision) {
  const [paso, setPaso] = useState<'acciones' | 'aprobar' | 'rechazar'>('acciones');
  const [motivo, setMotivo] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [trabajando, setTrabajando] = useState(false);
  const confirmar = useRef<HTMLButtonElement>(null);
  const campoMotivo = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (paso === 'aprobar') confirmar.current?.focus();
    if (paso === 'rechazar') campoMotivo.current?.focus();
  }, [paso]);
  async function hacer(accion: () => Promise<void>) {
    setTrabajando(true);
    try { await accion(); } finally { setTrabajando(false); }
  }
  function rechazar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const texto = motivo.trim();
    const problema = p.validarMotivo(texto);
    setError(problema);
    if (problema) { campoMotivo.current?.focus(); return; }
    void hacer(() => p.alRechazar(texto));
  }
  const cancelar = <button type="button" className="btn btn-ghost btn-cancelar-revision" disabled={trabajando} onClick={() => { setPaso('acciones'); setError(null); }}>Cancelar</button>;
  return (
    <>
      <div className={`tarjeta-acciones transferencia-acciones ${paso === 'acciones' ? '' : 'hidden'}`}>
        {p.mostrarRechazar !== false && <button type="button" className={`btn btn-outline ${p.claseRechazar}`} disabled={trabajando} onClick={() => setPaso('rechazar')}>{p.textoRechazar}</button>}
        {p.mostrarAprobar !== false && <button type="button" className={`btn btn-primary ${p.claseAprobar}`} disabled={trabajando} onClick={() => setPaso('aprobar')}>{p.textoAprobar}</button>}
      </div>
      {paso === 'aprobar' && (
        <div className="transferencia-confirmar" role="group" aria-label={p.etiquetaConfirmar}>
          <p><i className="ti ti-alert-circle" aria-hidden="true" /> {p.pregunta}</p>
          <div className="tarjeta-acciones">
            {cancelar}
            <button ref={confirmar} type="button" className="btn btn-primary btn-confirmar-aprobacion" disabled={trabajando} onClick={() => { void hacer(p.alAprobar); }}>{p.confirmar}</button>
          </div>
        </div>
      )}
      {paso === 'rechazar' && (
        <form className="form-rechazar-transferencia" noValidate onSubmit={rechazar}>
          <label>{p.etiquetaMotivo}
            <textarea ref={campoMotivo} name="motivo" rows={3} maxLength={300} required placeholder={p.placeholderMotivo} value={motivo} onChange={(e) => setMotivo(e.target.value)} />
          </label>
          <MensajeError texto={error} />
          <div className="tarjeta-acciones">
            {cancelar}
            <button type="submit" className="btn btn-danger" disabled={trabajando}>{p.enviarRechazo}</button>
          </div>
        </form>
      )}
    </>
  );
}

// "1 500 kg · 30 sacos" (un saco son 50 kg).
export function kgYSacos(kg: number | string | null | undefined): string {
  const n = Number(kg) || 0;
  return `${n.toLocaleString('es-PE', { maximumFractionDigits: 0 })} kg · ${Math.floor(n / 50)} sacos`;
}
