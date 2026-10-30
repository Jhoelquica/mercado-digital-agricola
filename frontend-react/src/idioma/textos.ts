// Traducciones de las pantallas del agricultor: castellano o quechua (runasimi, variedad chanka de Ayacucho). Pedido del
// usuario el 2026-10-15. En cada pantalla el texto queda en castellano; lo que no está aquí sigue en castellano.
//
// PRIMERA VERSIÓN: las traducciones son un borrador que debe revisar un hablante de quechua chanka antes de usarlo con
// agricultores. Están todas aquí, en un solo lugar, para corregirlas sin tocar las pantallas. La voz del asistente
// (lectura y dictado) sigue en castellano: los navegadores no traen voz en quechua.
export const TEXTOS: Record<'qu', Record<string, string>> = {
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
