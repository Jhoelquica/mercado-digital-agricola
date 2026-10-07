// ============ ASISTENTE PASO A PASO CON VOZ ============
// Para los formularios del agricultor (registrar la siembra, completar la cosecha), pensados para personas mayores de
// 65 años (presentación del 2026-10-01): una pregunta por pantalla, letra y botones grandes, y voz en las dos
// direcciones: la app lee la pregunta en voz alta y el agricultor puede responder hablando (Web Speech API del
// navegador; el dictado necesita internet y funciona en Chrome y Edge). Nada depende de la voz: todo se puede hacer
// tocando. Este archivo es genérico: los pasos de cada asistente están en app.js (asistenteSiembra, asistenteCosecha).

// ---------------------------------------------------------------- voz
const Voz = (() => {
  // Se busca al usarlo (no al cargar): así una prueba puede simularlo, y da igual en el uso normal.
  const reconocimiento = () => window.SpeechRecognition || window.webkitSpeechRecognition;
  const CLAVE_LECTURA = 'agro_asistente_leer';
  let voces = [];
  let reconocimientoActivo = null;

  function cargarVoces() {
    if ('speechSynthesis' in window) voces = window.speechSynthesis.getVoices();
  }
  cargarVoces();
  if ('speechSynthesis' in window) window.speechSynthesis.addEventListener?.('voiceschanged', cargarVoces);

  // Una voz en español, mejor de Latinoamérica (más cercana a como se habla en Ayacucho) que de España.
  function vozEnEspanol() {
    const preferidas = ['es-PE', 'es-419', 'es-US', 'es-MX', 'es-CO', 'es-AR', 'es-CL', 'es-ES'];
    for (const idioma of preferidas) {
      const voz = voces.find((v) => v.lang?.replace('_', '-').toLowerCase() === idioma.toLowerCase());
      if (voz) return voz;
    }
    return voces.find((v) => v.lang?.toLowerCase().startsWith('es')) || null;
  }

  return {
    puedeHablar: 'speechSynthesis' in window,
    get puedeEscuchar() { return Boolean(reconocimiento()); },

    // Si la pregunta se lee sola al cambiar de paso. Por defecto sí: es lo que se pidió para los agricultores.
    get leerSolo() {
      try { return localStorage.getItem(CLAVE_LECTURA) !== '0'; } catch { return true; }
    },
    set leerSolo(valor) {
      try { localStorage.setItem(CLAVE_LECTURA, valor ? '1' : '0'); } catch { /* sin almacenamiento: vale para esta visita */ }
    },

    // encolar: se lee después de lo que se está leyendo (p. ej. una sugerencia que llega mientras se lee la pregunta).
    hablar(texto, { encolar = false } = {}) {
      if (!this.puedeHablar || !texto) return;
      if (!encolar) window.speechSynthesis.cancel();
      const frase = new SpeechSynthesisUtterance(texto);
      const voz = vozEnEspanol();
      if (voz) frase.voice = voz;
      frase.lang = voz?.lang || 'es-PE';
      frase.rate = 0.92; // un poco más lento que lo normal: se entiende mejor
      window.speechSynthesis.speak(frase);
    },

    callar() {
      if (this.puedeHablar) window.speechSynthesis.cancel();
    },

    // Escucha una frase y la devuelve por alTexto (también los resultados parciales, para mostrarlos mientras habla).
    escuchar({ alTexto, alTerminar, alError }) {
      if (!this.puedeEscuchar) { alError?.('sin-soporte'); return null; }
      this.detenerEscucha();
      this.callar(); // que no se dicte a sí misma
      const Reconocimiento = reconocimiento();
      const rec = new Reconocimiento();
      rec.lang = 'es-PE';
      rec.interimResults = true;
      rec.maxAlternatives = 1;
      rec.continuous = false;
      let final = '';
      rec.onresult = (e) => {
        let parcial = '';
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) final += e.results[i][0].transcript;
          else parcial += e.results[i][0].transcript;
        }
        alTexto?.((final || parcial).trim(), Boolean(final));
      };
      rec.onerror = (e) => alError?.(e.error);
      rec.onend = () => {
        if (reconocimientoActivo === rec) reconocimientoActivo = null;
        alTerminar?.(final.trim());
      };
      reconocimientoActivo = rec;
      rec.start();
      return rec;
    },

    detenerEscucha() {
      if (reconocimientoActivo) {
        const rec = reconocimientoActivo;
        reconocimientoActivo = null;
        try { rec.abort(); } catch { /* ya terminó */ }
      }
    },
  };
})();

function mensajeErrorDictado(codigo) {
  return {
    'not-allowed': 'No diste permiso para usar el micrófono. Puedes escribir la respuesta, o darlo en el candado junto a la dirección de la página.',
    'service-not-allowed': 'Este navegador no deja usar el micrófono aquí. Puedes escribir la respuesta.',
    'no-speech': 'No te escuché. Toca el micrófono y habla cerca del teléfono.',
    'audio-capture': 'No se encontró un micrófono. Puedes escribir la respuesta.',
    network: 'Para dictar se necesita internet. Puedes escribir la respuesta.',
    'sin-soporte': 'Este navegador no puede dictar. Puedes escribir la respuesta (en Chrome sí se puede dictar).',
  }[codigo] || 'No se pudo usar el micrófono. Puedes escribir la respuesta.';
}

// ---------------------------------------------------------------- números dichos en voz alta
// El dictado a veces trae cifras ("120") y a veces palabras ("ciento veinte", "dos y medio"). Devuelve el número o null.
const NUMEROS_EN_PALABRAS = {
  cero: 0, un: 1, uno: 1, una: 1, dos: 2, tres: 3, cuatro: 4, cinco: 5, seis: 6, siete: 7, ocho: 8, nueve: 9,
  diez: 10, once: 11, doce: 12, trece: 13, catorce: 14, quince: 15, dieciseis: 16, diecisiete: 17, dieciocho: 18,
  diecinueve: 19, veinte: 20, veintiun: 21, veintiuno: 21, veintiuna: 21, veintidos: 22, veintitres: 23,
  veinticuatro: 24, veinticinco: 25, veintiseis: 26, veintisiete: 27, veintiocho: 28, veintinueve: 29, treinta: 30,
  cuarenta: 40, cincuenta: 50, sesenta: 60, setenta: 70, ochenta: 80, noventa: 90, cien: 100, ciento: 100,
  doscientos: 200, doscientas: 200, trescientos: 300, trescientas: 300, cuatrocientos: 400, cuatrocientas: 400,
  quinientos: 500, quinientas: 500, seiscientos: 600, seiscientas: 600, setecientos: 700, setecientas: 700,
  ochocientos: 800, ochocientas: 800, novecientos: 900, novecientas: 900,
};

function textoANumero(texto) {
  const limpio = String(texto || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim();
  if (!limpio) return null;
  const medio = /\by (medio|media)\b/.test(limpio) ? 0.5 : 0;

  const cifra = limpio.match(/\d[\d.,]*/);
  if (cifra) {
    let valor = cifra[0].replace(/[.,]$/, '');
    if (/^\d{1,3}([.,]\d{3})+$/.test(valor)) valor = valor.replace(/[.,]/g, ''); // 1.500 o 1,500 = mil quinientos
    else valor = valor.replace(',', '.');
    const numero = Number(valor);
    return Number.isFinite(numero) ? numero + medio : null;
  }

  let total = 0;
  let actual = 0;
  let decimales = null;
  let encontrado = false;
  for (const palabra of limpio.split(/[\s-]+/)) {
    if (palabra === 'punto' || palabra === 'coma') { decimales = ''; continue; }
    if (palabra === 'mil') { actual = (actual || 1) * 1000; total += actual; actual = 0; encontrado = true; continue; }
    if (palabra in NUMEROS_EN_PALABRAS) {
      encontrado = true;
      if (decimales !== null) decimales += String(NUMEROS_EN_PALABRAS[palabra]);
      else actual += NUMEROS_EN_PALABRAS[palabra];
    }
  }
  if (!encontrado) return null;
  const entero = total + actual;
  return (decimales ? Number(`${entero}.${decimales}`) : entero) + medio;
}

// ---------------------------------------------------------------- piezas de los pasos
function escaparAsistente(valor) {
  return String(valor ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Botón de micrófono que dicta en el campo `para` (id del input). tipo: 'texto' o 'numero'.
function botonDictar(para, tipo = 'texto') {
  if (!Voz.puedeEscuchar) return '';
  return `
    <button type="button" class="asistente-mic" data-dictar="${escaparAsistente(para)}" data-tipo="${tipo}" aria-label="Responder hablando">
      <i class="ti ti-microphone" aria-hidden="true"></i><span>Hablar</span>
    </button>`;
}

function campoTextoAsistente({ id, etiqueta, valor = '', ejemplo = '', maximo = 80, sugerencias = [] }) {
  return `
    <label class="asistente-etiqueta" for="${id}">${escaparAsistente(etiqueta)}</label>
    <div class="asistente-campo-fila">
      <input class="asistente-input" type="text" id="${id}" maxlength="${maximo}" value="${escaparAsistente(valor)}"
        placeholder="${escaparAsistente(ejemplo)}" autocomplete="off">
      ${botonDictar(id, 'texto')}
    </div>
    ${sugerencias.length ? `
      <div class="asistente-sugerencias" role="group" aria-label="Respuestas rápidas">
        ${sugerencias.map((s) => `<button type="button" class="asistente-chip" data-llenar="${id}" data-valor="${escaparAsistente(s)}">${escaparAsistente(s)}</button>`).join('')}
      </div>` : ''}`;
}

// Número con botones − / + grandes (para no tener que escribir) y micrófono.
function campoNumeroAsistente({ id, etiqueta, valor = '', unidad = '', minimo = 0, maximo = 1000000, paso = 1, decimales = true, prefijo = '' }) {
  return `
    <label class="asistente-etiqueta" for="${id}">${escaparAsistente(etiqueta)}</label>
    <div class="asistente-campo-fila asistente-numero">
      <button type="button" class="asistente-paso-num" data-numero="${id}" data-delta="-${paso}" aria-label="Menos ${paso}"><i class="ti ti-minus" aria-hidden="true"></i></button>
      <div class="asistente-numero-caja">
        ${prefijo ? `<span class="asistente-numero-prefijo" aria-hidden="true">${escaparAsistente(prefijo)}</span>` : ''}
        <input class="asistente-input" type="number" id="${id}" inputmode="${decimales ? 'decimal' : 'numeric'}"
          min="${minimo}" max="${maximo}" step="${decimales ? 'any' : 1}" value="${escaparAsistente(valor)}">
        ${unidad ? `<span class="asistente-numero-unidad" aria-hidden="true">${escaparAsistente(unidad)}</span>` : ''}
      </div>
      <button type="button" class="asistente-paso-num" data-numero="${id}" data-delta="${paso}" aria-label="Más ${paso}"><i class="ti ti-plus" aria-hidden="true"></i></button>
      ${botonDictar(id, 'numero')}
    </div>`;
}

// Opciones grandes con ícono (radios nativos: se usan con teclado y los anuncia un lector de pantalla).
function campoOpcionesAsistente({ nombre, opciones, valor }) {
  return `
    <div class="asistente-opciones" role="radiogroup" aria-labelledby="asistente-pregunta">
      ${opciones.map((o) => `
        <label class="asistente-opcion">
          <input type="radio" name="${nombre}" value="${escaparAsistente(o.valor)}" ${String(o.valor) === String(valor) ? 'checked' : ''}>
          <span class="asistente-opcion-caja">
            ${o.icono ? `<span class="asistente-opcion-icono"><i class="ti ti-${o.icono}" aria-hidden="true"></i></span>` : ''}
            <span class="asistente-opcion-texto"><strong>${escaparAsistente(o.titulo)}</strong>${o.detalle ? `<small>${escaparAsistente(o.detalle)}</small>` : ''}</span>
            <i class="ti ti-circle-check asistente-opcion-marca" aria-hidden="true"></i>
          </span>
        </label>`).join('')}
    </div>`;
}

function fechaIso(fecha) {
  const local = new Date(fecha.getTime() - fecha.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function sumarDias(fecha, dias) {
  const nueva = new Date(fecha);
  nueva.setDate(nueva.getDate() + dias);
  return nueva;
}

function fechaLegible(iso) {
  if (!iso) return '—';
  const [a, m, d] = iso.split('-').map(Number);
  return new Date(a, m - 1, d).toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

// Fecha con atajos ("Hoy", "Ayer"…) y, para cualquier otra, el calendario del teléfono.
function campoFechaAsistente({ id, valor = '', atajos, minimo = '', maximo = '' }) {
  return `
    <div class="asistente-atajos" role="group" aria-label="Elige una fecha">
      ${atajos.map((a) => `
        <button type="button" class="asistente-atajo ${a.fecha === valor ? 'activo' : ''}" data-fecha="${id}" data-valor="${a.fecha}" aria-pressed="${a.fecha === valor}">
          <strong>${escaparAsistente(a.etiqueta)}</strong><small>${escaparAsistente(fechaLegible(a.fecha))}</small>
        </button>`).join('')}
    </div>
    <label class="asistente-etiqueta" for="${id}">O elige otro día en el calendario</label>
    <input class="asistente-input asistente-fecha" type="date" id="${id}" value="${escaparAsistente(valor)}"
      ${minimo ? `min="${minimo}"` : ''} ${maximo ? `max="${maximo}"` : ''}>
    <p class="asistente-fecha-elegida" aria-live="polite">${valor ? `Elegiste: ${escaparAsistente(fechaLegible(valor))}` : ''}</p>`;
}

// ---------------------------------------------------------------- el asistente
// config = { titulo, icono, pasos: [{ id, pregunta, ayuda, voz?(estado), render(estado), alMostrar?(cont, estado),
//            recoger?(cont, estado), validar?(estado) -> mensaje|null, resumen?(estado) }], textoFinal,
//            enviar(estado, ui) -> true si terminó, estado, alClic?(e, estado), alCambiar?(e, estado), alCerrar?(terminado) }
// El último paso es siempre "Revisa y confirma": lo arma el propio asistente con `resumen(estado)` de cada paso.
const Asistente = (() => {
  let config = null;
  let estado = null;
  let indice = 0;
  let volverAlResumen = false;
  let focoPrevio = null;
  let enviando = false;

  const $ = (sel) => document.querySelector(`#modal-asistente ${sel}`);

  // La pregunta y la ayuda pueden ser texto o una función del estado (p. ej. "¿Cuándo sembraste?" o "¿Cuándo empezó el
  // periodo?" según lo que produce).
  function textoDelPaso(valor) {
    return typeof valor === 'function' ? valor(estado) : valor;
  }

  // Un paso con omitir(estado) = true no se muestra (p. ej. las parcelas cuando se elabora un queso).
  function visibles() {
    return config.pasos.filter((p) => !p.omitir?.(estado));
  }

  function pasos() {
    return [...visibles(), {
      id: 'revisar',
      pregunta: 'Revisa y confirma',
      ayuda: 'Mira que todo esté bien. Si algo no está bien, toca "Cambiar" en esa línea.',
      render: renderRevisar,
    }];
  }

  function textoParaLeer(paso) {
    if (paso.id === 'revisar') {
      const lineas = visibles().filter((p) => p.resumen).map((p) => {
        const r = p.resumen(estado);
        return `${r.etiqueta}: ${r.textoVoz || r.valor}.`;
      });
      return `Revisa y confirma. ${lineas.join(' ')} Si todo está bien, toca ${config.textoFinal}.`;
    }
    return paso.voz ? paso.voz(estado) : `${textoDelPaso(paso.pregunta)}. ${textoDelPaso(paso.ayuda) || ''}`;
  }

  function renderRevisar() {
    return `
      <ul class="asistente-resumen">
        ${visibles().map((p, i) => {
          if (!p.resumen) return '';
          const r = p.resumen(estado);
          return `
            <li class="${r.advertencia ? 'es-advertencia' : ''}">
              <span class="asistente-resumen-icono"><i class="ti ti-${r.icono || 'point'}" aria-hidden="true"></i></span>
              <span class="asistente-resumen-texto"><small>${escaparAsistente(r.etiqueta)}</small><strong>${escaparAsistente(r.valor)}</strong></span>
              <button type="button" class="asistente-cambiar" data-ir-paso="${i}" aria-label="Cambiar ${escaparAsistente(r.etiqueta)}">Cambiar</button>
            </li>`;
        }).join('')}
      </ul>`;
  }

  function mostrarError(mensaje) {
    const el = $('.asistente-error');
    el.innerHTML = mensaje ? `<i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escaparAsistente(mensaje)}</span>` : '';
    el.classList.toggle('hidden', !mensaje);
    if (mensaje) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); // en el celular puede quedar abajo
    if (mensaje && Voz.leerSolo) Voz.hablar(mensaje);
  }

  function pintar() {
    const lista = pasos();
    const paso = lista[indice];
    const total = lista.length;
    $('.asistente-contador').textContent = `Paso ${indice + 1} de ${total}`;
    $('.asistente-progreso').innerHTML = lista.map((_, i) =>
      `<span class="${i < indice ? 'hecho' : i === indice ? 'actual' : ''}"></span>`).join('');
    $('.asistente-progreso').setAttribute('aria-valuenow', String(indice + 1));
    $('.asistente-progreso').setAttribute('aria-valuemax', String(total));
    const pregunta = $('#asistente-pregunta');
    pregunta.textContent = textoDelPaso(paso.pregunta);
    $('.asistente-ayuda-texto').textContent = textoDelPaso(paso.ayuda) || '';
    $('.asistente-ayuda').classList.add('hidden');
    $('.btn-asistente-ayuda').setAttribute('aria-expanded', 'false');
    $('.btn-asistente-ayuda').classList.toggle('hidden', !textoDelPaso(paso.ayuda));
    $('.asistente-contenido').innerHTML = paso.render(estado);
    mostrarError('');
    const esUltimo = indice === total - 1;
    $('.btn-asistente-atras').classList.toggle('invisible', indice === 0);
    const siguiente = $('.btn-asistente-siguiente');
    siguiente.innerHTML = esUltimo
      ? `<i class="ti ti-check" aria-hidden="true"></i> ${escaparAsistente(config.textoFinal)}`
      : volverAlResumen
        ? 'Volver al resumen <i class="ti ti-arrow-back-up" aria-hidden="true"></i>'
        : 'Siguiente <i class="ti ti-arrow-right" aria-hidden="true"></i>';
    siguiente.classList.toggle('es-final', esUltimo);
    paso.alMostrar?.($('.asistente-contenido'), estado);
    // El foco va a la pregunta: un lector de pantalla la anuncia y Tab sigue desde ahí.
    pregunta.focus({ preventScroll: true });
    $('.asistente-cuerpo').scrollTop = 0;
    if (Voz.leerSolo) Voz.hablar(textoParaLeer(paso));
  }

  function recogerYValidar() {
    const paso = pasos()[indice];
    paso.recoger?.($('.asistente-contenido'), estado);
    const error = paso.validar?.(estado);
    mostrarError(error || '');
    return !error;
  }

  async function siguiente() {
    if (enviando) return;
    Voz.detenerEscucha();
    const lista = pasos();
    if (!recogerYValidar()) return;
    if (indice === lista.length - 1) {
      enviando = true;
      const boton = $('.btn-asistente-siguiente');
      const textoBoton = boton.innerHTML;
      boton.disabled = true;
      $('.btn-asistente-atras').disabled = true;
      const ui = {
        progreso: (texto) => {
          boton.innerHTML = `<i class="ti ti-loader-2 icono-girando" aria-hidden="true"></i> ${escaparAsistente(texto)}`;
          $('.asistente-estado').textContent = texto;
        },
        error: mostrarError,
      };
      try {
        const ok = await config.enviar(estado, ui);
        if (ok) cerrar(true);
      } finally {
        enviando = false;
        boton.disabled = false;
        boton.innerHTML = textoBoton;
        $('.btn-asistente-atras').disabled = false;
        $('.asistente-estado').textContent = '';
      }
      return;
    }
    indice = volverAlResumen ? lista.length - 1 : indice + 1;
    volverAlResumen = false;
    pintar();
  }

  function atras() {
    if (enviando || indice === 0) return;
    Voz.detenerEscucha();
    pasos()[indice].recoger?.($('.asistente-contenido'), estado); // no se pierde lo escrito al volver
    volverAlResumen = false;
    indice -= 1;
    pintar();
  }

  function irAPaso(i) {
    volverAlResumen = true;
    indice = i;
    pintar();
  }

  function cerrar(terminado = false) {
    if (!config || (enviando && !terminado)) return;
    // A mitad de camino se pregunta antes: cerrar sin querer (Escape, la X) no debe borrar lo que ya llenó.
    if (!terminado && indice > 0 && !window.confirm('¿Quieres salir? Se perderá lo que llenaste.')) return;
    Voz.detenerEscucha();
    Voz.callar();
    document.getElementById('modal-asistente').classList.add('hidden');
    document.body.classList.remove('asistente-abierto');
    const alCerrar = config?.alCerrar;
    config = null;
    if (focoPrevio?.isConnected) focoPrevio.focus?.();
    focoPrevio = null;
    alCerrar?.(terminado);
  }

  // ---- dictado: el micrófono llena el campo; para números, convierte lo dicho ("ciento veinte") a cifra.
  function dictar(boton) {
    const campo = document.getElementById(boton.dataset.dictar);
    if (!campo) return;
    if (boton.classList.contains('escuchando')) { Voz.detenerEscucha(); return; } // tocar otra vez = dejar de escuchar
    const estadoVoz = $('.asistente-estado');
    const antes = campo.value;
    let huboError = false;
    boton.classList.add('escuchando');
    boton.querySelector('span').textContent = 'Te escucho…';
    estadoVoz.textContent = 'Te escucho. Habla ahora.';
    mostrarError('');
    Voz.escuchar({
      alTexto: (texto) => {
        if (boton.dataset.tipo === 'numero') {
          const numero = textoANumero(texto);
          if (numero !== null) campo.value = String(numero);
        } else {
          campo.value = texto.replace(/[.。]$/, '').replace(/^./, (c) => c.toUpperCase());
        }
      },
      alTerminar: (final) => {
        boton.classList.remove('escuchando');
        boton.querySelector('span').textContent = 'Hablar';
        if (huboError) return; // el mensaje ya lo puso alError (y no se debe borrar)
        if (!final) { // terminó sin oír nada (silencio)
          estadoVoz.textContent = '';
          mostrarError(mensajeErrorDictado('no-speech'));
          return;
        }
        if (boton.dataset.tipo === 'numero' && textoANumero(final) === null) {
          campo.value = antes;
          estadoVoz.textContent = '';
          mostrarError(`Escuché "${final}", pero no es un número. Prueba otra vez, por ejemplo "ciento veinte".`);
          return;
        }
        estadoVoz.textContent = `Escuché: ${campo.value}`;
        campo.dispatchEvent(new Event('input', { bubbles: true }));
        if (Voz.leerSolo) Voz.hablar(`Escuché ${campo.value}`);
      },
      alError: (codigo) => {
        huboError = true;
        boton.classList.remove('escuchando');
        boton.querySelector('span').textContent = 'Hablar';
        estadoVoz.textContent = '';
        if (codigo !== 'aborted') mostrarError(mensajeErrorDictado(codigo));
      },
    });
  }

  function alHacerClic(e) {
    const t = e.target;
    if (t.closest('.btn-asistente-cerrar')) { cerrar(); return; }
    if (t.closest('.btn-asistente-atras')) { atras(); return; }
    if (t.closest('.btn-asistente-escuchar')) { Voz.hablar(textoParaLeer(pasos()[indice])); return; }
    const ayuda = t.closest('.btn-asistente-ayuda');
    if (ayuda) {
      const caja = $('.asistente-ayuda');
      const abrir = caja.classList.contains('hidden');
      caja.classList.toggle('hidden', !abrir);
      ayuda.setAttribute('aria-expanded', String(abrir));
      if (abrir) Voz.hablar(textoDelPaso(pasos()[indice].ayuda));
      return;
    }
    const mic = t.closest('.asistente-mic');
    if (mic) { dictar(mic); return; }
    const chip = t.closest('.asistente-chip');
    if (chip) {
      const campo = document.getElementById(chip.dataset.llenar);
      campo.value = chip.dataset.valor;
      campo.dispatchEvent(new Event('input', { bubbles: true }));
      campo.focus();
      return;
    }
    const masMenos = t.closest('.asistente-paso-num');
    if (masMenos) {
      const campo = document.getElementById(masMenos.dataset.numero);
      const minimo = Number(campo.min || 0);
      const maximo = Number(campo.max || Infinity);
      const nuevo = Math.min(maximo, Math.max(minimo, (Number(campo.value) || 0) + Number(masMenos.dataset.delta)));
      campo.value = String(Math.round(nuevo * 100) / 100);
      campo.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    const atajo = t.closest('.asistente-atajo');
    if (atajo) {
      const campo = document.getElementById(atajo.dataset.fecha);
      campo.value = atajo.dataset.valor;
      campo.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    const cambiar = t.closest('.asistente-cambiar');
    if (cambiar) { irAPaso(Number(cambiar.dataset.irPaso)); return; }
    config?.alClic?.(e, estado); // botones propios de un paso (p. ej. "Tomar mi ubicación")
  }

  function alEscribir(e) {
    const fecha = e.target.closest('.asistente-fecha');
    if (fecha) {
      document.querySelectorAll(`#modal-asistente .asistente-atajo[data-fecha="${fecha.id}"]`).forEach((b) => {
        const activo = b.dataset.valor === fecha.value;
        b.classList.toggle('activo', activo);
        b.setAttribute('aria-pressed', String(activo));
      });
      const elegida = $('.asistente-fecha-elegida');
      if (elegida) elegida.textContent = fecha.value ? `Elegiste: ${fechaLegible(fecha.value)}` : '';
    }
    if (e.target.matches('input') && !e.target.closest('.asistente-voz-lectura')) mostrarError('');
  }

  function alTeclear(e) {
    if (e.key === 'Escape') { e.preventDefault(); cerrar(); return; }
    // Enter en un campo de texto o número avanza (como un formulario); en los botones hace su propia acción.
    if (e.key === 'Enter' && e.target.matches('.asistente-input:not(.asistente-fecha)')) {
      e.preventDefault();
      siguiente();
      return;
    }
    if (typeof atraparFoco === 'function') atraparFoco(e, $('.asistente'));
  }

  let enlazado = false;
  function enlazar() {
    if (enlazado) return;
    enlazado = true;
    const modal = document.getElementById('modal-asistente');
    modal.addEventListener('click', alHacerClic);
    modal.addEventListener('input', alEscribir);
    modal.addEventListener('change', (e) => config?.alCambiar?.(e, estado));
    modal.addEventListener('keydown', alTeclear);
    modal.querySelector('.asistente-form').addEventListener('submit', (e) => { e.preventDefault(); siguiente(); });
    const lectura = modal.querySelector('#asistente-leer-solo');
    lectura.addEventListener('change', () => {
      Voz.leerSolo = lectura.checked;
      if (!lectura.checked) Voz.callar();
    });
  }

  return {
    abrir(nuevaConfig) {
      enlazar();
      config = nuevaConfig;
      estado = nuevaConfig.estado || {};
      indice = 0;
      volverAlResumen = false;
      enviando = false;
      focoPrevio = document.activeElement;
      $('.asistente-titulo-texto').textContent = config.titulo;
      $('.asistente-titulo-icono').className = `ti ti-${config.icono || 'list-check'} asistente-titulo-icono`;
      const lectura = document.getElementById('asistente-leer-solo');
      lectura.checked = Voz.leerSolo;
      lectura.closest('.asistente-voz-lectura').classList.toggle('hidden', !Voz.puedeHablar);
      $('.btn-asistente-escuchar').classList.toggle('hidden', !Voz.puedeHablar);
      document.getElementById('modal-asistente').classList.remove('hidden');
      document.body.classList.add('asistente-abierto');
      pintar();
    },
    cerrar,
    // Para los pasos que necesitan repintarse solos (p. ej. al elegir una opción que muestra otro campo).
    repintar() { if (config) pintar(); },
    get abierto() { return Boolean(config); },
  };
})();
