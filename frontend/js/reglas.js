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
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = Reglas;
  else raiz.Reglas = Reglas;
})(typeof window !== 'undefined' ? window : globalThis);
