import { request, requestFormData } from './client';
import type { components } from './esquemas/productores';

type Esquemas = components['schemas'];

export type ProductorPublico = Esquemas['ProductorPublico'];
// De qué chacra salió un producto (público, sin sesión): su nombre, su área y si tiene contorno. Nunca dónde está.
export type OrigenPublico = Esquemas['OrigenPublico'];
// La geocerca de una siembra y su cosecha para el admin (llegada al almacén): resultados, sin coordenadas.
export type GeocercaRegistro = Esquemas['GeocercaRegistro'];

// Lista completa, con contacto y coordenadas exactas: el dueño o un admin.
export interface Productor {
  id: string;
  usuario_id: string | null;
  nombre: string;
  comunidad: string | null;
  contacto: string | null;
  ubicacion: string | null;
  fecha_registro: string | null;
  latitud: string | null;
  longitud: string | null;
}

export interface Etapa {
  clave: string;
  nombre: string;
  que_se_hace: string;
  rubros: string[];
  momento: string;
}

export interface TipoProduccion {
  nombre: string;
  inicio: string;
  fin: string;
  verbo_inicio: string;
  verbo_fin: string;
  lugar: string;
  obtenidos: string;
  desde: string;
  hasta: string;
  al_inicio: string;
  al_fin: string;
  foto: string;
}

export interface CatalogoPlantillas {
  tipos: Record<string, TipoProduccion>;
  rubros: Record<string, { nombre: string; icono: string }>;
  familias: Record<string, { tipo: string; etapas: Etapa[] }>;
  alimentos: {
    clave: string;
    nombre: string;
    familia: string;
    tipo: string;
    unidad: string;
    alias: string[];
    unidades_venta: string[] | null;
    unidades_cosecha: string[] | null;
  }[];
}

export interface Plantilla {
  clave: string;
  nombre: string;
  familia: string;
  tipo: string;
  unidad: string;
  unidades_venta: string[] | null;
  unidades_cosecha: string[] | null;
  etiquetas: TipoProduccion;
  etapas: Etapa[];
  reconocido: boolean;
}

export interface CultivoRecomendado {
  clave: string;
  nombre: string;
  puntaje: number;
  precio_kg: number | null;
  fuente_precio: string | null;
  costo_kg: number | null;
  fuente_costo: string | null;
  margen_kg: number | null;
  margen_pct: number | null;
  demanda_kg: number;
  tendencia: string | null;
  ciclo_dias: number | null;
  cosecha_estimada: string | null;
  competencia: number;
  riesgo: string;
  stock_catalogo_kg: number;
  rendimiento_kg_ha: number | null;
  frases: string[];
  completo: boolean;
}

export interface QueSembrar {
  recomendaciones: CultivoRecomendado[];
  sin_datos: string[];
  generado: string;
  ventas_disponibles: boolean;
  consejo: string | null;
  referencias: { cultivo: string; fuente: string }[];
}

export interface GastoPorEtapa {
  clave: string;
  nombre: string;
  momento: string;
  total: number;
  gastos: number;
}

// Decimal llega como número (jsonable_encoder de FastAPI).
export interface RegistroProduccion {
  id: string;
  productor_id: string;
  chacra_id: string;
  cultivo: string;
  numero_parcelas: number;
  ubicacion_cosecha: string | null;
  costo_semillas: number;
  costo_mano_obra: number;
  costo_insumos: number;
  cantidad_cosechada: number | null;
  unidad_medida: string;
  equivalencia_kg: number | null;
  fecha_siembra: string;
  fecha_cosecha_estimada: string;
  fecha_cosecha_real: string | null;
  estado: string;
  fecha_registro: string | null;
  motivo_rechazo: string | null;
  siembra_distancia_km: number | null;
  cosecha_distancia_km: number | null;
  radio_chacra_km: number;
  // Geocerca de cada momento: "poligono" (contra el contorno de la chacra) o "radio"; el resultado es dentro | cerca |
  // fuera | precision_baja | sin_gps | radio. Nulos en los registros de antes.
  siembra_metodo_geocerca: string | null;
  siembra_resultado_geocerca: string | null;
  siembra_distancia_borde_m: number | null;
  siembra_precision_m: number | null;
  siembra_contorno_hash: string | null;
  cosecha_metodo_geocerca: string | null;
  cosecha_resultado_geocerca: string | null;
  cosecha_distancia_borde_m: number | null;
  cosecha_precision_m: number | null;
  cosecha_contorno_hash: string | null;
  foto_cosecha_url: string | null;
  plantilla: string | null;
  tipo_produccion: string | null;
  alimento: string | null;
  gastos_por_etapa: GastoPorEtapa[] | null;
  costo_total: number;
  precio_referencia_kg: number | null;
  ingreso_estimado: number | null;
  ganancia_estimada: number | null;
  margen_porcentaje: number | null;
  motivo_sin_calculo: string | null;
}

export interface CosechaCompletada extends RegistroProduccion {
  producto_id: string | null;
  certificacion_registrada: boolean;
}

export interface Gasto {
  id: string;
  etapa: string;
  rubro: string;
  descripcion: string | null;
  cantidad: number | null;
  unidad: string | null;
  precio_unitario: number | null;
  monto: number;
  peones: number | null;
  dias: number | null;
  fecha: string;
}

export interface DetalleGastos {
  plantilla: string | null;
  tipo_produccion: string | null;
  alimento: string | null;
  gastos_por_etapa: GastoPorEtapa[] | null;
  etapas: Etapa[];
  gastos: Gasto[];
  total: number;
  costo_semillas: number;
  costo_insumos: number;
  costo_mano_obra: number;
}

export interface SugerenciaCosecha {
  fecha_sugerida: string;
  fecha_desde: string;
  fecha_hasta: string;
  dias_min: number;
  dias_probable: number;
  dias_max: number;
  explicacion: string;
  fuente: 'ia' | 'tabla';
  cultivo_tabla: string | null;
  senal_cosecha: string | null;
}

export const productores = {
  listar: () => request<ProductorPublico[]>('productores', '/productores'),
  // Lista completa (contacto y coordenadas exactas): solo la puede pedir un admin.
  listarAdmin: () => request<Productor[]>('productores', '/productores/admin/todos', { auth: true }),
  obtener: (id: string) => request<ProductorPublico>('productores', `/productores/${id}`),
  miPerfil: () => request<Productor>('productores', '/productores/me', { auth: true }),
  crear: (datos: Esquemas['ProductorCrear']) => request<Productor>('productores', '/productores', { method: 'POST', body: datos, auth: true }),
  // Cómo se obtiene cada alimento: tipos, rubros de gasto y etapas (público).
  plantillas: () => request<CatalogoPlantillas>('productores', '/plantillas'),
  // Ranking de cultivos para sembrar ahora (ventas, costos, ciclo y competencia).
  queSembrar: () => request<QueSembrar>('productores', '/que-sembrar', { auth: true }),
  plantillaPara: (nombre: string) => request<Plantilla>('productores', `/plantillas/para?nombre=${encodeURIComponent(nombre)}`),
  produccion: {
    // Público: la chacra de origen de la siembra de la que salió un producto, sin el contorno ni coordenadas.
    origen: (id: string) => request<OrigenPublico>('productores', `/productores/produccion/${id}/origen`),
    // Solo el admin: dónde estaba el agricultor respecto de su chacra al sembrar y al cosechar (sin coordenadas).
    geocerca: (id: string) => request<GeocercaRegistro>('productores', `/productores/produccion/${id}/geocerca`, { auth: true }),
    crear: (datos: Esquemas['RegistroProduccionCrear']) => request<RegistroProduccion>('productores', '/productores/produccion', { method: 'POST', body: datos, auth: true }),
    // Gastos por etapa de un registro (con plantilla): se anotan cuando ocurren.
    gastos: (id: string) => request<DetalleGastos>('productores', `/productores/produccion/${id}/gastos`, { auth: true }),
    anotarGasto: (id: string, datos: Esquemas['GastoDatos']) => request<DetalleGastos>('productores', `/productores/produccion/${id}/gastos`, { method: 'POST', body: datos, auth: true }),
    borrarGasto: (id: string, gastoId: string) => request<DetalleGastos>('productores', `/productores/produccion/${id}/gastos/${gastoId}`, { method: 'DELETE', auth: true }),
    sugerirCosecha: (datos: Esquemas['SugerirCosechaDatos']) => request<SugerenciaCosecha>('productores', '/productores/produccion/sugerir-cosecha', { method: 'POST', body: datos, auth: true }),
    listarMe: () => request<RegistroProduccion[]>('productores', '/productores/produccion/me', { auth: true }),
    completarCosecha: (id: string, datos: Esquemas['CompletarCosechaDatos']) => request<CosechaCompletada>('productores', `/productores/produccion/${id}/completar-cosecha`, { method: 'PATCH', body: datos, auth: true }),
    // Mientras no se cosecha: corregir un dato mal puesto o anular la siembra (no se borra, queda su rastro).
    corregir: (id: string, datos: Esquemas['RegistroProduccionEditar']) => request<RegistroProduccion>('productores', `/productores/produccion/${id}`, { method: 'PUT', body: datos, auth: true }),
    anular: (id: string) => request<{ id: string; estado: string }>('productores', `/productores/produccion/${id}/anular`, { method: 'POST', auth: true }),
    // La foto de la cosecha va antes que completarCosecha (que la exige) y aparte, para que esa siga siendo JSON.
    subirFotoCosecha: (id: string, archivo: File) => {
      const formData = new FormData();
      formData.append('archivo', archivo);
      return requestFormData<{ foto_cosecha_url: string; foto_cosecha_sha256: string }>('productores', `/productores/produccion/${id}/foto-cosecha`, formData);
    },
  },
};
