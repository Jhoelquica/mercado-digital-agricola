// Lo que comparten las pantallas del repartidor: sus datos (perfil, propuestas, envíos, recojos, viajes) en la caché de
// TanStack Query bajo ['repartidor', …], los estados de un envío y las piezas de tarjeta que se repiten.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../api/errors';
import { pagos } from '../../api/pagos';
import { pedidos } from '../../api/pedidos';
import { recojos } from '../../api/recojos';
import { repartidores } from '../../api/repartidores';
import { transporte, type Envio } from '../../api/transporte';
import { manejarError } from '../../components/Toast';
import { formatearMoneda, parsearFechaServidor } from '../../lib/formato';

// Envíos que siguen a cargo del repartidor. "en_destino": el camión ya está en la ciudad del comprador, esperando el saldo
// (30%) y el código para descargar.
export const ESTADOS_ENVIO_EN_CURSO = ['asignado', 'en_camino', 'en_destino'];
// Su parte ya terminó: "en_agencia" es un pedido que dejó en la agencia de carga (el comprador lo recoge en su ciudad).
export const ESTADOS_ENVIO_CUMPLIDOS = ['entregado', 'en_agencia'];

// ---- datos
export function useMiRepartidor() {
  return useQuery({
    queryKey: ['repartidor', 'perfil'],
    // Sin perfil todavía (404): se le pide crearlo.
    queryFn: () => repartidores.miPerfil().catch((err) => {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }),
  });
}

export const usePropuestas = (activo: boolean) => useQuery({ queryKey: ['repartidor', 'propuestas'], queryFn: () => transporte.propuestas().catch(() => []), enabled: activo });

// Los envíos a su nombre (sin los que todavía son propuestas: esos van aparte).
export function useMisEnvios(repartidorId: string | undefined) {
  const propuestas = usePropuestas(Boolean(repartidorId));
  const envios = useQuery({
    queryKey: ['repartidor', 'envios', repartidorId],
    queryFn: () => transporte.listarTodos().catch((err) => { manejarError(err, 'cargar tus envíos'); throw err; }),
    enabled: Boolean(repartidorId),
    select: (todos: Envio[]) => todos.filter((e) => String(e.repartidor_id) === String(repartidorId)),
  });
  const idsPropuestas = new Set((propuestas.data ?? []).map((p) => p.id));
  return {
    propuestas,
    envios,
    mios: (envios.data ?? []).filter((e) => !idsPropuestas.has(e.id)),
    cargando: propuestas.isLoading || envios.isLoading,
  };
}

// Recojos de cosechas: los que le ofrecieron y los que tiene a cargo (sección discreta: si falla, queda vacía).
export const useRecojosRepartidor = (activo: boolean) => useQuery({
  queryKey: ['repartidor', 'recojos'],
  queryFn: () => Promise.all([recojos.propuestas(), recojos.asignados()]).then(([p, a]) => [...p, ...a]).catch(() => []),
  enabled: activo,
});

export const useViajes = (activo: boolean) => useQuery({ queryKey: ['repartidor', 'viajes'], queryFn: () => transporte.viajesMios().catch(() => []), enabled: activo });
export const useCondicionesPago = () => useQuery({ queryKey: ['condiciones-pago'], queryFn: pagos.condiciones, staleTime: Infinity, retry: false });
export const useAlmacen = (activo: boolean) => useQuery({ queryKey: ['configuracion', 'almacen'], queryFn: () => pedidos.almacen().catch(() => null), staleTime: Infinity, enabled: activo });
export const useAgenciaDespacho = (activo: boolean) => useQuery({ queryKey: ['configuracion', 'agencia-despacho'], queryFn: () => pedidos.agenciaDespacho().catch(() => null), staleTime: Infinity, enabled: activo });

// Tras aceptar, rechazar, cambiar un estado o entregar se repinta TODO el panel (banner, contadores, gráfico, listas).
export function useRefrescarPanel() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ['repartidor'] });
}

// ---- reglas de presentación
// Lo que gana el repartidor: el envío que paga el comprador menos la comisión de la plataforma.
export function repartoDelEnvio(costoEnvio: number | string, porcentaje: number) {
  const bruto = Number(costoEnvio) || 0;
  const comision = Math.round((bruto * porcentaje / 100 + Number.EPSILON) * 100) / 100;
  return { bruto, comision, neto: Math.round((bruto - comision) * 100) / 100 };
}

export function enlaceMapaOsm(latitud: number | string, longitud: number | string): string {
  const lat = encodeURIComponent(latitud);
  const lng = encodeURIComponent(longitud);
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}`;
}

const ICONOS_ESTADO: Record<string, string> = {
  pendiente: 'hourglass', pendiente_asignacion: 'hourglass', propuesto: 'mail', confirmado: 'receipt', asignado: 'clipboard-list',
  en_camino: 'truck', en_transito: 'truck', en_ruta: 'truck', entregado: 'circle-check', cancelado: 'circle-x', rechazado: 'circle-x',
  aprobado: 'circle-check', en_agencia: 'building-warehouse', en_destino: 'map-pin', esperando_camion: 'hourglass', recogido: 'package',
  en_almacen: 'building-warehouse', completado: 'circle-check', error: 'alert-triangle',
};

// El estado llega como identificador ("en_camino"): se muestra con espacios, no con guiones bajos.
export function BadgeEstadoEnvio({ estado }: { estado: string | null | undefined }) {
  const clave = (estado || 'pendiente').toLowerCase().replace(/\s+/g, '_');
  const icono = ICONOS_ESTADO[clave];
  return (
    <span className={`pill pill-estado ${icono ? `pill-${clave}` : 'pill-default'}`}>
      {icono ? <i className={`ti ti-${icono}`} /> : '•'} {String(estado || 'pendiente').replace(/_/g, ' ')}
    </span>
  );
}

// Hasta cuándo puede responder una propuesta: después se le ofrece a otro (transporte/logica_repartidores).
export function PlazoPropuesta({ hasta }: { hasta: string | null | undefined }) {
  const fecha = hasta ? parsearFechaServidor(hasta) : null;
  if (!fecha || isNaN(fecha.getTime())) return null;
  const hora = fecha.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' });
  return <p className="propuesta-plazo"><i className="ti ti-clock" aria-hidden="true" /> Responde antes de las {hora}: si no, se le ofrece a otro repartidor.</p>;
}

export function Metrica({ icono, valor, etiqueta }: { icono: string; valor: string; etiqueta: string }) {
  return (
    <div className="propuesta-metrica">
      <i className={`ti ti-${icono}`} aria-hidden="true" />
      <strong>{valor}</strong>
      <span>{etiqueta}</span>
    </div>
  );
}

export const numeroCorto = (valor: number | string | null | undefined) => Number(valor || 0).toLocaleString('es-PE', { maximumFractionDigits: 1 });
export const montoQueGana = (costo: number | string | null | undefined, porcentaje: number | null | undefined) =>
  (costo != null && porcentaje != null ? repartoDelEnvio(costo, porcentaje) : null);
export { formatearMoneda };
