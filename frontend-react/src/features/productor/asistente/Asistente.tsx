// ============ ASISTENTE PASO A PASO CON VOZ (de js/asistente.js) ============
// Para los formularios del agricultor (registrar la siembra, completar la cosecha), pensados para personas mayores de
// 65 años (presentación del 2026-10-01): una pregunta por pantalla, letra y botones grandes, y voz en las dos
// direcciones: la app lee la pregunta en voz alta y el agricultor puede responder hablando. Nada depende de la voz: todo
// se puede hacer tocando. Este archivo es genérico: los pasos de cada asistente están en AsistenteSiembra/AsistenteCosecha.
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Texto, useIdioma } from '../../../idioma/IdiomaContext';
import { atraparFoco } from '../../../lib/foco';
import { fechaLegible } from '../comun';
import { mensajeErrorDictado, textoANumero, Voz } from './voz';

export interface ResumenPaso { etiqueta: string; valor: string; textoVoz?: string; icono?: string; advertencia?: boolean }
type Cambio<E> = Partial<E> | ((anterior: E) => Partial<E>);

export interface PropsPaso<E> {
  estado: E;
  // Cambia el estado del asistente; limpiarError = false para lo que llega solo (el GPS, una sugerencia).
  cambiar: (cambio: Cambio<E>, limpiarError?: boolean) => void;
}

// La pregunta y la ayuda pueden ser texto o una función del estado ("¿Cuándo sembraste?" o "¿Cuándo empezó el
// periodo?" según lo que produce). Un paso con omitir(estado) = true no se muestra.
export interface Paso<E> {
  id: string;
  pregunta: string | ((e: E) => string);
  ayuda?: string | ((e: E) => string);
  voz?: (e: E) => string;
  omitir?: (e: E) => boolean;
  Contenido: (props: PropsPaso<E>) => ReactNode;
  // Lo que se deduce de lo respondido al dejar el paso (con "Siguiente" o "Atrás").
  recoger?: (e: E) => Partial<E>;
  validar?: (e: E) => string | null;
  resumen?: (e: E) => ResumenPaso;
}

export interface UiEnvio { progreso: (texto: string) => void; error: (mensaje: string) => void }

export interface ConfigAsistente<E> {
  titulo: string;
  icono: string;
  textoFinal: string;
  estadoInicial: E;
  pasos: Paso<E>[];
  enviar: (estado: E, ui: UiEnvio) => Promise<boolean>;
  alCerrar: () => void;
}

// Lo que los campos necesitan del asistente: mostrar un error y anunciar algo al lector de pantalla.
interface UiAsistente { mostrarError: (mensaje: string) => void; anunciar: (texto: string) => void }
const ContextoAsistente = createContext<UiAsistente>({ mostrarError: () => {}, anunciar: () => {} });
export const useUiAsistente = () => useContext(ContextoAsistente);

// ---------------------------------------------------------------- piezas de los pasos
// Botón de micrófono que dicta en un campo. tipo: 'texto' o 'numero' (convierte lo dicho, "ciento veinte", a cifra).
function BotonDictar({ tipo, valor, alDictar }: { tipo: 'texto' | 'numero'; valor: string; alDictar: (valor: string) => void }) {
  const { mostrarError, anunciar } = useUiAsistente();
  const [escuchando, setEscuchando] = useState(false);
  if (!Voz.puedeEscuchar) return null;

  function dictar() {
    if (escuchando) { Voz.detenerEscucha(); return; } // tocar otra vez = dejar de escuchar
    const antes = valor;
    let ultimo = valor;
    let huboError = false;
    setEscuchando(true);
    anunciar('Te escucho. Habla ahora.');
    mostrarError('');
    Voz.escuchar({
      alTexto: (texto) => {
        if (tipo === 'numero') {
          const numero = textoANumero(texto);
          if (numero !== null) { ultimo = String(numero); alDictar(ultimo); }
        } else {
          ultimo = texto.replace(/[.。]$/, '').replace(/^./, (c) => c.toUpperCase());
          alDictar(ultimo);
        }
      },
      alTerminar: (final) => {
        setEscuchando(false);
        if (huboError) return; // el mensaje ya lo puso alError (y no se debe borrar)
        if (!final) { // terminó sin oír nada (silencio)
          anunciar('');
          mostrarError(mensajeErrorDictado('no-speech'));
          return;
        }
        if (tipo === 'numero' && textoANumero(final) === null) {
          alDictar(antes);
          anunciar('');
          mostrarError(`Escuché "${final}", pero no es un número. Prueba otra vez, por ejemplo "ciento veinte".`);
          return;
        }
        anunciar(`Escuché: ${ultimo}`);
        if (Voz.leerSolo) Voz.hablar(`Escuché ${ultimo}`);
      },
      alError: (codigo) => {
        huboError = true;
        setEscuchando(false);
        anunciar('');
        if (codigo !== 'aborted') mostrarError(mensajeErrorDictado(codigo));
      },
    });
  }

  return (
    <button type="button" className={`asistente-mic${escuchando ? ' escuchando' : ''}`} data-tipo={tipo} aria-label="Responder hablando" onClick={dictar}>
      <i className="ti ti-microphone" aria-hidden="true" /><span>{escuchando ? 'Te escucho…' : 'Hablar'}</span>
    </button>
  );
}

export function CampoTexto({ id, etiqueta, valor, ejemplo = '', maximo = 80, sugerencias = [], alCambiar }: {
  id: string; etiqueta: string; valor: string; ejemplo?: string; maximo?: number; sugerencias?: string[]; alCambiar: (valor: string) => void;
}) {
  const campo = useRef<HTMLInputElement>(null);
  return (
    <>
      <label className="asistente-etiqueta" htmlFor={id}>{etiqueta}</label>
      <div className="asistente-campo-fila">
        <input ref={campo} className="asistente-input" type="text" id={id} maxLength={maximo} value={valor} placeholder={ejemplo} autoComplete="off" onChange={(e) => alCambiar(e.target.value)} />
        <BotonDictar tipo="texto" valor={valor} alDictar={alCambiar} />
      </div>
      {sugerencias.length > 0 && (
        <div className="asistente-sugerencias" role="group" aria-label="Respuestas rápidas">
          {sugerencias.map((s) => <button key={s} type="button" className="asistente-chip" onClick={() => { alCambiar(s); campo.current?.focus(); }}>{s}</button>)}
        </div>
      )}
    </>
  );
}

// Número con botones − / + grandes (para no tener que escribir) y micrófono. El valor va como texto: puede estar vacío.
export function CampoNumero({ id, etiqueta, valor, unidad = '', minimo = 0, maximo = 1000000, paso = 1, decimales = true, prefijo = '', alCambiar }: {
  id: string; etiqueta: string; valor: string; unidad?: string; minimo?: number; maximo?: number; paso?: number; decimales?: boolean; prefijo?: string; alCambiar: (valor: string) => void;
}) {
  const sumar = (delta: number) => {
    const nuevo = Math.min(maximo, Math.max(minimo, (Number(valor) || 0) + delta));
    alCambiar(String(Math.round(nuevo * 100) / 100));
  };
  return (
    <>
      <label className="asistente-etiqueta" htmlFor={id}>{etiqueta}</label>
      <div className="asistente-campo-fila asistente-numero">
        <button type="button" className="asistente-paso-num" aria-label={`Menos ${paso}`} onClick={() => sumar(-paso)}><i className="ti ti-minus" aria-hidden="true" /></button>
        <div className="asistente-numero-caja">
          {prefijo && <span className="asistente-numero-prefijo" aria-hidden="true">{prefijo}</span>}
          <input className="asistente-input" type="number" id={id} inputMode={decimales ? 'decimal' : 'numeric'} min={minimo} max={maximo} step={decimales ? 'any' : 1} value={valor} onChange={(e) => alCambiar(e.target.value)} />
          {unidad && <span className="asistente-numero-unidad" aria-hidden="true">{unidad}</span>}
        </div>
        <button type="button" className="asistente-paso-num" aria-label={`Más ${paso}`} onClick={() => sumar(paso)}><i className="ti ti-plus" aria-hidden="true" /></button>
        <BotonDictar tipo="numero" valor={valor} alDictar={alCambiar} />
      </div>
    </>
  );
}

export interface OpcionAsistente { valor: string; titulo: string; detalle?: string; icono?: string }

// Opciones grandes con ícono (radios nativos: se usan con teclado y los anuncia un lector de pantalla).
export function CampoOpciones({ nombre, opciones, valor, alCambiar }: { nombre: string; opciones: OpcionAsistente[]; valor: string | null | undefined; alCambiar: (valor: string) => void }) {
  return (
    <div className="asistente-opciones" role="radiogroup" aria-labelledby="asistente-pregunta">
      {opciones.map((o) => (
        <label className="asistente-opcion" key={o.valor}>
          <input type="radio" name={nombre} value={o.valor} checked={String(o.valor) === String(valor)} onChange={() => alCambiar(o.valor)} />
          <span className="asistente-opcion-caja">
            {o.icono && <span className="asistente-opcion-icono"><i className={`ti ti-${o.icono}`} aria-hidden="true" /></span>}
            <span className="asistente-opcion-texto"><strong>{o.titulo}</strong>{o.detalle && <small>{o.detalle}</small>}</span>
            <i className="ti ti-circle-check asistente-opcion-marca" aria-hidden="true" />
          </span>
        </label>
      ))}
    </div>
  );
}

export interface AtajoFecha { etiqueta: string; fecha: string }

// Fecha con atajos ("Hoy", "Ayer"…) y, para cualquier otra, el calendario del teléfono.
export function CampoFecha({ id, valor, atajos, minimo = '', maximo = '', alCambiar }: { id: string; valor: string; atajos: AtajoFecha[]; minimo?: string; maximo?: string; alCambiar: (valor: string) => void }) {
  return (
    <>
      <div className="asistente-atajos" role="group" aria-label="Elige una fecha">
        {atajos.map((a) => (
          <button key={`${a.etiqueta}-${a.fecha}`} type="button" className={`asistente-atajo ${a.fecha === valor ? 'activo' : ''}`} aria-pressed={a.fecha === valor} onClick={() => alCambiar(a.fecha)}>
            <strong>{a.etiqueta}</strong><small>{fechaLegible(a.fecha)}</small>
          </button>
        ))}
      </div>
      <label className="asistente-etiqueta" htmlFor={id}>O elige otro día en el calendario</label>
      <input className="asistente-input asistente-fecha" type="date" id={id} value={valor} min={minimo || undefined} max={maximo || undefined} onChange={(e) => alCambiar(e.target.value)} />
      <p className="asistente-fecha-elegida" aria-live="polite">{valor ? `Elegiste: ${fechaLegible(valor)}` : ''}</p>
    </>
  );
}

// ---------------------------------------------------------------- el asistente
// El último paso es siempre "Revisa y confirma": lo arma el propio asistente con `resumen(estado)` de cada paso.
const textoDelPaso = <E,>(valor: string | ((e: E) => string) | undefined, estado: E) => (typeof valor === 'function' ? valor(estado) : valor);

export function Asistente<E extends object>({ config }: { config: ConfigAsistente<E> }) {
  const { t } = useIdioma();
  const [estado, setEstado] = useState<E>(config.estadoInicial);
  const [indice, setIndice] = useState(0);
  const [volverAlResumen, setVolverAlResumen] = useState(false);
  const [error, setError] = useState('');
  const [verAyuda, setVerAyuda] = useState(false);
  const [aviso, setAviso] = useState('');
  const [enviando, setEnviando] = useState<string | null>(null);
  const [leerSolo, setLeerSolo] = useState(Voz.leerSolo);
  const estadoActual = useRef(estado);
  estadoActual.current = estado;
  const pregunta = useRef<HTMLHeadingElement>(null);
  const cuerpo = useRef<HTMLDivElement>(null);
  const cajaError = useRef<HTMLParagraphElement>(null);
  const focoPrevio = useRef<Element | null>(null);

  const visibles = config.pasos.filter((p) => !p.omitir?.(estado));
  const total = visibles.length + 1;
  const esUltimo = indice === total - 1;
  const paso = esUltimo ? null : visibles[indice];

  const mostrarError = useCallback((mensaje: string) => {
    setError(mensaje);
    if (mensaje && Voz.leerSolo) Voz.hablar(mensaje);
  }, []);
  const ui = useRef<UiAsistente>({ mostrarError, anunciar: setAviso }).current;

  const cambiar = useCallback((cambio: Cambio<E>, limpiarError = true) => {
    setEstado((anterior) => ({ ...anterior, ...(typeof cambio === 'function' ? cambio(anterior) : cambio) }));
    if (limpiarError) setError('');
  }, []);

  function textoParaLeer(e: E): string {
    const lista = config.pasos.filter((p) => !p.omitir?.(e));
    if (indice >= lista.length) {
      const lineas = lista.filter((p) => p.resumen).map((p) => {
        const r = p.resumen!(e);
        return `${r.etiqueta}: ${r.textoVoz || r.valor}.`;
      });
      return `Revisa y confirma. ${lineas.join(' ')} Si todo está bien, toca ${config.textoFinal}.`;
    }
    const p = lista[indice]!;
    return p.voz ? p.voz(e) : `${textoDelPaso(p.pregunta, e)}. ${textoDelPaso(p.ayuda, e) || ''}`;
  }

  // Al abrir: el foco vuelve después adonde estaba; el fondo no se mueve.
  useLayoutEffect(() => {
    focoPrevio.current = document.activeElement;
    document.body.classList.add('asistente-abierto');
    return () => {
      document.body.classList.remove('asistente-abierto');
      Voz.detenerEscucha();
      Voz.callar();
      const previo = focoPrevio.current as HTMLElement | null;
      if (previo?.isConnected) previo.focus?.();
    };
  }, []);

  // Cada paso: el foco va a la pregunta (un lector de pantalla la anuncia y Tab sigue desde ahí) y se lee en voz alta.
  useEffect(() => {
    setVerAyuda(false);
    pregunta.current?.focus({ preventScroll: true });
    if (cuerpo.current) cuerpo.current.scrollTop = 0;
    if (Voz.leerSolo) Voz.hablar(textoParaLeer(estadoActual.current));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indice]);

  useEffect(() => {
    if (error) cajaError.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); // en el celular puede quedar abajo
  }, [error]);

  function recoger(): E {
    const recogido = paso?.recoger ? { ...estado, ...paso.recoger(estado) } : estado;
    if (recogido !== estado) setEstado(recogido);
    return recogido;
  }

  function irA(nuevo: number) {
    setError('');
    setIndice(nuevo);
  }

  async function siguiente() {
    if (enviando) return;
    Voz.detenerEscucha();
    const recogido = recoger();
    const mensaje = paso?.validar?.(recogido) || '';
    mostrarError(mensaje);
    if (mensaje) return;
    if (!esUltimo) {
      const lista = config.pasos.filter((p) => !p.omitir?.(recogido));
      setVolverAlResumen(false);
      irA(volverAlResumen ? lista.length : indice + 1);
      return;
    }
    setEnviando('');
    try {
      const ok = await config.enviar(recogido, {
        progreso: (texto) => { setEnviando(texto); setAviso(texto); },
        error: mostrarError,
      });
      if (ok) { config.alCerrar(); return; }
    } catch (err) {
      mostrarError((err as Error).message);
    }
    setEnviando(null);
    setAviso('');
  }

  function atras() {
    if (enviando !== null || indice === 0) return;
    Voz.detenerEscucha();
    recoger(); // no se pierde lo escrito al volver
    setVolverAlResumen(false);
    irA(indice - 1);
  }

  function cerrar() {
    if (enviando !== null) return;
    // A mitad de camino se pregunta antes: cerrar sin querer (Escape, la X) no debe borrar lo que ya llenó.
    if (indice > 0 && !window.confirm('¿Quieres salir? Se perderá lo que llenaste.')) return;
    config.alCerrar();
  }

  function alTeclear(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') { e.preventDefault(); cerrar(); return; }
    // Enter en un campo de texto o número avanza (como un formulario); en los botones hace su propia acción.
    if (e.key === 'Enter' && (e.target as HTMLElement).matches('.asistente-input:not(.asistente-fecha)')) {
      e.preventDefault();
      void siguiente();
      return;
    }
    atraparFoco(e);
  }

  const ayuda = paso ? textoDelPaso(paso.ayuda, estado) : 'Mira que todo esté bien. Si algo no está bien, toca "Cambiar" en esa línea.';
  const Contenido = paso?.Contenido;

  return (
    <ContextoAsistente.Provider value={ui}>
      <div className="modal-overlay asistente-overlay" id="modal-asistente" onKeyDown={alTeclear}>
        <div className="asistente" role="dialog" aria-modal="true" aria-labelledby="asistente-pregunta">
          <header className="asistente-cabecera">
            <span className="asistente-titulo"><i className={`ti ti-${config.icono || 'list-check'} asistente-titulo-icono`} aria-hidden="true" /> <span className="asistente-titulo-texto">{config.titulo}</span></span>
            <span className="asistente-contador">Paso {indice + 1} de {total}</span>
            <button type="button" className="btn-asistente-cerrar" aria-label="Cerrar el asistente" onClick={cerrar}><i className="ti ti-x" aria-hidden="true" /></button>
          </header>
          <div className="asistente-progreso" role="progressbar" aria-label="Avance" aria-valuemin={1} aria-valuenow={indice + 1} aria-valuemax={total}>
            {Array.from({ length: total }, (_, i) => <span key={i} className={i < indice ? 'hecho' : i === indice ? 'actual' : ''} />)}
          </div>
          <form className="asistente-form" noValidate onSubmit={(e) => { e.preventDefault(); void siguiente(); }}>
            <div className="asistente-cuerpo" ref={cuerpo}>
              <h2 id="asistente-pregunta" className="asistente-pregunta" tabIndex={-1} ref={pregunta}>{paso ? textoDelPaso(paso.pregunta, estado) : 'Revisa y confirma'}</h2>
              <div className="asistente-voz">
                {Voz.puedeHablar && <button type="button" className="btn-asistente-escuchar" onClick={() => Voz.hablar(textoParaLeer(estado))}><i className="ti ti-volume" aria-hidden="true" /> <Texto clave="asistente.escuchar">Escuchar</Texto></button>}
                {ayuda && (
                  <button type="button" className="btn-asistente-ayuda" aria-expanded={verAyuda} aria-controls="asistente-ayuda" onClick={() => { const abrir = !verAyuda; setVerAyuda(abrir); if (abrir) Voz.hablar(ayuda); }}>
                    <i className="ti ti-help-circle" aria-hidden="true" /> <Texto clave="asistente.ayuda">¿Necesitas ayuda?</Texto>
                  </button>
                )}
              </div>
              <div className={`asistente-ayuda ${verAyuda ? '' : 'hidden'}`} id="asistente-ayuda"><i className="ti ti-bulb" aria-hidden="true" /><p className="asistente-ayuda-texto">{ayuda}</p></div>
              <div className="asistente-contenido">
                {Contenido
                  ? <Contenido key={paso.id} estado={estado} cambiar={cambiar} />
                  : <Revisar pasos={visibles} estado={estado} cambiarTexto={t('asistente.cambiar', 'Cambiar')} alCambiar={(i) => { setVolverAlResumen(true); irA(i); }} />}
              </div>
              <p ref={cajaError} className={`asistente-error ${error ? '' : 'hidden'}`} role="alert">{error && <><i className="ti ti-alert-circle" aria-hidden="true" /><span>{error}</span></>}</p>
              <p className="asistente-estado sr-only" role="status" aria-live="polite">{aviso}</p>
            </div>
            <footer className="asistente-pie">
              <label className={`asistente-voz-lectura ${Voz.puedeHablar ? '' : 'hidden'}`}>
                <input type="checkbox" id="asistente-leer-solo" checked={leerSolo} onChange={(e) => { Voz.leerSolo = e.target.checked; setLeerSolo(e.target.checked); if (!e.target.checked) Voz.callar(); }} />
                {' '}<Texto clave="asistente.leer">Leer las preguntas en voz alta</Texto>
              </label>
              <div className="asistente-botones">
                <button type="button" className={`btn btn-outline btn-asistente-atras ${indice === 0 ? 'invisible' : ''}`} disabled={enviando !== null} onClick={atras}>
                  <i className="ti ti-arrow-left" aria-hidden="true" /> <Texto clave="asistente.atras">Atrás</Texto>
                </button>
                <button type="submit" className={`btn btn-primary btn-asistente-siguiente ${esUltimo ? 'es-final' : ''}`} disabled={enviando !== null}>
                  {enviando ? <><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> {enviando}</>
                    : esUltimo ? <><i className="ti ti-check" aria-hidden="true" /> {config.textoFinal}</>
                      : volverAlResumen ? <>{t('asistente.volver_resumen', 'Volver al resumen')} <i className="ti ti-arrow-back-up" aria-hidden="true" /></>
                        : <>{t('asistente.siguiente', 'Siguiente')} <i className="ti ti-arrow-right" aria-hidden="true" /></>}
                </button>
              </div>
            </footer>
          </form>
        </div>
      </div>
    </ContextoAsistente.Provider>
  );
}

function Revisar<E>({ pasos, estado, cambiarTexto, alCambiar }: { pasos: Paso<E>[]; estado: E; cambiarTexto: string; alCambiar: (i: number) => void }) {
  return (
    <ul className="asistente-resumen">
      {pasos.map((p, i) => {
        if (!p.resumen) return null;
        const r = p.resumen(estado);
        return (
          <li key={p.id} className={r.advertencia ? 'es-advertencia' : ''}>
            <span className="asistente-resumen-icono"><i className={`ti ti-${r.icono || 'point'}`} aria-hidden="true" /></span>
            <span className="asistente-resumen-texto"><small>{r.etiqueta}</small><strong>{r.valor}</strong></span>
            <button type="button" className="asistente-cambiar" aria-label={`Cambiar ${r.etiqueta}`} onClick={() => alCambiar(i)}>{cambiarTexto}</button>
          </li>
        );
      })}
    </ul>
  );
}
