// Reglas de negocio puras (sin DOM ni llamadas a la API) que usa app.js. Están aparte para poder probarlas con node:
//   node --test "frontend/pruebas/*.test.js"
// En el navegador quedan en window.Reglas; en node, en module.exports.
(function (raiz) {
  const Reglas = {
    // H1 (validación con usuarios del 2026-10-13): cada alimento se mide y se vende solo en sus unidades. Sin lista
    // (alimento no reconocido o producto sin categoría) no se restringe nada.
    opcionesPermitidas(opciones, permitidas) {
      if (!Array.isArray(permitidas) || !permitidas.length) return opciones;
      return opciones.filter((o) => permitidas.includes(o.valor));
    },

    // Unidades de venta adicionales que el agricultor todavía puede agregar a su producto: las del alimento (o todas, si
    // no tiene lista) menos la base y las que ya agregó.
    unidadesParaAgregar(todas, permitidas, base, yaAgregadas = []) {
      const posibles = Array.isArray(permitidas) && permitidas.length ? todas.filter((u) => permitidas.includes(u)) : todas;
      return posibles.filter((u) => u !== base && !yaAgregadas.includes(u));
    },

    // La unidad con que se mide la cosecha al cambiar de cultivo: la que ya eligió si sigue permitida; si no, la típica
    // del alimento, o la primera permitida.
    unidadDeCosecha(actual, tipica, permitidas) {
      if (!Array.isArray(permitidas) || !permitidas.length) return actual || tipica || 'kg';
      if (actual && permitidas.includes(actual)) return actual;
      return permitidas.includes(tipica) ? tipica : permitidas[0];
    },

    // H3: lo que ya se anotó de una siembra (sus gastos por etapa), para no volver a pedirlo al cosechar. Solo las
    // etapas con gasto; `final` marca las del final (cosecha), donde lo nuevo se suma a lo ya anotado.
    gastosYaRegistrados(etapas) {
      const conGasto = (etapas || []).filter((e) => Number(e.total) > 0)
        .map((e) => ({ clave: e.clave, nombre: e.nombre, total: Number(e.total), final: e.momento === 'final' }));
      const total = Math.round(conGasto.reduce((s, e) => s + e.total, 0) * 100) / 100;
      return { total, etapas: conGasto, enElFinal: conGasto.filter((e) => e.final) };
    },

    // H5: la respuesta de "¿qué me conviene sembrar?" en una sola frase, con la primera del ranking (su puntaje, cuánto
    // se gana por kg y el riesgo de que sobre). El porqué va debajo, con sus frases.
    fraseResumenSiembra(f, moneda = (n) => `S/ ${Number(n).toFixed(2)}`) {
      if (!f || !f.nombre) return null;
      const nivel = f.puntaje >= 70 ? 'conviene mucho' : f.puntaje >= 50 ? 'conviene' : f.puntaje >= 35 ? 'puede ser' : 'conviene poco';
      const riesgo = { bajo: 'hay poco riesgo de que sobre', medio: 'hay algo de riesgo de que sobre', alto: 'hay mucho riesgo de que sobre' }[f.riesgo];
      const ganancia = f.margen_kg == null ? 'todavía falta saber cuánto cuesta producirlo'
        : `${f.margen_kg >= 0 ? 'ganas' : 'pierdes'} ${moneda(Math.abs(f.margen_kg))} por kg`;
      const inicio = f.puntaje >= 35 ? `Lo que más te conviene sembrar ahora es ${f.nombre}`
        : `Ahora ningún cultivo conviene mucho; el mejor es ${f.nombre}`;
      return `${inicio} (${f.puntaje} de 100, ${nivel}): ${ganancia}${riesgo ? ` y ${riesgo}` : ''}.`;
    },

    // H7: lo que se manda al reservar. Vacío = solo el aviso (como antes); si escribe algo, tiene que ser mayor que 0.
    cantidadDeReserva(texto, unidad) {
      const limpio = String(texto ?? '').trim().replace(',', '.');
      if (!limpio) return { datos: null, error: null };
      const cantidad = Number(limpio);
      if (!Number.isFinite(cantidad) || cantidad <= 0) return { datos: null, error: 'Escribe cuánto te interesa con un número mayor que 0, o déjalo vacío.' };
      return { datos: { cantidad, unidad: unidad || null }, error: null };
    },

    // H6: la clave pública VAPID (base64url, como la da el servidor) en bytes, como la pide pushManager.subscribe.
    claveVapidABytes(base64url) {
      const texto = String(base64url || '');
      const binario = atob((texto + '='.repeat((4 - (texto.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
      return Uint8Array.from(binario, (c) => c.charCodeAt(0));
    },

    // H6: ¿la suscripción del navegador es con la clave que tiene hoy el servidor? (si se cambiaron las claves, hay que
    // volver a suscribirse; si no, el navegador rechaza la suscripción nueva).
    mismaClaveVapid(bytesDelNavegador, base64url) {
      if (!bytesDelNavegador) return false;
      const a = new Uint8Array(bytesDelNavegador);
      const b = Reglas.claveVapidABytes(base64url);
      return a.length === b.length && a.every((x, i) => x === b[i]);
    },

    // H6: qué dice la fila "En este celular" de la tarjeta de avisos. En iPhone los avisos solo existen con ChakraShop
    // agregado a la pantalla de inicio, por eso se mira antes que el soporte del navegador.
    estadoAvisosCelular({ configurado, soportado, iphoneSinInstalar, permiso, suscrito }) {
      if (!configurado) return 'apagado-servidor';
      if (iphoneSinInstalar) return 'instalar-iphone';
      if (!soportado) return 'no-soportado';
      if (permiso === 'denied') return 'bloqueado';
      return suscrito && permiso === 'granted' ? 'activo' : 'inactivo';
    },
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = Reglas;
  else raiz.Reglas = Reglas;
})(typeof window !== 'undefined' ? window : globalThis);
