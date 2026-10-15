// Idioma de las pantallas del agricultor: castellano o quechua (runasimi, variedad chanka de Ayacucho). Pedido del usuario
// el 2026-10-15: "agregar quechua para los agricultores, una opción de poder cambiar de idioma".
//
// Cómo funciona: en el HTML el texto queda en castellano dentro de un elemento con data-i18n="clave"; al elegir runasimi
// se cambia su texto por el de TEXTOS.qu (y se marca lang="qu" para el lector de pantalla). Lo que no está en el
// diccionario sigue en castellano. La elección se guarda en este navegador.
//
// PRIMERA VERSIÓN: las traducciones son un borrador que debe revisar un hablante de quechua chanka antes de usarlo con
// agricultores. Están todas aquí, en un solo lugar, para corregirlas sin tocar las pantallas. La voz del asistente
// (lectura y dictado) sigue en castellano: los navegadores no traen voz en quechua.
//
// En el navegador queda en window.Idioma; en node (pruebas), en module.exports.
(function (raiz) {
  const CLAVE_GUARDADA = 'chakrashop.idioma';

  const TEXTOS = {
    qu: {
      // Panel del agricultor
      'panel.titulo': 'Tarpuqpa panelnin',
      'tab.inicio': 'Qallariy',
      'tab.que_sembrar': '¿Imatataq tarpusaq?',
      'tab.siembras': 'Tarpusqaykuna',
      'tab.chacras': 'Chakraykuna',
      'tab.borradores': 'Allichanaykuna',
      'tab.productos': 'Rantikunaykuna',
      'tab.ganancias': 'Qullqiy',
      'tab.economia': 'Qullqi kamachiy',
      'seccion.siembras': 'Tarpusqaykuna, uywasqaykuna',
      'seccion.chacras': 'Chakraykuna',
      'seccion.borradores': 'Manaraq tukusqa rantikunaykuna',
      'seccion.almacen': 'Almacenman apanay pallasqaykuna',
      'seccion.publicados': 'Rikuchisqa rantikunaykuna',
      'seccion.ganancias': 'Qullqiy',
      'seccion.que_sembrar': '¿Imata tarpuyqa allin kanman?',
      'seccion.ventas': 'Rantikusqaykuna',
      'seccion.costos': 'Gastasqayki, ganasqaykipas',
      // Tarjeta de Inicio
      'cta.titulo': '¿Imatapas tarpurqankichu, icha ruraytachu qallarirqanki?',
      'cta.registrar': 'Tarpusqaykita qillqay',
      'cta.chacra_primero': 'Ñawpaqta chakraykita qillqay',
      'cta.que_sembrar': '¿Imata tarpuyqa allin kanman?',
      // Asistente paso a paso
      'asistente.escuchar': 'Uyariy',
      'asistente.ayuda': '¿Yanapayta munankichu?',
      'asistente.leer': 'Tapukuykunata kunkawan ñawinchay',
      'asistente.atras': 'Qipaman',
      'asistente.siguiente': 'Qatiqnin',
      'asistente.volver_resumen': 'Llapanta qawanaman kutiy',
      'asistente.cambiar': 'Tikray',
      // Aviso de que la traducción todavía no está completa
      'idioma.nota': 'Runasimipi qallarichkanchikraq: wakin rimaykuna castellanopi kachkan.',
    },
  };

  const Idioma = {
    idiomas: [{ codigo: 'es', nombre: 'Castellano' }, { codigo: 'qu', nombre: 'Runasimi' }],
    textos: TEXTOS,
    actual: 'es',

    // El texto de `clave` en el idioma elegido; si no hay traducción, el castellano que se pasa.
    t(clave, castellano) {
      return (this.actual !== 'es' && TEXTOS[this.actual]?.[clave]) || castellano;
    },

    // 'es' para cualquier código que no conocemos.
    normalizar(codigo) {
      return codigo && TEXTOS[codigo] ? codigo : 'es';
    },

    leerGuardado() {
      try { return this.normalizar(raiz.localStorage?.getItem(CLAVE_GUARDADA)); } catch { return 'es'; }
    },

    // Cambia el texto de cada [data-i18n] de `contenedor` (guarda el castellano la primera vez para poder volver),
    // muestra los [data-solo-idioma] del idioma elegido y marca el botón del selector.
    traducir(contenedor = raiz.document) {
      if (!contenedor?.querySelectorAll) return;
      contenedor.querySelectorAll('[data-i18n]').forEach((el) => {
        if (el.dataset.i18nEs === undefined) el.dataset.i18nEs = el.textContent;
        const traducido = this.actual !== 'es' && TEXTOS[this.actual]?.[el.dataset.i18n];
        el.textContent = traducido || el.dataset.i18nEs;
        if (traducido) el.setAttribute('lang', this.actual);
        else el.removeAttribute('lang');
      });
      contenedor.querySelectorAll('[data-solo-idioma]').forEach((el) => {
        el.hidden = el.dataset.soloIdioma !== this.actual;
      });
      contenedor.querySelectorAll('[data-idioma]').forEach((b) => {
        b.setAttribute('aria-pressed', String(b.dataset.idioma === this.actual));
      });
    },

    cambiar(codigo) {
      this.actual = this.normalizar(codigo);
      try { raiz.localStorage?.setItem(CLAVE_GUARDADA, this.actual); } catch { /* sin almacenamiento: solo esta vez */ }
      this.traducir();
    },

    // Al cargar la página: el idioma guardado y los botones [data-idioma] (en cualquier parte de la página).
    iniciar() {
      this.actual = this.leerGuardado();
      this.traducir();
      raiz.document.addEventListener('click', (e) => {
        const boton = e.target.closest?.('[data-idioma]');
        if (boton) this.cambiar(boton.dataset.idioma);
      });
    },
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = Idioma;
  else raiz.Idioma = Idioma;
})(typeof window !== 'undefined' ? window : globalThis);
