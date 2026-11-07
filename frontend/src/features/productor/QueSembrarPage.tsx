import { useQuery } from '@tanstack/react-query';
import { productores, type CultivoRecomendado, type QueSembrar } from '../../api/productores';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearMoneda } from '../../lib/formato';
import { fraseResumenSiembra } from '../../lib/reglas';

// 21st.dev "Financial Score Cards" (puntaje con su nivel) + "Leaderboard Table" (barras de puntaje en orden).
// ui-ux-pro-max: el riesgo nunca va solo con color (ícono + texto), y cada recomendación dice por qué (sus frases) y de
// dónde sale cada número (fuentes al pie). Skill dataviz: medidor de un solo tono con su pista más clara.
const RIESGO_SIEMBRA: Record<string, { texto: string; icono: string; clase: string }> = {
  bajo: { texto: 'Poco riesgo de que sobre', icono: 'shield-check', clase: 'bajo' },
  medio: { texto: 'Algo de riesgo de que sobre', icono: 'alert-triangle', clase: 'medio' },
  alto: { texto: 'Mucho riesgo de que sobre', icono: 'alert-octagon', clase: 'alto' },
};
const TENDENCIA: Record<string, [string, string]> = { sube: ['trending-up', 'va subiendo'], baja: ['trending-down', 'va bajando'], estable: ['minus', 'se mantiene'] };

const nivelPuntaje = (p: number) => (p >= 70 ? 'Conviene mucho' : p >= 50 ? 'Conviene' : p >= 35 ? 'Puede ser' : 'Poco conveniente');

function TarjetaSiembraSugerida({ f, i }: { f: CultivoRecomendado; i: number }) {
  const riesgo = RIESGO_SIEMBRA[f.riesgo] || RIESGO_SIEMBRA.medio!;
  const tendencia = f.tendencia ? TENDENCIA[f.tendencia] : undefined;
  const cosecha = f.cosecha_estimada ? new Date(`${f.cosecha_estimada}T12:00:00`).toLocaleDateString('es-PE', { month: 'long', year: 'numeric' }) : null;
  return (
    <article className={`qs-card${i < 3 && f.completo ? ' destacada' : ''}`} style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}>
      <header className="qs-cabeza">
        <span className="qs-puesto" aria-label={`Puesto ${i + 1}`}>{i + 1}</span>
        <h4>{f.nombre}</h4>
        <span className={`qs-riesgo ${riesgo.clase}`}><i className={`ti ti-${riesgo.icono}`} aria-hidden="true" /> {riesgo.texto}</span>
      </header>
      <div className="qs-puntaje">
        <div className="qs-puntaje-texto"><strong>{f.puntaje}</strong><span>/100 · {nivelPuntaje(f.puntaje)}</span></div>
        <div className="qs-medidor" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={f.puntaje} aria-label={`Qué tanto conviene: ${f.puntaje} de 100`}><span style={{ width: `${f.puntaje}%` }} /></div>
      </div>
      <ul className="qs-datos">
        {f.margen_kg != null
          ? <li><i className="ti ti-coin" aria-hidden="true" /><span><strong>{f.margen_kg >= 0 ? 'Ganas' : 'Pierdes'} {formatearMoneda(Math.abs(f.margen_kg))}</strong> por kg ({f.margen_pct} %)</span></li>
          : <li><i className="ti ti-coin" aria-hidden="true" /><span>Ganancia: <strong>falta el costo</strong></span></li>}
        <li><i className="ti ti-shopping-cart" aria-hidden="true" /><span>{f.demanda_kg ? <><strong>{Math.round(f.demanda_kg).toLocaleString('es-PE')} kg</strong> vendidos</> : <><strong>Sin ventas</strong> todavía</>}
          {tendencia && <> · <i className={`ti ti-${tendencia[0]}`} aria-hidden="true" /> {tendencia[1]}</>}</span></li>
        {cosecha && <li><i className="ti ti-calendar" aria-hidden="true" /><span>Cosecharías en <strong>{cosecha}</strong></span></li>}
      </ul>
      <details className="qs-porque" open={i < 3}>
        <summary>¿Por qué?</summary>
        <ul>{f.frases.map((t) => <li key={t}>{t}</li>)}</ul>
      </details>
    </article>
  );
}

const NOTA_IA = 'Resumen hecho con IA a partir de los datos de abajo: revísalo con tu experiencia.';

// H5 (validación con usuarios del 2026-10-13): primero una frase con la respuesta y, debajo, el porqué.
function ResumenQueSembrar({ d }: { d: QueSembrar }) {
  const primera = d.recomendaciones[0];
  const frase = fraseResumenSiembra(primera, formatearMoneda);
  if (!frase || !primera) return null;
  return (
    <section className="qs-consejo qs-resumen" aria-label="Resumen">
      <span className="qs-consejo-icono"><i className="ti ti-bulb" aria-hidden="true" /></span>
      <div>
        <p className="qs-resumen-frase">{frase}</p>
        {primera.frases?.length > 0 && <div className="qs-resumen-motivo"><strong>¿Por qué?</strong><ul>{primera.frases.map((t) => <li key={t}>{t}</li>)}</ul></div>}
        {d.consejo && <div className="qs-resumen-ia"><strong><i className="ti ti-sparkles" aria-hidden="true" /> En pocas palabras</strong><p>{d.consejo}</p><small>{NOTA_IA}</small></div>}
      </div>
    </section>
  );
}

function ContenidoQueSembrar({ d }: { d: QueSembrar }) {
  return (
    <>
      {d.recomendaciones.length ? <ResumenQueSembrar d={d} /> : d.consejo && (
        <section className="qs-consejo" aria-label="Consejo">
          <span className="qs-consejo-icono"><i className="ti ti-sparkles" aria-hidden="true" /></span>
          <div><strong>En pocas palabras</strong><p>{d.consejo}</p><small>{NOTA_IA}</small></div>
        </section>
      )}
      {!d.ventas_disponibles && <p className="ganancias-nota alerta"><i className="ti ti-alert-triangle" aria-hidden="true" /> Ahora no pudimos traer las ventas: el ranking usa solo el catálogo y las siembras.</p>}
      {d.recomendaciones.length
        ? <div className="qs-grid">{d.recomendaciones.map((f, i) => <TarjetaSiembraSugerida key={f.clave} f={f} i={i} />)}</div>
        : (
          <div className="empty-state"><span className="empty-state-icon"><i className="ti ti-bulb" /></span>
            <p><strong>Todavía no hay datos suficientes</strong></p>
            <p className="muted">Cuando se vendan productos en ChakraShop o los agricultores registren sus cosechas con sus gastos, aquí verás qué conviene sembrar.</p></div>
        )}
      {d.sin_datos.length > 0 && <p className="qs-sin-datos"><i className="ti ti-info-circle" aria-hidden="true" /> Aún sin datos de venta en ChakraShop: {d.sin_datos.join(', ')}.</p>}
      <details className="qs-fuentes"><summary>¿De dónde salen los números?</summary>
        <p>Las ventas, los precios y las siembras son de ChakraShop. El costo por kg sale de tus cosechas con gastos; si no tienes, de las de otros agricultores; si tampoco hay, de estas referencias:</p>
        <ul>{d.referencias.map((r) => <li key={r.cultivo}><strong>{r.cultivo}:</strong> {r.fuente}</li>)}</ul>
        <p>Es una guía para decidir, no una promesa: el clima, las plagas y el precio del día también cuentan.</p>
      </details>
    </>
  );
}

export default function QueSembrarPage() {
  // Como el original: se calcula al abrir la pestaña y con "Actualizar"; no se queda guardado entre visitas.
  const consulta = useQuery({ queryKey: ['productor', 'que-sembrar'], queryFn: () => productores.queSembrar(), gcTime: 0, staleTime: 0 });
  return (
    <div className="productor-tab-panel active" data-tab-panel="que-sembrar">
      <div className="section-header inner">
        <h3><i className="ti ti-bulb" /> <Texto clave="seccion.que_sembrar">¿Qué me conviene sembrar?</Texto></h3>
        <button type="button" className="btn btn-outline" id="btn-refrescar-que-sembrar" onClick={() => { void consulta.refetch(); }}>↻ Actualizar</button>
      </div>
      <p className="muted ventas-intro">Los cultivos ordenados por lo que más te conviene sembrar hoy: lo que se vende en ChakraShop y a cuánto, lo que cuesta producirlo, cuánto tarda y cuántos otros agricultores van a cosechar lo mismo en la misma fecha.</p>
      <div id="productor-que-sembrar" className="que-sembrar">
        {consulta.isFetching
          ? [0, 1].map((i) => <div className="skeleton-row-card" key={i}><div className="skeleton skeleton-line w-40" /><div className="skeleton skeleton-line tall w-70" /><div className="skeleton skeleton-line w-60" /></div>)
          : consulta.isError
            ? <p className="muted">{(consulta.error as Error).message || 'No se pudo calcular qué conviene sembrar.'}</p>
            : consulta.data && <ContenidoQueSembrar d={consulta.data} />}
      </div>
    </div>
  );
}
