// ---- Registrar siembra o producción (de abrirAsistenteSiembra y pasosAsistenteSiembra de app.js) ----
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { chacras as apiChacras } from '../../../api/chacras';
import { productores, type CatalogoPlantillas, type Plantilla, type SugerenciaCosecha } from '../../../api/productores';
import { productos } from '../../../api/productos';
import { toast } from '../../../components/Toast';
import { formatearMoneda } from '../../../lib/formato';
import { atajosDeCosecha, opcionesPermitidas, unidadDeCosecha } from '../../../lib/reglas';
import { alimentoPorNombre, fechaIso, fechaLegible, palabrasDe, sumarDias } from '../comun';
import { Asistente, CampoFecha, CampoNumero, CampoOpciones, CampoTexto, type AtajoFecha, type ConfigAsistente, type Paso, type PropsPaso } from './Asistente';
import { CamposGastos, gastosParaEnviar, pasoUbicacion, recogerGastos, resumenGastos, validarGastos, type ConUbicacion, type GastoAsistente, type Lugar } from './piezas';
import { Voz } from './voz';

// La parcela que el agricultor marcó en su perfil, ofrecida como chacra si todavía no registró ninguna (H2 de la
// validación con usuarios: no volver a marcarla). Se crea de verdad recién al registrar la siembra.
export const ID_CHACRA_DEL_PERFIL = 'chacra-del-perfil';

const CULTIVOS_FRECUENTES = ['Papa', 'Maíz', 'Quinua', 'Habas', 'Arveja', 'Trigo', 'Cebada', 'Oca', 'Olluco', 'Kiwicha'];
const OPCIONES_UNIDAD_SIEMBRA = [
  { valor: 'kg', titulo: 'Por kilos', detalle: 'Lo vas a pesar en kilos (lo más común)', icono: 'scale' },
  { valor: 'unidad', titulo: 'Por unidades', detalle: 'Se cuenta uno por uno, por ejemplo choclos o zapallos', icono: 'box' },
  { valor: 'litro', titulo: 'Por litros', detalle: 'Por ejemplo leche o miel', icono: 'droplet' },
];

type Sugerencia = { clave: string; datos?: SugerenciaCosecha; error?: string };

interface EstadoSiembra extends ConUbicacion {
  cultivo: string;
  tipo?: string;
  unidadesCosecha?: string[] | null;
  unidad?: string;
  unidadTocada?: boolean;
  parcelas: string;
  fechaSiembra: string;
  fechaCosecha: string;
  sugerenciaCosecha?: Sugerencia | null;
  equivalencia: string;
  plantilla?: (Plantilla & { nombrePedido: string }) | null;
  gastosInicioValores: Record<string, string>;
  gastosInicio?: GastoAsistente[];
}

const nombreChacra = (chacra: Lugar | null) => (chacra ? `${chacra.codigo}${chacra.nombre ? ` — ${chacra.nombre}` : ''}` : '—');

// ---- Sugerencia de la fecha de cosecha (observación de la presentación del 2026-10-01) ----
// Productores la calcula con IA (Gemini) para el cultivo y la zona de la chacra, o con una tabla de ciclos típicos si la
// IA no está disponible. Es solo una ayuda: el agricultor la usa con un toque o elige otra fecha.
const claveSugerenciaCosecha = (e: EstadoSiembra) => `${e.chacraId}|${(e.cultivo || '').trim().toLowerCase()}|${e.fechaSiembra}`;

function tiempoHastaCosecha(dias: number) {
  if (dias < 60) return `unas ${Math.round(dias / 7)} semanas`;
  const meses = Math.round(dias / 30.4);
  return meses === 1 ? 'un mes' : `unos ${meses} meses`;
}

function fechaDiaMes(iso: string, conAnio: boolean) {
  const [a, m, d] = iso.split('-').map(Number) as [number, number, number];
  return new Date(a, m - 1, d).toLocaleDateString('es-PE', { day: 'numeric', month: 'long', ...(conAnio ? { year: 'numeric' } : {}) });
}

function rangoSugerenciaCosecha(s: SugerenciaCosecha) {
  const mismoAnio = s.fecha_desde.slice(0, 4) === s.fecha_hasta.slice(0, 4);
  return `entre el ${fechaDiaMes(s.fecha_desde, !mismoAnio)} y el ${fechaDiaMes(s.fecha_hasta, true)}`;
}

// Validación con usuarios del 2026-10-14 (A05): un plazo solo no le sirvió. Se dice el rango, lo más probable, cómo darse
// cuenta de que ya está (la planta manda) y por qué varía.
function vozSugerenciaCosecha(s: SugerenciaCosecha) {
  return `Podrías cosechar ${rangoSugerenciaCosecha(s)}. Lo más probable, hacia el ${fechaLegible(s.fecha_sugerida)}, `
    + `${tiempoHastaCosecha(s.dias_probable)} después de sembrar. `
    + `${s.senal_cosecha ? `Sabrás que ya está así: ${s.senal_cosecha} ` : ''}${s.explicacion} `
    + 'Si te parece bien, toca "Usar la fecha más probable".';
}

const sugerenciaVigente = (e: EstadoSiembra) => (e.sugerenciaCosecha?.clave === claveSugerenciaCosecha(e) ? e.sugerenciaCosecha : null);

// Los atajos de la fecha de cosecha: los del rango del cultivo si ya llegó la sugerencia; si no, por meses.
function atajosFechaCosecha(e: EstadoSiembra): AtajoFecha[] {
  const delCultivo = atajosDeCosecha(sugerenciaVigente(e)?.datos) as AtajoFecha[];
  if (delCultivo.length) return delCultivo;
  const siembra = new Date(`${e.fechaSiembra}T00:00:00`);
  return [3, 4, 5, 6].map((meses) => {
    const fecha = new Date(siembra);
    fecha.setMonth(fecha.getMonth() + meses);
    return { etiqueta: `En ${meses} meses`, fecha: fechaIso(fecha) };
  });
}

function TarjetaSugerencia({ sugerencia, elegida, alUsar }: { sugerencia: Sugerencia | null | undefined; elegida: string; alUsar: (fecha: string) => void }) {
  if (!sugerencia) {
    return (
      <div className="sugerencia-cosecha cargando" aria-busy="true">
        <div className="sugerencia-cosecha-cabecera"><i className="ti ti-sparkles" aria-hidden="true" /><span>Calculando cuándo podrías cosechar…</span></div>
        <div className="sugerencia-cosecha-esqueleto" aria-hidden="true"><span /><span /><span /></div>
      </div>
    );
  }
  if (!sugerencia.datos) return <p className="sugerencia-cosecha-sin-dato"><i className="ti ti-info-circle" aria-hidden="true" /><span>{sugerencia.error}</span></p>;
  const s = sugerencia.datos;
  const esIa = s.fuente === 'ia';
  const rango = rangoSugerenciaCosecha(s);
  const usada = s.fecha_sugerida === elegida;
  return (
    <section className={`sugerencia-cosecha${usada ? ' usada' : ''}`} aria-labelledby="sugerencia-cosecha-titulo">
      <div className="sugerencia-cosecha-cabecera">
        <i className={`ti ${esIa ? 'ti-sparkles' : 'ti-book-2'}`} aria-hidden="true" />
        <span id="sugerencia-cosecha-titulo">Podrías cosechar</span>
        <span className="sugerencia-cosecha-fuente">{esIa ? 'Sugerencia con IA' : 'Tabla de cultivos'}</span>
      </div>
      <p className="sugerencia-cosecha-fecha">{rango.charAt(0).toUpperCase() + rango.slice(1)}</p>
      <p className="sugerencia-cosecha-detalle">Lo más probable: hacia el {fechaLegible(s.fecha_sugerida)}, {tiempoHastaCosecha(s.dias_probable)} después de sembrar.</p>
      {s.senal_cosecha && <p className="sugerencia-cosecha-senal"><i className="ti ti-plant" aria-hidden="true" /><span><strong>Cuándo cosechar:</strong> {s.senal_cosecha}</span></p>}
      <p className="sugerencia-cosecha-explicacion"><strong>Por qué varía:</strong> {s.explicacion}</p>
      <p className="sugerencia-cosecha-origen">{esIa
        ? 'Las fechas las calculó una inteligencia artificial para tu cultivo y tu zona.'
        : `Es lo que suele tardar un cultivo de ${(s.cultivo_tabla || '').toLowerCase()} en la sierra.`} Es una ayuda: tú decides la fecha, y la planta te dice cuándo.</p>
      <button type="button" className="btn btn-primary btn-usar-sugerencia" aria-pressed={usada} onClick={() => alUsar(s.fecha_sugerida)}>
        <i className={`ti ${usada ? 'ti-circle-check' : 'ti-calendar-check'}`} aria-hidden="true" /><span>{usada ? 'Elegiste esta fecha' : 'Usar la fecha más probable'}</span>
      </button>
    </section>
  );
}

// ---- paso "cultivo": la plantilla que reconoce y el precio de referencia de la tienda
function PasoCultivo({ estado, cambiar }: PropsPaso<EstadoSiembra>) {
  const [plantilla, setPlantilla] = useState<Plantilla | null>(null);
  const [precio, setPrecio] = useState('');
  const nombre = estado.cultivo.trim();

  useEffect(() => {
    if (nombre.length < 2) { setPlantilla(null); return undefined; }
    let vigente = true;
    const espera = setTimeout(() => {
      productores.plantillaPara(nombre).then((p) => { if (vigente) setPlantilla(p); }).catch(() => { if (vigente) setPlantilla(null); });
    }, 350);
    return () => { vigente = false; clearTimeout(espera); };
  }, [nombre]);

  useEffect(() => {
    if (nombre.length < 3) { setPrecio(''); return undefined; }
    let vigente = true;
    const espera = setTimeout(() => {
      productos.precioReferencia(nombre)
        .then((d) => { if (vigente) setPrecio(d.precio_promedio != null ? `En la tienda, "${nombre}" se vende a ${formatearMoneda(d.precio_promedio)} el kilo (promedio).` : ''); })
        .catch(() => { if (vigente) setPrecio(''); });
    }, 500);
    return () => { vigente = false; clearTimeout(espera); };
  }, [nombre]);

  const etapas = plantilla?.etapas.map((e) => e.nombre).join(' → ');
  return (
    <>
      <CampoTexto id="asistente-cultivo" etiqueta="Nombre del cultivo o producto" valor={estado.cultivo} ejemplo="Ej. Papa amarilla" sugerencias={CULTIVOS_FRECUENTES} alCambiar={(cultivo) => cambiar({ cultivo })} />
      <p className="asistente-plantilla" id="asistente-plantilla" aria-live="polite">
        {plantilla && (plantilla.reconocido
          ? <><i className="ti ti-circle-check" aria-hidden="true" /><span><strong>{plantilla.nombre}</strong> · {plantilla.etiquetas.nombre.toLowerCase()}. Etapas: {etapas}.</span></>
          : <><i className="ti ti-info-circle" aria-hidden="true" /><span>No lo tenemos en la lista: usaremos las etapas de un cultivo común ({etapas}).</span></>)}
      </p>
      <p className="asistente-nota" id="asistente-precio-referencia" aria-live="polite">{precio}</p>
    </>
  );
}

// ---- paso "fecha de cosecha": con la sugerencia (solo los cultivos de temporada)
function PasoFechaCosecha({ estado, cambiar }: PropsPaso<EstadoSiembra>) {
  const montado = useRef(true);
  const conSugerencia = (estado.tipo || 'temporada') === 'temporada';

  useEffect(() => {
    montado.current = true;
    if (conSugerencia && !sugerenciaVigente(estado)) {
      const clave = claveSugerenciaCosecha(estado);
      cambiar({ sugerenciaCosecha: null }, false);
      productores.produccion.sugerirCosecha({ chacra_id: estado.chacraId as string, cultivo: estado.cultivo, fecha_siembra: estado.fechaSiembra })
        .then((datos): Sugerencia => ({ clave, datos }))
        .catch((err: { status?: number }): Sugerencia => ({
          clave,
          error: err.status === 404 ? 'No conocemos cuánto tarda ese cultivo: elige tú la fecha aquí abajo.'
            : 'Ahora no pudimos calcular una fecha sugerida. Elige tú la fecha aquí abajo.',
        }))
        .then((sugerencia) => {
          // Si mientras tanto cambió el cultivo, la chacra o la siembra, esta ya no sirve.
          cambiar((anterior) => (claveSugerenciaCosecha(anterior) === clave ? { sugerenciaCosecha: sugerencia } : {}), false);
          if (montado.current && Voz.leerSolo) Voz.hablar(sugerencia.datos ? vozSugerenciaCosecha(sugerencia.datos) : sugerencia.error, { encolar: true });
        });
    }
    return () => { montado.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const siembra = new Date(`${estado.fechaSiembra}T00:00:00`);
  // Atajos según el tipo: un cultivo tarda meses; un periodo de huevos, semanas; un queso, días.
  const enDias = (dias: number, etiqueta: string) => ({ etiqueta, fecha: fechaIso(sumarDias(siembra, dias)) });
  const atajos = ({
    crianza: [enDias(7, 'En 1 semana'), enDias(14, 'En 2 semanas'), enDias(30, 'En 1 mes'), enDias(60, 'En 2 meses')],
    elaboracion: [enDias(0, 'El mismo día'), enDias(1, 'Al día siguiente'), enDias(7, 'En 1 semana'), enDias(30, 'En 1 mes')],
  } as Record<string, AtajoFecha[]>)[estado.tipo || ''] || atajosFechaCosecha(estado);
  const usar = (fecha: string) => {
    cambiar({ fechaCosecha: fecha });
    if (Voz.leerSolo) Voz.hablar(`Listo. Elegiste el ${fechaLegible(fecha)}.`);
  };
  return (
    <>
      {conSugerencia && <div id="asistente-sugerencia-cosecha" aria-live="polite"><TarjetaSugerencia sugerencia={sugerenciaVigente(estado)} elegida={estado.fechaCosecha} alUsar={usar} /></div>}
      <CampoFecha id="asistente-fecha-cosecha" valor={estado.fechaCosecha} minimo={estado.fechaSiembra} maximo={fechaIso(sumarDias(siembra, 1826))} atajos={atajos} alCambiar={(fechaCosecha) => cambiar({ fechaCosecha })} />
    </>
  );
}

// ---- paso "unidad": cómo se mide la cosecha (y cuánto pesa una unidad o un litro)
function PasoUnidad({ estado, cambiar }: PropsPaso<EstadoSiembra>) {
  const unidad = estado.unidad || 'kg';
  const caja = useRef<HTMLDivElement>(null);
  return (
    <>
      <CampoOpciones nombre="asistente-unidad" valor={unidad} opciones={opcionesPermitidas(OPCIONES_UNIDAD_SIEMBRA, estado.unidadesCosecha)} alCambiar={(valor) => {
        cambiar({ unidad: valor, unidadTocada: true });
        if (valor !== 'kg') setTimeout(() => caja.current?.querySelector('input')?.focus());
      }} />
      <div ref={caja} className={`asistente-equivalencia ${unidad === 'kg' ? 'hidden' : ''}`}>
        <CampoNumero id="asistente-equivalencia" etiqueta="¿Cuántos kilos pesa una unidad (o un litro)?" valor={estado.equivalencia} minimo={0} maximo={5000} paso={0.5} unidad="kg" alCambiar={(equivalencia) => cambiar({ equivalencia })} />
      </div>
    </>
  );
}

// ---- paso "gastos": los de las etapas del inicio, según la plantilla del cultivo
const etapasDelInicio = (e: EstadoSiembra) => (e.plantilla?.etapas || []).filter((x) => x.momento === 'inicio');

function PasoGastos({ estado, cambiar, catalogo }: PropsPaso<EstadoSiembra> & { catalogo: CatalogoPlantillas | null }) {
  const [listo, setListo] = useState(estado.plantilla?.nombrePedido === estado.cultivo);
  useEffect(() => {
    if (listo) return undefined;
    let vigente = true;
    const nombrePedido = estado.cultivo;
    productores.plantillaPara(nombrePedido)
      .then((p) => ({ ...p, nombrePedido }))
      .catch(() => null)
      .then((plantilla) => { cambiar({ plantilla }, false); if (vigente) setListo(true); });
    return () => { vigente = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const etapas = etapasDelInicio(estado);
  return (
    <div id="asistente-gastos-etapas" className="asistente-gastos" aria-live="polite">
      {!listo ? <p className="muted">Buscando las etapas de tu cultivo…</p>
        : etapas.length ? <CamposGastos etapas={etapas} valores={estado.gastosInicioValores} catalogo={catalogo} alCambiar={(gastosInicioValores) => cambiar({ gastosInicioValores })} />
          : <p className="muted">Ahora no pudimos traer las etapas. Puedes seguir: anota tus gastos después en tu siembra.</p>}
    </div>
  );
}

function pasosAsistenteSiembra(lugares: Lugar[], chacraPorId: (id: string | null) => Lugar | null, catalogo: CatalogoPlantillas | null): Paso<EstadoSiembra>[] {
  const hoy = new Date();
  return [
    {
      id: 'chacra',
      pregunta: '¿En qué chacra o granja es?',
      ayuda: 'Toca el lugar donde está esta siembra o tu producción (tu chacra, tu granja o tu taller). Si no aparece, primero regístralo en "Mis chacras".',
      voz: () => `¿En qué chacra o granja es? Tus lugares son: ${lugares.map((c) => nombreChacra(c).replace(' — ', ', ')).join('; ')}. Toca uno.`,
      Contenido: ({ estado, cambiar }) => (
        <CampoOpciones nombre="asistente-chacra" valor={estado.chacraId} alCambiar={(chacraId) => cambiar({ chacraId })} opciones={lugares.map((c) => ({
          valor: c.id, titulo: c.id === ID_CHACRA_DEL_PERFIL ? 'Usar mi chacra registrada' : c.codigo,
          detalle: c.id === ID_CHACRA_DEL_PERFIL ? 'La parcela que marcaste en tu perfil' : (c.nombre || 'Sin nombre'), icono: 'map-pin',
        }))} />
      ),
      validar: (e) => (e.chacraId ? null : 'Toca una de tus chacras para seguir.'),
      resumen: (e) => ({ etiqueta: 'Lugar', valor: nombreChacra(chacraPorId(e.chacraId)), icono: 'map-2' }),
    },
    pasoUbicacion<EstadoSiembra>('registrar la siembra', 'siembra', chacraPorId),
    {
      id: 'cultivo',
      pregunta: '¿Qué sembraste o qué produces?',
      ayuda: 'Escribe o di el nombre, por ejemplo "papa amarilla", "quinua", "huevos de gallina" o "queso fresco". '
        + 'También puedes tocar uno de la lista.',
      Contenido: PasoCultivo,
      recoger: (e) => {
        const cultivo = e.cultivo.trim();
        const alimento = alimentoPorNombre(catalogo, cultivo);
        // H1 (validación con usuarios): cada alimento se mide solo en sus unidades (la papa en kilos, los huevos por unidad).
        const unidadesCosecha = alimento?.unidades_cosecha || null;
        return {
          cultivo, tipo: alimento?.tipo || 'temporada', unidadesCosecha,
          unidad: unidadDeCosecha(e.unidadTocada ? e.unidad : null, alimento?.unidad || 'kg', unidadesCosecha),
        };
      },
      validar: (e) => (e.cultivo.length >= 2 ? null : 'Escribe o di qué sembraste o qué produces.'),
      resumen: (e) => ({ etiqueta: ['temporada', 'frutal'].includes(e.tipo || 'temporada') ? 'Cultivo' : 'Producto', valor: e.cultivo, icono: 'plant' }),
    },
    {
      id: 'parcelas',
      omitir: (e) => e.tipo === 'elaboracion',
      pregunta: (e) => palabrasDe(e.tipo).preguntaCantidad,
      ayuda: (e) => (e.tipo === 'crianza'
        ? 'Cuenta las gallinas, vacas o colmenas de este lote. Usa los botones de menos y más, o di el número.'
        : 'Cuenta las parcelas donde está este cultivo. Usa los botones de menos y más, o di el número.'),
      Contenido: ({ estado, cambiar }) => (
        <CampoNumero id="asistente-parcelas" etiqueta={estado.tipo === 'crianza' ? 'Número de animales' : 'Número de parcelas'} valor={estado.parcelas}
          minimo={1} maximo={1000} decimales={false} unidad={palabrasDe(estado.tipo).unidadCantidad} alCambiar={(parcelas) => cambiar({ parcelas })} />
      ),
      validar: (e) => {
        const n = Number(e.parcelas);
        return e.parcelas !== '' && Number.isInteger(n) && n >= 1 && n <= 1000 ? null : 'Debe ser un número entero, de 1 a 1000.';
      },
      resumen: (e) => ({ etiqueta: palabrasDe(e.tipo).cantidad || '', valor: String(Number(e.parcelas)), icono: e.tipo === 'crianza' ? 'paw' : 'layout-grid' }),
    },
    {
      id: 'fecha-siembra',
      pregunta: (e) => palabrasDe(e.tipo).cuandoInicio,
      ayuda: (e) => ({
        crianza: 'El día en que empezaste a contar este periodo (por ejemplo, el lunes de esta semana). Toca una opción o elige en el calendario.',
        elaboracion: 'El día en que empezaste a elaborar este lote. Toca una opción o elige en el calendario.',
        frutal: 'El día en que empezaste los trabajos de esta campaña (la poda o el abonamiento). Toca una opción o elige en el calendario.',
      } as Record<string, string>)[e.tipo || ''] || 'Toca "Hoy", "Ayer" u otra opción. Si todavía vas a sembrar, elige el día en el calendario.',
      Contenido: ({ estado, cambiar }) => (
        <CampoFecha id="asistente-fecha-siembra" valor={estado.fechaSiembra} minimo="2000-01-01" maximo={fechaIso(sumarDias(hoy, 730))} alCambiar={(fechaSiembra) => cambiar({ fechaSiembra })} atajos={[
          { etiqueta: 'Hoy', fecha: fechaIso(hoy) },
          { etiqueta: 'Ayer', fecha: fechaIso(sumarDias(hoy, -1)) },
          { etiqueta: 'Hace una semana', fecha: fechaIso(sumarDias(hoy, -7)) },
          { etiqueta: 'Hace un mes', fecha: fechaIso(sumarDias(hoy, -30)) },
        ]} />
      ),
      validar: (e) => {
        if (!e.fechaSiembra) return 'Elige el día.';
        if (e.fechaSiembra < '2000-01-01' || e.fechaSiembra > fechaIso(sumarDias(hoy, 730))) return 'Esa fecha no es válida: puede ser desde el año 2000 hasta dos años desde hoy.';
        return null;
      },
      resumen: (e) => ({ etiqueta: palabrasDe(e.tipo).resumenInicio, valor: fechaLegible(e.fechaSiembra), icono: 'calendar' }),
    },
    {
      id: 'fecha-cosecha',
      pregunta: (e) => palabrasDe(e.tipo).cuandoFin,
      ayuda: (e) => ({
        crianza: 'Elige hasta cuándo cuentas este periodo: por ejemplo una semana o un mes. Al terminar registras lo que recogiste.',
        elaboracion: 'El día en que el producto estará listo para vender (el queso fresco, el mismo día o al siguiente).',
      } as Record<string, string>)[e.tipo || ''] || 'Arriba te decimos entre qué fechas podrías cosechar tu cultivo y cómo saber que ya está. '
        + 'No tiene que ser exacto: toca "Usar la fecha más probable", una de las opciones o un día en el calendario.',
      voz: (e) => {
        if (e.tipo && e.tipo !== 'temporada') return `${palabrasDe(e.tipo).cuandoFin} Toca una de las opciones o elige el día en el calendario.`;
        const lista = sugerenciaVigente(e)?.datos;
        return lista ? `¿Cuándo crees que vas a cosechar? ${vozSugerenciaCosecha(lista)}`
          : '¿Cuándo crees que vas a cosechar? Un momento: estamos calculando una fecha sugerida para tu cultivo.';
      },
      Contenido: PasoFechaCosecha,
      validar: (e) => {
        if (!e.fechaCosecha) return 'Elige la fecha.';
        if (e.fechaCosecha < e.fechaSiembra) return 'No puede ser antes del inicio.';
        return null;
      },
      resumen: (e) => ({ etiqueta: palabrasDe(e.tipo).resumenFin, valor: fechaLegible(e.fechaCosecha), icono: 'calendar-event' }),
    },
    {
      id: 'unidad',
      // Si el alimento solo se mide en kilos (papa, quinua...), no se pregunta: ya se sabe.
      omitir: (e) => (e.unidadesCosecha || []).length === 1 && e.unidadesCosecha?.[0] === 'kg',
      pregunta: (e) => palabrasDe(e.tipo).medir,
      ayuda: 'Casi siempre es por kilos. Si la cuentas por unidades o por litros, dinos cuánto pesa cada una.',
      voz: (e) => `¿Cómo vas a medir tu cosecha? Puedes elegir: ${opcionesPermitidas(OPCIONES_UNIDAD_SIEMBRA, e.unidadesCosecha).map((o) => o.titulo.toLowerCase()).join(', ')}.`,
      Contenido: PasoUnidad,
      recoger: (e) => ({ unidad: e.unidad || 'kg', unidadTocada: true }),
      validar: (e) => {
        if ((e.unidad || 'kg') === 'kg') return null;
        const eq = Number(e.equivalencia);
        return e.equivalencia !== '' && eq > 0 && eq <= 5000 ? null : 'Escribe cuántos kilos pesa una unidad (o un litro).';
      },
      resumen: (e) => ({
        etiqueta: 'Se mide',
        valor: (e.unidad || 'kg') === 'kg' ? 'Por kilos'
          : `${e.unidad === 'litro' ? 'Por litros' : 'Por unidades'} (1 = ${String(Number(e.equivalencia)).replace('.', ',')} kg)`,
        icono: 'scale',
      }),
    },
    {
      id: 'gastos',
      pregunta: '¿Cuánto gastaste para empezar?',
      ayuda: (e) => 'Lo que gastaste en las primeras etapas, en soles. Si no gastaste en algo o no lo sabes, déjalo vacío. '
        + (({
          crianza: 'Lo que gastes después (vacunas, limpieza) lo anotas en "Mis siembras", cuando pase.',
          elaboracion: 'Lo que gastes al terminar (la mano de obra, el gas, los envases) te lo preguntamos al registrar lo elaborado.',
        } as Record<string, string>)[e.tipo || ''] || 'Lo que gastes después (por ejemplo el deshierbo o la fumigación) lo anotas en "Mis siembras", cuando pase.'),
      Contenido: (props) => <PasoGastos {...props} catalogo={catalogo} />,
      recoger: (e) => ({ gastosInicio: recogerGastos(etapasDelInicio(e), e.gastosInicioValores) }),
      validar: (e) => validarGastos(e.gastosInicio),
      resumen: (e) => ({ etiqueta: 'Gastos para empezar', valor: resumenGastos(e.gastosInicio), icono: 'coin' }),
    },
  ];
}

export function AsistenteSiembra({ chacras, chacraDelPerfil, catalogo, alCerrar }: { chacras: Lugar[]; chacraDelPerfil: Lugar | null; catalogo: CatalogoPlantillas | null; alCerrar: () => void }) {
  const queryClient = useQueryClient();
  const lugares = chacraDelPerfil ? [chacraDelPerfil] : chacras;
  const chacraPorId = (id: string | null) => (id === ID_CHACRA_DEL_PERFIL ? chacraDelPerfil : chacras.find((c) => String(c.id) === String(id)) || null);
  // Si la chacra del perfil ya se creó y falló la siembra, el reintento usa esa chacra (no crea otra).
  const chacraCreada = useRef<string | null>(null);
  const [config] = useState<ConfigAsistente<EstadoSiembra>>(() => ({
    titulo: 'Registrar siembra o producción',
    icono: 'seedling',
    textoFinal: 'Registrar',
    estadoInicial: {
      chacraId: chacraDelPerfil ? ID_CHACRA_DEL_PERFIL : (chacras.length === 1 ? chacras[0]!.id : null),
      cultivo: '', parcelas: '1', fechaSiembra: '', fechaCosecha: '', equivalencia: '', gastosInicioValores: {},
    },
    pasos: pasosAsistenteSiembra(lugares, chacraPorId, catalogo),
    alCerrar,
    enviar: async (estado, ui) => {
      ui.progreso('Registrando tu siembra…');
      let chacraId = estado.chacraId === ID_CHACRA_DEL_PERFIL && chacraCreada.current ? chacraCreada.current : estado.chacraId as string;
      if (chacraId === ID_CHACRA_DEL_PERFIL && chacraDelPerfil) {
        try {
          const nueva = await apiChacras.crear({
            codigo: 'CH-01', nombre: 'Mi chacra',
            ubicacion_latitud: chacraDelPerfil.ubicacion_latitud, ubicacion_longitud: chacraDelPerfil.ubicacion_longitud,
          });
          chacraId = nueva.id;
          chacraCreada.current = nueva.id;
          void queryClient.invalidateQueries({ queryKey: ['productor', 'chacras'] });
        } catch (err) {
          ui.error((err as Error).message || 'No se pudo guardar tu chacra. Intenta de nuevo.');
          return false;
        }
      }
      const unidad = estado.unidad || 'kg';
      try {
        await productores.produccion.crear({
          chacra_id: chacraId,
          cultivo: estado.cultivo,
          numero_parcelas: Number(estado.parcelas) || 1, // una elaboración no pregunta parcelas
          plantilla: estado.plantilla?.clave || null,
          gastos: gastosParaEnviar(estado.gastosInicio),
          unidad_medida: unidad,
          equivalencia_kg: unidad !== 'kg' ? Number(estado.equivalencia) : null,
          fecha_siembra: estado.fechaSiembra,
          fecha_cosecha_estimada: estado.fechaCosecha,
          ...(estado.ubicacion ? { latitud: estado.ubicacion.lat, longitud: estado.ubicacion.lng } : {}),
        });
      } catch (err) {
        console.error('registrar la siembra', err);
        ui.error((err as Error).message || 'No se pudo registrar la siembra. Intenta de nuevo.');
        return false;
      }
      toast(estado.tipo === 'temporada' || !estado.tipo ? 'Siembra registrada 🌱' : 'Registrado 🌱');
      if (Voz.leerSolo) Voz.hablar('Listo. Quedó registrado.');
      void queryClient.invalidateQueries({ queryKey: ['productor'] });
      return true;
    },
  }));
  return <Asistente config={config} />;
}
