// Reglas de negocio puras: las mismas de frontend/js/reglas.js que usan estas pantallas.

// H7: lo que el comprador dice que le interesa al reservar. Sin cantidad, la reserva es solo el aviso.
export function cantidadDeReserva(texto: string | undefined, unidad: string | undefined) {
  const limpio = String(texto ?? '').trim().replace(',', '.');
  if (!limpio) return { datos: null, error: null };
  const cantidad = Number(limpio);
  if (!Number.isFinite(cantidad) || cantidad <= 0) return { datos: null, error: 'Escribe cuánto te interesa con un número mayor que 0, o déjalo vacío.' };
  return { datos: { cantidad, unidad: unidad || null }, error: null };
}

// H6: la clave pública VAPID (base64url, como la da el servidor) en bytes, como la pide pushManager.subscribe.
export function claveVapidABytes(base64url: string | null | undefined): Uint8Array<ArrayBuffer> {
  const texto = String(base64url || '');
  const binario = atob((texto + '='.repeat((4 - (texto.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(binario, (c) => c.charCodeAt(0));
}

// H6: ¿la suscripción del navegador es con la clave que tiene hoy el servidor? Si se cambiaron las claves, hay que volver
// a suscribirse; si no, el navegador rechaza la suscripción nueva.
export function mismaClaveVapid(bytesDelNavegador: ArrayBuffer | null | undefined, base64url: string | null | undefined): boolean {
  if (!bytesDelNavegador) return false;
  const a = new Uint8Array(bytesDelNavegador);
  const b = claveVapidABytes(base64url);
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

// Mismo criterio que Usuarios (ruc_valido): 11 dígitos, empieza con 10, 15, 17 o 20 y el último es el dígito
// verificador de SUNAT. Aquí solo para avisar antes de enviar; el servidor lo vuelve a revisar.
export function rucValido(ruc: string): boolean {
  if (!/^(10|15|17|20)\d{9}$/.test(ruc)) return false;
  const pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
  const resto = 11 - (pesos.reduce((acc, p, i) => acc + p * Number(ruc[i]), 0) % 11);
  const esperado = resto === 10 ? 0 : resto === 11 ? 1 : resto;
  return Number(ruc[10]) === esperado;
}

export type EstadoAvisosCelular = 'apagado-servidor' | 'instalar-iphone' | 'no-soportado' | 'bloqueado' | 'activo' | 'inactivo';

// H6: qué dice la fila "En este celular" de la tarjeta de avisos. En iPhone los avisos solo existen con ChakraShop
// agregado a la pantalla de inicio, por eso se mira antes que el soporte del navegador.
export function estadoAvisosCelular({ configurado, soportado, iphoneSinInstalar, permiso, suscrito }: {
  configurado: boolean; soportado: boolean; iphoneSinInstalar: boolean; permiso: NotificationPermission; suscrito: boolean;
}): EstadoAvisosCelular {
  if (!configurado) return 'apagado-servidor';
  if (iphoneSinInstalar) return 'instalar-iphone';
  if (!soportado) return 'no-soportado';
  if (permiso === 'denied') return 'bloqueado';
  return suscrito && permiso === 'granted' ? 'activo' : 'inactivo';
}

// H1: cada alimento se mide y se vende solo en sus unidades. Sin lista (alimento no reconocido) no se restringe nada.
export function opcionesPermitidas<T extends { valor: string }>(opciones: T[], permitidas: string[] | null | undefined): T[] {
  if (!Array.isArray(permitidas) || !permitidas.length) return opciones;
  return opciones.filter((o) => permitidas.includes(o.valor));
}

// La unidad con que se mide la cosecha al cambiar de cultivo: la que ya eligió si sigue permitida; si no, la típica del
// alimento, o la primera permitida.
export function unidadDeCosecha(actual: string | null | undefined, tipica: string | null | undefined, permitidas: string[] | null | undefined): string {
  if (!Array.isArray(permitidas) || !permitidas.length) return actual || tipica || 'kg';
  if (actual && permitidas.includes(actual)) return actual;
  return tipica && permitidas.includes(tipica) ? tipica : (permitidas[0] as string);
}

// H3: lo que ya se anotó de una siembra (sus gastos por etapa), para no volver a pedirlo al cosechar.
export function gastosYaRegistrados(etapas: { clave: string; nombre: string; total: number | string; momento: string }[] | null | undefined) {
  const conGasto = (etapas || []).filter((e) => Number(e.total) > 0)
    .map((e) => ({ clave: e.clave, nombre: e.nombre, total: Number(e.total), final: e.momento === 'final' }));
  const total = Math.round(conGasto.reduce((s, e) => s + e.total, 0) * 100) / 100;
  return { total, etapas: conGasto, enElFinal: conGasto.filter((e) => e.final) };
}

// H5: la respuesta de "¿qué me conviene sembrar?" en una sola frase, con la primera del ranking.
export function fraseResumenSiembra(f: { nombre?: string; puntaje: number; riesgo: string; margen_kg: number | null } | undefined, moneda: (n: number) => string): string | null {
  if (!f || !f.nombre) return null;
  const nivel = f.puntaje >= 70 ? 'conviene mucho' : f.puntaje >= 50 ? 'conviene' : f.puntaje >= 35 ? 'puede ser' : 'conviene poco';
  const riesgo = ({ bajo: 'hay poco riesgo de que sobre', medio: 'hay algo de riesgo de que sobre', alto: 'hay mucho riesgo de que sobre' } as Record<string, string>)[f.riesgo];
  const ganancia = f.margen_kg == null ? 'todavía falta saber cuánto cuesta producirlo'
    : `${f.margen_kg >= 0 ? 'ganas' : 'pierdes'} ${moneda(Math.abs(f.margen_kg))} por kg`;
  const inicio = f.puntaje >= 35 ? `Lo que más te conviene sembrar ahora es ${f.nombre}` : `Ahora ningún cultivo conviene mucho; el mejor es ${f.nombre}`;
  return `${inicio} (${f.puntaje} de 100, ${nivel}): ${ganancia}${riesgo ? ` y ${riesgo}` : ''}.`;
}

export type ManoDeObra =
  | { datos: null; total: 0; error: null; campo: null }
  | { datos: null; total: 0; error: string; campo: 'peones' | 'dias' | 'pago' }
  | { datos: { peones: number; dias: number; precio_unitario: number; unidad: 'jornal' }; total: number; error: null; campo: null };

// Mano de obra como se cuenta en el campo: peones enteros, días con decimales (medio día = 0,5) y el pago de cada peón por
// día. Los tres vacíos = sin gasto; a medias = error que dice qué falta. Mismas reglas que el servidor.
export function manoDeObra({ peones, dias, pago }: { peones: unknown; dias: unknown; pago: unknown }): ManoDeObra {
  const texto = (v: unknown) => String(v ?? '').trim();
  const vacios = [peones, dias, pago].map(texto).filter((v) => v === '').length;
  if (vacios === 3) return { datos: null, total: 0, error: null, campo: null };
  const p = Number(texto(peones));
  const d = Math.round(Number(texto(dias).replace(',', '.')) * 100) / 100;
  const s = Number(texto(pago).replace(',', '.'));
  if (!(Number.isInteger(p) && p >= 1 && p <= 500)) return { datos: null, total: 0, campo: 'peones', error: 'Escribe cuántos peones trabajaron (un número entero, como 3).' };
  if (!(d > 0 && d <= 366)) return { datos: null, total: 0, campo: 'dias', error: 'Escribe cuántos días trabajaron (por ejemplo 2, o 0,5 si fue medio día).' };
  if (!(s > 0)) return { datos: null, total: 0, campo: 'pago', error: 'Escribe cuánto le pagaste a cada peón por día.' };
  const total = Math.round(p * d * s * 100) / 100;
  if (total > 9999999.99) return { datos: null, total: 0, campo: 'pago', error: 'Ese gasto de mano de obra es demasiado grande: revisa los peones, los días y el pago.' };
  return { datos: { peones: p, dias: d, precio_unitario: s, unidad: 'jornal' }, total, error: null, campo: null };
}

// Los atajos de la fecha de cosecha salen del rango de ESE cultivo (lo más pronto, lo más probable, lo más tarde).
export function atajosDeCosecha(s: { fecha_sugerida?: string | null; fecha_desde?: string | null; fecha_hasta?: string | null } | null | undefined) {
  if (!s || !s.fecha_sugerida) return [];
  const atajos = [
    { etiqueta: 'Lo más pronto', fecha: s.fecha_desde },
    { etiqueta: 'Lo más probable', fecha: s.fecha_sugerida },
    { etiqueta: 'Lo más tarde', fecha: s.fecha_hasta },
  ].filter((a): a is { etiqueta: string; fecha: string } => Boolean(a.fecha));
  return atajos.filter((a, i) => atajos.findIndex((b) => b.fecha === a.fecha) === i);
}

// Unidades de venta adicionales que el agricultor todavía puede agregar a su producto: las del alimento (o todas, si no
// tiene lista) menos la base y las que ya agregó.
export function unidadesParaAgregar(todas: string[], permitidas: string[] | null | undefined, base: string | null | undefined, yaAgregadas: string[] = []): string[] {
  const posibles = Array.isArray(permitidas) && permitidas.length ? todas.filter((u) => permitidas.includes(u)) : todas;
  return posibles.filter((u) => u !== base && !yaAgregadas.includes(u));
}

// H6 (validación con usuarios del 2026-10-14): el precio sugerido al publicar es el precio de referencia por kg de la
// tienda, llevado a la unidad del producto con su equivalencia (1 unidad = X kg). Sin referencia, o sin equivalencia para
// otra unidad, no se sugiere nada.
export function precioSugerido({ precioReferenciaKg, unidad, equivalenciaKg }: { precioReferenciaKg: number | string | null | undefined; unidad: string | null | undefined; equivalenciaKg: number | string | null | undefined }): number | null {
  const porKg = Number(precioReferenciaKg);
  if (precioReferenciaKg == null || !(porKg > 0)) return null;
  const factor = unidad === 'kg' || !unidad ? 1 : Number(equivalenciaKg);
  if (!(factor > 0)) return null;
  return Math.round(porKg * factor * 100) / 100;
}

// H6: una descripción hecha con lo que ya se sabe de la cosecha (cultivo, chacra, fechas y cantidad). Solo dice lo que hay.
export function descripcionSugerida({ cultivo, chacra, fechaSiembra, fechaCosecha, cantidad, unidad }: { cultivo: string | null | undefined; chacra: string | null | undefined; fechaSiembra: string | null | undefined; fechaCosecha: string | null | undefined; cantidad: number | string | null | undefined; unidad: string | null | undefined }): string | null {
  if (!cultivo) return null;
  const meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'setiembre', 'octubre', 'noviembre', 'diciembre'];
  const fecha = (iso: string | null | undefined) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return m ? `${Number(m[3])} de ${meses[Number(m[2]) - 1]} de ${m[1]}` : null;
  };
  const siembra = fecha(fechaSiembra);
  const cosecha = fecha(fechaCosecha);
  const partes = [`${cultivo.trim()}${chacra ? ` de la chacra «${String(chacra).trim()}»` : ''}.`];
  if (siembra && cosecha) partes.push(`Se sembró el ${siembra} y se cosechó el ${cosecha}.`);
  else if (cosecha) partes.push(`Se cosechó el ${cosecha}.`);
  const n = Number(cantidad);
  if (n > 0) {
    const nombres: Record<string, string> = { kg: 'kg', unidad: n === 1 ? 'unidad' : 'unidades', litro: n === 1 ? 'litro' : 'litros' };
    partes.push(`De esta cosecha salieron ${String(Math.round(n * 100) / 100).replace('.', ',')} ${nombres[unidad || ''] || unidad || 'kg'}.`);
  }
  return partes.join(' ');
}

// H11 (validación con usuarios del 2026-10-13, R03): el estado que sigue en el orden de ese envío, o null si ya está en el
// último o fuera del orden.
export function siguienteEstadoEnvio(estado: string | null | undefined, estados: string[]): string | null {
  const i = estados.indexOf(estado || '');
  return i >= 0 && i < estados.length - 1 ? (estados[i + 1] as string) : null;
}

// H8: "jueves 15/10 a las 8:30" de "2026-10-15T08:30" (hora de Perú tal como la escribió el admin: sin zona horaria).
export function fechaHoraLegible(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ''));
  if (!m) return '';
  const dias = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
  const dia = dias[new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).getUTCDay()];
  return `${dia} ${m[3]}/${m[2]} a las ${Number(m[4])}:${m[5]}`;
}

// H8: el transporte que contrata el admin. Mismas reglas que el servidor (transporte/main.py, contratar_transporte);
// devuelve el campo con problema para mostrar el error junto a él.
export interface CamposTransporteContratado {
  nombre: string;
  telefono: string;
  placa: string;
  capacidadKg: string;
  capacidadSacos: string;
  fecha: string;
  flete: string;
}
export type ResultadoTransporteContratado =
  | { datos: { nombre: string; telefono: string; placa: string; capacidad_kg: number; capacidad_sacos: number | null; fecha_programada: string; flete: number }; campo: null; error: null }
  | { datos: null; campo: keyof CamposTransporteContratado; error: string };

export function datosTransporteContratado(campos: Partial<CamposTransporteContratado>, { pesoKg = null, sacos = null }: { pesoKg?: number | null; sacos?: number | null } = {}): ResultadoTransporteContratado {
  const error = (campo: keyof CamposTransporteContratado, texto: string): ResultadoTransporteContratado => ({ datos: null, campo, error: texto });
  const nombre = String(campos.nombre || '').trim();
  const telefono = String(campos.telefono || '').replace(/\D/g, '');
  const placa = String(campos.placa || '').replace(/\s/g, '').toUpperCase();
  const capacidadKg = Number(campos.capacidadKg);
  const capacidadSacos = String(campos.capacidadSacos ?? '').trim() === '' ? null : Number(campos.capacidadSacos);
  const flete = Number(campos.flete);
  if (nombre.length < 2) return error('nombre', 'Escribe el nombre del conductor.');
  if (!/^9\d{8}$/.test(telefono)) return error('telefono', 'El celular debe tener 9 dígitos y empezar con 9.');
  if (!/^[A-Z0-9]{2,4}-?[A-Z0-9]{2,4}$/.test(placa)) return error('placa', 'Escribe la placa como en la tarjeta del vehículo (ej. ABC-123).');
  if (!(capacidadKg > 0)) return error('capacidadKg', 'Escribe cuántos kilos carga el vehículo.');
  if (pesoKg && capacidadKg < pesoKg) return error('capacidadKg', `Carga ${capacidadKg} kg y la cosecha pesa ${pesoKg} kg: no alcanza.`);
  if (capacidadSacos !== null && !(Number.isInteger(capacidadSacos) && capacidadSacos > 0)) return error('capacidadSacos', 'Los sacos van en número entero (o déjalo vacío).');
  if (capacidadSacos && sacos && capacidadSacos < sacos) return error('capacidadSacos', `Lleva ${capacidadSacos} sacos y la cosecha son ${sacos}: no alcanza.`);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(String(campos.fecha || ''))) return error('fecha', 'Elige el día y la hora del recojo.');
  if (!(flete > 0)) return error('flete', 'Escribe el flete que acordaste, en soles.');
  return {
    datos: { nombre, telefono, placa, capacidad_kg: capacidadKg, capacidad_sacos: capacidadSacos, fecha_programada: String(campos.fecha).slice(0, 16), flete },
    campo: null,
    error: null,
  };
}

// La cantidad que pesó el almacén contra lo declarado por el productor: el mensaje junto al campo y si se puede confirmar.
// El servidor vuelve a comprobarlo todo: esto solo evita el viaje de ida y vuelta y explica el porqué al instante.
export function evaluarCantidadLlegada(texto: string, declarada: number, unidad: string): { estado: 'ok' | 'aviso' | 'error'; mensaje: string; cantidad: number | null } {
  const limpio = texto.trim();
  const cantidad = Number(limpio);
  if (!limpio || !Number.isInteger(cantidad) || cantidad < 1) return { estado: 'error', mensaje: `Escribe un número entero de 1 a ${declarada}.`, cantidad: null };
  if (cantidad > declarada) return { estado: 'error', mensaje: `No puede llegar más de lo declarado (${declarada} ${unidad}): esa es la cantidad que certificó la cosecha.`, cantidad: null };
  if (cantidad < declarada) return { estado: 'aviso', mensaje: `Faltan ${declarada - cantidad} ${unidad} de los ${declarada} declarados. La diferencia queda escrita en la cadena de certificación.`, cantidad };
  return { estado: 'ok', mensaje: 'Llegó todo lo declarado.', cantidad };
}
