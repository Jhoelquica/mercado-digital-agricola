import { useQuery } from '@tanstack/react-query';
import { chacras } from '../../api/chacras';
import { ApiError } from '../../api/errors';
import { productores, type CatalogoPlantillas, type RegistroProduccion } from '../../api/productores';
import { productos } from '../../api/productos';
import { transporte } from '../../api/transporte';
import { useAuth } from '../../auth/AuthContext';

// ---- Consultas del panel del agricultor (compartidas por sus pestañas) ----
export function usePerfilProductor() {
  const { sesion } = useAuth();
  return useQuery({
    queryKey: ['productor', 'perfil', sesion.usuarioId],
    // 404 = todavía no tiene perfil: no es un error.
    queryFn: () => productores.miPerfil().catch((err) => { if (err instanceof ApiError && err.status === 404) return null; throw err; }),
    staleTime: 60_000,
  });
}

export const useRegistros = (activo = true) => useQuery({ queryKey: ['productor', 'registros'], queryFn: productores.produccion.listarMe, enabled: activo });
export const useChacras = (activo = true) => useQuery({ queryKey: ['productor', 'chacras'], queryFn: chacras.misChacras, enabled: activo });
export const useMisProductos = (activo = true) => useQuery({ queryKey: ['productor', 'mis-productos'], queryFn: productos.mios, enabled: activo });
// Sección discreta y poco frecuente: si falla, el panel sigue igual (sin entregas).
export const useEntregasDirectas = (activo = true) => useQuery({ queryKey: ['productor', 'entregas-directas'], queryFn: () => transporte.pendientesEntregaDirecta().catch(() => []), enabled: activo });
// El catálogo de etapas y rubros (público): sin él, solo no se muestran los nombres de las etapas.
export const usePlantillas = () => useQuery({ queryKey: ['plantillas'], queryFn: () => productores.plantillas().catch(() => null), staleTime: Infinity });

// ---- Fechas (las de siembra y cosecha son días de calendario: sin zona horaria) ----
export function formatearFechaCorta(fechaStr: string | null | undefined): string {
  if (!fechaStr) return '—';
  const sinHora = /^(\d{4})-(\d{2})-(\d{2})$/.exec(fechaStr);
  const d = sinHora ? new Date(Number(sinHora[1]), Number(sinHora[2]) - 1, Number(sinHora[3])) : new Date(fechaStr);
  if (isNaN(d.getTime())) return fechaStr;
  return d.toLocaleDateString('es-PE', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function fechaIso(fecha: Date): string {
  const local = new Date(fecha.getTime() - fecha.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

export function sumarDias(fecha: Date, dias: number): Date {
  const nueva = new Date(fecha);
  nueva.setDate(nueva.getDate() + dias);
  return nueva;
}

export function fechaLegible(iso: string | null | undefined): string {
  if (!iso) return '—';
  const [a = 0, m = 1, d = 1] = iso.split('-').map(Number);
  return new Date(a, m - 1, d).toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

// ---- Cómo se habla de cada tipo de producción (los huevos no se "siembran" ni se "cosechan") ----
export interface Palabras {
  nombre: string; inicio: string; fin: string; cuandoInicio: string; cuandoFin: string; resumenInicio: string; resumenFin: string;
  cantidad: string | null; preguntaCantidad: string; unidadCantidad: string; completar: string; tituloFin: string; accionFin: string;
  foto: string; cuanto: string; cuando: string; resumenCuanto: string; resumenCuando: string; confirmar: string; listo: string; medir: string;
}

const PALABRAS_TIPO: Record<string, Palabras> = {
  temporada: {
    nombre: 'Cultivo', inicio: 'Siembra', fin: 'Cosecha estimada', cuandoInicio: '¿Cuándo sembraste?', cuandoFin: '¿Cuándo crees que vas a cosechar?',
    resumenInicio: 'Sembraste', resumenFin: 'Cosecharías', cantidad: 'Parcelas', preguntaCantidad: '¿En cuántas parcelas sembraste?', unidadCantidad: 'parc.',
    completar: 'Completar cosecha', tituloFin: 'Completar cosecha', accionFin: 'completar la cosecha', foto: 'Toma una foto de tu cosecha',
    cuanto: '¿Cuánto cosechaste?', cuando: '¿Cuándo cosechaste?', resumenCuanto: 'Cosechaste', resumenCuando: 'Cosechaste el',
    confirmar: 'Confirmar cosecha', listo: 'Cosechado', medir: '¿Cómo vas a medir tu cosecha?',
  },
  frutal: {
    nombre: 'Frutal', inicio: 'Inicio de campaña', fin: 'Cosecha estimada', cuandoInicio: '¿Cuándo empezó esta campaña?', cuandoFin: '¿Cuándo crees que vas a cosechar?',
    resumenInicio: 'Empezó', resumenFin: 'Cosecharías', cantidad: 'Parcelas', preguntaCantidad: '¿En cuántas parcelas están tus plantas?', unidadCantidad: 'parc.',
    completar: 'Completar cosecha', tituloFin: 'Completar cosecha', accionFin: 'completar la cosecha', foto: 'Toma una foto de tu cosecha',
    cuanto: '¿Cuánto cosechaste?', cuando: '¿Cuándo cosechaste?', resumenCuanto: 'Cosechaste', resumenCuando: 'Cosechaste el',
    confirmar: 'Confirmar cosecha', listo: 'Cosechado', medir: '¿Cómo vas a medir tu cosecha?',
  },
  crianza: {
    nombre: 'Crianza', inicio: 'Inicio del periodo', fin: 'Fin del periodo', cuandoInicio: '¿Cuándo empezó este periodo?', cuandoFin: '¿Hasta cuándo es este periodo?',
    resumenInicio: 'Empezó', resumenFin: 'Termina', cantidad: 'Animales', preguntaCantidad: '¿Cuántos animales tiene tu lote?', unidadCantidad: 'anim.',
    completar: 'Registrar lo recogido', tituloFin: 'Registrar lo recogido', accionFin: 'registrar lo recogido', foto: 'Toma una foto de lo que recogiste',
    cuanto: '¿Cuánto recogiste en este periodo?', cuando: '¿Cuándo terminaste de recoger?', resumenCuanto: 'Recogiste', resumenCuando: 'Terminaste el',
    confirmar: 'Confirmar lo recogido', listo: 'Recogido', medir: '¿Cómo vas a medir lo que recojas?',
  },
  elaboracion: {
    nombre: 'Elaboración', inicio: 'Inicio', fin: 'Listo para', cuandoInicio: '¿Cuándo empezaste a elaborar?', cuandoFin: '¿Cuándo estará listo?',
    resumenInicio: 'Empezaste', resumenFin: 'Listo', cantidad: null, preguntaCantidad: '', unidadCantidad: '',
    completar: 'Registrar lo elaborado', tituloFin: 'Registrar lo elaborado', accionFin: 'registrar lo elaborado', foto: 'Toma una foto de lo que elaboraste',
    cuanto: '¿Cuánto elaboraste?', cuando: '¿Cuándo terminaste?', resumenCuanto: 'Elaboraste', resumenCuando: 'Terminaste el',
    confirmar: 'Confirmar lo elaborado', listo: 'Listo', medir: '¿Cómo vas a medir lo que elabores?',
  },
};

export const palabrasDe = (tipo: string | null | undefined): Palabras => PALABRAS_TIPO[tipo || ''] ?? (PALABRAS_TIPO.temporada as Palabras);

// ---- Estados de un registro (productores/models.py): planificado -> cosechado. "aprobado" equivale a cosechado y
// "pendiente_verificacion"/"rechazado" quedaron a medio camino de cuando existía el Verificador: se completan con su foto.
export const ESTADOS_POR_COMPLETAR = ['planificado', 'pendiente_verificacion', 'rechazado'];
const BADGE_PLANIFICADO = { clase: 'pill-pendiente', icono: 'seedling', etiqueta: 'Planificado' };
export const BADGES_ESTADO_REGISTRO: Record<string, { clase: string; icono: string; etiqueta: string }> = {
  planificado: BADGE_PLANIFICADO,
  cosechado: { clase: 'pill-aprobado', icono: 'circle-check', etiqueta: 'Cosechado' },
  aprobado: { clase: 'pill-aprobado', icono: 'circle-check', etiqueta: 'Cosechado' },
  pendiente_verificacion: { clase: 'pill-pendiente', icono: 'camera', etiqueta: 'Falta la foto' },
  rechazado: { clase: 'pill-pendiente', icono: 'camera', etiqueta: 'Falta la foto' },
};
export const badgeDeEstado = (estado: string) => BADGES_ESTADO_REGISTRO[estado] ?? BADGE_PLANIFICADO;

// ---- Evidencia en origen: igual que RADIO_CHACRA_KM en productores/main.py ----
export const RADIO_CHACRA_KM = 1.5;

export function distanciaKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = (g: number) => (g * Math.PI) / 180;
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function formatearDistancia(km: number): string {
  return km < 1 ? `${Math.max(10, Math.round((km * 1000) / 10) * 10)} m` : `${km.toFixed(1).replace('.', ',')} km`;
}

// ---- Etapas y rubros del catálogo (ver productores/plantillas_produccion.py) ----
const FAMILIA_GENERICA: Record<string, string> = { temporada: 'temporada_generica', frutal: 'frutales', crianza: 'crianza_generica', elaboracion: 'elaboracion_generica' };

export function etapasDePlantilla(catalogo: CatalogoPlantillas | null | undefined, clave: string | null | undefined) {
  if (!catalogo || !clave) return [];
  const familia = clave.startsWith('generica_') ? FAMILIA_GENERICA[clave.slice('generica_'.length)]
    : catalogo.alimentos.find((a) => a.clave === clave)?.familia;
  return (familia && catalogo.familias[familia]?.etapas) || [];
}

export const nombreRubro = (catalogo: CatalogoPlantillas | null | undefined, rubro: string) => catalogo?.rubros?.[rubro]?.nombre || rubro;

// El alimento que se reconoce en un nombre: mismo criterio que plantillas_produccion.clave_para (palabra completa o su
// plural), con los alias del catálogo; así el asistente sabe el tipo sin esperar al servidor.
export function alimentoPorNombre(catalogo: CatalogoPlantillas | null | undefined, nombre: string) {
  if (!catalogo) return null;
  const palabras = (nombre || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().split(/\s+/).map((p) => p.replace(/[.,;:()]/g, ''));
  return catalogo.alimentos.find((a) => (a.alias || []).some((al) => palabras.includes(al) || palabras.includes(`${al}s`) || palabras.includes(`${al}es`))) || null;
}

export function textoPeonesDias(peones: number | string, dias: number | string): string {
  const d = Number(dias);
  return `${peones} ${Number(peones) === 1 ? 'peón' : 'peones'} × ${String(d).replace('.', ',')} ${d === 1 ? 'día' : 'días'}`;
}

// Cálculo de "Gestión económica": lo usan Mi perfil y la pestaña del panel del productor (mismos números).
export function calcularResumenEconomico(registros: RegistroProduccion[]) {
  const totalInvertido = registros.reduce((suma, r) => suma + (Number(r.costo_total) || 0), 0);
  // "cosechado" es donde termina hoy una cosecha; "aprobado" es su equivalente de antes de retirar al Verificador.
  const cosechados = registros.filter((r) => r.estado === 'aprobado' || r.estado === 'cosechado');
  const totalCosechadoKg = cosechados.reduce((suma, r) => {
    if (r.cantidad_cosechada == null) return suma;
    const enKg = r.unidad_medida === 'kg' ? Number(r.cantidad_cosechada) : Number(r.cantidad_cosechada) * Number(r.equivalencia_kg || 0);
    return suma + enKg;
  }, 0);
  const conGanancia = cosechados.filter((r) => r.ganancia_estimada != null);
  const gananciaAcumulada = conGanancia.reduce((suma, r) => suma + Number(r.ganancia_estimada), 0);
  return { totalInvertido, cosechados, totalCosechadoKg, conGanancia, gananciaAcumulada, sinCalcular: cosechados.length - conGanancia.length };
}
