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

    // H8: el transporte que contrata el admin. Mismas reglas que el servidor (transporte/main.py, contratar_transporte);
    // devuelve el campo con problema para mostrar el error junto a él.
    datosTransporteContratado(campos, { pesoKg = null, sacos = null } = {}) {
      const error = (campo, texto) => ({ datos: null, campo, error: texto });
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
        datos: { nombre, telefono, placa, capacidad_kg: capacidadKg, capacidad_sacos: capacidadSacos,
          fecha_programada: String(campos.fecha).slice(0, 16), flete },
        campo: null,
        error: null,
      };
    },

    // Mano de obra como se cuenta en el campo (validación con usuarios del 2026-10-14, A04: "no pregunta cuántos peones
    // trabajaron; además ... acepta en decimales"): peones enteros, días con decimales (medio día = 0,5) y el pago de
    // cada peón por día. Los tres vacíos = sin gasto; a medias = error que dice qué falta. Mismas reglas que el servidor
    // (productores/main.py, _gasto_validado: los días se guardan con 2 decimales).
    manoDeObra({ peones, dias, pago }) {
      const texto = (v) => String(v ?? '').trim();
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
    },

    // Validación con usuarios del 2026-10-14 (A05: "no es útil que me sugiera cosechar en 3 o 4 meses la Kiwicha"): los
    // atajos de la fecha de cosecha salen del rango de ESE cultivo (lo más pronto, lo más probable, lo más tarde), no de
    // unos meses fijos. Sin sugerencia (cultivo que no conocemos), [] y la pantalla deja los atajos por meses.
    atajosDeCosecha(s) {
      if (!s || !s.fecha_sugerida) return [];
      const atajos = [
        { etiqueta: 'Lo más pronto', fecha: s.fecha_desde },
        { etiqueta: 'Lo más probable', fecha: s.fecha_sugerida },
        { etiqueta: 'Lo más tarde', fecha: s.fecha_hasta },
      ].filter((a) => a.fecha);
      return atajos.filter((a, i) => atajos.findIndex((b) => b.fecha === a.fecha) === i);
    },

    // H6 (validación con usuarios del 2026-10-14, A04/A05: "que me sugieran precio"): el precio sugerido al publicar es
    // el precio de referencia por kg de la tienda (el mismo de "Mis siembras"), llevado a la unidad del producto con su
    // equivalencia (1 unidad = X kg). Sin referencia, o sin equivalencia para otra unidad, no se sugiere nada.
    precioSugerido({ precioReferenciaKg, unidad, equivalenciaKg }) {
      const porKg = Number(precioReferenciaKg);
      if (precioReferenciaKg == null || !(porKg > 0)) return null;
      const factor = unidad === 'kg' || !unidad ? 1 : Number(equivalenciaKg);
      if (!(factor > 0)) return null;
      return Math.round(porKg * factor * 100) / 100;
    },

    // H6 (A04: "en descripción quiere una sugerencia con datos de la cosecha o siembra"): una descripción hecha con lo que
    // ya se sabe de la cosecha (cultivo, chacra, fechas y cantidad). Solo dice lo que hay: sin un dato, esa parte no va.
    descripcionSugerida({ cultivo, chacra, fechaSiembra, fechaCosecha, cantidad, unidad }) {
      if (!cultivo) return null;
      const meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'setiembre', 'octubre', 'noviembre', 'diciembre'];
      const fecha = (iso) => {
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
        const nombres = { kg: 'kg', unidad: n === 1 ? 'unidad' : 'unidades', litro: n === 1 ? 'litro' : 'litros' };
        partes.push(`De esta cosecha salieron ${String(Math.round(n * 100) / 100).replace('.', ',')} ${nombres[unidad] || unidad || 'kg'}.`);
      }
      return partes.join(' ');
    },

    // H11 (validación con usuarios del 2026-10-13, R03: el repartidor elegía cada estado a mano en un selector que dejaba
    // marcado el actual): el estado que sigue en el orden de ese envío, o null si ya está en el último o fuera del orden.
    siguienteEstadoEnvio(estado, estados) {
      const i = (estados || []).indexOf(estado);
      return i >= 0 && i < estados.length - 1 ? estados[i + 1] : null;
    },

    // H8: "jueves 15/10 a las 8:30" de "2026-10-15T08:30" (hora de Perú tal como la escribió el admin: sin zona horaria).
    fechaHoraLegible(iso) {
      const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ''));
      if (!m) return '';
      const dias = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
      const dia = dias[new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()];
      return `${dia} ${m[3]}/${m[2]} a las ${Number(m[4])}:${m[5]}`;
    },
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = Reglas;
  else raiz.Reglas = Reglas;
})(typeof window !== 'undefined' ? window : globalThis);
