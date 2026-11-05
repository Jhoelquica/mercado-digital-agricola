// Piezas que comparten los dos asistentes del agricultor: el paso "¿Estás en tu chacra?" (GPS) y los gastos por etapa.
import { useEffect, useRef, useState } from 'react';
import type { CatalogoPlantillas, Etapa } from '../../../api/productores';
import { formatearMoneda } from '../../../lib/formato';
import { manoDeObra } from '../../../lib/reglas';
import { distanciaKm, formatearDistancia, nombreRubro, RADIO_CHACRA_KM, textoPeonesDias } from '../comun';
import type { Paso, PropsPaso, ResumenPaso } from './Asistente';
import { Voz } from './voz';

// ---------------------------------------------------------------- ubicación (evidencia de origen)
// Una chacra registrada o la parcela del perfil ofrecida como chacra (se crea de verdad al registrar la siembra).
export interface Lugar { id: string; codigo: string; nombre?: string | null; ubicacion_latitud: number; ubicacion_longitud: number }
export interface ConUbicacion { chacraId: string | null; ubicacion?: { lat: number; lng: number } | null; sinUbicacion?: string }
type EstadoGps = 'espera' | 'buscando' | 'tomada' | 'dentro' | 'fuera' | 'sin-gps';

const ESTADOS_UBICACION: Record<EstadoGps, { icono: string; etiqueta: string }> = {
  espera: { icono: 'map-pin', etiqueta: 'Sin tomar' },
  buscando: { icono: 'loader-2', etiqueta: 'Buscando…' },
  tomada: { icono: 'circle-check', etiqueta: 'Ubicación tomada' },
  dentro: { icono: 'circle-check', etiqueta: 'En tu chacra' },
  fuera: { icono: 'alert-triangle', etiqueta: 'Lejos de tu chacra' },
  'sin-gps': { icono: 'map-pin-off', etiqueta: 'Sin ubicación' },
};

function pedirUbicacionGps(): Promise<{ lat: number; lng: number }> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation || !window.isSecureContext) { reject({ code: 'sin-soporte' }); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      reject,
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 },
    );
  });
}

function mensajeSinUbicacion(err: { code?: number | string } | null): string {
  if (err?.code === 1) {
    return 'No diste permiso para usar tu ubicación. Se registrará igual, pero quedará como advertencia. '
      + 'Para darlo, toca el candado junto a la dirección de la página y vuelve a intentarlo.';
  }
  if (err?.code === 'sin-soporte') return 'Este navegador no puede dar tu ubicación aquí. Se registrará igual, pero quedará como advertencia.';
  return 'No se pudo obtener tu ubicación (poca señal de GPS). Se registrará igual, pero quedará como advertencia. '
    + 'Puedes intentarlo otra vez.';
}

// Con la ubicación ya tomada, dice si el agricultor está dentro de su chacra.
function evaluarUbicacion(ubicacion: { lat: number; lng: number }, chacra: Lugar | null): [EstadoGps, string] {
  if (!chacra) return ['tomada', 'Ubicación tomada. Elige tu chacra para ver si estás dentro de ella.'];
  const km = distanciaKm(ubicacion.lat, ubicacion.lng, chacra.ubicacion_latitud, chacra.ubicacion_longitud);
  if (km <= RADIO_CHACRA_KM) return ['dentro', `Estás dentro de tu chacra ${chacra.codigo} (a ${formatearDistancia(km)} del punto que registraste).`];
  return ['fuera', `Estás a ${formatearDistancia(km)} de tu chacra ${chacra.codigo}. Se registrará igual, `
    + 'pero quedará como advertencia en el certificado. Si puedes, hazlo desde tu chacra.'];
}

function AvisoEnLaChacra({ momento }: { momento: 'siembra' | 'cosecha' }) {
  const texto = momento === 'siembra'
    ? 'Registra la siembra estando en tu chacra: tu ubicación queda como constancia en el certificado del producto.'
    : 'Toma la foto de tu cosecha y confirma estando en tu chacra: la foto y tu ubicación quedan selladas en el '
      + 'certificado que ve el comprador.';
  return (
    <div className="aviso-en-chacra">
      <i className="ti ti-plant-2" aria-hidden="true" />
      <div>
        <strong>Hazlo dentro de tu chacra</strong>
        <p>{texto} Si estás a más de {String(RADIO_CHACRA_KM).replace('.', ',')} km, se registra igual, pero queda como advertencia.</p>
      </div>
    </div>
  );
}

// El bloque de ubicación de antes; lo tomado se guarda en el estado del asistente para no perderlo al ir y volver entre
// pasos. Nunca bloquea: lejos o sin GPS se sigue igual.
function PasoUbicacion<E extends ConUbicacion>({ estado, cambiar, momento, tipoAviso, chacraPorId }: PropsPaso<E> & { momento: string; tipoAviso: 'siembra' | 'cosecha'; chacraPorId: (id: string | null) => Lugar | null }) {
  const chacra = chacraPorId(estado.chacraId);
  const [buscando, setBuscando] = useState(false);
  const montado = useRef(true);
  const pedido = useRef(false);

  async function tomar() {
    setBuscando(true);
    cambiar({ ubicacion: null, sinUbicacion: undefined } as Partial<E>, false);
    let texto: string;
    try {
      const ubicacion = await pedirUbicacionGps();
      cambiar({ ubicacion, sinUbicacion: undefined } as Partial<E>, false);
      texto = evaluarUbicacion(ubicacion, chacra)[1];
    } catch (err) {
      texto = mensajeSinUbicacion(err as { code?: number });
      cambiar({ ubicacion: null, sinUbicacion: texto } as Partial<E>, false);
    }
    if (!montado.current) return;
    setBuscando(false);
    if (Voz.leerSolo) Voz.hablar(texto);
  }

  useEffect(() => {
    montado.current = true;
    // Si nunca se intentó, se pide al mostrar el paso.
    if (!pedido.current && !estado.ubicacion && !estado.sinUbicacion) { pedido.current = true; void tomar(); }
    return () => { montado.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [gps, mensaje]: [EstadoGps, string] = buscando ? ['buscando', 'Buscando tu ubicación… puede tardar unos segundos.']
    : estado.ubicacion ? evaluarUbicacion(estado.ubicacion, chacra)
      : estado.sinUbicacion ? ['sin-gps', estado.sinUbicacion]
        : ['espera', 'Toca el botón estando dentro de tu chacra. Si no lo haces, se pedirá al enviar.'];
  const info = ESTADOS_UBICACION[gps];
  return (
    <>
      <AvisoEnLaChacra momento={tipoAviso} />
      <div className="evidencia-gps" data-estado={gps}>
        <div className="evidencia-gps-cabecera">
          <span className="evidencia-gps-titulo" id="gps-titulo-asistente">Tu ubicación al {momento}</span>
          <span className="evidencia-gps-estado"><i className={`ti ti-${info.icono}`} aria-hidden="true" /> {info.etiqueta}</span>
        </div>
        <p className="evidencia-gps-texto" role="status" aria-live="polite">{mensaje}</p>
        <button type="button" className="btn btn-outline btn-gps-tomar" aria-describedby="gps-titulo-asistente" disabled={buscando} onClick={() => { void tomar(); }}>
          <i className="ti ti-current-location" aria-hidden="true" /> <span>{buscando ? 'Buscando…' : estado.ubicacion ? 'Volver a tomar' : 'Tomar mi ubicación'}</span>
        </button>
      </div>
    </>
  );
}

// Paso "¿Estás en tu chacra?" (siembra y cosecha).
export function pasoUbicacion<E extends ConUbicacion>(momento: string, tipoAviso: 'siembra' | 'cosecha', chacraPorId: (id: string | null) => Lugar | null): Paso<E> {
  const resumen = (estado: E): ResumenPaso => {
    const chacra = chacraPorId(estado.chacraId);
    const km = estado.ubicacion && chacra ? distanciaKm(estado.ubicacion.lat, estado.ubicacion.lng, chacra.ubicacion_latitud, chacra.ubicacion_longitud) : null;
    if (km == null) return { etiqueta: 'Tu ubicación', valor: 'Sin ubicación (queda como advertencia)', icono: 'map-pin-off', advertencia: true };
    if (km <= RADIO_CHACRA_KM) return { etiqueta: 'Tu ubicación', valor: `En tu chacra (a ${formatearDistancia(km)})`, icono: 'map-pin' };
    return { etiqueta: 'Tu ubicación', valor: `A ${formatearDistancia(km)} de tu chacra (queda como advertencia)`, icono: 'alert-triangle', advertencia: true };
  };
  return {
    id: 'ubicacion',
    pregunta: '¿Estás en tu chacra?',
    ayuda: 'Vamos a ver dónde estás con el GPS del teléfono. Hazlo estando dentro de tu chacra: queda como constancia en '
      + 'el certificado del producto. Si estás lejos o no hay señal, igual puedes seguir, pero queda como advertencia.',
    voz: (estado) => `¿Estás en tu chacra ${chacraPorId(estado.chacraId)?.codigo || ''}? Hazlo estando dentro de tu chacra. `
      + 'Vamos a tomar tu ubicación con el GPS del teléfono.',
    Contenido: (props) => <PasoUbicacion {...props} momento={momento} tipoAviso={tipoAviso} chacraPorId={chacraPorId} />,
    resumen,
  };
}

// ---------------------------------------------------------------- gastos por etapa
export type GastoAsistente =
  | { etapa: string; rubro: string; monto: number }
  | { etapa: string; rubro: 'mano_obra'; peones: number; dias: number; precio_unitario: number; unidad: 'jornal' }
  | { etapa: string; rubro: 'mano_obra'; unidad: 'jornal'; errorManoObra: string };

// "3 peones × 2 días × S/ 50 = S/ 300" debajo de los campos de mano de obra; la ayuda si todavía no se escribió nada.
export function textoTotalJornales(peones: unknown, dias: unknown, pago: unknown) {
  const r = manoDeObra({ peones, dias, pago });
  if (r.datos) return <>{textoPeonesDias(r.datos.peones, r.datos.dias)} × {formatearMoneda(r.datos.precio_unitario)} = <strong>{formatearMoneda(r.total)}</strong></>;
  return 'Cuántos peones trabajaron, cuántos días (medio día = 0,5) y lo que le pagaste a cada uno por día. El tractor no va aquí: va en Maquinaria.';
}

// Un campo de gasto por rubro de cada etapa; la mano de obra va en peones × días × pago de cada peón por día.
export function CamposGastos({ etapas, valores, catalogo, alCambiar }: { etapas: Etapa[]; valores: Record<string, string>; catalogo: CatalogoPlantillas | null; alCambiar: (valores: Record<string, string>) => void }) {
  const fijar = (id: string, valor: string) => alCambiar({ ...valores, [id]: valor });
  const campo = (id: string, etiqueta: string) => (
    <label className="ag-campo" htmlFor={id} key={id}><span>{etiqueta}</span>
      <span className="ag-entrada"><span aria-hidden="true">S/</span><input id={id} type="number" inputMode="decimal" min="0" step="any" value={valores[id] ?? ''} onChange={(e) => fijar(id, e.target.value)} /></span>
    </label>
  );
  return (
    <>
      {etapas.map((e) => (
        <fieldset className="ag-etapa" key={e.clave}>
          <legend>{e.nombre}</legend>
          <p>{e.que_se_hace}</p>
          <div className="ag-campos">
            {e.rubros.map((r) => {
              const base = `ag-${e.clave}-${r}`;
              if (r !== 'mano_obra') return campo(base, nombreRubro(catalogo, r));
              // Validación con usuarios del 2026-10-14 (A04): cuántos peones (enteros), cuántos días (medio día = 0,5) y
              // el pago de cada peón por día. Los días van como texto para aceptar la coma en cualquier teclado.
              const cantidad = (sufijo: string, etiqueta: string, unidad: string, entrada: Record<string, string>) => (
                <label className="ag-campo" htmlFor={`${base}-${sufijo}`}><span>{etiqueta}</span>
                  <span className="ag-entrada"><input id={`${base}-${sufijo}`} {...entrada} value={valores[`${base}-${sufijo}`] ?? ''} onChange={(ev) => fijar(`${base}-${sufijo}`, ev.target.value)} /><span aria-hidden="true">{unidad}</span></span>
                </label>
              );
              return (
                <div className="ag-jornales" key={base}>
                  {cantidad('peones', '¿Cuántos peones?', 'personas', { type: 'number', inputMode: 'numeric', min: '1', step: '1', placeholder: '0' })}
                  {cantidad('dias', '¿Cuántos días?', 'días', { type: 'text', inputMode: 'decimal', autoComplete: 'off', placeholder: 'Ej.: 2 o 0,5' })}
                  {campo(`${base}-precio`, 'Pago por peón al día')}
                  <p className="ag-jornales-total" aria-live="polite">{textoTotalJornales(valores[`${base}-peones`], valores[`${base}-dias`], valores[`${base}-precio`])}</p>
                </div>
              );
            })}
          </div>
        </fieldset>
      ))}
    </>
  );
}

// Los gastos escritos: [{etapa, rubro, monto}] o la mano de obra como {peones, dias, precio_unitario, unidad: 'jornal'}.
export function recogerGastos(etapas: Etapa[], valores: Record<string, string> = {}): GastoAsistente[] {
  const gastos: GastoAsistente[] = [];
  for (const e of etapas) {
    for (const r of e.rubros) {
      if (r === 'mano_obra') continue;
      const v = valores[`ag-${e.clave}-${r}`];
      const monto = v != null && v !== '' ? Number(v) : null;
      if (monto) gastos.push({ etapa: e.clave, rubro: r, monto });
    }
  }
  for (const e of etapas) {
    if (!e.rubros.includes('mano_obra')) continue;
    const base = `ag-${e.clave}-mano_obra`;
    const r = manoDeObra({ peones: valores[`${base}-peones`] ?? '', dias: valores[`${base}-dias`] ?? '', pago: valores[`${base}-precio`] ?? '' });
    // Con los tres vacíos no hay gasto; a medias, se guarda el error para que validar lo muestre.
    if (r.datos) gastos.push({ etapa: e.clave, rubro: 'mano_obra', ...r.datos, unidad: 'jornal' });
    else if (r.error) gastos.push({ etapa: e.clave, rubro: 'mano_obra', unidad: 'jornal', errorManoObra: r.error });
  }
  return gastos;
}

export function validarGastos(gastos: GastoAsistente[] = []): string | null {
  for (const g of gastos) {
    if ('errorManoObra' in g) return `En la mano de obra: ${g.errorManoObra} (O deja los tres vacíos.)`;
    if ('monto' in g && (!(g.monto > 0) || g.monto > 9999999.99)) return 'Los gastos deben ser montos mayores a cero, sin signos.';
  }
  return null;
}

function totalGastos(gastos: GastoAsistente[] = []) {
  return gastos.reduce((s, g) => s + ('monto' in g ? g.monto : 'peones' in g ? g.peones * g.dias * (g.precio_unitario || 0) : 0), 0);
}

export function resumenGastos(gastos: GastoAsistente[] = []): string {
  if (!gastos.length) return 'Sin gastos por ahora';
  return `${formatearMoneda(totalGastos(gastos))} en ${gastos.length} gasto${gastos.length === 1 ? '' : 's'}`;
}

// Lo que se manda a la API (sin el error de una mano de obra a medias: validar ya no lo deja pasar).
export const gastosParaEnviar = (gastos: GastoAsistente[] = []) => gastos.filter((g) => !('errorManoObra' in g)) as Exclude<GastoAsistente, { errorManoObra: string }>[];

// H3 (validación con usuarios del 2026-10-13): al cosechar, lo ya anotado se muestra (no se vuelve a pedir).
export function GastosYaRegistrados({ etapas, total, nota }: { etapas: { nombre: string; total: number }[]; total: number; nota: string }) {
  return (
    <section className="ag-ya-registrado" aria-label="Gastos ya registrados">
      <div className="ag-ya-cabeza"><i className="ti ti-receipt" aria-hidden="true" /><span>Gastos ya registrados</span><strong>{formatearMoneda(total)}</strong></div>
      <ul>{etapas.map((e) => <li key={e.nombre}><span>{e.nombre}</span><span>{formatearMoneda(e.total)}</span></li>)}</ul>
      <p>{nota}</p>
    </section>
  );
}
