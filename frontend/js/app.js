// ============ ESTADO GLOBAL ============
const Estado = {
  token: null,
  usuarioId: null,
  nombre: null,
  email: null,
  rol: null,
  productos: [],
  carrito: [],        // {producto_id, nombre, precio, cantidad, stockDisponible}
  productorId: null,
  repartidorId: null,
  enviosRepartidor: [],
  chacras: [], // chacras del productor autenticado — las usan tanto "Mis chacras" (panel-productor)
               // como el selector de chacra en "Registrar nueva siembra" (Mi Perfil)
  registrosProduccion: [], // RegistroProduccion del productor — resumen de panel-productor y Mi Perfil
  misProductos: [], // Api.productos.mios() — borradores Y publicados del productor autenticado,
                     // separado de Estado.productos (que es el catálogo público, solo publicados)
  imagenesPorProducto: {}, // producto_id -> [{id, url, orden}], caché en memoria para el gestor de fotos
  unidadesAlternativasPorProducto: {}, // producto_id -> [{id, unidad, precio, factor_a_base}], mismo patrón que imagenesPorProducto
  entregasDirectasPendientes: [], // Envio en "pendiente_entrega_directa" del productor autenticado — ver cargarEntregasDirectasPendientes
};

const SESSION_KEY = 'agro_sesion';
let sesionVencidaAlIniciar = false; // había una sesión guardada, pero su token ya había vencido (ver cargarSesionGuardada)

// A qué vista aterriza cada rol apenas hay sesión activa — login exitoso (manejarLogin) o F5 con
// sesión restaurada desde localStorage (iniciar). Único lugar donde vive este mapeo, para no
// duplicar "qué vista le corresponde a cada rol" en dos sitios que puedan desincronizarse.
// Comprador (y cualquier rol no listado acá, incl. sin sesión) sigue aterrizando en 'inicio' —
// esto SOLO decide el destino inicial, no restringe la navegación: cualquier rol puede seguir
// entrando a 'inicio' manualmente (navbar/logo) en cualquier momento, cambiarVista('inicio') no
// tiene ningún guard de rol.
const VISTA_INICIAL_POR_ROL = {
  productor: 'panel-productor',
  repartidor: 'gestion-envios',
  verificador: 'panel-verificador',
  admin: 'panel-admin',
};

function vistaInicialPara(rol) {
  return VISTA_INICIAL_POR_ROL[rol] || 'inicio';
}

// ============ UTILIDADES ============
function toast(mensaje, tipo = 'ok') {
  const cont = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${tipo === 'error' ? 'error' : ''}`;
  el.textContent = mensaje;
  cont.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function manejarError(err, contexto) {
  console.error(contexto, err);
  toast(err.message || `Ocurrió un error en ${contexto}`, 'error');
}

function decodeJwt(token) {
  try {
    const payload = token.split('.')[1];
    const json = decodeURIComponent(
      atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function formatearFecha(fechaStr) {
  if (!fechaStr) return '—';
  const d = new Date(fechaStr);
  if (isNaN(d)) return fechaStr;
  return d.toLocaleString('es-PE', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Para fechas de calendario que escribe el usuario (siembra, cosecha): la API las guarda a
// medianoche, así que mostrar la hora ("12:00 a. m.") solo mete ruido. Una cadena sin hora
// ("2026-04-13") se lee como fecha local: con new Date() directo se interpretaría en UTC y en Perú
// saldría el día anterior.
function formatearFechaCorta(fechaStr) {
  if (!fechaStr) return '—';
  const sinHora = /^(\d{4})-(\d{2})-(\d{2})$/.exec(fechaStr);
  const d = sinHora ? new Date(Number(sinHora[1]), Number(sinHora[2]) - 1, Number(sinHora[3])) : new Date(fechaStr);
  if (isNaN(d)) return fechaStr;
  return d.toLocaleDateString('es-PE', { day: '2-digit', month: 'short', year: 'numeric' });
}

// Para timestamps generados por el servidor (datetime.utcnow, que el JSON entrega sin zona): sin la Z
// el navegador los lee como hora local y los muestra corridos (en Perú, +5 h — a veces el día
// siguiente). NO usar con fechas que escribe el usuario (siembra, cosecha): esas son fechas de
// calendario, no instantes en UTC.
function parsearFechaServidor(fechaStr) {
  if (!fechaStr) return new Date(NaN);
  return new Date(/(Z|[+-]\d{2}:?\d{2})$/i.test(fechaStr) ? fechaStr : `${fechaStr}Z`);
}

function formatearFechaServidor(fechaStr) {
  if (!fechaStr) return '—';
  const d = parsearFechaServidor(fechaStr);
  return isNaN(d) ? fechaStr : formatearFecha(d.toISOString());
}

function formatearMoneda(valor) {
  const n = Number(valor) || 0;
  const monto = Math.abs(n).toFixed(2);
  return `${n < 0 && Number(monto) !== 0 ? '-' : ''}S/ ${monto}`; // "-S/ 199.46", no "S/ -199.46"
}

function hashCorto(hash) {
  if (!hash) return '—';
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

const EVENTOS_CERTIFICACION = {
  cosecha_registrada: { emoji: '<i class="ti ti-wheat"></i>', texto: 'Cosecha registrada' },
  certificado_productor: { emoji: '<i class="ti ti-circle-check"></i>', texto: 'Certificado por el productor' },
  llegada_almacen: { emoji: '<i class="ti ti-building-warehouse"></i>', texto: 'Llegada al almacén' },
  verificado_punto_venta: { emoji: '<i class="ti ti-building-store"></i>', texto: 'Verificado en punto de venta' },
};

function labelEventoCertificacion(evento) {
  return EVENTOS_CERTIFICACION[evento] || { emoji: '<i class="ti ti-package"></i>', texto: evento };
}

function renderLineaTiempoCertificacion(historial) {
  if (!historial.length) {
    return '<p class="muted" style="text-align:center;padding:12px 0 28px;">Aún no hay eventos registrados para este producto.</p>';
  }
  return `
    <div class="timeline">
      ${historial.map((b) => {
        const info = labelEventoCertificacion(b.evento);
        return `
          <div class="timeline-item">
            <div class="timeline-marker">${info.emoji}</div>
            <div class="timeline-content">
              <div class="timeline-header">
                <strong>${escapeAttr(info.texto)}</strong>
                <span class="timeline-indice">Bloque #${b.indice}</span>
              </div>
              <div class="timeline-fecha">${formatearFechaServidor(b.fecha)}</div>
              ${renderVerificadorLinea(b)}
              ${b.datos ? `<p class="timeline-datos">${escapeAttr(b.datos)}</p>` : ''}
              <div class="timeline-hash">Hash: <code>${escapeAttr(hashCorto(b.hash_actual))}</code></div>
            </div>
          </div>`;
      }).join('')}
    </div>`;
}

// Solo aplica al evento certificado_productor; el backend ya resuelve el nombre en /historial
// (verificador_nombre puede venir null si el verificador fue borrado, ahí caemos al id truncado).
function renderVerificadorLinea(b) {
  if (b.evento !== 'certificado_productor' || !b.verificador_id) return '';
  const nombre = b.verificador_nombre || `verificador ${String(b.verificador_id).slice(0, 8)}`;
  return `<p class="timeline-verificador"><i class="ti ti-user"></i> Certificado por: ${escapeAttr(nombre)}</p>`;
}

async function abrirModalCertificacion(productoId) {
  abrirModal('modal-certificacion');
  const cont = document.getElementById('certificacion-content');
  cont.innerHTML = `<div class="verificar-cargando"><div class="spinner"></div><p>Cargando certificación...</p></div>`;

  try {
    const [historial, producto] = await Promise.all([
      Api.certificacion.historial(productoId),
      Api.productos.obtener(productoId),
    ]);
    cont.innerHTML = `
      <h3><i class="ti ti-link"></i> Certificación de trazabilidad</h3>
      <p class="muted">${escapeAttr(producto.nombre)}</p>
      <div class="certificacion-qr-bloque">
        <img src="${escapeAttr(Api.certificacion.qrUrl(productoId))}" alt="Código QR de certificación" class="certificacion-qr" id="certificacion-qr-img">
        <p class="muted certificacion-qr-nota">Comparte este QR con tus compradores para que verifiquen el origen del producto.</p>
      </div>
      <h4 class="timeline-titulo">Historial de la cadena</h4>
      ${renderLineaTiempoCertificacion(historial)}
    `;
    document.getElementById('certificacion-qr-img')?.addEventListener('error', function () {
      const p = document.createElement('p');
      p.className = 'muted';
      p.textContent = 'No se pudo cargar el código QR.';
      this.replaceWith(p);
    });
  } catch (err) {
    cont.innerHTML = `
      <div class="verificar-banner banner-error">
        <span class="banner-icon"><i class="ti ti-alert-triangle"></i></span>
        <div>
          <strong>No se pudo cargar la certificación</strong>
          <p>${escapeAttr(err.message)}</p>
        </div>
      </div>`;
  }
}

// Las 8 categorías fijas del catálogo, cada una con su ícono propio.
const ICONOS_CATEGORIA = {
  fruta: '🍎',
  verdura: '🥬',
  tuberculo: '🥔',
  grano: '🌾',
  legumbre: '🫘',
  lacteo: '🥛',
  huevo: '🥚',
  hierba: '🌿',
};

const NOMBRE_CATEGORIA = {
  fruta: 'Fruta',
  verdura: 'Verdura',
  tuberculo: 'Tubérculo',
  grano: 'Grano',
  legumbre: 'Legumbre',
  lacteo: 'Lácteo',
  huevo: 'Huevo',
  hierba: 'Hierba',
};

// Normaliza variantes/typos de datos existentes (p.ej. "tuberculos", "cereales") a la categoría canónica.
function normalizarCategoria(categoria) {
  const c = String(categoria || '').toLowerCase().trim();
  if (c.startsWith('tuber')) return 'tuberculo';
  if (c.startsWith('cerea') || c.startsWith('gran')) return 'grano';
  if (c.startsWith('verdur')) return 'verdura';
  if (c.startsWith('frut')) return 'fruta';
  if (c.startsWith('legum')) return 'legumbre';
  if (c.startsWith('lact')) return 'lacteo';
  if (c.startsWith('huev')) return 'huevo';
  if (c.startsWith('hierb')) return 'hierba';
  return ICONOS_CATEGORIA[c] ? c : null;
}

function iconoCategoria(categoria) {
  const cat = normalizarCategoria(categoria);
  return (cat && ICONOS_CATEGORIA[cat]) || '🧺';
}

function nombreCategoria(categoria) {
  const cat = normalizarCategoria(categoria);
  return (cat && NOMBRE_CATEGORIA[cat]) || categoria || 'General';
}

// El emoji de respaldo de un producto (cuando no tiene foto) es el ícono de su categoría.
function emojiParaProducto(producto) {
  return iconoCategoria(producto?.categoria);
}

// ---- Favoritos (localStorage, por cuenta) ----
function claveFavoritos() {
  return `agro_favoritos_${Estado.usuarioId || 'invitado'}`;
}

function obtenerFavoritos() {
  try {
    return new Set(JSON.parse(localStorage.getItem(claveFavoritos())) || []);
  } catch {
    return new Set();
  }
}

function esFavorito(productoId) {
  return obtenerFavoritos().has(productoId);
}

function alternarFavorito(productoId) {
  const favoritos = obtenerFavoritos();
  const yaEstaba = favoritos.has(productoId);
  if (yaEstaba) favoritos.delete(productoId);
  else favoritos.add(productoId);
  localStorage.setItem(claveFavoritos(), JSON.stringify([...favoritos]));
  return !yaEstaba;
}

function manejarClickFavorito(boton) {
  const ahoraFavorito = alternarFavorito(boton.dataset.id);
  boton.classList.toggle('activo', ahoraFavorito);
  boton.setAttribute('aria-pressed', String(ahoraFavorito));
  boton.classList.remove('favorito-pop');
  void boton.offsetWidth; // reinicia la animación aunque se haga click varias veces seguidas
  boton.classList.add('favorito-pop');
  // Un solo ícono (ti-heart): el estado guardado/no-guardado se distingue por el color de
  // ".activo" en el CSS, no por cambiar de ícono (Tabler no trae una variante "heart-filled"
  // en el mismo set sin cargar una segunda hoja de estilos que pisaría esta misma clase).
  if (boton.classList.contains('btn-favorito-detalle')) {
    boton.innerHTML = ahoraFavorito ? '<i class="ti ti-heart"></i> Guardado' : '<i class="ti ti-heart"></i> Guardar';
  } else {
    boton.innerHTML = '<i class="ti ti-heart"></i>';
  }
}

// ---- Micro-feedback visual en botones de acción ----
function destellarBoton(boton, textoExito, duracion = 900) {
  if (!boton) return;
  const textoOriginal = boton.innerHTML;
  const anchoOriginal = boton.offsetWidth;
  boton.style.minWidth = `${anchoOriginal}px`;
  boton.disabled = true;
  boton.classList.add('destello-exito');
  boton.innerHTML = textoExito;
  setTimeout(() => {
    boton.classList.remove('destello-exito');
    boton.innerHTML = textoOriginal;
    boton.style.minWidth = '';
    boton.disabled = false;
  }, duracion);
}

function escapeAttr(valor) {
  return String(valor ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Espera a que un elemento renderizado async (p. ej. tras un cambiarVista a una vista que carga
// datos antes de pintar, como Mi Perfil) aparezca en el DOM, y recién entonces hace scroll. Un
// scrollIntoView inmediato fallaría porque el contenido todavía es el skeleton de carga.
function esperarElementoYScroll(id, intentosMax = 40) {
  let intentos = 0;
  const intervalo = setInterval(() => {
    const el = document.getElementById(id);
    intentos++;
    if (el) {
      clearInterval(intervalo);
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else if (intentos >= intentosMax) {
      clearInterval(intervalo);
    }
  }, 50);
}

function renderMediaProducto(producto, contenedorClase = 'product-emoji') {
  const emoji = emojiParaProducto(producto);
  const url = (producto.imagen_url || producto.imagen_principal || '').trim();
  if (!url) return `<div class="${contenedorClase}">${emoji}</div>`;

  return `
    <div class="${contenedorClase} product-media">
      <img src="${escapeAttr(url)}" alt="${escapeAttr(producto.nombre || '')}" loading="lazy"
           onerror="this.parentElement.textContent='${emoji}'">
    </div>`;
}

// ---- Gestor de fotos de producto (subida + eliminación, hasta 5 por producto) ----
const TIPOS_IMAGEN_VALIDOS = ['image/jpeg', 'image/png', 'image/webp'];

function renderGestorFotosHTML(productoId, imagenes) {
  const n = imagenes.length;
  const lleno = n >= 5;
  const ordenadas = [...imagenes].sort((a, b) => a.orden - b.orden);
  return `
    <div class="gestor-fotos" data-producto-id="${escapeAttr(productoId)}">
      <div class="fotos-upload-header">
        <span class="fotos-contador">${n}/5 fotos</span>
      </div>
      <div class="upload-zona ${lleno ? 'deshabilitada' : ''}">
        <input type="file" class="upload-input" accept="image/jpeg,image/png,image/webp" multiple ${lleno ? 'disabled' : ''} hidden>
        <span class="upload-zona-icono"><i class="ti ti-upload"></i></span>
        <span>${lleno ? 'Alcanzaste el máximo de 5 fotos' : 'Arrastra tus fotos aquí o <strong>haz clic para elegir</strong>'}</span>
      </div>
      ${n ? `
      <div class="upload-miniaturas">
        ${ordenadas.map((img, i) => `
          <div class="upload-miniatura">
            ${i === 0 ? '<span class="miniatura-badge-principal">Principal</span>' : ''}
            <img src="${escapeAttr(img.url)}" alt="" loading="lazy">
            <button type="button" class="upload-miniatura-quitar" data-imagen-id="${img.id}" aria-label="Quitar foto"><i class="ti ti-x"></i></button>
            <div class="upload-miniatura-mover">
              <button type="button" class="miniatura-mover-btn" data-imagen-id="${img.id}" data-direccion="izq" aria-label="Mover foto a la izquierda" ${i === 0 ? 'disabled' : ''}>‹</button>
              <button type="button" class="miniatura-mover-btn" data-imagen-id="${img.id}" data-direccion="der" aria-label="Mover foto a la derecha" ${i === ordenadas.length - 1 ? 'disabled' : ''}>›</button>
            </div>
          </div>`).join('')}
      </div>` : ''}
    </div>`;
}

function rerenderGestorFotos(productoId) {
  const imagenes = Estado.imagenesPorProducto[productoId] || [];
  document.querySelectorAll(`.gestor-fotos[data-producto-id="${productoId}"]`).forEach((el) => {
    el.outerHTML = renderGestorFotosHTML(productoId, imagenes);
  });
  document.querySelectorAll(`.btn-toggle-fotos[data-id="${productoId}"]`).forEach((btn) => {
    btn.innerHTML = `<i class="ti ti-camera"></i> Fotos (${imagenes.length}/5)`;
  });
}

async function subirFotosProducto(productoId, fileList) {
  const actuales = Estado.imagenesPorProducto[productoId] || [];
  const disponibles = 5 - actuales.length;
  if (disponibles <= 0) {
    toast('Ya tienes el máximo de 5 fotos para este producto.', 'error');
    return;
  }

  const archivos = Array.from(fileList).slice(0, disponibles);
  for (const archivo of archivos) {
    if (!TIPOS_IMAGEN_VALIDOS.includes(archivo.type)) {
      toast(`"${archivo.name}" no es una imagen JPG, PNG o WEBP.`, 'error');
      continue;
    }
    try {
      const nuevaImagen = await Api.productos.subirImagen(productoId, archivo, actuales.length);
      actuales.push(nuevaImagen);
      Estado.imagenesPorProducto[productoId] = [...actuales];
      rerenderGestorFotos(productoId);
      sincronizarBorradorTrasCambioDeFotos(productoId);
    } catch (err) {
      manejarError(err, 'subir la foto');
    }
  }
}

async function eliminarFotoProducto(productoId, imagenId) {
  try {
    await Api.productos.eliminarImagen(imagenId);
    Estado.imagenesPorProducto[productoId] = (Estado.imagenesPorProducto[productoId] || []).filter((img) => img.id !== imagenId);
    rerenderGestorFotos(productoId);
    sincronizarBorradorTrasCambioDeFotos(productoId);
    toast('Foto eliminada.');
  } catch (err) {
    manejarError(err, 'eliminar la foto');
  }
}

// subirFotosProducto/eliminarFotoProducto/moverFotoProducto son genéricas (las usa tanto un
// borrador como el panel de fotos de un producto ya publicado) — este helper es un no-op seguro
// cuando productoId no corresponde a un borrador visible en pantalla (sincronizarBotonPublicar
// Borrador ya se protege con ese caso; actualizarPreviewBorrador simplemente no encuentra la
// tarjeta ni el producto en Estado.misProductos y no hace nada).
function sincronizarBorradorTrasCambioDeFotos(productoId) {
  sincronizarBotonPublicarBorrador(productoId);
  actualizarPreviewBorrador(productoId);
}

async function moverFotoProducto(productoId, imagenId, direccion) {
  const lista = Estado.imagenesPorProducto[productoId];
  if (!lista) return;

  const ordenadas = [...lista].sort((a, b) => a.orden - b.orden);
  const indice = ordenadas.findIndex((img) => img.id === imagenId);
  const destino = direccion === 'izq' ? indice - 1 : indice + 1;
  if (indice === -1 || destino < 0 || destino >= ordenadas.length) return;

  const snapshot = lista.map((img) => ({ ...img }));

  const actual = ordenadas[indice];
  const vecino = ordenadas[destino];
  const ordenTemp = actual.orden;
  actual.orden = vecino.orden;
  vecino.orden = ordenTemp;

  Estado.imagenesPorProducto[productoId] = [...lista];
  rerenderGestorFotos(productoId);
  actualizarPreviewBorrador(productoId); // el orden decide cuál es la "principal" en la preview

  try {
    await Promise.all([
      Api.productos.actualizarOrdenImagen(actual.id, actual.orden),
      Api.productos.actualizarOrdenImagen(vecino.id, vecino.orden),
    ]);
  } catch (err) {
    Estado.imagenesPorProducto[productoId] = snapshot;
    rerenderGestorFotos(productoId);
    actualizarPreviewBorrador(productoId);
    manejarError(err, 'reordenar las fotos');
  }
}

const ICONOS_ESTADO = {
  pendiente: 'hourglass',
  pendiente_asignacion: 'hourglass',
  propuesto: 'mail',
  confirmado: 'receipt',
  asignado: 'clipboard-list',
  en_camino: 'truck',
  en_transito: 'truck',
  en_ruta: 'truck',
  entregado: 'circle-check',
  cancelado: 'circle-x',
  rechazado: 'circle-x',
  aprobado: 'circle-check',
  error: 'alert-triangle',
};

const ESTADOS_BADGE_CONOCIDOS = new Set([
  'pendiente', 'pendiente_asignacion', 'propuesto', 'confirmado', 'asignado',
  'en_camino', 'en_transito', 'en_ruta', 'entregado', 'aprobado',
  'cancelado', 'rechazado', 'error',
]);

function badgeEstadoEnvio(estado) {
  const clave = (estado || 'pendiente').toLowerCase().replace(/\s+/g, '_');
  const clase = ESTADOS_BADGE_CONOCIDOS.has(clave) ? `pill-${clave}` : 'pill-default';
  const iconoClase = ICONOS_ESTADO[clave];
  const icono = iconoClase ? `<i class="ti ti-${iconoClase}"></i>` : '•';
  // el estado llega como identificador ("en_camino"): se muestra con espacios, no con guiones bajos
  return `<span class="pill pill-estado ${clase}">${icono} ${String(estado || 'pendiente').replace(/_/g, ' ')}</span>`;
}

// Única fuente de verdad de las unidades de medida válidas — la usan tanto la visualización
// (catálogo, carrito, detalle, vía infoUnidad/unidadCorta/unidadPlural/unidadEtiqueta) como los
// dos <select> que la consumen (opcionesUnidadMedidaHtml): unidad_medida de una cosecha
// (RegistroProduccion, "Registrar nueva siembra") y unidad alternativa de un producto ya
// publicado. Mismo criterio que ROLES_INVITABLES: agregar una unidad nueva es sumarla acá, no
// tocar el HTML ni duplicar la lista en otro lado. "label" es el texto que ve el productor en
// esos <select>; singular/plural/etiqueta son para los textos de catálogo/carrito ("S/ X / kg",
// "3 sacos", etc.). "esBase": true = unidad chica/granular válida como unidad_medida de una
// cosecha (kg, unidad, litro) — las de empaque grande (saco, arroba) quedan fuera de esa
// categoría a propósito: solo se agregan después, como unidad alternativa del producto (ver
// UNIDADES_COSECHA_VALIDAS en productores/main.py, que valida esto mismo del lado del backend).
const UNIDADES_MEDIDA = {
  kg: { singular: 'kg', plural: 'kg', etiqueta: 'kg', label: 'Kilogramo (kg)', esBase: true },
  unidad: { singular: 'unidad', plural: 'unidades', etiqueta: 'unidades', label: 'Unidad', esBase: true },
  saco: { singular: 'saco', plural: 'sacos', etiqueta: 'sacos', label: 'Saco', esBase: false },
  arroba: { singular: 'arroba', plural: 'arrobas', etiqueta: 'arrobas', label: 'Arroba', esBase: false },
  litro: { singular: 'litro', plural: 'litros', etiqueta: 'litros', label: 'Litro', esBase: true },
};

function infoUnidad(codigo) {
  return UNIDADES_MEDIDA[codigo] || UNIDADES_MEDIDA.unidad;
}

function opcionesUnidadMedidaHtml(excluir = [], { soloBase = false } = {}) {
  return Object.entries(UNIDADES_MEDIDA)
    .filter(([valor, info]) => !excluir.includes(valor) && (!soloBase || info.esBase))
    .map(([valor, info]) => `<option value="${escapeAttr(valor)}">${escapeAttr(info.label)}</option>`)
    .join('');
}

function unidadPlural(codigo, cantidad) {
  const info = infoUnidad(codigo);
  return cantidad === 1 ? info.singular : info.plural;
}

function unidadCorta(codigo) {
  return infoUnidad(codigo).singular;
}

function unidadEtiqueta(codigo) {
  return infoUnidad(codigo).etiqueta;
}

// ---- Precio/stock de un producto según la unidad de compra elegida — unidad=null siempre
// significa "unidad base" (Producto.precio/Producto.stock tal cual), un valor busca esa unidad
// dentro de producto.unidades_alternativas (ya viene embebida en GET /productos y
// GET /productos/{id}). Usadas tanto en la página de detalle como en agregarAlCarrito. ----
function unidadAlternativaDe(producto, unidad) {
  if (!unidad) return null;
  return (producto.unidades_alternativas || []).find((u) => u.unidad === unidad) || null;
}

function precioEnUnidad(producto, unidad) {
  const alt = unidadAlternativaDe(producto, unidad);
  return Number(alt ? alt.precio : producto.precio);
}

// Stock disponible EXPRESADO en la unidad elegida — si es una alternativa, el stock real
// (siempre en la unidad base) se convierte con factor_a_base. Math.floor a propósito: no tiene
// sentido ofrecer "2.7 sacos" como tope si sobran kg pero no alcanzan para un saco completo más.
function stockEnUnidad(producto, unidad) {
  const alt = unidadAlternativaDe(producto, unidad);
  if (!alt) return producto.stock;
  return Math.floor(producto.stock / Number(alt.factor_a_base));
}

function renderEstrellas(promedio) {
  // Un solo ícono (ti-star): "llena" vs "vacía" se distingue por color (ver .estrella-llena en
  // CSS), no por cambiar de ícono — el set outline de Tabler no trae una variante rellena sin
  // cargar una segunda hoja de estilos que pisaría esta misma clase (ver nota en alternarFavorito).
  const llenas = Math.round(promedio || 0);
  let html = '';
  for (let i = 1; i <= 5; i++) {
    html += `<i class="ti ti-star${i <= llenas ? ' estrella-llena' : ''}"></i>`;
  }
  return html;
}

// ============ SKELETON LOADERS ============
function renderSkeletonProductos(n = 8) {
  const tarjeta = `
    <div class="product-card skeleton-card">
      <div class="skeleton skeleton-media"></div>
      <div class="skeleton-body">
        <div class="skeleton skeleton-line w-40"></div>
        <div class="skeleton skeleton-line tall w-70"></div>
        <div class="skeleton skeleton-line w-60"></div>
        <div class="skeleton skeleton-line w-40"></div>
        <div class="skeleton skeleton-btn"></div>
      </div>
    </div>`;
  return tarjeta.repeat(n);
}

function renderSkeletonDetalle() {
  return `
    <div class="detalle-skeleton" aria-hidden="true">
      <div class="skeleton detalle-skeleton-visor"></div>
      <div class="detalle-skeleton-info">
        <div class="skeleton skeleton-line w-40"></div>
        <div class="skeleton skeleton-line tall w-70"></div>
        <div class="skeleton skeleton-line w-60"></div>
        <div class="skeleton skeleton-line tall w-40"></div>
        <div class="skeleton skeleton-btn"></div>
      </div>
    </div>`;
}

function renderSkeletonFilas(n = 3) {
  const fila = `
    <div class="skeleton-row-card">
      <div class="skeleton skeleton-line w-40"></div>
      <div class="skeleton skeleton-line tall w-70"></div>
      <div class="skeleton skeleton-line w-60"></div>
    </div>`;
  return fila.repeat(n);
}

// Usado por los 3 dashboards de panel (productor/repartidor/admin) mientras cargan sus métricas.
function renderSkeletonResumen(n = 3) {
  const stat = `
    <div class="resumen-stat">
      <div class="skeleton" style="width:44px;height:44px;border-radius:50%;flex-shrink:0;"></div>
      <div style="flex:1">
        <div class="skeleton skeleton-line w-40" style="margin-bottom:8px;"></div>
        <div class="skeleton skeleton-line w-70"></div>
      </div>
    </div>`;
  return `
    <div class="card-panel resumen-panel">
      <div class="resumen-stats">${stat.repeat(n)}</div>
    </div>`;
}

// Animación de conteo para .resumen-stat-valor (Productor/Repartidor/Admin) — puramente
// decorativa, respeta prefers-reduced-motion. Solo anima valores enteros simples (los únicos
// que usan esa clase); si el texto no es un número, se deja tal cual sin romper nada.
function animarContadoresResumen(contenedor) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  contenedor.querySelectorAll('.resumen-stat-valor').forEach((el) => {
    const destino = parseInt(el.textContent, 10);
    if (!Number.isFinite(destino)) return;
    const duracion = 600;
    const inicio = performance.now();
    function paso(ahora) {
      const avance = Math.min(1, (ahora - inicio) / duracion);
      const facilitado = 1 - Math.pow(1 - avance, 3); // ease-out cubic
      el.textContent = Math.round(destino * facilitado);
      if (avance < 1) requestAnimationFrame(paso);
      else el.textContent = destino;
    }
    requestAnimationFrame(paso);
  });
}

// ============ MAPAS (LEAFLET + OPENSTREETMAP) ============
const MAPA_TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAPA_ATRIBUCION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
const MAPA_CENTRO_PERU = [-9.19, -75.015];

function hayCoordenadas(lat, lng) {
  return lat != null && lng != null && lat !== '' && lng !== '' && !isNaN(Number(lat)) && !isNaN(Number(lng));
}

function crearIconoMarcador(emoji, color) {
  return L.divIcon({
    className: 'marcador-mapa-wrap',
    html: `<span class="marcador-mapa" style="background:${color}">${emoji}</span>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -18],
  });
}

function agregarCapaBase(mapa) {
  L.tileLayer(MAPA_TILE_URL, { attribution: MAPA_ATRIBUCION, maxZoom: 19 }).addTo(mapa);
}

function crearMapaSoloLectura(contenedorId, lat, lng, emoji, color, popupTexto) {
  const el = document.getElementById(contenedorId);
  if (!el || typeof L === 'undefined') return null;
  const mapa = L.map(el, { scrollWheelZoom: false }).setView([Number(lat), Number(lng)], 14);
  agregarCapaBase(mapa);
  const marcador = L.marker([Number(lat), Number(lng)], { icon: crearIconoMarcador(emoji, color) }).addTo(mapa);
  if (popupTexto) marcador.bindPopup(popupTexto);
  setTimeout(() => mapa.invalidateSize(), 80);
  return mapa;
}

function crearMapaSeleccionable(contenedorId, { latInicial, lngInicial, emoji = '<i class="ti ti-map-pin"></i>', color = '#e76f51', onSeleccionar } = {}) {
  const el = document.getElementById(contenedorId);
  if (!el || typeof L === 'undefined') return null;

  const tieneInicial = hayCoordenadas(latInicial, lngInicial);
  const centro = tieneInicial ? [Number(latInicial), Number(lngInicial)] : MAPA_CENTRO_PERU;
  const mapa = L.map(el).setView(centro, tieneInicial ? 14 : 5);
  agregarCapaBase(mapa);

  let marcador = tieneInicial
    ? L.marker(centro, { icon: crearIconoMarcador(emoji, color) }).addTo(mapa)
    : null;

  mapa.on('click', (e) => {
    const { lat, lng } = e.latlng;
    if (marcador) marcador.setLatLng([lat, lng]);
    else marcador = L.marker([lat, lng], { icon: crearIconoMarcador(emoji, color) }).addTo(mapa);
    onSeleccionar?.(lat, lng);
  });

  setTimeout(() => mapa.invalidateSize(), 150);

  return {
    mapa,
    moverMarcador(lat, lng) {
      mapa.setView([lat, lng], 15);
      if (marcador) marcador.setLatLng([lat, lng]);
      else marcador = L.marker([lat, lng], { icon: crearIconoMarcador(emoji, color) }).addTo(mapa);
    },
  };
}

function crearMapaRuta(contenedorId, origen, destino) {
  const el = document.getElementById(contenedorId);
  if (!el || typeof L === 'undefined') return null;
  if (!hayCoordenadas(origen?.latitud, origen?.longitud) || !hayCoordenadas(destino?.latitud, destino?.longitud)) return null;

  const puntoOrigen = [Number(origen.latitud), Number(origen.longitud)];
  const puntoDestino = [Number(destino.latitud), Number(destino.longitud)];

  const mapa = L.map(el, { scrollWheelZoom: false });
  mapa.fitBounds([puntoOrigen, puntoDestino], { padding: [28, 28], maxZoom: 15 });
  agregarCapaBase(mapa);

  const marcadorOrigen = L.marker(puntoOrigen, { icon: crearIconoMarcador('<i class="ti ti-moped"></i>', '#2d6a4f') }).addTo(mapa).bindPopup('Repartidor');
  L.marker(puntoDestino, { icon: crearIconoMarcador('<i class="ti ti-map-pin"></i>', '#e76f51') }).addTo(mapa).bindPopup('Destino');
  const linea = L.polyline([puntoOrigen, puntoDestino], { color: '#40916c', weight: 3, dashArray: '6 8' }).addTo(mapa);

  setTimeout(() => mapa.invalidateSize(), 80);

  return { mapa, marcadorOrigen, linea };
}

// ---- Geolocalización del navegador ----
function mensajeErrorGeolocalizacion(err) {
  if (!err) return 'No se pudo obtener tu ubicación.';
  if (err.code === err.PERMISSION_DENIED) return 'Denegaste el permiso de ubicación. Puedes marcar el punto manualmente en el mapa.';
  if (err.code === err.POSITION_UNAVAILABLE) return 'No se pudo determinar tu ubicación actual.';
  if (err.code === err.TIMEOUT) return 'Se agotó el tiempo esperando tu ubicación.';
  return 'No se pudo obtener tu ubicación.';
}

function obtenerUbicacionActual() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('Tu navegador no soporta geolocalización.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      (err) => reject(new Error(mensajeErrorGeolocalizacion(err))),
      { enableHighAccuracy: true, timeout: 10000 }
    );
  });
}

// ============ SESIÓN ============
function guardarSesion() {
  localStorage.setItem(SESSION_KEY, JSON.stringify({
    token: Estado.token,
    usuarioId: Estado.usuarioId,
    nombre: Estado.nombre,
    email: Estado.email,
    rol: Estado.rol,
  }));
}

// ¿El token sigue vigente por su fecha de vencimiento (exp)? Es solo una comprobación local para no
// restaurar una sesión que ya venció: quien decide de verdad es el servidor (ver manejarSesionExpirada).
function tokenVigente(token) {
  const payload = decodeJwt(token);
  if (!payload) return false;
  if (typeof payload.exp !== 'number') return true;
  return payload.exp * 1000 > Date.now();
}

function cargarSesionGuardada() {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) return;
  try {
    const datos = JSON.parse(raw);
    if (!datos.token || !tokenVigente(datos.token)) {
      // Los tokens duran 60 minutos. Antes se restauraba igual: la interfaz seguía "con sesión" y cada
      // llamada fallaba con "Token inválido o expirado" hasta que el usuario cerraba sesión a mano.
      localStorage.removeItem(SESSION_KEY);
      sesionVencidaAlIniciar = true;
      return;
    }
    Object.assign(Estado, datos);
  } catch { /* sesión corrupta, se ignora */ }
}

// Vistas que necesitan sesión: si esta vence mientras el usuario está en una, se le saca de ahí. En las
// públicas (inicio, detalle de producto) se queda donde estaba.
const VISTAS_CON_SESION = ['mis-pedidos', 'notificaciones', 'panel-productor', 'gestion-envios', 'panel-verificador', 'panel-admin', 'perfil'];

// Un servicio respondió 401 a una petición con sesión (api.js): el token venció o ya no vale. Se cierra la
// sesión local y se abre el acceso con el motivo a la vista. Varias peticiones en vuelo responden 401 a la
// vez, y una respuesta tardía puede ser de una sesión anterior: solo se actúa si ese token sigue siendo el
// de la sesión actual. El carrito se conserva (no tiene datos personales y el usuario suele volver a entrar).
function manejarSesionExpirada(tokenUsado) {
  if (!Estado.token || (tokenUsado && tokenUsado !== Estado.token)) return;
  Estado.token = null;
  Estado.usuarioId = null;
  Estado.nombre = null;
  Estado.email = null;
  Estado.rol = null;
  Estado.productorId = null;
  Estado.repartidorId = null;
  localStorage.removeItem(SESSION_KEY);
  actualizarUIAuth();
  renderCarrito();
  const vistaActual = document.querySelector('.view.active')?.id?.replace('view-', '');
  if (VISTAS_CON_SESION.includes(vistaActual)) cambiarVista('landing');
  cambiarTabAuth('login');
  abrirModal('modal-auth');
  mostrarErrorAuth('login-alerta', MENSAJE_SESION_EXPIRADA);
}

function cerrarSesion() {
  Estado.token = null;
  Estado.usuarioId = null;
  Estado.nombre = null;
  Estado.email = null;
  Estado.rol = null;
  Estado.productorId = null;
  Estado.carrito = [];
  localStorage.removeItem(SESSION_KEY);
  actualizarUIAuth();
  renderCarrito();
  cambiarVista('landing');
  toast('Sesión cerrada. ¡Hasta pronto! 👋');
}

function actualizarUIAuth() {
  const logueado = !!Estado.token;
  // El ícono de cuenta (#nav-account) siempre está visible; solo cambia el contenido de su dropdown.
  document.getElementById('nav-account-guest').classList.toggle('hidden', logueado);
  document.getElementById('nav-account-user').classList.toggle('hidden', !logueado);
  document.querySelector('.nav-productor').classList.toggle('hidden', !(logueado && Estado.rol === 'productor'));
  document.querySelector('.nav-repartidor').classList.toggle('hidden', !(logueado && Estado.rol === 'repartidor'));
  document.querySelector('.nav-verificador').classList.toggle('hidden', !(logueado && Estado.rol === 'verificador'));
  document.querySelector('.nav-admin').classList.toggle('hidden', !(logueado && Estado.rol === 'admin'));
  document.getElementById('footer-ctas').classList.toggle('hidden', logueado);

  // Categorías, búsqueda y carrito son controles de compra — solo tienen sentido para comprador
  // (o un visitante anónimo, comprador potencial). Los demás roles no compran, así que no los ven.
  const esCompradorOInvitado = !logueado || Estado.rol === 'comprador';
  document.getElementById('nav-categorias').classList.toggle('hidden', !esCompradorOInvitado);
  document.getElementById('btn-abrir-busqueda').classList.toggle('hidden', !esCompradorOInvitado);
  document.getElementById('btn-cart').classList.toggle('hidden', !esCompradorOInvitado);
  // "Mis Pedidos" también es un ítem de comprador — vive dentro de #nav-account-user, que ya está
  // oculto por completo sin sesión, así que acá solo importa el caso logueado-pero-no-comprador.
  document.querySelector('.nav-account-mis-pedidos').classList.toggle('hidden', !(logueado && Estado.rol === 'comprador'));

  if (logueado) {
    document.getElementById('user-name').textContent = Estado.nombre || 'Usuario';
  }
}

async function iniciarSesionConToken(token) {
  const payload = decodeJwt(token);
  if (!payload) throw new Error('Token inválido recibido del servidor.');

  Estado.token = token;
  Estado.usuarioId = payload.sub;
  Estado.rol = payload.rol;

  try {
    const usuario = await Api.usuarios.obtener(payload.sub);
    Estado.nombre = usuario.nombre;
    Estado.email = usuario.email;
  } catch {
    Estado.nombre = Estado.nombre || 'Usuario';
  }

  guardarSesion();
  actualizarUIAuth();
}

// ============ NAVBAR DINÁMICO SEGÚN SCROLL ============
// Dos efectos independientes, combinados en el mismo handler con throttle de requestAnimationFrame:
// 1) transparente/blanco según POSICIÓN de scroll (ver actualizarNavbarScroll)
// 2) se oculta/muestra según DIRECCIÓN de scroll (ver actualizarVisibilidadNavbar)

// ---- 1) Transparente sobre el hero, blanco sólido al pasarlo ----
// El umbral es la altura real del hero (0 si la vista activa no lo tiene, ej. catálogo) con margen.
let navbarUmbralScroll = 20;
let navbarScrollTicking = false;

function actualizarUmbralNavbarScroll() {
  const hero = document.querySelector('.landing-hero');
  const altura = hero ? hero.offsetHeight : 0;
  navbarUmbralScroll = altura > 100 ? altura - 80 : 20;
}

function actualizarNavbarScroll() {
  document.querySelector('.navbar').classList.toggle('navbar-scrolled', window.scrollY > navbarUmbralScroll);
}

// ---- 2) Ocultar al bajar, mostrar de inmediato al subir ----
let navbarUltimoScrollY = 0;
let navbarAcumuladoBajada = 0; // scroll continuo hacia abajo, para ignorar micro-scrolls (evita parpadeo)
let navbarOculto = false;
const NAVBAR_ACUMULADO_PARA_OCULTAR = 8; // px de scroll seguido hacia abajo antes de reaccionar
const NAVBAR_SCROLL_MINIMO_PARA_OCULTAR = 80; // cerca del tope siempre visible, sin importar dirección

function mostrarNavbar() {
  if (!navbarOculto) return;
  navbarOculto = false;
  document.querySelector('.navbar').classList.remove('navbar-oculto');
}

function ocultarNavbar() {
  if (navbarOculto) return;
  navbarOculto = true;
  document.querySelector('.navbar').classList.add('navbar-oculto');
}

function actualizarVisibilidadNavbar() {
  const scrollActual = window.scrollY;
  const delta = scrollActual - navbarUltimoScrollY;

  if (scrollActual <= NAVBAR_SCROLL_MINIMO_PARA_OCULTAR) {
    mostrarNavbar();
    navbarAcumuladoBajada = 0;
  } else if (delta > 0) {
    // bajando: acumula antes de ocultar, así un par de pixeles accidentales no lo esconden
    navbarAcumuladoBajada += delta;
    if (navbarAcumuladoBajada > NAVBAR_ACUMULADO_PARA_OCULTAR) ocultarNavbar();
  } else if (delta < 0) {
    // subiendo, aunque sea un poco: reaparece de inmediato, sin umbral
    mostrarNavbar();
    navbarAcumuladoBajada = 0;
  }

  navbarUltimoScrollY = scrollActual;
}

// Throttle simple con requestAnimationFrame: como mucho una actualización por frame, no por pixel.
function manejarScrollNavbar() {
  if (navbarScrollTicking) return;
  navbarScrollTicking = true;
  requestAnimationFrame(() => {
    actualizarNavbarScroll();
    actualizarVisibilidadNavbar();
    navbarScrollTicking = false;
  });
}

// ============ NAVEGACIÓN ============
function cambiarVista(nombre, parametro) {
  if (nombre === 'inicio') nombre = 'landing';

  // Recuerda desde qué vista se llegó a "detalle-producto" (puede abrirse desde cualquier
  // lugar: mega-dropdown, landing, panel de búsqueda, Mis Pedidos...) para que el botón
  // "Volver" de esa página sepa a dónde regresar. Si ya estábamos en detalle-producto (p. ej.
  // un enlace a otro producto desde ahí), se conserva la vista original en vez de sobrescribirla.
  if (nombre === 'detalle-producto') {
    const actual = document.querySelector('.view.active')?.id?.replace('view-', '');
    if (actual && actual !== 'detalle-producto') vistaAntesDeDetalle = actual;
  }

  detenerPollingRutas();
  detenerSeguimientoRepartidor();
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  document.getElementById(`view-${nombre}`).classList.add('active');
  document.querySelectorAll('.nav-link').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.nav === nombre);
  });
  document.getElementById('nav-links').classList.remove('open');

  // El umbral depende de si la vista activa tiene el hero alto (solo landing) — se recalcula al
  // cambiar de vista, y se sincroniza el estado del navbar de inmediato (sin esperar el próximo scroll).
  actualizarUmbralNavbarScroll();
  actualizarNavbarScroll();
  // Tras navegar, el navbar siempre vuelve a mostrarse y el tracking de dirección arranca de cero.
  mostrarNavbar();
  navbarUltimoScrollY = window.scrollY;
  navbarAcumuladoBajada = 0;

  if (nombre === 'landing') cargarLanding();
  if (nombre === 'mis-pedidos') cargarMisPedidos();
  if (nombre === 'notificaciones') cargarNotificaciones();
  if (nombre === 'panel-productor') iniciarPanelProductor();
  if (nombre === 'gestion-envios') cargarGestionEnvios();
  if (nombre === 'panel-verificador') iniciarPanelVerificador();
  if (nombre === 'panel-admin') iniciarPanelAdmin();
  if (nombre === 'perfil') cargarPerfil();
  if (nombre === 'detalle-producto') cargarDetalleProducto(parametro);
}

function abrirRegistroConRol(rol) {
  cambiarTabAuth('registro');
  const select = document.getElementById('registro-rol');
  if (rol) select.value = rol;
  actualizarCampoCodigoInvitacion();
  abrirModal('modal-auth');
}

// Muestra/oculta el campo "Código de invitación" del formulario de registro según el rol
// elegido. ROLES_INVITABLES (definido más abajo, junto al Panel Admin) es la única fuente de
// verdad de qué roles lo requieren — nada de repetir "verificador" hardcodeado acá.
function actualizarCampoCodigoInvitacion() {
  const rol = document.getElementById('registro-rol').value;
  const wrap = document.getElementById('registro-codigo-invitacion-wrap');
  const input = document.getElementById('registro-codigo-invitacion');
  const requiere = rolRequiereInvitacion(rol);
  wrap.classList.toggle('hidden', !requiere);
  input.required = requiere;
  if (!requiere) input.value = ''; // no arrastrar un código viejo si el usuario cambia de rol
  sincronizarTarjetasRol();
}

// Las tarjetas de rol del modal de acceso son radios; el <select id="registro-rol"> (oculto) sigue
// siendo la fuente de verdad que leen manejarRegistro y manejarRegistroGoogle. Esto las alinea cuando
// el select cambia por código (abrirRegistroConRol, form.reset()).
function sincronizarTarjetasRol() {
  const rol = document.getElementById('registro-rol').value;
  document.querySelectorAll('input[name="registro-rol-opcion"]').forEach((radio) => { radio.checked = radio.value === rol; });
}

// ============ MODALES ============
let focoPrevioModalAuth = null; // quién tenía el foco antes de abrir el modal de acceso: se lo devolvemos al cerrarlo
let focoPrevioCheckout = null; // ídem para el checkout

function abrirModal(id) {
  document.getElementById(id).classList.remove('hidden');
  if (id === 'modal-auth') {
    focoPrevioModalAuth = document.activeElement;
    requestAnimationFrame(() => {
      enfocarPrimerCampoAuth();
      ajustarBotonesGoogle();
    });
  }
  if (id === 'modal-pedido') {
    focoPrevioCheckout = document.activeElement;
    requestAnimationFrame(() => document.querySelector('#modal-pedido .modal-checkout').focus());
  }
}

function cerrarModal(id) {
  document.getElementById(id).classList.add('hidden');
  if (id === 'modal-auth') {
    limpiarErroresAuth();
    if (focoPrevioModalAuth?.isConnected) focoPrevioModalAuth.focus?.();
    focoPrevioModalAuth = null;
  }
  if (id === 'modal-pedido') {
    if (focoPrevioCheckout?.isConnected) focoPrevioCheckout.focus?.();
    focoPrevioCheckout = null;
  }
}

// ============ LANDING (INVITADOS) ============
// Compartida con los banners de categoría del hero (cargarBannersCategoriaFijos) — un solo criterio
// de "esto es dato de QA/pruebas" en toda la app.
function esProductoDePrueba(p) {
  return /\btest\b|\bqa\b/i.test(`${p.nombre} ${p.categoria}`);
}

// Mismo criterio de siempre para "lo destacado": QA fuera, mejor calificado primero (sin
// calificaciones aún, el sort es estable y conserva el orden del catálogo). Compartido por
// "Recomendado para ti" (landing) y las recomendaciones del panel de búsqueda.
function ordenarPorDestacado(productos) {
  const candidatos = productos.filter((p) => !esProductoDePrueba(p));
  const fuente = candidatos.length >= 3 ? candidatos : productos;
  return [...fuente]
    .sort((a, b) => (b.calificacion_promedio || 0) - (a.calificacion_promedio || 0) || (b.total_resenas || 0) - (a.total_resenas || 0));
}

async function cargarLanding() {
  const carrusel = document.getElementById('landing-productos-carousel');
  carrusel.innerHTML = renderSkeletonProductos(6);
  try {
    const productos = await Api.productos.listar();
    Estado.productos = productos;

    document.getElementById('landing-stat-productos').textContent = productos.length;
    const productoresUnicos = new Set(productos.map((p) => p.productor_id)).size;
    document.getElementById('landing-stat-productores').textContent = productoresUnicos;

    const ordenados = ordenarPorDestacado(productos);

    // Antes se reservaban los primeros 8 para el grid estático "Productos destacados" (eliminado
    // por redundante); el carrusel ahora arranca directo desde el mejor calificado. 10 en vez de
    // 30: con tarjetas más grandes (nombre/precio) 30 quedaba demasiado largo para desplazar.
    const recomendados = ordenados.slice(0, 10);
    carrusel.innerHTML = recomendados.length
      ? renderCarruselProductos(recomendados)
      : '<p class="empty-state"><i class="ti ti-basket-off"></i> Todavía no hay productos publicados.</p>';
    actualizarProgresoCarrusel();

    cargarBannersCategoriaFijos(productos);
  } catch (err) {
    carrusel.innerHTML = '';
    manejarError(err, 'cargar los productos recomendados');
  }
}

// Tarjeta simplificada compartida por las tarjetas de los banners y el carrusel: imagen + nombre +
// (opcional) precio + (opcional) botón "Comprar" que navega al detalle ya existente. La tarjeta
// completa del catálogo (renderTarjetaProductoCatalogo, con precio/calificación/stock) es un
// componente aparte, usado en el catálogo y "Mis productos" — no se toca.
// conPrecio: solo el carrusel lo pide (ver renderCarruselProductos) — las tarjetas de los banners
// se quedan sin precio, sin cambios. Reutiliza el mismo .product-price/.price-unit que ya usa el
// resto de la app (detalle de producto, tarjeta de catálogo) — precio único real de p.precio, sin
// inventar precio "antes/ahora" ni descuento: el catálogo no maneja precios de oferta.
// conBoton: el carrusel lo pide en false — ahí toda la tarjeta ya navega al detalle con un solo
// clic en cualquier parte (imagen incluida, ver el listener de click de landing-productos-carousel),
// así que el botón "Comprar" sobraba. Las tarjetas de los banners siguen con su botón de siempre
// (visible solo al pasar el puntero, ver .landing-banner-tarjetas--cine).
function renderContenidoTarjetaSimple(p, { conPrecio = false, conBoton = true } = {}) {
  const precioHtml = conPrecio
    ? `<span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>`
    : '';
  const botonHtml = conBoton
    ? `<button type="button" class="btn btn-primary btn-sm producto-simple-comprar" data-id="${p.id}">Comprar</button>`
    : '';
  return `
    ${renderMediaProducto(p, 'producto-simple-img')}
    <span class="producto-simple-nombre">${escapeAttr(p.nombre)}</span>
    ${precioHtml}
    ${botonHtml}`;
}

// ============ CARRUSEL "RECOMENDADO PARA TI" (LANDING) ============
function renderCarruselProductos(productos) {
  return productos.map((p) => `
    <div class="landing-carousel-tarjeta" data-id="${p.id}">
      ${renderContenidoTarjetaSimple(p, { conPrecio: true, conBoton: false })}
    </div>`).join('');
}

function actualizarProgresoCarrusel() {
  const cont = document.getElementById('landing-productos-carousel');
  const barra = document.getElementById('landing-carousel-progreso-barra');
  if (!cont || !barra) return;
  const maximoDesplazable = cont.scrollWidth - cont.clientWidth;
  const porcentaje = maximoDesplazable > 0 ? (cont.scrollLeft / maximoDesplazable) * 100 : 0;
  barra.style.width = `${Math.min(100, Math.max(0, porcentaje))}%`;
}

function desplazarCarrusel(direccion) {
  const cont = document.getElementById('landing-productos-carousel');
  if (!cont) return;
  const item = cont.querySelector('.landing-carousel-tarjeta');
  const anchoItem = item ? item.getBoundingClientRect().width + 16 : 166; // 16px = gap del flex
  cont.scrollBy({ left: direccion * anchoItem * 2, behavior: 'smooth' }); // avanza ~2 tarjetas por clic
}

// ============ BANNERS DE CATEGORÍA (LANDING) ============
// Título/subtítulo/CTA de cada banner son texto fijo, ya escrito en el HTML — esta función SOLO
// completa la imagen de fondo y las fotos+nombres de las 4 tarjetas con productos reales del
// catálogo (sin selección dinámica por stock, sin rotación: son 3 secciones siempre visibles).
function normalizarTexto(s) {
  return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
}

// Cada casillero de data-nombres puede traer varios nombres separados por coma (ej. "quinua,kiwicha")
// como alternativas para el mismo lugar — se usa el primero que exista en el catálogo real.
function buscarProductoPorNombre(productos, nombresCsv) {
  const alternativas = String(nombresCsv || '').split(',').map((s) => s.trim()).filter(Boolean);
  for (const alt of alternativas) {
    const objetivo = normalizarTexto(alt);
    const match = productos.find((p) => {
      const n = normalizarTexto(p.nombre);
      return n === objetivo || n.includes(objetivo) || objetivo.includes(n);
    });
    if (match) return match;
  }
  return null;
}

function cargarBannersCategoriaFijos(productos) {
  const candidatos = productos.filter((p) => !esProductoDePrueba(p));

  document.querySelectorAll('.landing-banner-bg[data-nombres]').forEach((el) => {
    const producto = buscarProductoPorNombre(candidatos, el.dataset.nombres);
    if (producto && producto.imagen_principal) {
      el.style.backgroundImage = `url("${producto.imagen_principal}")`;
    }
    // Sin match (o sin foto): se queda el fondo verde sólido ya definido en CSS, no se rompe el banner.
  });

  document.querySelectorAll('.landing-banner-tarjeta[data-nombres]').forEach((el) => {
    const producto = buscarProductoPorNombre(candidatos, el.dataset.nombres);
    if (!producto) return; // sin match en el catálogo actual: se omite en silencio, se queda oculta

    el.innerHTML = renderContenidoTarjetaSimple(producto);
    el.dataset.id = producto.id;
    el.classList.remove('hidden');
  });
}

// ============ BARRA DE CATEGORÍAS (NAVBAR, estilo Samsung Shop) ============
// Un botón por categoría en la barra; cada uno abre su propio dropdown ya filtrado a ESA categoría
// (sin niveles anidados: a diferencia del mega-dropdown genérico anterior, aquí no hace falta un
// clic interno sobre una pestaña para recién cargar contenido, que es lo que causaba el bug de cierre).
// Reutiliza CATEGORIAS_PRODUCTO/ICONOS_CATEGORIA/NOMBRE_CATEGORIA (definidas más abajo, junto al Panel Productor).
const navCatCache = {}; // categoria -> productos[] (evita refetch al reabrir la misma categoría)
const navCatCerrarTimeouts = {}; // categoria -> timeoutId (delay de cierre en hover, evita parpadeo al mover el mouse)

function renderNavCategorias() {
  const cont = document.getElementById('nav-categorias');
  cont.innerHTML = CATEGORIAS_PRODUCTO.map((c) => `
    <div class="nav-cat" data-categoria="${c}">
      <button type="button" class="nav-link nav-cat-trigger" aria-haspopup="true" aria-expanded="false">${NOMBRE_CATEGORIA[c]}</button>
      <div class="nav-cat-panel">
        <div class="nav-cat-panel-inner">
          <div class="nav-cat-panel-productos">
            <div class="nav-cat-cabecera">
              <span class="nav-cat-cabecera-icono" aria-hidden="true">${ICONOS_CATEGORIA[c]}</span>
              <div class="nav-cat-cabecera-texto">
                <strong>${NOMBRE_CATEGORIA[c]}</strong>
                <span class="nav-cat-conteo" id="nav-cat-conteo-${c}"></span>
              </div>
              <button type="button" class="nav-cat-vertodo" data-categoria="${c}">Ver todos <i class="ti ti-arrow-right" aria-hidden="true"></i></button>
            </div>
            <div class="nav-mega-productos" id="nav-cat-productos-${c}"></div>
          </div>
          <div class="nav-cat-panel-descubre">
            <span class="nav-cat-descubre-titulo">Descubre</span>
            <button type="button" class="nav-cat-descubre-link" data-accion="buscar">Ver todo el catálogo</button>
            <button type="button" class="nav-cat-descubre-link" data-accion="blockchain">Cómo funciona la trazabilidad blockchain</button>
          </div>
        </div>
      </div>
    </div>`).join('');
}

function renderSkeletonMega(n = 4) {
  const item = `
    <div class="nav-mega-producto">
      <div class="skeleton" style="aspect-ratio:1/1;border-radius:10px;"></div>
      <div class="skeleton skeleton-line w-70"></div>
      <div class="skeleton skeleton-line w-40"></div>
    </div>`;
  return item.repeat(n);
}

// Estado del producto para la insignia de su tarjeta: en camino al almacén, agotado o pocas unidades.
function insigniaProducto(p) {
  if (p.estado === 'en_transito') return { clase: 'transito', texto: 'En tránsito' };
  if (p.stock <= 0) return { clase: 'agotado', texto: 'Agotado' };
  if (p.stock <= 5) return { clase: 'pocas', texto: 'Últimas unidades' };
  return null;
}

// Tarjeta compacta de producto: la comparten el menú de categorías (donde el CSS oculta la línea del
// productor) y el panel de búsqueda (donde se ve completa).
function renderProductoMega(p) {
  const insignia = insigniaProducto(p);
  const tieneCalificacion = p.calificacion_promedio != null && p.total_resenas > 0;
  return `
    <button type="button" class="nav-mega-producto${insignia?.clase === 'agotado' ? ' es-agotado' : ''}" data-id="${p.id}">
      <span class="nmp-media">
        ${renderMediaProducto(p, 'nav-mega-producto-img')}
        ${insignia ? `<span class="nmp-insignia nmp-insignia--${insignia.clase}">${insignia.texto}</span>` : ''}
      </span>
      <span class="nav-mega-producto-nombre">${escapeAttr(p.nombre)}</span>
      <span class="nmp-productor"><i class="ti ti-plant-2" aria-hidden="true"></i><span>${escapeAttr(p.productor_nombre || 'Productor local')}</span></span>
      <span class="nmp-fila">
        <span class="nav-mega-producto-precio">${formatearMoneda(p.precio)} <small>/ ${unidadCorta(p.unidad_medida)}</small></span>
        ${tieneCalificacion ? `<span class="nmp-rating" aria-label="Calificación ${Number(p.calificacion_promedio).toFixed(1)} de 5"><i class="ti ti-star" aria-hidden="true"></i>${Number(p.calificacion_promedio).toFixed(1)}</span>` : ''}
      </span>
    </button>`;
}

function pintarProductosNavCat(categoria, productos) {
  const cont = document.getElementById(`nav-cat-productos-${categoria}`);
  if (!cont) return;
  cont.innerHTML = productos.length
    ? productos.map(renderProductoMega).join('')
    : '<p class="muted" style="grid-column:1/-1;margin:0;">Sin productos en esta categoría por ahora.</p>';
}

async function cargarProductosNavCat(categoria) {
  if (navCatCache[categoria]) {
    pintarProductosNavCat(categoria, navCatCache[categoria]);
    return;
  }

  const cont = document.getElementById(`nav-cat-productos-${categoria}`);
  if (cont) cont.innerHTML = renderSkeletonMega();

  if (!Estado.productos.length) {
    try { Estado.productos = await Api.productos.listar(); } catch { /* seguimos sin preview si falla */ }
  }
  const todos = Estado.productos.filter((p) => normalizarCategoria(p.categoria) === categoria);
  // Los mejor calificados primero (mismo criterio de "destacados" del landing), no el orden del catálogo.
  const destacados = ordenarPorDestacado(todos).slice(0, 4);
  navCatCache[categoria] = destacados;

  const conteo = document.getElementById(`nav-cat-conteo-${categoria}`);
  if (conteo) conteo.textContent = `${todos.length} producto${todos.length === 1 ? '' : 's'}`;
  pintarProductosNavCat(categoria, destacados);
}

function cerrarTodosLosNavCat(excepto = null) {
  document.querySelectorAll('.nav-cat.open').forEach((el) => {
    if (el.dataset.categoria === excepto) return;
    el.classList.remove('open');
    el.querySelector('.nav-cat-trigger')?.setAttribute('aria-expanded', 'false');
  });
}

// El panel es position:fixed (ver comentario en el CSS de .nav-cat-panel) — hay que posicionarlo a
// mano contra el trigger real, en vez de depender de top:100%/left:0 relativos al padre.
function posicionarNavCatPanel(trigger, panel) {
  const rect = trigger.getBoundingClientRect();
  const anchoPanel = panel.offsetWidth || 560;
  const left = Math.min(rect.left, window.innerWidth - anchoPanel - 16);
  panel.style.top = `${rect.bottom}px`;
  panel.style.left = `${Math.max(16, left)}px`;
}

function abrirNavCat(categoria) {
  clearTimeout(navCatCerrarTimeouts[categoria]);
  cerrarTodosLosNavCat(categoria);
  cerrarNavAccount();
  const el = document.querySelector(`.nav-cat[data-categoria="${categoria}"]`);
  if (!el) return;
  el.classList.add('open');
  el.querySelector('.nav-cat-trigger')?.setAttribute('aria-expanded', 'true');
  const panel = el.querySelector('.nav-cat-panel');
  if (panel) posicionarNavCatPanel(el, panel);
  cargarProductosNavCat(categoria);
}

function cerrarNavCat(categoria) {
  const el = document.querySelector(`.nav-cat[data-categoria="${categoria}"]`);
  if (!el) return;
  el.classList.remove('open');
  el.querySelector('.nav-cat-trigger')?.setAttribute('aria-expanded', 'false');
}

function cerrarNavCatConDelay(categoria) {
  clearTimeout(navCatCerrarTimeouts[categoria]);
  navCatCerrarTimeouts[categoria] = setTimeout(() => cerrarNavCat(categoria), 180);
}

// ============ PANEL DE BÚSQUEDA (NAVBAR, overlay estilo Samsung) ============
// Reemplaza a la antigua vista "Catálogo": la categoría ya la resuelve el mega-dropdown de arriba,
// pero acá también se puede filtrar por categoría (chips), ordenar y ver cuántos productos hay.
// Reutiliza renderProductoMega (misma tarjeta del mega-dropdown) y el criterio de "destacados" ya
// usado en el landing.
let busquedaCategoria = '';       // '' = todas las categorías
let busquedaExplorando = false;   // true = mostrar el catálogo completo en vez de solo las recomendaciones
let busquedaOrden = 'relevancia';
let focoPrevioBusqueda = null;    // quién tenía el foco antes de abrir el panel, para devolvérselo al cerrarlo

async function asegurarProductosCargados() {
  if (Estado.productos.length) return;
  try { Estado.productos = await Api.productos.listar(); } catch { /* seguimos con lo que haya */ }
}

// Un chip por categoría (las mismas de la barra) más "Todo". Se dibujan una vez; después solo se
// actualiza cuál está activo, sin reemplazar los botones (así el foco del teclado no se pierde).
function renderChipsBusqueda() {
  const chip = (valor, etiqueta, emoji) => `
    <button type="button" class="search-chip" data-categoria="${valor}" aria-pressed="false">
      ${emoji ? `<span aria-hidden="true">${emoji}</span> ` : ''}${etiqueta}
    </button>`;
  document.getElementById('search-panel-chips').innerHTML =
    chip('', 'Todo', '') + CATEGORIAS_PRODUCTO.map((c) => chip(c, NOMBRE_CATEGORIA[c], ICONOS_CATEGORIA[c])).join('');
}

function marcarChipsBusqueda() {
  const cont = document.getElementById('search-panel-chips');
  cont.querySelectorAll('.search-chip').forEach((chip) => {
    const activo = chip.dataset.categoria === busquedaCategoria;
    chip.classList.toggle('active', activo);
    chip.setAttribute('aria-pressed', String(activo));
  });
  // En móvil las categorías van en una fila desplazable: centra la activa. Se mueve solo esa fila
  // (scrollIntoView podría desplazar también el panel entero).
  const activo = cont.querySelector('.search-chip.active');
  if (activo && cont.scrollWidth > cont.clientWidth) {
    const izquierda = activo.getBoundingClientRect().left - cont.getBoundingClientRect().left + cont.scrollLeft;
    cont.scrollLeft = izquierda - (cont.clientWidth - activo.offsetWidth) / 2;
  }
}

// Menor = más relevante: primero los que empiezan con lo escrito, luego los que lo contienen en el
// nombre y al final los que solo coinciden por categoría o productor.
function puntuarCoincidencia(p, texto) {
  const nombre = String(p.nombre || '').toLowerCase();
  if (nombre.startsWith(texto)) return 0;
  if (nombre.includes(texto)) return 1;
  return 2;
}

const ORDENES_BUSQUEDA = {
  'precio-asc': (a, b) => Number(a.precio) - Number(b.precio),
  'precio-desc': (a, b) => Number(b.precio) - Number(a.precio),
  calificacion: (a, b) => (b.calificacion_promedio || 0) - (a.calificacion_promedio || 0) || (b.total_resenas || 0) - (a.total_resenas || 0),
};

function actualizarResultadosBusqueda() {
  const textoOriginal = document.getElementById('input-busqueda-panel').value.trim();
  const texto = textoOriginal.toLowerCase();

  let productos = Estado.productos;
  if (busquedaCategoria) productos = productos.filter((p) => normalizarCategoria(p.categoria) === busquedaCategoria);
  if (texto) productos = productos.filter((p) => `${p.nombre} ${p.categoria || ''} ${p.productor_nombre || ''}`.toLowerCase().includes(texto));

  let titulo;
  let icono;
  if (texto) {
    titulo = `Resultados para "${textoOriginal}"`;
    icono = 'search';
    productos = [...productos].sort((a, b) => puntuarCoincidencia(a, texto) - puntuarCoincidencia(b, texto));
  } else if (busquedaCategoria) {
    titulo = `Categoría: ${NOMBRE_CATEGORIA[busquedaCategoria]}`;
    icono = 'category';
    productos = ordenarPorDestacado(productos);
  } else if (busquedaExplorando) {
    titulo = 'Todo el catálogo';
    icono = 'building-store';
    productos = ordenarPorDestacado(productos);
  } else {
    titulo = 'Recomendaciones';
    icono = 'sparkles';
    productos = ordenarPorDestacado(productos).slice(0, 8);
  }

  const orden = ORDENES_BUSQUEDA[busquedaOrden];
  if (orden) productos = [...productos].sort(orden);
  pintarResultadosBusqueda(productos, titulo, icono, textoOriginal);
}

function pintarResultadosBusqueda(productos, titulo, icono, termino = '') {
  document.getElementById('search-panel-heading').innerHTML = `<i class="ti ti-${icono}"></i> ${escapeAttr(titulo)}`;
  const grid = document.getElementById('search-panel-grid');
  const vacio = document.getElementById('search-panel-empty');
  document.getElementById('search-panel-conteo').textContent = productos.length
    ? `${productos.length} producto${productos.length === 1 ? '' : 's'}` : '';

  if (!productos.length) {
    grid.innerHTML = '';
    vacio.innerHTML = `
      <span class="search-vacio-icono"><i class="ti ti-search-off" aria-hidden="true"></i></span>
      <strong>${termino ? `No encontramos productos para "${escapeAttr(termino)}"` : 'No hay productos en esta categoría por ahora'}</strong>
      <p>Revisa la ortografía o prueba con otra categoría.</p>
      <button type="button" class="btn btn-outline" id="btn-ver-todo-catalogo">Ver todo el catálogo</button>`;
    vacio.classList.remove('hidden');
    return;
  }
  vacio.classList.add('hidden');
  grid.innerHTML = productos.map(renderProductoMega).join('');
}

// "Búsquedas populares" (columna angosta, término real más buscado en los últimos 7 días — ver
// GET /productos/busquedas/populares). Se carga una sola vez al abrir el panel y queda fija ahí
// aunque el usuario escriba (no es parte del área de resultados que cambia con la búsqueda).
async function cargarBusquedasPopulares() {
  const cont = document.getElementById('search-panel-populares');
  try {
    const populares = await Api.productos.busquedasPopulares();
    if (!populares.length) {
      cont.classList.add('hidden');
      return;
    }
    document.getElementById('search-panel-populares-lista').innerHTML = populares
      .map((p) => `<button type="button" class="search-panel-popular-item" data-termino="${escapeAttr(p.termino)}">${escapeAttr(p.termino)}</button>`)
      .join('');
    cont.classList.remove('hidden');
  } catch {
    cont.classList.add('hidden');
  }
}

let debounceRegistrarBusqueda = null;
// Fire-and-forget: no bloquea ni se muestra al usuario, y si falla no afecta la búsqueda en sí
// (por eso el .catch vacío). Con debounce para no registrar cada tecla suelta, solo cuando el
// usuario deja de escribir un momento.
function registrarBusquedaConDebounce(termino) {
  clearTimeout(debounceRegistrarBusqueda);
  if (termino.trim().length < 2) return;
  debounceRegistrarBusqueda = setTimeout(() => {
    Api.productos.registrarBusqueda(termino.trim()).catch(() => {});
  }, 600);
}

function buscarEnPanel() {
  const input = document.getElementById('input-busqueda-panel');
  document.getElementById('btn-limpiar-busqueda').classList.toggle('hidden', !input.value);
  if (input.value.trim()) registrarBusquedaConDebounce(input.value.trim().toLowerCase());
  else clearTimeout(debounceRegistrarBusqueda);
  actualizarResultadosBusqueda();
}

// categoria: abre ya filtrado (los botones "Ver todos" del menú de categorías); explorar: abre con el
// catálogo completo ("Ver todo el catálogo"). Sin argumentos: recomendaciones, como siempre.
// OJO: también se usa directo como listener de un click, así que el primer argumento puede ser un
// evento — solo vale si es un string.
async function abrirPanelBusqueda(categoria = '', { explorar = false } = {}) {
  cerrarTodosLosNavCat();
  cerrarNavAccount();
  document.getElementById('nav-links').classList.remove('open');
  focoPrevioBusqueda = document.activeElement;

  busquedaCategoria = typeof categoria === 'string' ? categoria : '';
  busquedaExplorando = explorar === true || Boolean(busquedaCategoria);
  busquedaOrden = 'relevancia';
  document.getElementById('search-panel-orden').value = 'relevancia';

  const input = document.getElementById('input-busqueda-panel');
  input.value = '';
  document.getElementById('btn-limpiar-busqueda').classList.add('hidden');
  const overlay = document.getElementById('panel-busqueda');
  overlay.classList.remove('hidden');
  overlay.scrollTop = 0;
  if (!document.querySelector('#search-panel-chips .search-chip')) renderChipsBusqueda();
  marcarChipsBusqueda();
  input.focus();
  cargarBusquedasPopulares();
  await asegurarProductosCargados();
  actualizarResultadosBusqueda();
}

function cerrarPanelBusqueda() {
  document.getElementById('panel-busqueda').classList.add('hidden');
  if (focoPrevioBusqueda?.isConnected) focoPrevioBusqueda.focus?.();
  focoPrevioBusqueda = null;
}

// ============ ÍCONO DE CUENTA (NAVBAR) ============
// Mismo patrón hover/tap que la barra de categorías, un solo nivel (ícono -> menú), sin anidar.
let navAccountCerrarTimeout = null;

function abrirNavAccount() {
  clearTimeout(navAccountCerrarTimeout);
  cerrarTodosLosNavCat();
  const el = document.getElementById('nav-account');
  el.classList.add('open');
  document.getElementById('btn-account').setAttribute('aria-expanded', 'true');
}

function cerrarNavAccount() {
  const el = document.getElementById('nav-account');
  el.classList.remove('open');
  document.getElementById('btn-account').setAttribute('aria-expanded', 'false');
}

function cerrarNavAccountConDelay() {
  clearTimeout(navAccountCerrarTimeout);
  navAccountCerrarTimeout = setTimeout(cerrarNavAccount, 180);
}

function renderTarjetaProductoCatalogo(p, { clickable = true, indice = 0 } = {}) {
  const stockBajo = p.stock <= 5;
  const favorito = clickable && esFavorito(p.id);
  const tieneCalificacion = p.calificacion_promedio != null && p.total_resenas > 0;
  const retraso = Math.min(indice, 12) * 35;
  const estilo = `animation-delay:${retraso}ms${clickable ? '' : ';cursor:default'}`;
  return `
    <div class="product-card" style="${estilo}" ${clickable ? `data-id="${p.id}"` : ''}>
      <div class="product-card-media">
        ${renderMediaProducto(p, 'product-banner')}
        ${clickable ? `
        <button type="button" class="btn-favorito ${favorito ? 'activo' : ''}" data-id="${p.id}" aria-label="Favorito" aria-pressed="${favorito}">
          <i class="ti ti-heart"></i>
        </button>` : ''}
      </div>
      <div class="product-card-body">
        <div class="product-card-top">
          <span class="pill pill-categoria">${iconoCategoria(p.categoria)} ${escapeAttr(nombreCategoria(p.categoria))}</span>
          ${tieneCalificacion ? `<span class="product-rating"><span class="rating-stars"><i class="ti ti-star estrella-llena"></i></span> ${Number(p.calificacion_promedio).toFixed(1)} <span class="rating-count-mini">(${p.total_resenas})</span></span>` : ''}
        </div>
        <h4>${escapeAttr(p.nombre)}</h4>
        <span class="product-productor"><i class="ti ti-plant-2"></i> ${escapeAttr(p.productor_nombre || 'Productor local')}</span>
        <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
        <span class="product-stock ${stockBajo ? 'low' : ''}">${p.stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles` : 'Sin stock'}</span>
        ${clickable ? `
        <button class="btn btn-primary btn-agregar" data-id="${p.id}" ${p.stock <= 0 ? 'disabled' : ''}>
          ${p.stock <= 0 ? 'Agotado' : '+ Agregar al pedido'}
        </button>` : ''}
      </div>
    </div>`;
}


function renderBloqueProductor(p, productor) {
  const nombre = productor?.nombre || p.productor_nombre || 'Productor local';
  const ubicacionTexto = productor?.comunidad || productor?.ubicacion || null;
  const tieneMapa = hayCoordenadas(productor?.latitud, productor?.longitud);

  return `
    <div class="detalle-productor-card">
      <div class="detalle-productor-header">
        <span class="detalle-productor-icono"><i class="ti ti-plant-2" aria-hidden="true"></i></span>
        <div class="detalle-productor-info">
          <span class="detalle-productor-label">Cultivado por</span>
          <h4 class="detalle-productor-nombre">${escapeAttr(nombre)}</h4>
          ${ubicacionTexto ? `<span class="detalle-productor-ubicacion"><i class="ti ti-map-pin" aria-hidden="true"></i> ${escapeAttr(ubicacionTexto)}</span>` : ''}
        </div>
      </div>
      ${tieneMapa ? `<div id="mapa-productor-detalle" class="mapa-mini" role="img" aria-label="Zona de origen aproximada de ${escapeAttr(nombre)} en el mapa"></div>
      <p class="mapa-ayuda">Zona de origen aproximada.</p>` : ''}
    </div>`;
}

function renderResumenCalificacion(p) {
  if (!p.calificacion_promedio || !p.total_resenas) {
    return '<p class="rating-row"><span class="rating-count">Sin reseñas todavía — ¡sé el primero en opinar!</span></p>';
  }
  return `
    <p class="rating-row">
      <span class="rating-stars">${renderEstrellas(p.calificacion_promedio)}</span>
      <span class="rating-value">${Number(p.calificacion_promedio).toFixed(1)}</span>
      <span class="rating-count">(basado en ${p.total_resenas} reseña${p.total_resenas === 1 ? '' : 's'})</span>
    </p>`;
}

// Reseñas de la página de detalle: a la izquierda el resumen (promedio, barras por estrellas — calculadas
// con las reseñas reales) y el formulario; a la derecha las opiniones. El título de la sección lo pone
// renderPaginaDetalleProducto, igual que en las demás secciones.
function renderSeccionResenas(resenas, p) {
  const total = resenas.length;
  const promedio = total ? (p?.calificacion_promedio ?? resenas.reduce((suma, r) => suma + r.calificacion, 0) / total) : null;

  const resumenHtml = total ? `
    <div class="resenas-resumen">
      <div class="resenas-promedio">
        <strong>${Number(promedio).toFixed(1)}</strong>
        <span class="mp-solo-lectores">de 5 estrellas</span>
        <span class="rating-stars" aria-hidden="true">${renderEstrellas(promedio)}</span>
        <span class="rating-count">${total} reseña${total === 1 ? '' : 's'}</span>
      </div>
      <ul class="resenas-barras" aria-label="Reseñas según su cantidad de estrellas">
        ${[5, 4, 3, 2, 1].map((n) => {
          const cantidad = resenas.filter((r) => Math.round(r.calificacion) === n).length;
          return `<li><span>${n}</span><i class="ti ti-star" aria-hidden="true"></i><span class="resenas-barra"><span style="width:${(cantidad / total) * 100}%"></span></span><span class="resenas-barra-n">${cantidad}</span></li>`;
        }).join('')}
      </ul>
    </div>` : '';

  const listaHtml = total
    ? resenas.map((r) => `
        <article class="resena-card">
          <header class="resena-header">
            <span class="resena-avatar" aria-hidden="true">${escapeAttr((r.usuario_nombre || '?').trim().charAt(0).toUpperCase())}</span>
            <div class="resena-quien">
              <span class="resena-usuario">${escapeAttr(r.usuario_nombre)}</span>
              <span class="resena-fecha">${formatearFechaServidor(r.fecha_creacion)}</span>
            </div>
            <span class="rating-stars" role="img" aria-label="${r.calificacion} de 5 estrellas">${renderEstrellas(r.calificacion)}</span>
          </header>
          ${r.comentario ? `<p class="resena-comentario">${escapeAttr(r.comentario)}</p>` : ''}
        </article>`).join('')
    : `
        <div class="resenas-vacio">
          <i class="ti ti-message-circle" aria-hidden="true"></i>
          <strong>Todavía no hay reseñas</strong>
          <p>Cuando alguien pruebe este producto, su opinión aparecerá aquí.</p>
        </div>`;

  const formularioHtml = Estado.token ? `
    <form id="form-resena" class="resena-form">
      <h4>Deja tu reseña</h4>
      <div class="estrellas-selector" role="group" aria-label="Tu calificación">
        ${[1, 2, 3, 4, 5].map((v) => `<button type="button" data-valor="${v}" aria-label="${v} estrella${v === 1 ? '' : 's'}" aria-pressed="false"><i class="ti ti-star" aria-hidden="true"></i></button>`).join('')}
      </div>
      <textarea placeholder="Cuéntanos qué te pareció (opcional)" aria-label="Tu comentario"></textarea>
      <button type="submit" class="btn btn-primary">Publicar reseña</button>
    </form>`
    : `
    <div class="resena-login">
      <i class="ti ti-lock" aria-hidden="true"></i>
      <p>Inicia sesión para dejar tu propia reseña.</p>
      <button type="button" class="btn btn-outline" id="btn-resena-login">Iniciar sesión</button>
    </div>`;

  return `
    <div class="detalle-resenas-grid">
      <div class="detalle-resenas-lateral">${resumenHtml}${formularioHtml}</div>
      <div class="detalle-resenas-lista">${listaHtml}</div>
    </div>`;
}

function renderResenasSoloLectura(resenas) {
  if (!resenas.length) {
    return '<p class="muted" style="padding-top:6px;">Aún no hay reseñas para este producto.</p>';
  }
  return `
    <div class="resenas-lista" style="margin-top:10px;">
      ${resenas.map((r) => `
        <div class="resena-card">
          <div class="resena-header">
            <span class="resena-usuario">${escapeAttr(r.usuario_nombre)}</span>
            <span class="rating-stars">${renderEstrellas(r.calificacion)}</span>
          </div>
          <div class="resena-fecha">${formatearFechaServidor(r.fecha_creacion)}</div>
          ${r.comentario ? `<p class="resena-comentario">${escapeAttr(r.comentario)}</p>` : ''}
        </div>`).join('')}
    </div>`;
}

// ============ DETALLE DE PRODUCTO (página propia, estilo Samsung) ============
// Header + pestañas fijos (sticky), secciones apiladas con scroll-reveal por IntersectionObserver
// (no animation-delay fijo: estas secciones aparecen según scroll real, no al cargar la vista).
let vistaAntesDeDetalle = 'landing';

async function cargarDetalleProducto(id) {
  const contenido = document.getElementById('detalle-producto-content');
  contenido.innerHTML = renderSkeletonDetalle();
  window.scrollTo({ top: 0 });

  let p;
  try {
    p = await Api.productos.obtener(id);
  } catch (err) {
    manejarError(err, 'cargar el producto');
    cambiarVista(vistaAntesDeDetalle);
    return;
  }

  let resenas = [];
  let productor = null;
  try { resenas = await Api.productos.listarResenas(id); } catch { /* sin reseñas disponibles por ahora */ }
  try { productor = await Api.productores.obtener(p.productor_id); } catch { /* seguimos sin mapa */ }

  renderPaginaDetalleProducto(p, resenas, productor);
}

// Productos con video en su galería de detalle. Convención de archivos (no de código): un
// producto nuevo se activa subiendo assets/videos/<slug>-video-1.mp4 (y -2/-3 si aplica) con el
// mismo slug que arma slugProducto(), y agregando ese slug acá — no hace falta tocar el resto de
// la lógica de render, que ya sabe usar el video si el archivo existe en esa posición.
const PRODUCTOS_CON_VIDEO = ['chirimoya'];

function slugProducto(nombre) {
  return normalizarTexto(nombre).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

// Etiqueta/título/párrafo/fondo/tema por posición (hero=0, primera foto extra=1, segunda=2) —
// estilo Samsung (etiqueta pequeña en mayúsculas vía CSS + titular corto + párrafo detallado,
// superpuestos sobre el video con degradado). Mismo texto para cualquier producto con video, con
// su nombre real interpolado en minúsculas (así lee natural en medio de la oración, ej. "la
// chirimoya es...", no "la Chirimoya es..."). "fondo" y "tema" ('oscuro'|'claro') van por sección
// — no un negro parejo global — así cada una complementa el tono de SU video específico; "tema"
// decide el color del degradado y del texto (blanco sobre oscuro, o el inverso sobre claro).
function textosVideoGaleria(p) {
  const nombre = (p.nombre || '').toLowerCase();
  return [
    {
      etiqueta: 'Nutrición', titulo: 'Nutritiva y rica en vitaminas',
      parrafo: `La ${nombre} es fuente natural de vitamina C, fibra y antioxidantes — un snack saludable directo de la chacra en Ayacucho.`,
      fondo: '#0f140f', tema: 'oscuro',
    },
    {
      etiqueta: 'Trazabilidad', titulo: 'Cada fruto, verificado',
      parrafo: `Cada ${nombre} que vendemos queda registrada en una cadena de bloques verificable — sabes exactamente de dónde viene.`,
      fondo: '#1a1410', tema: 'oscuro',
    },
    {
      etiqueta: 'Origen', titulo: 'Cultivada por manos que conoces',
      parrafo: `Detrás de cada ${nombre} hay un productor real de tu comunidad, no una gran distribuidora.`,
      fondo: '#f5f0e8', tema: 'claro',
    },
  ];
}

// Fotos del producto en su orden (la primera es la principal). Sin fotos: lista vacía y el visor
// muestra el emoji de su categoría.
function fotosProducto(p) {
  const urls = (Array.isArray(p.imagenes) ? [...p.imagenes].sort((a, b) => a.orden - b.orden).map((img) => img.url) : []).filter(Boolean);
  if (!urls.length && p.imagen_url) urls.push(p.imagen_url);
  return urls;
}

// Galería: foto principal grande + tira de miniaturas (patrón de las fichas de producto de
// marketplace). Con una sola foto no hay flechas ni miniaturas. El comportamiento (cambiar de foto,
// teclado, deslizar) lo pone inicializarGaleriaDetalle.
function renderGaleriaDetalle(p, fotos, insignia) {
  const varias = fotos.length > 1;
  return `
    <div class="detalle-galeria" id="detalle-galeria">
      <div class="detalle-visor"${varias ? ` tabindex="0" role="group" aria-roledescription="carrusel" aria-label="Fotos de ${escapeAttr(p.nombre)}"` : ''}>
        ${fotos.length
          ? `<img class="detalle-visor-img" src="${escapeAttr(fotos[0])}" alt="${escapeAttr(p.nombre || '')}" fetchpriority="high">`
          : `<span class="detalle-visor-emoji" aria-hidden="true">${emojiParaProducto(p)}</span>`}
        ${insignia ? `<span class="nmp-insignia nmp-insignia--${insignia.clase}">${insignia.texto}</span>` : ''}
        ${varias ? `
        <button type="button" class="detalle-visor-nav detalle-visor-prev" aria-label="Foto anterior"><i class="ti ti-chevron-left" aria-hidden="true"></i></button>
        <button type="button" class="detalle-visor-nav detalle-visor-next" aria-label="Foto siguiente"><i class="ti ti-chevron-right" aria-hidden="true"></i></button>
        <span class="detalle-contador" role="status" aria-live="polite">1 / ${fotos.length}</span>` : ''}
      </div>
      ${varias ? `
      <div class="detalle-miniaturas">
        ${fotos.map((url, i) => `
          <button type="button" class="detalle-miniatura${i === 0 ? ' active' : ''}" data-src="${escapeAttr(url)}" aria-label="Ver foto ${i + 1} de ${fotos.length}" aria-current="${i === 0}">
            <img src="${escapeAttr(url)}" alt="" loading="lazy">
          </button>`).join('')}
      </div>` : ''}
    </div>`;
}

// Secciones con video (a sangre completa, debajo de la ficha de compra). Un video solo reemplaza una
// posición que YA tiene una foto real (esa foto pasa a ser el poster): si el producto tiene menos fotos
// que videos definidos, esa posición no existe. Ver PRODUCTOS_CON_VIDEO/textosVideoGaleria.
function renderVideosDetalle(p, fotos) {
  const slug = slugProducto(p.nombre);
  if (!PRODUCTOS_CON_VIDEO.includes(slug)) return '';
  const textos = textosVideoGaleria(p);

  return fotos.map((url, i) => {
    const t = textos[i];
    if (!url || !t) return '';
    const videoUrl = `assets/videos/${slug}-video-${i + 1}.mp4`;
    return `
      <div class="${i === 0 ? 'detalle-hero' : 'detalle-galeria-item'} detalle-scroll-seccion detalle-video-seccion detalle-video-seccion--${t.tema}" style="background:${t.fondo}">
        <div class="detalle-video-wrap">
          <video class="detalle-video" muted playsinline preload="metadata" poster="${escapeAttr(url)}" data-src="${escapeAttr(videoUrl)}"></video>
          <button type="button" class="detalle-video-replay hidden" aria-label="Reproducir de nuevo"><i class="ti ti-player-play"></i></button>
          <div class="detalle-video-degradado"></div>
          <div class="detalle-video-texto">
            <span class="detalle-video-etiqueta">${escapeAttr(t.etiqueta)}</span>
            <h3>${escapeAttr(t.titulo)}</h3>
            <p>${escapeAttr(t.parrafo)}</p>
          </div>
        </div>
      </div>`;
  }).join('');
}

// Adjunta los listeners UNA sola vez por sección (llamado al armar la página, antes de que entre
// en viewport). El play/pause real lo disparan reproducirVideoGaleria/pausarYReiniciarVideoGaleria,
// llamadas repetidamente por el observer dedicado a video cada vez que la sección entra/sale.
function configurarVideoGaleria(seccion) {
  const video = seccion.querySelector('.detalle-video');
  const replayBtn = seccion.querySelector('.detalle-video-replay');
  if (!video || !replayBtn) return;

  video.addEventListener('error', () => {
    const wrap = seccion.querySelector('.detalle-video-wrap');
    const poster = video.getAttribute('poster');
    if (wrap) wrap.outerHTML = poster ? `<img src="${escapeAttr(poster)}" alt="" loading="lazy">` : '';
  });
  video.addEventListener('ended', () => replayBtn.classList.remove('hidden'));
  replayBtn.addEventListener('click', () => {
    // Guarda de idempotencia: si ya está oculto, un doble click (o un segundo evento duplicado)
    // no debe disparar un segundo play() superpuesto al primero — llamar play() dos veces casi
    // junto puede pisarse entre sí y dejar el video pausado a mitad de arranque en algunos
    // navegadores, aunque la promesa del primero haya resuelto bien.
    if (replayBtn.classList.contains('hidden')) return;
    replayBtn.classList.add('hidden');
    // Nada de "video.currentTime = 0" antes del play(): un video terminado ya reinicia solo desde
    // el principio al llamar play() (comportamiento nativo del elemento) — forzar el seek justo
    // antes rompe la promesa de play() en algunos navegadores (queda "colgado" en pausa, sin
    // avisar del error, y el botón de repetir ya se ocultó dando la falsa impresión de que sí
    // arrancó). Visto en pruebas reales con Chromium.
    video.play().catch(() => replayBtn.classList.remove('hidden'));
  });
}

// Se llama cada vez que la sección de video CRUZA el umbral alto de visibilidad hacia adentro
// (ver inicializarScrollRevealDetalle). Carga el src real la primera vez (estaba en data-src para
// no descargar los 3 videos de golpe al abrir la página) y reproduce desde donde haya quedado —
// que gracias a pausarYReiniciarVideoGaleria siempre es 0, así que en la práctica es "desde el
// principio" cada vez que la sección vuelve a quedar mayormente visible.
function reproducirVideoGaleria(seccion) {
  const video = seccion.querySelector('.detalle-video');
  const replayBtn = seccion.querySelector('.detalle-video-replay');
  if (!video) return;
  if (!video.src) video.src = video.dataset.src;
  replayBtn?.classList.add('hidden');
  video.play().catch(() => { /* algunos navegadores bloquean hasta un autoplay muted (ahorro de
    datos, etc.) — no rompe nada, el usuario puede iniciarlo con el botón de repetir manualmente */
    replayBtn?.classList.remove('hidden');
  });
}

// Se llama cuando la sección de video sale del umbral alto de visibilidad (el usuario sigue
// bajando/subiendo). Pausa y reinicia explícitamente — así nunca queda sonando de fondo ni
// avanzando fuera de pantalla, y nunca hay más de un video "activo" a la vez sin importar qué tan
// rápido haga scroll el usuario (ver CORRECCIÓN 2).
function pausarYReiniciarVideoGaleria(seccion) {
  const video = seccion.querySelector('.detalle-video');
  const replayBtn = seccion.querySelector('.detalle-video-replay');
  if (!video || !video.src) return; // nunca llegó a cargar (nunca entró en viewport) — nada que pausar
  video.pause();
  video.currentTime = 0;
  replayBtn?.classList.add('hidden');
}

// El video del hero de landing NO tiene lógica JS: es un <video autoplay muted loop playsinline>
// nativo con src directo (ver index.html). No usa configurarVideoGaleria() ni el observer de
// play-al-scroll — eso es exclusivo de los videos de producto (reproducir una vez + botón de
// repetir). El hero simplemente hace loop de fondo desde que carga.

// Tratamiento "banner cinematográfico" de la landing — ya aplicado a los 3 banners de categoría
// (Frutas de temporada, Sabor de la sierra, Lácteos frescos; ver .landing-banner-cine en index.html
// y su comentario en style.css). Genérico por diseño — querySelectorAll agarra CUALQUIER banner con
// esa clase, así que sumar uno nuevo es solo agregar la clase en el HTML, sin tocar esta función.
// El observer mira
// .landing-banner-content (el bloque de texto en sí, no la sección .landing-banner-cine completa de
// ~78vh) — el texto vive centrado verticalmente ahí dentro (align-items:center), así que observar la
// sección entera cruzaba el umbral mucho antes de que el texto llegara a estar en pantalla de
// verdad. El efecto escalonado (título → párrafo → botón) lo da el CSS (transition-delay creciente
// por elemento), no este observer.
// A diferencia de inicializarScrollRevealDetalle (más abajo, dispara UNA vez y se desconecta): acá
// se repite cada vez que el bloque entra/sale de pantalla — .visible se agrega Y se quita según
// entry.isIntersecting, sin unobserve(), a propósito (así lo pidió el usuario: el efecto debe
// repetirse siempre que se pasa por el banner, no solo la primera vez). Se llama UNA sola vez desde
// inicializarEventos(): el HTML de la landing es estático, no hace falta reconectar el observer.
// Control del video de fondo del hero: un video que arranca solo tiene que poder detenerse
// (WCAG 2.2.2). Además se pausa cuando el hero sale de pantalla (no decodifica video que nadie ve) y
// no arranca si el usuario pidió menos movimiento: en ese caso queda el poster.
function inicializarControlVideoLanding() {
  const video = document.querySelector('.landing-hero video');
  const boton = document.getElementById('landing-video-control');
  if (!video || !boton) return;

  let pausadoPorUsuario = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const pintar = () => {
    const detenido = video.paused;
    boton.setAttribute('aria-pressed', String(detenido));
    boton.setAttribute('aria-label', detenido ? 'Reproducir video de fondo' : 'Pausar video de fondo');
    boton.innerHTML = `<i class="ti ti-player-${detenido ? 'play' : 'pause'}"></i>`;
  };
  if (pausadoPorUsuario) video.pause();

  boton.addEventListener('click', () => {
    pausadoPorUsuario = !video.paused; // si se estaba reproduciendo, lo detiene el usuario
    if (video.paused) video.play().catch(() => {}); else video.pause();
  });
  video.addEventListener('play', pintar);
  video.addEventListener('pause', pintar);

  new IntersectionObserver(([entrada]) => {
    if (!entrada.isIntersecting) video.pause();
    else if (!pausadoPorUsuario) video.play().catch(() => {});
  }, { threshold: 0.15 }).observe(video);
  pintar();
}

function inicializarBannerCinematico() {
  const contenidos = document.querySelectorAll('.landing-banner-cine .landing-banner-content');
  if (!contenidos.length) return;

  if (!('IntersectionObserver' in window)) {
    contenidos.forEach((c) => c.classList.add('visible'));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      entry.target.classList.toggle('visible', entry.isIntersecting);
    });
  }, { threshold: 0.3 });
  contenidos.forEach((c) => observer.observe(c));
}

// Unidad base (con su propio precio) + cada unidad alternativa, mismo formato de label que ya
// usan los <select> de unidad en Registrar-siembra / gestión de unidades del productor
// (infoUnidad(...).label) — así el comprador ve el mismo vocabulario en todo el sitio. value=""
// para la base (select.value || null da directamente lo que espera unidadSeleccionada).
function opcionesUnidadCompraHtml(p) {
  const base = `<option value="">${escapeAttr(infoUnidad(p.unidad_medida).label)} — ${formatearMoneda(p.precio)}</option>`;
  const alternativas = (p.unidades_alternativas || [])
    .map((u) => `<option value="${escapeAttr(u.unidad)}">${escapeAttr(infoUnidad(u.unidad).label)} — ${formatearMoneda(u.precio)}</option>`)
    .join('');
  return base + alternativas;
}

// Tarjeta de compra (columna derecha de la ficha): todo lo necesario para decidir y comprar sin ir a
// buscarlo más abajo. Los ids se conservan: renderPaginaDetalleProducto
// los cablea (precio, stock, unidad, cantidad, botones).
function renderCompraDetalle(p, productor, { favorito, stockBajo, sinStock, tieneAlternativas, precioHtml }) {
  const nombreProductor = productor?.nombre || p.productor_nombre || 'Productor local';
  const ubicacion = productor?.comunidad || productor?.ubicacion || '';
  return `
    <aside class="detalle-compra" id="detalle-compra" aria-label="Comprar ${escapeAttr(p.nombre)}">
      <div class="detalle-encabezado">
        <span class="pill pill-categoria">${iconoCategoria(p.categoria)} ${escapeAttr(nombreCategoria(p.categoria))}</span>
        <button type="button" class="btn-favorito btn-favorito-detalle ${favorito ? 'activo' : ''}" data-id="${p.id}" aria-label="Favorito" aria-pressed="${favorito}">
          <i class="ti ti-heart"></i> ${favorito ? 'Guardado' : 'Guardar'}
        </button>
      </div>

      <div class="detalle-cabecera">
        <h2>${escapeAttr(p.nombre)}</h2>
        ${renderResumenCalificacion(p)}
      </div>

      <div class="detalle-precio-row">
        <span class="product-price" id="detalle-precio-principal">${precioHtml}</span>
        <span class="product-stock detalle-stock ${sinStock ? 'agotado' : stockBajo ? 'low' : ''}" id="detalle-stock-texto">${sinStock ? 'Sin stock disponible' : `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles`}</span>
      </div>

      ${!sinStock && tieneAlternativas ? `
      <label class="detalle-unidad-label">Unidad de compra
        <select id="detalle-unidad-selector">${opcionesUnidadCompraHtml(p)}</select>
      </label>` : ''}

      <div class="detalle-accion-principal">
        ${!sinStock ? `
        <div class="detalle-cantidad-fila">
          <div class="carrito-qty detalle-cantidad-selector" role="group" aria-label="Cantidad">
            <button type="button" id="btn-cantidad-menos" aria-label="Disminuir cantidad">−</button>
            <span id="detalle-cantidad-valor" role="status" aria-live="polite">1</span>
            <button type="button" id="btn-cantidad-mas" aria-label="Aumentar cantidad">+</button>
          </div>
          <div class="detalle-total" aria-live="polite">
            <span>Subtotal</span>
            <strong id="detalle-total-valor">${formatearMoneda(p.precio)}</strong>
          </div>
        </div>` : ''}
        <button class="btn btn-primary btn-lg btn-block" data-id="${p.id}" id="btn-agregar-detalle" ${sinStock ? 'disabled' : ''}>
          ${sinStock ? 'Agotado' : '+ Agregar al pedido'}
        </button>
      </div>

      <ul class="detalle-confianza">
        <li><i class="ti ti-shield-check" aria-hidden="true"></i><span>Origen verificado</span></li>
        <li><i class="ti ti-plant-2" aria-hidden="true"></i><span>Directo del productor</span></li>
        <li><i class="ti ti-truck-delivery" aria-hidden="true"></i><span>Entrega con repartidor</span></li>
      </ul>

      <button type="button" class="detalle-productor-mini" data-ir="detalle-seccion-productor">
        <span class="detalle-productor-mini-icono"><i class="ti ti-plant-2" aria-hidden="true"></i></span>
        <span class="detalle-productor-mini-texto">
          <small>Cultivado por</small>
          <strong>${escapeAttr(nombreProductor)}</strong>
          ${ubicacion ? `<small><i class="ti ti-map-pin" aria-hidden="true"></i> ${escapeAttr(ubicacion)}</small>` : ''}
        </span>
        <i class="ti ti-arrow-right" aria-hidden="true"></i>
      </button>
    </aside>`;
}

function renderPaginaDetalleProducto(p, resenas, productor) {
  const contenido = document.getElementById('detalle-producto-content');
  const favorito = esFavorito(p.id);
  const stockBajo = p.stock > 0 && p.stock <= 5;
  const sinStock = p.stock <= 0;
  const tieneAlternativas = (p.unidades_alternativas || []).length > 0;
  const precioHtml = `${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span>`;
  const fotos = fotosProducto(p);
  // El detalle no trae "estado"; si el producto está en el catálogo cargado se usa el de allí (igual que las tarjetas).
  const enCatalogo = Estado.productos.find((x) => x.id === p.id);
  const insignia = insigniaProducto({ ...p, estado: p.estado ?? enCatalogo?.estado });

  contenido.innerHTML = `
    <header class="detalle-barra" id="detalle-barra">
      <button type="button" class="detalle-volver" id="btn-volver-detalle" aria-label="Volver"><i class="ti ti-arrow-left" aria-hidden="true"></i><span class="detalle-volver-texto">Volver</span></button>
      <nav class="detalle-tabs" id="detalle-tabs" aria-label="Secciones del producto">
        <button type="button" class="detalle-tab active" data-target="detalle-seccion-descripcion" aria-current="true">Producto</button>
        <button type="button" class="detalle-tab" data-target="detalle-seccion-productor">Productor</button>
        <button type="button" class="detalle-tab" data-target="detalle-seccion-certificacion">Certificación</button>
        <button type="button" class="detalle-tab" data-target="detalle-seccion-resenas">Reseñas</button>
      </nav>
      <div class="detalle-barra-compra" id="detalle-barra-compra">
        <span class="detalle-sticky-nombre">${escapeAttr(p.nombre)}</span>
        <span class="detalle-sticky-precio" id="detalle-sticky-precio">${precioHtml}</span>
        <button type="button" class="btn btn-primary" id="btn-agregar-sticky" ${sinStock ? 'disabled' : ''}>
          ${sinStock ? 'Agotado' : '+ Agregar al carrito'}
        </button>
      </div>
    </header>

    <section class="detalle-principal" id="detalle-seccion-descripcion">
      ${renderGaleriaDetalle(p, fotos, insignia)}
      ${renderCompraDetalle(p, productor, { favorito, stockBajo, sinStock, tieneAlternativas, precioHtml })}
    </section>

    ${renderVideosDetalle(p, fotos)}

    <div class="detalle-secciones">
      <section class="detalle-bloque detalle-bloque--productor detalle-scroll-seccion" id="detalle-seccion-productor">
        <h3 class="detalle-seccion-titulo"><i class="ti ti-plant-2" aria-hidden="true"></i> De la chacra a tu mesa</h3>
        ${renderBloqueProductor(p, productor)}
      </section>

      <section class="detalle-bloque detalle-bloque--certificacion detalle-scroll-seccion" id="detalle-seccion-certificacion">
        <h3 class="detalle-seccion-titulo"><i class="ti ti-link" aria-hidden="true"></i> Certificación de trazabilidad</h3>
        <div id="detalle-certificacion-contenido"><div class="verificar-cargando"><div class="spinner"></div><p>Cargando certificación...</p></div></div>
      </section>

      <section class="detalle-bloque detalle-bloque--resenas detalle-scroll-seccion" id="detalle-seccion-resenas">
        <h3 class="detalle-seccion-titulo"><i class="ti ti-star" aria-hidden="true"></i> Reseñas${resenas.length ? ` (${resenas.length})` : ''}</h3>
        ${renderSeccionResenas(resenas, p)}
      </section>
    </div>

    <section class="detalle-relacionados" id="detalle-relacionados" hidden>
      <h3 class="detalle-seccion-titulo"><i class="ti ti-basket" aria-hidden="true"></i> También te puede gustar</h3>
      <div class="detalle-relacionados-grid" id="detalle-relacionados-grid"></div>
    </section>
  `;

  let cantidadSeleccionada = 1;
  let unidadSeleccionada = null; // null = unidad base del producto (comportamiento de siempre)
  const cantidadValorEl = document.getElementById('detalle-cantidad-valor');
  const totalEl = document.getElementById('detalle-total-valor');

  const stockMaximoActual = () => stockEnUnidad(p, unidadSeleccionada);

  const actualizarTotalDetalle = () => {
    if (totalEl) totalEl.textContent = formatearMoneda(precioEnUnidad(p, unidadSeleccionada) * cantidadSeleccionada);
  };

  // Se llama solo al cambiar la unidad elegida (el render inicial ya muestra los valores de la
  // unidad base correctos) — actualiza precio (barra fija + fila principal), el texto de stock
  // convertido a esa unidad, y recorta la cantidad si ya no entra en el nuevo tope.
  function refrescarPrecioYStockDetalle() {
    const unidadParaMostrar = unidadSeleccionada || p.unidad_medida;
    const precioHtmlActual = `${formatearMoneda(precioEnUnidad(p, unidadSeleccionada))} <span class="price-unit">/ ${unidadCorta(unidadParaMostrar)}</span>`;
    document.getElementById('detalle-sticky-precio').innerHTML = precioHtmlActual;
    document.getElementById('detalle-precio-principal').innerHTML = precioHtmlActual;

    const maximo = stockMaximoActual();
    const sinStockEnEstaUnidad = maximo <= 0;
    const stockTextoEl = document.getElementById('detalle-stock-texto');
    stockTextoEl.textContent = sinStockEnEstaUnidad
      ? 'Sin stock disponible en esta unidad'
      : `${maximo} ${unidadPlural(unidadParaMostrar, maximo)} disponibles`;
    stockTextoEl.classList.toggle('low', !sinStockEnEstaUnidad && maximo <= 5);
    stockTextoEl.classList.toggle('agotado', sinStockEnEstaUnidad);

    // Caso borde: el factor de conversión deja la unidad elegida sin stock entero disponible
    // (ej. 5 kg en stock, 1 saco = 10 kg) — no tiene sentido dejar seleccionada esa unidad.
    document.querySelector('.detalle-cantidad-fila')?.classList.toggle('hidden', sinStockEnEstaUnidad);
    [
      { el: document.getElementById('btn-agregar-detalle'), textoActivo: '+ Agregar al pedido' },
      { el: document.getElementById('btn-agregar-sticky'), textoActivo: '+ Agregar al carrito' },
    ].forEach(({ el, textoActivo }) => {
      if (!el) return;
      el.disabled = sinStockEnEstaUnidad;
      el.textContent = sinStockEnEstaUnidad ? 'Agotado' : textoActivo;
    });

    if (cantidadSeleccionada > maximo) {
      cantidadSeleccionada = Math.max(1, maximo);
    }
    if (cantidadValorEl) cantidadValorEl.textContent = cantidadSeleccionada;
    actualizarTotalDetalle();
  }

  document.getElementById('detalle-unidad-selector')?.addEventListener('change', (e) => {
    unidadSeleccionada = e.target.value || null;
    refrescarPrecioYStockDetalle();
  });

  document.getElementById('btn-cantidad-menos')?.addEventListener('click', () => {
    if (cantidadSeleccionada > 1) {
      cantidadSeleccionada--;
      cantidadValorEl.textContent = cantidadSeleccionada;
      actualizarTotalDetalle();
    }
  });
  document.getElementById('btn-cantidad-mas')?.addEventListener('click', () => {
    if (cantidadSeleccionada < stockMaximoActual()) {
      cantidadSeleccionada++;
      cantidadValorEl.textContent = cantidadSeleccionada;
      actualizarTotalDetalle();
    } else {
      toast('Alcanzaste el stock disponible.', 'error');
    }
  });

  // Botón grande (tarjeta de compra) y botón compacto de la barra fija comparten la misma cantidad Y
  // unidad seleccionadas — a diferencia del modal viejo, agregar al carrito ya NO navega fuera de
  // la página (no hay "cerrar" en una página propia; el usuario sigue viendo el producto).
  const agregarConFeedback = (btn) => {
    agregarAlCarrito(p.id, cantidadSeleccionada, unidadSeleccionada);
    destellarBoton(btn, '<i class="ti ti-check"></i> Agregado');
  };
  document.getElementById('btn-agregar-detalle')?.addEventListener('click', (e) => agregarConFeedback(e.currentTarget));
  document.getElementById('btn-agregar-sticky')?.addEventListener('click', (e) => agregarConFeedback(e.currentTarget));

  contenido.querySelector('.btn-favorito-detalle')?.addEventListener('click', (e) => {
    manejarClickFavorito(e.currentTarget);
  });

  document.getElementById('btn-volver-detalle')?.addEventListener('click', () => cambiarVista(vistaAntesDeDetalle));
  contenido.querySelector('.detalle-productor-mini')?.addEventListener('click', (e) => {
    contenido.querySelector(`.detalle-tab[data-target="${e.currentTarget.dataset.ir}"]`)?.click();
  });

  if (hayCoordenadas(productor?.latitud, productor?.longitud)) {
    crearMapaSoloLectura('mapa-productor-detalle', productor.latitud, productor.longitud, '<i class="ti ti-map-pin"></i>', '#2d6a4f', escapeAttr(productor.nombre || 'Productor'));
  }

  inicializarGaleriaDetalle(p);
  inicializarPestanasDetalle();
  inicializarBarraDetalle();
  inicializarScrollRevealDetalle();
  cargarCertificacionDetalle(p.id);
  cargarRelacionadosDetalle(p);

  document.getElementById('btn-resena-login')?.addEventListener('click', () => abrirModal('modal-auth'));
  const formResena = document.getElementById('form-resena');
  if (formResena) {
    let calificacionSeleccionada = 0;
    const botones = formResena.querySelectorAll('.estrellas-selector button');
    botones.forEach((btn) => {
      btn.addEventListener('click', () => {
        calificacionSeleccionada = Number(btn.dataset.valor);
        botones.forEach((b) => {
          const activa = Number(b.dataset.valor) <= calificacionSeleccionada;
          b.classList.toggle('activa', activa);
          b.setAttribute('aria-pressed', String(Number(b.dataset.valor) === calificacionSeleccionada));
        });
      });
    });
    formResena.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!calificacionSeleccionada) {
        toast('Selecciona una calificación de 1 a 5 estrellas.', 'error');
        return;
      }
      enviarResena(p.id, calificacionSeleccionada, formResena.querySelector('textarea').value.trim());
    });
  }
}

let limpiarPestanasDetalle = null;
let observadorBarraDetalle = null;

// Pestañas: scroll suave a la sección, descontando la barra fija (scroll-margin-top), y la pestaña
// activa sigue a la sección que se está viendo. Productor y Certificación comparten fila (van una al
// lado de la otra), así que llegan al borde a la vez: si la pestaña activa ya es una de las dos, se
// respeta en vez de saltar a la última.
function inicializarPestanasDetalle() {
  const barra = document.getElementById('detalle-barra');
  const tabsEl = document.getElementById('detalle-tabs');
  if (!barra || !tabsEl) return;

  if (limpiarPestanasDetalle) limpiarPestanasDetalle();
  const tabs = [...tabsEl.querySelectorAll('.detalle-tab')];
  const secciones = tabs.map((t) => document.getElementById(t.dataset.target));

  // El alto de la barra (una o dos filas según el ancho) lo descuentan las secciones para no quedar tapadas.
  const aplicarAltoBarra = () => {
    const alto = barra.offsetHeight;
    secciones.forEach((s) => { if (s) s.style.scrollMarginTop = `${alto + 16}px`; });
  };
  aplicarAltoBarra();

  let tabActiva = tabs[0];
  let ignorarHasta = 0; // mientras dura el scroll suave de un clic, el "seguimiento" no pisa la pestaña elegida
  const marcar = (tab) => {
    tabActiva = tab;
    tabs.forEach((t) => {
      const activa = t === tab;
      t.classList.toggle('active', activa);
      if (activa) t.setAttribute('aria-current', 'true'); else t.removeAttribute('aria-current');
    });
    // Deja visible la pestaña activa en la fila desplazable (móvil) sin mover la página.
    if (tabsEl.scrollWidth > tabsEl.clientWidth) {
      const izquierda = tab.getBoundingClientRect().left - tabsEl.getBoundingClientRect().left + tabsEl.scrollLeft;
      tabsEl.scrollLeft = izquierda - (tabsEl.clientWidth - tab.offsetWidth) / 2;
    }
  };

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      marcar(tab);
      ignorarHasta = Date.now() + 900;
      document.getElementById(tab.dataset.target)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  let pendiente = false;
  const seguirScroll = () => {
    pendiente = false;
    if (Date.now() < ignorarHasta) return;
    if (!document.getElementById('view-detalle-producto')?.classList.contains('active')) return;

    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
      marcar(tabs[tabs.length - 1]);
      return;
    }
    const limite = barra.offsetHeight + window.innerHeight * 0.3; // una sección cuenta como "actual" al pasar el 30% de la pantalla
    const tops = secciones.map((s) => (s ? s.getBoundingClientRect().top : Infinity));
    const alcanzadas = tabs.filter((_, i) => tops[i] <= limite);
    if (!alcanzadas.length) { marcar(tabs[0]); return; }
    const topUltima = tops[tabs.indexOf(alcanzadas[alcanzadas.length - 1])];
    const fila = alcanzadas.filter((t) => Math.abs(tops[tabs.indexOf(t)] - topUltima) < 8);
    if (!fila.includes(tabActiva)) marcar(fila[0]);
  };
  const alScroll = () => {
    if (pendiente) return;
    pendiente = true;
    requestAnimationFrame(seguirScroll);
  };
  const alRedimensionar = () => aplicarAltoBarra();
  window.addEventListener('scroll', alScroll, { passive: true });
  window.addEventListener('resize', alRedimensionar);
  limpiarPestanasDetalle = () => {
    window.removeEventListener('scroll', alScroll);
    window.removeEventListener('resize', alRedimensionar);
  };
}

// La barra fija solo muestra nombre, precio y botón de compra cuando la tarjeta de compra ya salió de
// pantalla: mientras se ve, repetirlos arriba solo estorba.
function inicializarBarraDetalle() {
  const barra = document.getElementById('detalle-barra');
  const tarjeta = document.getElementById('detalle-compra');
  if (observadorBarraDetalle) observadorBarraDetalle.disconnect();
  if (!barra || !tarjeta) return;

  if (!('IntersectionObserver' in window)) {
    barra.classList.add('con-compra');
    return;
  }
  observadorBarraDetalle = new IntersectionObserver(([entrada]) => {
    barra.classList.toggle('con-compra', !entrada.isIntersecting);
  }, { rootMargin: `-${barra.offsetHeight}px 0px 0px 0px` });
  observadorBarraDetalle.observe(tarjeta);
}

// Foto principal + miniaturas: clic, flechas, teclado (← →) y deslizar en pantallas táctiles.
function inicializarGaleriaDetalle(p) {
  const galeria = document.getElementById('detalle-galeria');
  const visor = galeria?.querySelector('.detalle-visor');
  if (!visor) return;
  const img = visor.querySelector('.detalle-visor-img');

  // Si la foto no carga, el visor cae al emoji de la categoría (igual que las tarjetas del catálogo).
  img?.addEventListener('error', () => {
    img.remove();
    visor.insertAdjacentHTML('afterbegin', `<span class="detalle-visor-emoji" aria-hidden="true">${emojiParaProducto(p)}</span>`);
  });

  const miniaturas = [...galeria.querySelectorAll('.detalle-miniatura')];
  if (!img || miniaturas.length < 2) return;
  const tira = galeria.querySelector('.detalle-miniaturas');
  const contador = galeria.querySelector('.detalle-contador');
  const total = miniaturas.length;
  let actual = 0;

  const mostrar = (indice) => {
    actual = (indice + total) % total;
    const mini = miniaturas[actual];
    img.src = mini.dataset.src;
    img.classList.remove('cambio');
    void img.offsetWidth; // reinicia la animación de entrada
    img.classList.add('cambio');
    miniaturas.forEach((m, i) => {
      m.classList.toggle('active', i === actual);
      m.setAttribute('aria-current', String(i === actual));
    });
    contador.textContent = `${actual + 1} / ${total}`;
    tira.scrollLeft = mini.offsetLeft - (tira.clientWidth - mini.offsetWidth) / 2; // solo la tira, no la página
  };

  miniaturas.forEach((mini, i) => mini.addEventListener('click', () => mostrar(i)));
  visor.querySelector('.detalle-visor-prev').addEventListener('click', () => mostrar(actual - 1));
  visor.querySelector('.detalle-visor-next').addEventListener('click', () => mostrar(actual + 1));
  visor.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    mostrar(actual + (e.key === 'ArrowRight' ? 1 : -1));
  });

  let inicioX = null;
  visor.addEventListener('touchstart', (e) => { inicioX = e.touches[0].clientX; }, { passive: true });
  visor.addEventListener('touchend', (e) => {
    if (inicioX == null) return;
    const dx = e.changedTouches[0].clientX - inicioX;
    inicioX = null;
    if (Math.abs(dx) > 40) mostrar(actual + (dx < 0 ? 1 : -1));
  }, { passive: true });
}

// "También te puede gustar": primero lo de su misma categoría, luego lo del mismo productor y después
// el resto, con los mejor calificados por delante (mismo criterio que el landing). Usa las mismas
// tarjetas compactas del menú de categorías y del buscador.
async function cargarRelacionadosDetalle(p) {
  const seccion = document.getElementById('detalle-relacionados');
  const grid = document.getElementById('detalle-relacionados-grid');
  if (!seccion || !grid) return;

  await asegurarProductosCargados();
  if (!seccion.isConnected) return; // el usuario ya cambió de producto mientras cargaba

  const otros = Estado.productos.filter((x) => x.id !== p.id);
  const categoria = normalizarCategoria(p.categoria);
  const mismaCategoria = otros.filter((x) => normalizarCategoria(x.categoria) === categoria);
  const delProductor = otros.filter((x) => x.productor_nombre === p.productor_nombre && !mismaCategoria.includes(x));
  const resto = otros.filter((x) => !mismaCategoria.includes(x) && !delProductor.includes(x));
  const lista = [...ordenarPorDestacado(mismaCategoria), ...ordenarPorDestacado(delProductor), ...ordenarPorDestacado(resto)].slice(0, 4);
  if (!lista.length) return;

  grid.innerHTML = lista.map(renderProductoMega).join('');
  seccion.hidden = false;
  grid.addEventListener('click', (e) => {
    const tarjeta = e.target.closest('.nav-mega-producto');
    if (tarjeta) cambiarVista('detalle-producto', tarjeta.dataset.id);
  });
}

function inicializarScrollRevealDetalle() {
  const secciones = document.querySelectorAll('#detalle-producto-content .detalle-scroll-seccion');
  const seccionesVideo = document.querySelectorAll('#detalle-producto-content .detalle-video-seccion');

  if (!('IntersectionObserver' in window)) {
    secciones.forEach((s) => s.classList.add('visible'));
    // Sin IntersectionObserver no hay forma de saber cuándo entra en pantalla — se le da
    // controles nativos en vez de intentar el play-al-scroll que el resto del navegador sí tiene.
    seccionesVideo.forEach((s) => {
      const video = s.querySelector('.detalle-video');
      if (video) {
        video.src = video.dataset.src;
        video.setAttribute('controls', '');
      }
    });
    return;
  }

  // 1) Revelado (fade-in/slide-up) de TODAS las secciones: umbral bajo, se dispara apenas asoma
  // un poco y deja de observar — esto no cambió, sigue siendo solo la animación de entrada.
  const observerRevelado = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observerRevelado.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  secciones.forEach((s) => observerRevelado.observe(s));

  // 2) Reproducción de video: umbral alto (recién dispara el play cuando la sección ya domina la
  // pantalla, no apenas asoma) y NUNCA se desconecta — sigue mirando para pausar+reiniciar en
  // cuanto la sección sale de vista. Con secciones de ~88vh y un umbral de 0.65, dos secciones
  // vecinas no pueden estar ambas por encima del umbral al mismo tiempo (0.65+0.65 > 1 viewport),
  // así que estructuralmente nunca hay dos videos "activos" a la vez — el pausar-al-salir es
  // además una red de seguridad explícita para scroll muy rápido (ver CORRECCIÓN 2).
  const observerVideo = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) reproducirVideoGaleria(entry.target);
      else pausarYReiniciarVideoGaleria(entry.target);
    });
  }, { threshold: 0.65 });
  seccionesVideo.forEach((s) => {
    configurarVideoGaleria(s);
    observerVideo.observe(s);
  });
}

// Certificación: misma lógica ya usada en el modal de certificación del Panel Productor
// (renderLineaTiempoCertificacion ya maneja el estado vacío), solo que integrada a la página
// en vez de abrir un modal aparte.
async function cargarCertificacionDetalle(productoId) {
  const cont = document.getElementById('detalle-certificacion-contenido');
  if (!cont) return;
  try {
    const historial = await Api.certificacion.historial(productoId);
    cont.innerHTML = `
      <div class="detalle-cert-cabecera">
        <div class="certificacion-qr-bloque">
          <img src="${escapeAttr(Api.certificacion.qrUrl(productoId))}" alt="Código QR de certificación" class="certificacion-qr" id="certificacion-qr-img-detalle">
        </div>
        <div class="detalle-cert-texto">
          <strong><i class="ti ti-shield-check" aria-hidden="true"></i> Origen verificable</strong>
          <p class="muted">Escanea el código para comprobar en cualquier momento de dónde viene este producto. También puedes compartirlo.</p>
        </div>
      </div>
      <h4 class="timeline-titulo">Historial de la cadena</h4>
      ${renderLineaTiempoCertificacion(historial)}
    `;
    document.getElementById('certificacion-qr-img-detalle')?.addEventListener('error', function () {
      const p = document.createElement('p');
      p.className = 'muted';
      p.textContent = 'No se pudo cargar el código QR.';
      this.replaceWith(p);
    });
  } catch (err) {
    cont.innerHTML = `
      <div class="verificar-banner banner-error">
        <span class="banner-icon"><i class="ti ti-alert-triangle"></i></span>
        <div>
          <strong>No se pudo cargar la certificación</strong>
          <p>${escapeAttr(err.message)}</p>
        </div>
      </div>`;
  }
}

async function enviarResena(productoId, calificacion, comentario) {
  const form = document.getElementById('form-resena');
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = 'Publicando...';
  try {
    await Api.productos.crearResena(productoId, { calificacion, comentario: comentario || null });
    toast('¡Gracias por tu reseña! 🌟');
    const p = await Api.productos.obtener(productoId);
    const [resenas, productor] = await Promise.all([
      Api.productos.listarResenas(productoId),
      Api.productores.obtener(p.productor_id).catch(() => null),
    ]);
    renderPaginaDetalleProducto(p, resenas, productor);
  } catch (err) {
    manejarError(err, 'publicar tu reseña');
    btn.disabled = false;
    btn.textContent = 'Publicar reseña';
  }
}

// ============ CARRITO / PEDIDO ============
// unidad: null = unidad base del producto (comportamiento de siempre); un valor = unidad
// alternativa elegida en la página de detalle. Dos líneas del mismo producto en unidades
// DISTINTAS no se combinan (precio y equivalencia de stock son distintos) — por eso identificar
// una línea de acá en adelante es siempre (producto_id, unidad), no solo producto_id.
function agregarAlCarrito(productoId, cantidad = 1, unidad = null) {
  if (!Estado.token) {
    toast('Inicia sesión para armar tu pedido 🌱', 'error');
    abrirModal('modal-auth');
    return;
  }
  const producto = Estado.productos.find((p) => p.id === productoId);
  if (!producto) return;

  const stockMax = stockEnUnidad(producto, unidad);
  const existente = Estado.carrito.find((i) => i.producto_id === productoId && (i.unidad || null) === unidad);
  if (existente) {
    const nuevaCantidad = Math.min(existente.cantidad + cantidad, stockMax);
    if (nuevaCantidad === existente.cantidad) toast('No hay más stock disponible de este producto.', 'error');
    existente.cantidad = nuevaCantidad;
  } else {
    Estado.carrito.push({
      producto_id: producto.id,
      nombre: producto.nombre,
      // Snapshot del precio de la unidad elegida al momento de agregar — solo para mostrar en el
      // carrito. El backend nunca confía en este valor de todas formas: Pedidos vuelve a
      // resolver el precio real contra Productos al confirmar (ver crear_pedido), snapshot o no
      // acá no cambia eso — evita además tener que volver a llamar a la API solo para pintar el
      // carrito.
      precio: precioEnUnidad(producto, unidad),
      cantidad: Math.min(cantidad, stockMax),
      stockDisponible: stockMax,
      unidad,
      // Unidad EFECTIVA de esta línea (base o alternativa) — unidadCorta/unidadPlural/
      // unidadEtiqueta ya reciben esto tal cual, no hace falta tocar su uso en renderCarrito.
      unidad_medida: unidad || producto.unidad_medida,
    });
  }
  renderCarrito();
  toast(`${producto.nombre} agregado al pedido 🧺`);
}

function cambiarCantidadCarrito(productoId, delta, unidad = null) {
  const item = Estado.carrito.find((i) => i.producto_id === productoId && (i.unidad || null) === unidad);
  if (!item) return;
  item.cantidad += delta;
  if (item.cantidad <= 0) {
    Estado.carrito = Estado.carrito.filter((i) => i !== item);
  } else if (item.cantidad > item.stockDisponible) {
    item.cantidad = item.stockDisponible;
    toast('Alcanzaste el stock máximo disponible.', 'error');
  }
  renderCarrito();
}

function quitarDelCarrito(productoId, unidad = null) {
  Estado.carrito = Estado.carrito.filter((i) => !(i.producto_id === productoId && (i.unidad || null) === unidad));
  renderCarrito();
}

// Producto del catálogo cargado para pintar la miniatura de una línea (foto o, si no hay, el emoji de su
// categoría); si el catálogo aún no está en memoria, solo el nombre alcanza para el emoji genérico.
function productoDeLinea(item) {
  return Estado.productos.find((p) => p.id === item.producto_id) || { nombre: item.nombre };
}

function renderCarrito() {
  const cantidadTotal = Estado.carrito.reduce((acc, i) => acc + i.cantidad, 0);
  const badge = document.getElementById('cart-count');
  if (badge.textContent !== String(cantidadTotal) && cantidadTotal > 0) {
    badge.classList.remove('bump');
    void badge.offsetWidth;
    badge.classList.add('bump');
  }
  badge.textContent = cantidadTotal;
  badge.classList.toggle('hidden', cantidadTotal === 0);

  const cont = document.getElementById('carrito-items');
  const vacio = document.getElementById('carrito-vacio');
  const totalEl = document.getElementById('carrito-total');
  const btnConfirmar = document.getElementById('btn-checkout-paso1-siguiente');
  const lineas = Estado.carrito.length;
  document.getElementById('carrito-conteo').textContent = lineas ? `(${lineas})` : '';
  document.getElementById('carrito-resumen-cantidad').textContent = lineas;
  document.getElementById('checkout-bloque-datos').classList.toggle('hidden', !lineas); // sin productos no hay nada que entregar

  if (!lineas) {
    cont.innerHTML = '';
    vacio.classList.remove('hidden');
    totalEl.textContent = formatearMoneda(0);
    btnConfirmar.disabled = true;
    return;
  }
  vacio.classList.add('hidden');
  btnConfirmar.disabled = false;

  cont.innerHTML = Estado.carrito.map((i) => {
    const nombre = escapeAttr(i.nombre);
    const datos = `data-id="${i.producto_id}" data-unidad="${i.unidad || ''}"`;
    return `
    <article class="carrito-item">
      ${renderMediaProducto(productoDeLinea(i), 'carrito-item-media')}
      <div class="carrito-item-info">
        <strong>${nombre}</strong>
        <span>${formatearMoneda(i.precio)} / ${unidadCorta(i.unidad_medida)}</span>
      </div>
      <div class="carrito-qty" role="group" aria-label="Cantidad de ${nombre}">
        <button type="button" data-accion="menos" ${datos} aria-label="Quitar una unidad de ${nombre}">−</button>
        <span>${i.cantidad} ${unidadPlural(i.unidad_medida, i.cantidad)}</span>
        <button type="button" data-accion="mas" ${datos} aria-label="Agregar una unidad de ${nombre}">+</button>
      </div>
      <strong class="carrito-item-subtotal">${formatearMoneda(i.precio * i.cantidad)}</strong>
      <button type="button" class="carrito-remove" data-accion="quitar" ${datos} aria-label="Quitar ${nombre} del pedido"><i class="ti ti-trash" aria-hidden="true"></i></button>
    </article>`;
  }).join('');

  const total = Estado.carrito.reduce((acc, i) => acc + i.precio * i.cantidad, 0);
  totalEl.textContent = formatearMoneda(total);
}

// ============ CHECKOUT POR PASOS ============
function irPasoCheckout(paso) {
  document.querySelectorAll('.checkout-panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === `checkout-panel-${paso}`);
  });
  document.querySelectorAll('.checkout-step').forEach((stepEl) => {
    const n = Number(stepEl.dataset.step);
    stepEl.classList.toggle('active', n === paso);
    stepEl.classList.toggle('completado', n < paso);
    if (n === paso) stepEl.setAttribute('aria-current', 'step'); else stepEl.removeAttribute('aria-current');
    const circulo = stepEl.querySelector('.checkout-step-circle');
    circulo.innerHTML = n < paso ? '<i class="ti ti-check"></i>' : String(n);
  });
  const cuerpo = document.querySelector('#modal-pedido .checkout-cuerpo');
  if (cuerpo) cuerpo.scrollTop = 0; // cada paso arranca desde arriba
}

function resetCheckout() {
  irPasoCheckout(1);
  resetearBotonPago();
  pedidoReintento = null;
  document.querySelectorAll('input[name="metodo-pago"]').forEach((r) => { r.checked = r.value === 'tarjeta'; });
  document.querySelectorAll('.metodo-pago-card').forEach((c) => {
    c.classList.toggle('seleccionado', c.querySelector('input').value === 'tarjeta');
  });
  inicializarMapaDestino();
}

// ---- Reintentar el pago de un pedido ya existente (rechazado) ----
let pedidoReintento = null; // { id, monto, envio }

function reintentarPago(pedidoId, monto, envio = 0) {
  pedidoReintento = { id: pedidoId, monto, envio };
  abrirModal('modal-pedido');
  pintarTotalesPaso2(monto - envio, envio);
  envioCotizado = null;
  document.getElementById('checkout-bloque-entrega').classList.add('hidden'); // el pedido ya tiene su entrega
  document.getElementById('checkout-recap').innerHTML = `
    <li class="checkout-recap-reintento">
      <i class="ti ti-receipt" aria-hidden="true"></i>
      <span>Pago pendiente del pedido <strong>#${String(pedidoId).slice(0, 8)}</strong></span>
    </li>`;
  irPasoCheckout(2);
}

async function manejarClickPagar() {
  if (pedidoReintento) {
    const { id, monto, envio } = pedidoReintento;
    const metodo = document.querySelector('input[name="metodo-pago"]:checked')?.value || 'tarjeta';
    const btn = document.getElementById('btn-checkout-pagar');
    btn.disabled = true;
    btn.textContent = 'Enviando pedido...';
    pedidoReintento = null;
    abrirCheckoutCulqi(id, monto, metodo, envio);
    return;
  }
  await confirmarYPagar();
}

// ---- Selector de destino (mapa del checkout) ----
let mapaDestinoPedido = null;
let destinoSeleccionado = null; // { lat, lng }

function inicializarMapaDestino() {
  if (mapaDestinoPedido) {
    try { mapaDestinoPedido.mapa.remove(); } catch { /* ya estaba destruido */ }
    mapaDestinoPedido = null;
  }
  destinoSeleccionado = null;
  quitarResaltadoMapaDestino(); // el aviso de "falta el destino" no debe sobrevivir a reabrir el checkout
  const ayuda = document.getElementById('mapa-destino-ayuda');
  if (ayuda) {
    ayuda.textContent = 'Toca el mapa para marcar dónde quieres recibir tu pedido.';
    ayuda.classList.remove('confirmado');
  }

  mapaDestinoPedido = crearMapaSeleccionable('mapa-destino-pedido', {
    emoji: '<i class="ti ti-map-pin"></i>',
    color: '#e76f51',
    onSeleccionar: (lat, lng) => {
      destinoSeleccionado = { lat, lng };
      actualizarAyudaDestino();
      quitarResaltadoMapaDestino();
    },
  });
}

function actualizarAyudaDestino() {
  const ayuda = document.getElementById('mapa-destino-ayuda');
  if (!ayuda || !destinoSeleccionado) return;
  ayuda.innerHTML = `<i class="ti ti-map-pin"></i> Destino marcado (${destinoSeleccionado.lat.toFixed(5)}, ${destinoSeleccionado.lng.toFixed(5)})`;
  ayuda.classList.add('confirmado');
}

// Resalta el mapa de destino (borde + scroll) cuando el usuario intenta confirmar sin haber
// marcado un punto — se quita en cuanto elige uno (arriba en onSeleccionar / usarMiUbicacionDestino).
function resaltarMapaDestino() {
  const mapaEl = document.getElementById('mapa-destino-pedido');
  if (!mapaEl) return;
  mapaEl.classList.add('destino-requerido');
  mapaEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function quitarResaltadoMapaDestino() {
  document.getElementById('mapa-destino-pedido')?.classList.remove('destino-requerido');
}

async function usarMiUbicacionDestino() {
  const btn = document.getElementById('btn-usar-mi-ubicacion');
  btn.disabled = true;
  btn.textContent = 'Obteniendo ubicación...';
  try {
    const { lat, lng } = await obtenerUbicacionActual();
    mapaDestinoPedido?.moverMarcador(lat, lng);
    destinoSeleccionado = { lat, lng };
    actualizarAyudaDestino();
    quitarResaltadoMapaDestino();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Usar mi ubicación';
  }
}

// Mismo criterio que valida el backend (pedidos/main.py, PedidoCrear): celular peruano de 9
// dígitos que empieza con 9. Devuelve el número normalizado o null si no es válido.
function telefonoContactoValido() {
  const input = document.getElementById('pedido-telefono');
  const valor = input.value.replace(/\s+/g, '');
  if (!/^9\d{8}$/.test(valor)) {
    toast('Ingresa un teléfono de contacto válido: 9 dígitos, empieza con 9 (ej. 987654321).', 'error');
    input.focus();
    return null;
  }
  return valor;
}

// Resumen del paso de pago: lo que se va a pagar, línea por línea, para no tener que volver atrás a revisarlo.
function renderRecapCheckout() {
  document.getElementById('checkout-recap').innerHTML = Estado.carrito.map((i) => `
    <li class="checkout-recap-item">
      ${renderMediaProducto(productoDeLinea(i), 'checkout-recap-media')}
      <span class="checkout-recap-texto">
        <strong>${escapeAttr(i.nombre)}</strong>
        <small>${i.cantidad} ${unidadPlural(i.unidad_medida, i.cantidad)} × ${formatearMoneda(i.precio)}</small>
      </span>
      <span class="checkout-recap-precio">${formatearMoneda(i.precio * i.cantidad)}</span>
    </li>`).join('');
}

let cotizandoEnvio = false;
let envioCotizado = null; // costo de envío que se le mostró al comprador en el paso 2

// Desglose del paso 2: lo que se ve aquí es lo que se cobra (Pagos suma subtotal + envío).
function pintarTotalesPaso2(subtotal, envio, distanciaKm = null) {
  document.getElementById('checkout-subtotal-paso2').textContent = formatearMoneda(subtotal);
  document.getElementById('checkout-envio-etiqueta').textContent =
    Number.isFinite(distanciaKm) ? `Envío · ${distanciaKm.toFixed(1)} km` : 'Envío';
  document.getElementById('checkout-envio-paso2').textContent = formatearMoneda(envio);
  document.getElementById('checkout-total-paso2').textContent = formatearMoneda(subtotal + envio);
}

async function irAPaso2() {
  if (!Estado.carrito.length || cotizandoEnvio) return;
  const telefono = telefonoContactoValido();
  if (!telefono) return;
  // El punto de entrega se marca en el paso 1: pedirlo aquí (donde el mapa se ve) y no recién al pagar,
  // cuando el aviso salía pero el mapa quedaba en un paso que ya no se muestra.
  if (!destinoSeleccionado) {
    toast('Marca el punto de entrega en el mapa antes de continuar.', 'error');
    resaltarMapaDestino();
    return;
  }

  // El envío depende de la distancia al almacén y lo calcula el backend: se cotiza acá para que el
  // total que ve el comprador sea el mismo que se cobra.
  const btn = document.getElementById('btn-checkout-paso1-siguiente');
  const textoBtn = btn.textContent;
  cotizandoEnvio = true;
  btn.disabled = true;
  btn.textContent = 'Calculando envío...';
  let cotizacion;
  try {
    cotizacion = await Api.pedidos.cotizarEnvio(destinoSeleccionado.lat, destinoSeleccionado.lng);
  } catch (err) {
    manejarError(err, 'calcular el envío');
    if (err.status === 400) resaltarMapaDestino(); // destino fuera de la región de entrega
    return;
  } finally {
    cotizandoEnvio = false;
    btn.disabled = !Estado.carrito.length;
    btn.textContent = textoBtn;
  }

  const subtotal = Estado.carrito.reduce((acc, i) => acc + i.precio * i.cantidad, 0);
  envioCotizado = Number(cotizacion.costo_envio) || 0;
  pintarTotalesPaso2(subtotal, envioCotizado, Number(cotizacion.distancia_km));
  document.getElementById('checkout-entrega-telefono').textContent = telefono;
  document.getElementById('checkout-entrega-punto').innerHTML =
    `<i class="ti ti-map-pin" aria-hidden="true"></i> Marcado en el mapa (${destinoSeleccionado.lat.toFixed(5)}, ${destinoSeleccionado.lng.toFixed(5)})`;
  document.getElementById('checkout-bloque-entrega').classList.remove('hidden');
  renderRecapCheckout();
  irPasoCheckout(2);
}

function resetearBotonPago() {
  const btn = document.getElementById('btn-checkout-pagar');
  btn.disabled = false;
  btn.textContent = 'Confirmar y pagar';
}

async function confirmarYPagar() {
  if (!telefonoContactoValido()) {
    irPasoCheckout(1);
    return;
  }
  if (!destinoSeleccionado) {
    toast('Selecciona el punto de entrega en el mapa antes de confirmar tu pedido.', 'error');
    resaltarMapaDestino();
    return;
  }

  const btn = document.getElementById('btn-checkout-pagar');
  const metodo = document.querySelector('input[name="metodo-pago"]:checked')?.value || 'tarjeta';
  btn.disabled = true;
  btn.textContent = 'Enviando pedido...';

  try {
    const subtotal = Estado.carrito.reduce((acc, i) => acc + i.precio * i.cantidad, 0);
    const datos = {
      comprador_nombre: Estado.nombre || 'Cliente Chakra Shop',
      comprador_telefono: document.getElementById('pedido-telefono').value.replace(/\s+/g, ''),
      destino_latitud: destinoSeleccionado ? String(destinoSeleccionado.lat) : null,
      destino_longitud: destinoSeleccionado ? String(destinoSeleccionado.lng) : null,
      // Sin precio acá — nunca lo tuvo, el backend siempre lo resuelve solo contra Productos
      // (ver crear_pedido). "unidad" solo se manda si no es null (unidad base, comportamiento
      // de siempre); si viene, Pedidos la busca en unidades_alternativas del producto.
      items: Estado.carrito.map((i) => (i.unidad
        ? { producto_id: i.producto_id, cantidad: i.cantidad, unidad: i.unidad }
        : { producto_id: i.producto_id, cantidad: i.cantidad })),
    };
    const pedido = await Api.pedidos.crear(datos);
    guardarPedidoTrackeado(pedido.id);

    Estado.carrito = [];
    renderCarrito();
    document.getElementById('pedido-telefono').value = '';
    // Refresca el stock en memoria (recién descontado por el pedido) para que el mega-dropdown y
    // el panel de búsqueda no sigan mostrando cantidades desactualizadas.
    try { Estado.productos = await Api.productos.listar(); } catch { /* no bloquea el checkout si falla */ }

    // El envío lo calcula el backend según la distancia al destino; Pagos cobra subtotal + envío,
    // así que Culqi y la confirmación deben mostrar ese mismo monto.
    const envio = Number(pedido.costo_envio) || 0;
    if (envioCotizado !== null && Math.abs(envio - envioCotizado) > 0.005) {
      toast(`El costo de envío cambió a ${formatearMoneda(envio)}. Revisa el monto antes de pagar.`);
    }
    envioCotizado = null;
    abrirCheckoutCulqi(pedido.id, subtotal + envio, metodo, envio);
  } catch (err) {
    manejarError(err, 'crear el pedido');
    resetearBotonPago();
  }
}

// ============ PAGO (CULQI) ============
let pagoEnCurso = null; // { pedidoId, email, monto, envio }

function abrirCheckoutCulqi(pedidoId, montoTotal, metodo = 'tarjeta', envio = 0) {
  if (typeof Culqi === 'undefined') {
    toast('No se pudo cargar la pasarela de pagos. Vuelve a intentar el pago desde "Mis Pedidos".', 'error');
    resetearBotonPago();
    return;
  }

  pagoEnCurso = { pedidoId, email: Estado.email, monto: montoTotal, envio };
  Culqi.publicKey = CULQI_PUBLIC_KEY;
  Culqi.settings({
    title: 'Chakra Shop',
    currency: 'PEN',
    amount: Math.round(montoTotal * 100),
  });
  Culqi.options({
    paymentMethods: {
      tarjeta: metodo === 'tarjeta',
      yape: metodo === 'yape',
    },
  });
  Culqi.open();
}

window.culqi = function () {
  if (!pagoEnCurso) return;

  if (window.Culqi.token) {
    // Culqi no cierra su propio modal solo tras tokenizar — hay que pedírselo explícitamente
    // (Culqi.close()) o se queda mostrando el mismo formulario mientras el cobro se procesa en
    // el backend. Sin esto, un usuario que piensa que "no pasó nada" puede volver a tocar
    // "Pagar" ahí adentro y generar un SEGUNDO token para el mismo pedido. Capturamos
    // pagoEnCurso en una variable local y lo limpiamos ANTES de procesar (no después, como
    // antes) para que ese segundo click — si llega a pasar mientras este primer cobro todavía
    // está en curso — quede bloqueado por el guard de arriba en vez de disparar un segundo cargo.
    const datosPago = pagoEnCurso;
    pagoEnCurso = null;
    Culqi.close();
    procesarPagoCulqi(window.Culqi.token.id, datosPago);
  } else if (window.Culqi.error) {
    toast(window.Culqi.error.user_message || 'Revisa los datos de tu tarjeta e intenta de nuevo.', 'error');
    resetearBotonPago();
  }
};

async function procesarPagoCulqi(tokenCulqi, datosPago) {
  const { pedidoId, email, monto, envio } = datosPago;
  let resultado = null;
  let errorMsg = null;
  try {
    resultado = await Api.pagos.procesar({
      pedido_id: pedidoId,
      token_culqi: tokenCulqi,
      email,
    });
  } catch (err) {
    errorMsg = err.message;
  }

  mostrarConfirmacionCheckout({ pedidoId, monto, envio, resultado, errorMsg });
  resetearBotonPago();
  irPasoCheckout(3);
}

function mostrarConfirmacionCheckout({ pedidoId, monto, envio = 0, resultado, errorMsg }) {
  const cont = document.getElementById('checkout-confirmacion');
  const aprobado = resultado?.estado === 'aprobado';
  const idCorto = String(pedidoId).slice(0, 8);

  if (aprobado) {
    cont.innerHTML = `
      <span class="checkout-exito-icono"><i class="ti ti-check" aria-hidden="true"></i></span>
      <h3>¡Pago aprobado!</h3>
      <p>Tu pedido <strong>#${idCorto}</strong> está confirmado.</p>
      <div class="checkout-exito-total"><span>Total pagado</span><strong>${formatearMoneda(monto)}</strong>${envio > 0 ? `<small>Incluye ${formatearMoneda(envio)} de envío</small>` : ''}</div>
      <ol class="checkout-siguientes">
        <li><i class="ti ti-receipt" aria-hidden="true"></i><div><strong>Recibimos tu pedido</strong><span>Ya está registrado y pagado.</span></div></li>
        <li><i class="ti ti-truck-delivery" aria-hidden="true"></i><div><strong>Un repartidor lo lleva a tu punto</strong><span>Te llamará al teléfono que indicaste para coordinar.</span></div></li>
        <li><i class="ti ti-key" aria-hidden="true"></i><div><strong>Confirma la entrega con tu código</strong><span>Lo verás en Mis Pedidos cuando el pedido salga.</span></div></li>
      </ol>`;
  } else {
    const pareceJson = errorMsg && (errorMsg.trim().startsWith('[') || errorMsg.trim().startsWith('{'));
    const motivo = (errorMsg && !pareceJson)
      ? errorMsg
      : `El pago quedó en estado "${resultado?.estado || 'pendiente'}".`;
    // Al reintentar el pago de un pedido rechazado, el servidor vuelve a reservar su stock: si ya no alcanza
    // responde "... Crea un nuevo pedido." y reintentar más tarde no serviría de nada.
    const sinReintento = /crea un nuevo pedido/i.test(errorMsg || '');
    cont.innerHTML = `
      <span class="checkout-exito-icono es-alerta"><i class="ti ti-alert-triangle" aria-hidden="true"></i></span>
      <h3>No se pudo confirmar el pago</h3>
      <p>${escapeAttr(motivo)}</p>
      ${sinReintento
        ? `<p>Tu pedido <strong>#${idCorto}</strong> ya no se puede pagar. Vuelve al catálogo y crea uno nuevo.</p>`
        : `<p>Tu pedido <strong>#${idCorto}</strong> quedó registrado — puedes reintentar el pago más tarde.</p>`}`;
  }
}

// ============ MIS PEDIDOS ============
function claveTrackeados() {
  return `agro_pedidos_${Estado.usuarioId}`;
}

function obtenerPedidosTrackeados() {
  if (!Estado.usuarioId) return [];
  try {
    return JSON.parse(localStorage.getItem(claveTrackeados())) || [];
  } catch {
    return [];
  }
}

function guardarPedidoTrackeado(id) {
  if (!Estado.usuarioId) return;
  const lista = obtenerPedidosTrackeados();
  if (!lista.includes(id)) {
    lista.unshift(id);
    localStorage.setItem(claveTrackeados(), JSON.stringify(lista));
  }
}

let pedidosCargados = []; // caché de {pedido, envio, pago, ruta} para filtrar por tab sin refetch
let filtroPedidoActual = 'todos';

function esPedidoEntregado(d) {
  return d.envio ? d.envio.estado === 'entregado' : d.pedido?.estado === 'entregado';
}

// Suma de las líneas del pedido, sin envío.
function subtotalPedido(pedido) {
  return (pedido.items || []).reduce((acc, i) => acc + Number(i.precio_unitario) * i.cantidad, 0);
}

// Lo que se cobra por el pedido: líneas + envío. Es el mismo monto que calcula Pagos al crear el
// pedido (subtotal + costo_envio), así que también es el que se reintenta si el pago se rechaza.
function totalPedido(pedido) {
  return subtotalPedido(pedido) + (Number(pedido.costo_envio) || 0);
}

function conteosPedidos() {
  const entregados = pedidosCargados.filter(esPedidoEntregado).length;
  return { todos: pedidosCargados.length, en_curso: pedidosCargados.length - entregados, entregados };
}

function actualizarConteosPedidos() {
  const conteos = conteosPedidos();
  document.querySelectorAll('#pedidos-tabs [data-conteo]').forEach((el) => {
    el.textContent = conteos[el.dataset.conteo] ?? 0;
  });
}

// Métricas de arriba de Mis Pedidos (mismo lenguaje que los dashboards de Productor/Repartidor/
// Admin). Si hay un envío en camino, un aviso clicable lleva a esa tarjeta.
function renderResumenPedidos() {
  const cont = document.getElementById('pedidos-resumen');
  if (!pedidosCargados.length) { cont.innerHTML = ''; return; }

  const { todos, en_curso: enCurso, entregados } = conteosPedidos();
  const gastado = pedidosCargados.reduce((acc, d) => acc + totalPedido(d.pedido), 0);
  const enCamino = pedidosCargados.filter((d) => d.envio?.estado === 'en_camino' && !esPedidoEntregado(d));

  const aviso = enCamino.length ? `
    <div class="resumen-destacado resumen-destacado-clicable resumen-destacado-camino" role="button" tabindex="0" data-pedido-id="${enCamino[0].pedido.id}">
      <i class="ti ti-truck"></i>
      <span>${enCamino.length === 1
        ? `Tu pedido <strong>#${String(enCamino[0].pedido.id).slice(0, 8)}</strong> va en camino — ten a mano tu código de entrega.`
        : `Tienes <strong>${enCamino.length} pedidos</strong> en camino.`}</span>
      <i class="ti ti-chevron-right mp-aviso-flecha"></i>
    </div>` : '';

  cont.innerHTML = `
    <div class="card-panel resumen-panel mp-resumen">
      <div class="resumen-stats">
        <div class="resumen-stat resumen-stat-destacado"><span class="resumen-stat-icono"><i class="ti ti-truck-delivery"></i></span><div><span class="resumen-stat-valor">${enCurso}</span><span class="resumen-stat-label">En curso</span></div></div>
        <div class="resumen-stat"><span class="resumen-stat-icono"><i class="ti ti-shopping-bag"></i></span><div><span class="resumen-stat-valor">${todos}</span><span class="resumen-stat-label">Pedidos totales</span></div></div>
        <div class="resumen-stat"><span class="resumen-stat-icono"><i class="ti ti-circle-check"></i></span><div><span class="resumen-stat-valor">${entregados}</span><span class="resumen-stat-label">Entregados</span></div></div>
        <div class="resumen-stat"><span class="resumen-stat-icono"><i class="ti ti-wallet"></i></span><div><span class="resumen-stat-valor">${formatearMoneda(gastado)}</span><span class="resumen-stat-label">Total comprado</span></div></div>
      </div>
      ${aviso}
    </div>`;
  animarContadoresResumen(cont);
}

// Skeleton con la misma forma que la tarjeta final (cabecera + progreso + cuerpo) para que no haya
// salto de layout al cargar.
function renderSkeletonPedidos(n = 2) {
  const tarjeta = `
    <div class="mp-card mp-skel" aria-hidden="true">
      <div class="mp-cabecera">
        <div class="skeleton mp-skel-thumb"></div>
        <div class="mp-skel-lineas">
          <div class="skeleton skeleton-line tall w-70"></div>
          <div class="skeleton skeleton-line w-40"></div>
        </div>
        <div class="skeleton mp-skel-total"></div>
      </div>
      <div class="mp-skel-progreso">${'<div class="skeleton"></div>'.repeat(4)}</div>
      <div class="mp-skel-cuerpo">
        <div class="skeleton skeleton-line w-70"></div>
        <div class="skeleton skeleton-line w-60"></div>
      </div>
    </div>`;
  return tarjeta.repeat(n);
}

// Lleva al comprador a la tarjeta de un pedido: scroll suave (salvo reduced-motion), resalte breve y
// foco para quien navega con teclado.
function resaltarPedido(pedidoId) {
  if (filtroPedidoActual === 'entregados') {
    filtroPedidoActual = 'todos';
    filtrarYRenderizarPedidos();
  }
  const card = document.querySelector(`.pedido-card[data-pedido-id="${CSS.escape(String(pedidoId))}"]`);
  if (!card) return;
  const reducido = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  card.scrollIntoView({ behavior: reducido ? 'auto' : 'smooth', block: 'center' });
  card.setAttribute('tabindex', '-1');
  card.focus({ preventScroll: true });
  card.classList.remove('mp-resaltado');
  void card.offsetWidth; // reinicia la animación si ya estaba resaltada
  card.classList.add('mp-resaltado');
  setTimeout(() => card.classList.remove('mp-resaltado'), 2200);
}

// API moderna del portapapeles y, si el navegador la rechaza o no existe (contexto no seguro,
// permiso denegado), el método clásico con un <textarea> temporal.
async function copiarTexto(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch { /* seguimos con el plan B */ }
  const area = document.createElement('textarea');
  area.value = texto;
  area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  area.remove();
  return ok;
}

async function copiarPinEntrega(btn) {
  if (!(await copiarTexto(btn.dataset.pin))) {
    toast('No se pudo copiar el código. Anótalo manualmente.', 'error');
    return;
  }
  const original = btn.innerHTML;
  btn.innerHTML = '<i class="ti ti-check"></i> Copiado';
  btn.disabled = true;
  setTimeout(() => { btn.innerHTML = original; btn.disabled = false; }, 1800);
}

function renderEmptyPedidos(tipo) {
  const vacio = document.getElementById('pedidos-empty');
  const plantillas = {
    'no-sesion': `
      <span class="empty-state-icon"><i class="ti ti-lock"></i></span>
      <p><strong>Inicia sesión para ver tus pedidos</strong></p>
      <p class="muted">Necesitas una cuenta para hacer seguimiento de tus compras.</p>
      <button type="button" class="btn btn-primary" id="btn-vacio-login">Iniciar sesión</button>`,
    'sin-pedidos': `
      <span class="empty-state-icon"><i class="ti ti-shopping-bag"></i></span>
      <p><strong>Aún no tienes pedidos</strong></p>
      <p class="muted">Busca productos y arma tu primer pedido directo de productores locales.</p>
      <button type="button" class="btn btn-primary" id="btn-vacio-catalogo">Buscar productos</button>`,
    'sin-en-curso': `
      <span class="empty-state-icon"><i class="ti ti-circle-check"></i></span>
      <p><strong>No tienes pedidos en curso</strong></p>
      <p class="muted">Todos tus pedidos ya fueron entregados.</p>`,
    'sin-entregados': `
      <span class="empty-state-icon"><i class="ti ti-package-off"></i></span>
      <p><strong>Aún no tienes pedidos entregados</strong></p>
      <p class="muted">Aquí verás el historial una vez que se complete una entrega.</p>`,
  };
  vacio.innerHTML = plantillas[tipo] || plantillas['sin-pedidos'];
  vacio.classList.remove('hidden');
  document.getElementById('btn-vacio-login')?.addEventListener('click', () => {
    cambiarTabAuth('login');
    abrirModal('modal-auth');
  });
  document.getElementById('btn-vacio-catalogo')?.addEventListener('click', () => abrirPanelBusqueda());
}

async function cargarMisPedidos() {
  detenerPollingRutas();
  const cont = document.getElementById('pedidos-list');
  const vacio = document.getElementById('pedidos-empty');
  const resumen = document.getElementById('pedidos-resumen');
  vacio.classList.add('hidden');

  if (!Estado.token) {
    cont.innerHTML = '';
    resumen.innerHTML = '';
    pedidosCargados = [];
    actualizarConteosPedidos();
    renderEmptyPedidos('no-sesion');
    return;
  }

  const ids = obtenerPedidosTrackeados();
  if (!ids.length) {
    cont.innerHTML = '';
    resumen.innerHTML = '';
    pedidosCargados = [];
    actualizarConteosPedidos();
    renderEmptyPedidos('sin-pedidos');
    return;
  }
  cont.setAttribute('aria-busy', 'true');
  cont.innerHTML = renderSkeletonPedidos(Math.min(ids.length, 2));
  resumen.innerHTML = renderSkeletonResumen(4);

  if (!Estado.productos.length) {
    try { Estado.productos = await Api.productos.listar(); } catch { /* seguimos sin fotos si falla */ }
  }

  const datos = await Promise.all(ids.map((id) => cargarDatosPedido(id)));
  pedidosCargados = datos.filter(Boolean);
  cont.removeAttribute('aria-busy');

  if (!pedidosCargados.length) {
    cont.innerHTML = '<p class="muted">No se pudieron cargar los pedidos.</p>';
    resumen.innerHTML = '';
    actualizarConteosPedidos();
    return;
  }

  renderResumenPedidos();
  filtrarYRenderizarPedidos();
}

function filtrarYRenderizarPedidos() {
  const cont = document.getElementById('pedidos-list');

  document.querySelectorAll('.pedidos-tab').forEach((tab) => {
    const activa = tab.dataset.filtro === filtroPedidoActual;
    tab.classList.toggle('active', activa);
    tab.setAttribute('aria-selected', activa ? 'true' : 'false');
  });
  actualizarConteosPedidos();

  let filtrados = pedidosCargados;
  if (filtroPedidoActual === 'en_curso') filtrados = pedidosCargados.filter((d) => !esPedidoEntregado(d));
  if (filtroPedidoActual === 'entregados') filtrados = pedidosCargados.filter(esPedidoEntregado);

  detenerPollingRutas();

  if (!filtrados.length) {
    cont.innerHTML = '';
    renderEmptyPedidos(
      filtroPedidoActual === 'en_curso' ? 'sin-en-curso' :
      filtroPedidoActual === 'entregados' ? 'sin-entregados' : 'sin-pedidos'
    );
    return;
  }
  document.getElementById('pedidos-empty').classList.add('hidden');
  cont.innerHTML = filtrados.map((d, i) => renderTarjetaPedido(d.pedido, d.envio, d.pago, d.ruta, i)).join('');

  // Aviso para lectores de pantalla (el contador de las pestañas cambia sin mover el foco).
  const n = filtrados.length;
  const sufijo = filtroPedidoActual === 'en_curso' ? ' en curso' : filtroPedidoActual === 'entregados' ? (n === 1 ? ' entregado' : ' entregados') : '';
  document.getElementById('pedidos-status').textContent = `Mostrando ${n} pedido${n === 1 ? '' : 's'}${sufijo}.`;

  filtrados.forEach((d) => {
    if (d.envio && ['asignado', 'en_camino'].includes(d.envio.estado) && d.ruta?.disponible) {
      iniciarMapaRutaPedido(d.envio.id, d.ruta);
    }
  });

  cargarEstadosResenaPedidos(filtrados);
}

async function cargarEstadosResenaPedidos(filtrados) {
  for (const d of filtrados.filter(esPedidoEntregado)) {
    const contenedor = document.querySelector(`.pedido-card[data-pedido-id="${d.pedido.id}"] .resena-cta`);
    if (!contenedor) continue;

    const pendientes = [];
    for (const item of (d.pedido.items || [])) {
      try {
        const resenas = await Api.productos.listarResenas(item.producto_id);
        if (!resenas.some((r) => String(r.usuario_id) === String(Estado.usuarioId))) pendientes.push(item.producto_id);
      } catch { /* si falla la consulta, no mostramos el CTA para ese producto */ }
    }

    if (pendientes.length) {
      contenedor.innerHTML = pendientes.map((pid) => {
        const nombre = Estado.productos.find((p) => p.id === pid)?.nombre || 'este producto';
        return `<button type="button" class="btn btn-outline btn-sm btn-calificar-producto" data-producto-id="${pid}"><i class="ti ti-star"></i> Califica ${escapeAttr(nombre)}</button>`;
      }).join('');
    }
  }
}

async function cargarDatosPedido(pedidoId) {
  let pedido;
  try {
    pedido = await Api.pedidos.obtener(pedidoId);
  } catch {
    return null;
  }

  let envio = null;
  try {
    envio = await Api.transporte.obtenerEnvio(pedidoId);
  } catch { /* aún no tiene envío asignado */ }

  let pago = null;
  try {
    pago = await Api.pagos.obtener(pedidoId);
  } catch { /* aún no hay un pago registrado para este pedido */ }

  let ruta = null;
  if (envio) {
    try {
      ruta = await Api.transporte.ruta(envio.id);
    } catch { /* seguimiento de ruta no disponible por ahora */ }
  }

  return { pedido, envio, pago, ruta };
}

function renderStatsRuta(ruta) {
  return `
    <div class="ruta-stat"><span class="ruta-stat-icon"><i class="ti ti-route"></i></span><div><strong>${ruta.distancia_km} km</strong><span>distancia</span></div></div>
    <div class="ruta-stat"><span class="ruta-stat-icon"><i class="ti ti-clock"></i></span><div><strong>~${ruta.tiempo_estimado_min} min</strong><span>tiempo estimado</span></div></div>`;
}

function renderBloqueRuta(envio, ruta) {
  if (!envio) return '';
  if (!ruta || !ruta.disponible) {
    const mensaje = ruta?.mensaje || 'El seguimiento en mapa aún no está disponible para este envío.';
    return `<div class="mapa-bloque-neutro"><span class="icon"><i class="ti ti-map"></i></span> ${escapeAttr(mensaje)}</div>`;
  }
  return `
    <div class="mapa-bloque">
      <div id="mapa-ruta-${envio.id}" class="mapa-mini"></div>
      <div class="ruta-stats" id="ruta-datos-${envio.id}">${renderStatsRuta(ruta)}</div>
    </div>`;
}

// ---- Tarjeta de pedido (Mis Pedidos) ----
function formatearTelefono(tel) {
  const digitos = String(tel || '').replace(/\D/g, '');
  return digitos.length === 9 ? `${digitos.slice(0, 3)} ${digitos.slice(3, 6)} ${digitos.slice(6)}` : String(tel || '');
}

// El pago cuenta como confirmado si Pagos lo aprobó o si el pedido ya avanzó por eso (confirmado /
// entregado): Pagos y Pedidos se sincronizan por eventos, así que uno puede ir un instante detrás.
function pagoConfirmadoPedido(d) {
  return d.pago?.estado === 'aprobado' || ['confirmado', 'entregado'].includes(d.pedido.estado) || esPedidoEntregado(d);
}

function pagoRechazadoPedido(d) {
  return d.pago?.estado === 'rechazado' && !pagoConfirmadoPedido(d);
}

// Estado global del pedido (chip + color de acento de la tarjeta). Sale de los tres estados reales
// — pedido, pago y envío —, no se inventa ninguno.
function estadoGeneralPedido(d) {
  const { pedido, envio } = d;
  if (esPedidoEntregado(d)) return { clave: 'entregado', etiqueta: 'Entregado', icono: 'circle-check', pill: 'entregado' };
  if (pedido.estado === 'cancelado') return { clave: 'pago_rechazado', etiqueta: 'Cancelado', icono: 'circle-x', pill: 'cancelado' };
  if (pagoRechazadoPedido(d)) return { clave: 'pago_rechazado', etiqueta: 'Pago rechazado', icono: 'alert-triangle', pill: 'rechazado' };
  if (envio?.estado === 'en_camino') return { clave: 'en_curso', etiqueta: 'En camino', icono: 'truck', pill: 'en_camino' };
  if (envio?.estado === 'asignado') return { clave: 'en_curso', etiqueta: 'Repartidor asignado', icono: 'user-check', pill: 'asignado' };
  if (envio?.estado === 'propuesto') return { clave: 'en_curso', etiqueta: 'Esperando repartidor', icono: 'hourglass', pill: 'propuesto' };
  if (pagoConfirmadoPedido(d)) return { clave: 'en_curso', etiqueta: 'Preparando tu pedido', icono: 'package', pill: 'confirmado' };
  return { clave: 'pendiente', etiqueta: 'Pago pendiente', icono: 'hourglass', pill: 'pendiente' };
}

// Cuatro hitos: Pedido → Pago → En camino → Entregado. Un hito está "hecho" cuando se alcanzó; el
// primero sin alcanzar es el "actual" (o "error" si el pago falló) y los siguientes quedan
// "pendientes" — mismo patrón de nodos que las líneas de tiempo de seguimiento de pedidos.
function pasosPedido(d) {
  const { pedido, envio } = d;
  const entregado = esPedidoEntregado(d);
  const pagoOk = pagoConfirmadoPedido(d);
  const pagoMal = !pagoOk && (pagoRechazadoPedido(d) || pedido.estado === 'cancelado');
  const enCamino = envio?.estado === 'en_camino' || entregado;

  const pasos = [
    { icono: 'receipt', titulo: 'Pedido', hecho: true, detalle: 'Realizado' },
    {
      icono: 'credit-card', titulo: 'Pago', hecho: pagoOk, error: pagoMal,
      detalle: pagoOk ? 'Confirmado' : pagoMal ? (pedido.estado === 'cancelado' ? 'Cancelado' : 'Rechazado') : 'Pendiente',
    },
    {
      icono: 'truck', titulo: 'En camino', hecho: enCamino,
      detalle: enCamino ? (entregado ? 'Completado' : 'En ruta hacia ti')
        : !pagoOk ? 'Por asignar'
        : envio?.estado === 'asignado' ? 'Repartidor asignado'
        : envio?.estado === 'propuesto' ? 'Esperando al repartidor'  // ya se le ofreció el envío; falta que acepte
        : envio ? 'Buscando repartidor' : 'Preparando el envío',
    },
    { icono: 'circle-check', titulo: 'Entregado', hecho: entregado, detalle: entregado ? 'Recibido' : 'Pendiente' },
  ];
  const primeroSinCumplir = pasos.findIndex((p) => !p.hecho);
  return pasos.map((p, i) => ({
    ...p,
    estado: p.hecho ? 'hecho' : i === primeroSinCumplir ? (p.error ? 'error' : 'actual') : 'pendiente',
  }));
}

// El estado no puede depender solo del color: cada hito lleva un prefijo solo para lectores de pantalla.
const PREFIJO_PASO = { hecho: 'Completado: ', actual: 'Paso actual: ', error: 'Con problema: ', pendiente: 'Pendiente: ' };

function renderProgresoPedido(pasos) {
  return `
    <ol class="mp-progreso" aria-label="Progreso del pedido">
      ${pasos.map((p) => `
        <li class="mp-paso mp-paso-${p.estado}"${p.estado === 'actual' || p.estado === 'error' ? ' aria-current="step"' : ''}>
          <span class="mp-paso-nodo" aria-hidden="true"><i class="ti ti-${p.estado === 'hecho' ? 'check' : p.estado === 'error' ? 'alert-triangle' : p.icono}"></i></span>
          <span class="mp-paso-texto">
            <strong><span class="mp-solo-lectores">${PREFIJO_PASO[p.estado]}</span>${p.titulo}</strong>
            <small>${p.detalle}</small>
          </span>
        </li>`).join('')}
    </ol>`;
}

function tituloPedido(items) {
  const nombres = items.map((i) => i.producto?.nombre || `Producto ${String(i.producto_id).slice(0, 8)}`);
  if (!nombres.length) return 'Pedido sin productos';
  if (nombres.length <= 2) return nombres.join(' y ');
  return `${nombres[0]}, ${nombres[1]} y ${nombres.length - 2} más`;
}

function renderPinEntrega(pin) {
  const digitos = String(pin).split('');
  return `
    <div class="mp-pin">
      <div class="mp-pin-cabecera">
        <i class="ti ti-key"></i>
        <div><strong>Código para el repartidor</strong><span>Dáselo cuando llegue: confirma que recibiste tu pedido.</span></div>
      </div>
      <div class="mp-pin-fila">
        <div class="mp-pin-digitos" role="group" aria-label="Código de entrega: ${escapeAttr(digitos.join(' '))}">
          ${digitos.map((c) => `<span class="mp-pin-digito" aria-hidden="true">${escapeAttr(c)}</span>`).join('')}
        </div>
        <button type="button" class="btn btn-outline btn-copiar-pin" data-pin="${escapeAttr(pin)}"><i class="ti ti-copy"></i> Copiar</button>
      </div>
    </div>`;
}

function renderTarjetaPedido(pedido, envio, pago, ruta, indice = 0) {
  const retraso = Math.min(indice, 8) * 60;
  const d = { pedido, envio, pago };
  const items = (pedido.items || []).map((i) => ({ ...i, producto: Estado.productos.find((p) => p.id === i.producto_id) }));
  const total = totalPedido(pedido);
  const costoEnvio = Number(pedido.costo_envio) || 0;
  const general = estadoGeneralPedido(d);
  const activo = envio && ['asignado', 'en_camino'].includes(envio.estado);
  const entregado = esPedidoEntregado(d);
  const idCorto = String(pedido.id).slice(0, 8);

  const miniaturas = items.slice(0, 3)
    .map((i) => renderMediaProducto(i.producto || { categoria: null, nombre: '' }, 'mp-thumb')).join('');
  const masMiniaturas = items.length > 3 ? `<div class="mp-thumb mp-thumb-mas">+${items.length - 3}</div>` : '';

  const itemsHtml = items.map((i) => {
    const unidad = i.unidad || i.producto?.unidad_medida;
    const cantidadTxt = unidad ? `${i.cantidad} ${unidadPlural(unidad, i.cantidad)}` : `${i.cantidad} ×`;
    const precioTxt = unidad ? `${formatearMoneda(i.precio_unitario)} / ${unidadCorta(unidad)}` : formatearMoneda(i.precio_unitario);
    return `
      <li class="mp-item">
        <div class="mp-item-media">${renderMediaProducto(i.producto || { categoria: null, nombre: '' }, 'mp-item-thumb')}</div>
        <div class="mp-item-info">
          <strong>${escapeAttr(i.producto?.nombre || `Producto ${String(i.producto_id).slice(0, 8)}`)}</strong>
          <small>${cantidadTxt} · ${precioTxt}</small>
        </div>
        <span class="mp-item-total">${formatearMoneda(i.precio_unitario * i.cantidad)}</span>
      </li>`;
  }).join('');

  const cobro = `
    <dl class="mp-cobro" aria-label="Detalle del cobro">
      <div><dt>Subtotal</dt><dd>${formatearMoneda(subtotalPedido(pedido))}</dd></div>
      <div><dt>Envío</dt><dd>${formatearMoneda(costoEnvio)}</dd></div>
      <div class="mp-cobro-total"><dt>Total</dt><dd>${formatearMoneda(total)}</dd></div>
    </dl>`;

  const contacto = `
    <dl class="mp-kv">
      ${pedido.comprador_nombre ? `<div><dt><i class="ti ti-user"></i> Recibe</dt><dd>${escapeAttr(pedido.comprador_nombre)}</dd></div>` : ''}
      ${pedido.comprador_telefono ? `<div><dt><i class="ti ti-phone"></i> Teléfono</dt><dd>${escapeAttr(formatearTelefono(pedido.comprador_telefono))}</dd></div>` : ''}
      <div><dt><i class="ti ti-credit-card"></i> Pago</dt><dd class="mp-kv-capital">${escapeAttr(pago?.estado || '—')}</dd></div>
    </dl>`;

  const alertaPago = pagoRechazadoPedido(d) ? `
    <div class="mp-alerta" role="alert">
      <i class="ti ti-alert-triangle"></i>
      <div><strong>Tu pago fue rechazado</strong><span>Puedes volver a intentarlo sin perder tu pedido.</span></div>
      <button type="button" class="btn btn-primary btn-reintentar-pago" data-pedido-id="${pedido.id}" data-monto="${total}" data-envio="${costoEnvio}">Reintentar pago</button>
    </div>` : '';

  const pin = activo && envio.pin_entrega ? renderPinEntrega(envio.pin_entrega) : '';

  const seguimiento = activo ? `
    <section class="mp-bloque">
      <h4 class="mp-bloque-titulo"><span class="mp-en-vivo" aria-hidden="true"></span> Seguimiento en vivo</h4>
      ${renderBloqueRuta(envio, ruta)}
    </section>` : '';

  const postEntrega = entregado ? `
    <section class="mp-bloque">
      <h4 class="mp-bloque-titulo"><i class="ti ti-shield-check"></i> Tu compra verificada</h4>
      <div class="resena-cta"></div>
      <div class="pedido-certificado-cta">
        <button type="button" class="btn btn-outline btn-ver-certificado-pedido" data-pedido-id="${pedido.id}"><i class="ti ti-certificate"></i> Ver certificado de compra</button>
        <div class="panel-expandible hidden" data-panel="certificado-pedido" data-pedido-id="${pedido.id}"></div>
      </div>
    </section>` : '';

  const lateral = [alertaPago, pin, seguimiento, postEntrega].filter(Boolean).join('');

  return `
    <article class="pedido-card mp-card" style="animation-delay:${retraso}ms" data-pedido-id="${pedido.id}" data-estado="${general.clave}">
      <header class="mp-cabecera">
        <div class="mp-thumbs" aria-hidden="true">${miniaturas}${masMiniaturas}</div>
        <div class="mp-cabecera-info">
          <h3 class="mp-titulo">${escapeAttr(tituloPedido(items))}</h3>
          <div class="mp-meta">
            <span class="mp-chip mp-chip-id" title="Número de pedido">#${idCorto}</span>
            <span class="mp-chip"><i class="ti ti-calendar-event"></i> ${formatearFechaServidor(pedido.fecha_creacion)}</span>
            <span class="mp-chip"><i class="ti ti-shopping-bag"></i> ${items.length} producto${items.length === 1 ? '' : 's'}</span>
          </div>
        </div>
        <div class="mp-total">
          <span class="mp-total-label">Total</span>
          <strong>${formatearMoneda(total)}</strong>
          <span class="pill pill-estado pill-${general.pill}"><i class="ti ti-${general.icono}"></i> ${general.etiqueta}</span>
        </div>
      </header>

      ${renderProgresoPedido(pasosPedido(d))}

      <div class="mp-cuerpo${lateral ? ' mp-cuerpo-lateral' : ''}">
        <section class="mp-col-items">
          <h4 class="mp-bloque-titulo"><i class="ti ti-basket"></i> Productos</h4>
          <ul class="mp-items">${itemsHtml}</ul>
          ${cobro}
          ${contacto}
        </section>
        ${lateral ? `<aside class="mp-col-lateral">${lateral}</aside>` : ''}
      </div>
    </article>
  `;
}

function renderCertificadoPedido(cert) {
  const detalle = Array.isArray(cert.detalle) ? cert.detalle : [];
  const filas = detalle.map((item) => {
    const producto = Estado.productos.find((p) => p.id === item.producto_id);
    const nombre = producto?.nombre || `Producto ${String(item.producto_id).slice(0, 8)}`;
    return `
      <div class="certificado-producto-linea">
        <span>${escapeAttr(nombre)} — <code>${escapeAttr(hashCorto(item.hash))}</code></span>
        <a href="verificar.html?producto_id=${encodeURIComponent(item.producto_id)}" target="_blank" rel="noopener" class="certificado-link-cadena">Ver cadena completa →</a>
      </div>`;
  }).join('');

  return `
    <div class="certificado-pedido">
      <p class="certificado-pedido-hash">Certificado: <code>${escapeAttr(hashCorto(cert.merkle_root))}</code></p>
      ${filas}
      <p class="muted certificado-pedido-nota"><i class="ti ti-lock"></i> Verificado mediante blockchain</p>
    </div>`;
}

async function alternarCertificadoPedido(pedidoId) {
  const panel = document.querySelector(`.panel-expandible[data-panel="certificado-pedido"][data-pedido-id="${pedidoId}"]`);
  if (!panel) return;

  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  if (panel.dataset.cargado) return;

  panel.innerHTML = '<p class="muted">Cargando certificado...</p>';
  try {
    const cert = await Api.certificacion.certificadoPedido(pedidoId);
    panel.innerHTML = renderCertificadoPedido(cert);
    panel.dataset.cargado = '1';
  } catch (err) {
    if (err.status === 404) {
      panel.innerHTML = '<p class="muted">Certificado en proceso, vuelve a intentar en un momento.</p>';
    } else {
      panel.innerHTML = '<p class="muted">No se pudo cargar el certificado por ahora.</p>';
    }
    // no marcamos data-cargado: al volver a abrir el acordeón se reintenta el fetch
  }
}

// ---- Mapa de ruta con polling cada 15s ----
const mapasRutaActivos = {}; // envioId -> { mapa, marcadorOrigen, linea }
let intervalosPollingRuta = [];

function iniciarMapaRutaPedido(envioId, ruta) {
  const resultado = crearMapaRuta(`mapa-ruta-${envioId}`, ruta.origen, ruta.destino);
  if (!resultado) return;
  mapasRutaActivos[envioId] = resultado;

  const intervalId = setInterval(async () => {
    try {
      const actualizada = await Api.transporte.ruta(envioId);
      const activo = mapasRutaActivos[envioId];
      if (!activo || !actualizada.disponible) return;

      const puntoOrigen = [Number(actualizada.origen.latitud), Number(actualizada.origen.longitud)];
      const puntoDestino = [Number(actualizada.destino.latitud), Number(actualizada.destino.longitud)];
      activo.marcadorOrigen.setLatLng(puntoOrigen);
      activo.linea.setLatLngs([puntoOrigen, puntoDestino]);

      const datosEl = document.getElementById(`ruta-datos-${envioId}`);
      if (datosEl) datosEl.innerHTML = renderStatsRuta(actualizada);
    } catch { /* se reintenta en el siguiente ciclo */ }
  }, 15000);

  intervalosPollingRuta.push(intervalId);
}

function detenerPollingRutas() {
  intervalosPollingRuta.forEach(clearInterval);
  intervalosPollingRuta = [];
  Object.keys(mapasRutaActivos).forEach((id) => {
    try { mapasRutaActivos[id].mapa.remove(); } catch { /* ya estaba destruido */ }
    delete mapasRutaActivos[id];
  });
}

async function buscarPedidoPorId() {
  const input = document.getElementById('input-buscar-pedido');
  const id = input.value.trim();
  if (!id) return;

  detenerPollingRutas();
  const cont = document.getElementById('pedidos-list');
  document.getElementById('pedidos-empty').classList.add('hidden');
  cont.setAttribute('aria-busy', 'true');
  cont.innerHTML = renderSkeletonPedidos(1);

  try {
    const d = await cargarDatosPedido(id);
    cont.removeAttribute('aria-busy');
    if (!d) throw new Error('No se encontró un pedido con ese ID.');
    if (!Estado.productos.length) {
      try { Estado.productos = await Api.productos.listar(); } catch { /* seguimos sin fotos si falla */ }
    }
    cont.innerHTML = renderTarjetaPedido(d.pedido, d.envio, d.pago, d.ruta);
    guardarPedidoTrackeado(id);
    if (d.envio && ['asignado', 'en_camino'].includes(d.envio.estado) && d.ruta?.disponible) {
      iniciarMapaRutaPedido(d.envio.id, d.ruta);
    }
    cargarEstadosResenaPedidos([d]);
  } catch (err) {
    cont.removeAttribute('aria-busy');
    manejarError(err, 'buscar el pedido');
    cargarMisPedidos();
  }
}

// ============ NOTIFICACIONES ============
// Cada tipo de aviso: a qué filtro pertenece, su ícono y su título. Un tipo desconocido igual se muestra
// (con ícono de campana) y solo aparece en "Todas".
const NOTIF_TIPOS = {
  pedido_creado: { grupo: 'pedido', icono: 'package', titulo: 'Pedido registrado' },
  envio_actualizado: { grupo: 'envio', icono: 'truck', titulo: 'Envío actualizado' },
  producto_disponible: { grupo: 'producto', icono: 'basket', titulo: 'Producto disponible' },
};
const NOTIF_FILTROS = [
  { clave: 'todas', etiqueta: 'Todas' },
  { clave: 'pedido', etiqueta: 'Pedidos' },
  { clave: 'envio', etiqueta: 'Envíos' },
  { clave: 'producto', etiqueta: 'Productos' },
];
const ETIQUETAS_ESTADO_ENVIO = {
  pendiente: 'Pendiente',
  pendiente_asignacion: 'Buscando repartidor',
  propuesto: 'Propuesto a un repartidor',
  asignado: 'Repartidor asignado',
  en_camino: 'En camino',
  entregado: 'Entregado',
  cancelado: 'Cancelado',
};

function tipoNotificacion(tipo) {
  return NOTIF_TIPOS[tipo] || { grupo: 'otro', icono: 'bell', titulo: 'Aviso' };
}

// El mensaje de un envío trae el estado en texto ("...cambió de estado a: en_camino."): se muestra
// como etiqueta legible en vez de la clave cruda.
function pillEstadoNotificacion(estado) {
  const clave = estado.toLowerCase();
  const clase = ESTADOS_BADGE_CONOCIDOS.has(clave) ? `pill-${clave}` : 'pill-default';
  const icono = ICONOS_ESTADO[clave] ? `<i class="ti ti-${ICONOS_ESTADO[clave]}" aria-hidden="true"></i>` : '';
  const etiqueta = ETIQUETAS_ESTADO_ENVIO[clave] || clave.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  return `<span class="pill pill-estado ${clase}">${icono} ${escapeAttr(etiqueta)}</span>`;
}

function tiempoRelativo(fechaStr) {
  const d = parsearFechaServidor(fechaStr);
  if (isNaN(d)) return '—';
  const min = Math.floor(Math.max(0, Date.now() - d.getTime()) / 60000);
  if (min < 1) return 'hace un momento';
  if (min < 60) return `hace ${min} min`;
  const horas = Math.floor(min / 60);
  if (horas < 24) return `hace ${horas} h`;
  const dias = Math.floor(horas / 24);
  if (dias === 1) return 'ayer';
  if (dias < 7) return `hace ${dias} días`;
  return d.toLocaleDateString('es-PE', { day: '2-digit', month: 'short', year: 'numeric' });
}

// Grupo de día para los encabezados de la lista (por día calendario local, no por horas transcurridas).
function grupoDeDia(fecha) {
  if (isNaN(fecha)) return 'Anteriores';
  const hoy = new Date(); hoy.setHours(0, 0, 0, 0);
  const dia = new Date(fecha); dia.setHours(0, 0, 0, 0);
  const diferencia = Math.round((hoy - dia) / 86400000);
  if (diferencia <= 0) return 'Hoy';
  if (diferencia === 1) return 'Ayer';
  if (diferencia < 7) return 'Esta semana';
  return 'Anteriores';
}

function renderVacioNotificaciones(tipo) {
  const vacio = document.getElementById('notificaciones-empty');
  const plantillas = {
    'no-sesion': `
      <span class="empty-state-icon"><i class="ti ti-lock"></i></span>
      <p><strong>Inicia sesión para ver tus notificaciones</strong></p>
      <button type="button" class="btn btn-primary" data-notif-accion="login">Iniciar sesión</button>`,
    'sin-notificaciones': `
      <span class="empty-state-icon"><i class="ti ti-bell"></i></span>
      <p><strong>No tienes notificaciones todavía</strong></p>
      <p class="muted">Aquí verás avisos sobre tus pedidos y envíos apenas ocurran.</p>
      <button type="button" class="btn btn-primary" data-notif-accion="explorar">Explorar el catálogo</button>`,
    'sin-filtro': `
      <span class="empty-state-icon"><i class="ti ti-filter-off"></i></span>
      <p><strong>No hay avisos de este tipo</strong></p>
      <button type="button" class="btn btn-outline" data-notif-accion="todas">Ver todas</button>`,
  };
  vacio.innerHTML = plantillas[tipo] || plantillas['sin-notificaciones'];
  vacio.classList.remove('hidden');
}

const TAMANO_PAGINA_NOTIF = 10;
let notificacionesCargadas = [];
let notifMostrar = TAMANO_PAGINA_NOTIF;
let notifFiltro = 'todas';
let notifNuevas = new Set(); // ids de los avisos posteriores a la última visita a esta pantalla

function claveNotifVisto() {
  return `agro_notif_visto_${Estado.usuarioId}`;
}

function notifFiltradas() {
  return notifFiltro === 'todas'
    ? notificacionesCargadas
    : notificacionesCargadas.filter((n) => tipoNotificacion(n.tipo).grupo === notifFiltro);
}

function conteosNotificaciones() {
  const conteos = { todas: notificacionesCargadas.length, pedido: 0, envio: 0, producto: 0 };
  notificacionesCargadas.forEach((n) => {
    const grupo = tipoNotificacion(n.tipo).grupo;
    if (grupo in conteos) conteos[grupo] += 1;
  });
  return conteos;
}

function renderNotifFiltros() {
  const cont = document.getElementById('notif-filtros');
  if (!notificacionesCargadas.length) { cont.innerHTML = ''; return; } // sin avisos no hay nada que filtrar
  const conteos = conteosNotificaciones();
  cont.innerHTML = NOTIF_FILTROS.map((f) => `
    <button type="button" class="notif-filtro${f.clave === notifFiltro ? ' active' : ''}" data-filtro="${f.clave}" aria-pressed="${f.clave === notifFiltro}">
      ${f.etiqueta} <span class="notif-filtro-cuenta">${conteos[f.clave]}</span>
    </button>`).join('');
}

function renderNotifResumen() {
  const cont = document.getElementById('notif-resumen');
  if (!notificacionesCargadas.length) { cont.innerHTML = ''; return; }
  const c = conteosNotificaciones();
  const fila = (icono, etiqueta, valor) => `<li><i class="ti ti-${icono}" aria-hidden="true"></i><span>${etiqueta}</span><strong>${valor}</strong></li>`;
  cont.innerHTML = `
    <div class="notif-resumen-cab">
      <span class="notif-resumen-icono"><i class="ti ti-bell-ringing" aria-hidden="true"></i></span>
      <div><strong>${c.todas}</strong><span>${c.todas === 1 ? 'aviso en total' : 'avisos en total'}</span></div>
    </div>
    <ul class="notif-resumen-lista">
      ${fila('package', 'Pedidos registrados', c.pedido)}
      ${fila('truck', 'Envíos actualizados', c.envio)}
      ${fila('basket', 'Productos disponibles', c.producto)}
    </ul>
    ${notifNuevas.size ? `<div class="notif-resumen-nuevas"><i class="ti ti-sparkles" aria-hidden="true"></i> ${notifNuevas.size} ${notifNuevas.size === 1 ? 'nueva' : 'nuevas'} desde tu última visita</div>` : ''}`;
}

function renderNotifCard(n, i) {
  const tipo = tipoNotificacion(n.tipo);
  const fecha = parsearFechaServidor(n.fecha_envio);
  const nueva = notifNuevas.has(n.id);
  const estado = n.tipo === 'envio_actualizado' ? /estado a:\s*([a-z_]+)/i.exec(n.mensaje || '')?.[1] : null;
  const mensaje = estado
    ? `<p class="notif-msg">Tu envío cambió de estado.</p><div class="notif-estado-envio">${pillEstadoNotificacion(estado)}</div>`
    : `<p class="notif-msg">${escapeAttr(n.mensaje)}</p>`;

  const acciones = [];
  if (n.pedido_id) {
    acciones.push(`<span class="notif-chip"><i class="ti ti-receipt" aria-hidden="true"></i> Pedido #${String(n.pedido_id).slice(0, 8)}</span>`);
    acciones.push('<button type="button" class="notif-accion" data-accion="ver-pedido">Ver mis pedidos <i class="ti ti-arrow-right" aria-hidden="true"></i></button>');
  } else if (n.producto_id) {
    acciones.push(`<button type="button" class="notif-accion" data-accion="ver-producto" data-id="${escapeAttr(n.producto_id)}">Ver producto <i class="ti ti-arrow-right" aria-hidden="true"></i></button>`);
  }

  return `
    <article class="notif-card${nueva ? ' notif-card--nueva' : ''}" style="animation-delay:${Math.min(i, 10) * 35}ms">
      <div class="notif-icon tipo-${escapeAttr(n.tipo)}"><i class="ti ti-${tipo.icono}" aria-hidden="true"></i></div>
      <div class="notif-cuerpo">
        <div class="notif-cabecera">
          <h4 class="notif-titulo">${tipo.titulo}${nueva ? ' <span class="notif-nueva">Nueva</span>' : ''}</h4>
          <time class="notif-tiempo"${isNaN(fecha) ? '' : ` datetime="${fecha.toISOString()}"`} title="${formatearFechaServidor(n.fecha_envio)}">${tiempoRelativo(n.fecha_envio)}</time>
        </div>
        ${mensaje}
        ${acciones.length ? `<div class="notif-acciones">${acciones.join('')}</div>` : ''}
      </div>
    </article>`;
}

function renderNotificacionesPagina() {
  const cont = document.getElementById('notificaciones-list');
  const btnMas = document.getElementById('btn-notif-cargar-mas');
  const vacio = document.getElementById('notificaciones-empty');
  const filtradas = notifFiltradas();
  const visibles = filtradas.slice(0, notifMostrar);

  renderNotifFiltros();
  renderNotifResumen();
  cont.removeAttribute('aria-busy');
  document.getElementById('notif-estado').textContent = `${filtradas.length} ${filtradas.length === 1 ? 'aviso' : 'avisos'}${notifNuevas.size ? `, ${notifNuevas.size} ${notifNuevas.size === 1 ? 'nuevo' : 'nuevos'}` : ''}`;

  if (!filtradas.length) {
    cont.innerHTML = '';
    btnMas.classList.add('hidden');
    renderVacioNotificaciones('sin-filtro');
    return;
  }
  vacio.classList.add('hidden');

  let html = '';
  let grupoActual = null;
  visibles.forEach((n, i) => {
    const grupo = grupoDeDia(parsearFechaServidor(n.fecha_envio));
    if (grupo !== grupoActual) {
      if (grupoActual !== null) html += '</section>';
      html += `<section class="notif-grupo"><h3 class="notif-grupo-titulo">${grupo}</h3>`;
      grupoActual = grupo;
    }
    html += renderNotifCard(n, i);
  });
  cont.innerHTML = `${html}</section>`;
  btnMas.classList.toggle('hidden', notifMostrar >= filtradas.length);
}

function renderSkeletonNotificaciones(n = 3) {
  const tarjeta = `
    <div class="notif-card" aria-hidden="true">
      <div class="skeleton notif-skel-icono"></div>
      <div class="notif-cuerpo">
        <div class="skeleton skeleton-line tall w-40"></div>
        <div class="skeleton skeleton-line w-70" style="margin-top:10px;"></div>
      </div>
    </div>`;
  return tarjeta.repeat(n);
}

async function cargarNotificaciones() {
  const cont = document.getElementById('notificaciones-list');
  const vacio = document.getElementById('notificaciones-empty');
  const btnMas = document.getElementById('btn-notif-cargar-mas');
  vacio.classList.add('hidden');
  btnMas.classList.add('hidden');
  notifMostrar = TAMANO_PAGINA_NOTIF;
  notifFiltro = 'todas';
  notifNuevas = new Set();
  notificacionesCargadas = [];
  renderNotifFiltros();
  renderNotifResumen();

  if (!Estado.token) {
    cont.innerHTML = '';
    document.getElementById('notif-estado').textContent = '';
    renderVacioNotificaciones('no-sesion');
    return;
  }

  cont.setAttribute('aria-busy', 'true');
  cont.innerHTML = renderSkeletonNotificaciones(3);
  try {
    const ids = new Set(obtenerPedidosTrackeados());
    const todas = await Api.notificaciones.listarTodas();
    // Los avisos de producto no cuelgan de un pedido (pedido_id vacío): no se filtran por pedido.
    notificacionesCargadas = ids.size ? todas.filter((n) => !n.pedido_id || ids.has(String(n.pedido_id))) : todas;

    // "Nuevas" = posteriores a la última vez que se abrió esta pantalla (guardado solo en este navegador:
    // el backend no lleva estado de lectura). La primera vez no se marca ninguna.
    let visto = null;
    try { visto = Number(localStorage.getItem(claveNotifVisto())) || null; } catch { /* sin almacenamiento: no hay marcas */ }
    if (visto) {
      notifNuevas = new Set(notificacionesCargadas
        .filter((n) => parsearFechaServidor(n.fecha_envio).getTime() > visto)
        .map((n) => n.id));
    }
    try { localStorage.setItem(claveNotifVisto(), String(Date.now())); } catch { /* idem */ }

    if (!notificacionesCargadas.length) {
      cont.removeAttribute('aria-busy');
      cont.innerHTML = '';
      renderNotifFiltros();
      renderVacioNotificaciones('sin-notificaciones');
      return;
    }
    renderNotificacionesPagina();
  } catch (err) {
    cont.removeAttribute('aria-busy');
    cont.innerHTML = '';
    manejarError(err, 'cargar notificaciones');
  }
}

// ============ PANEL PRODUCTOR ============
function claveProductorId() {
  return `agro_productor_id_${Estado.usuarioId}`;
}

// El perfil de productor vive en el servidor; localStorage solo lo recuerda para no preguntarlo cada
// vez. Si este navegador no lo recuerda (otro dispositivo, sesión nueva, datos borrados) se le pregunta
// al servidor, en vez de pedirle crear un perfil que ya tiene (POST /productores respondería 409).
// Devuelve null solo cuando de verdad todavía no tiene perfil; cualquier otro error se propaga.
async function resolverProductorId() {
  const guardado = localStorage.getItem(claveProductorId());
  if (guardado) {
    try {
      await Api.productores.obtener(guardado);
      return guardado;
    } catch (err) {
      if (err.status !== 404) throw err;
      localStorage.removeItem(claveProductorId());
    }
  }
  try {
    const perfil = await Api.productores.miPerfil();
    localStorage.setItem(claveProductorId(), perfil.id);
    return perfil.id;
  } catch (err) {
    if (err.status === 404) return null;
    throw err;
  }
}

async function iniciarPanelProductor() {
  if (!Estado.token || Estado.rol !== 'productor') {
    cambiarVista('inicio');
    return;
  }

  try {
    Estado.productorId = await resolverProductorId();
  } catch (err) {
    manejarError(err, 'cargar tu perfil de productor');
    cambiarVista('inicio');
    return;
  }

  const setup = document.getElementById('productor-setup');
  const panel = document.getElementById('productor-panel');

  if (Estado.productorId) {
    setup.classList.add('hidden');
    panel.classList.remove('hidden');
    inicializarMapaUbicacionChacra();
    document.getElementById('productor-resumen').innerHTML = renderSkeletonResumen(3);
    // Las 4 cargas son independientes entre sí (cada una pinta su propia sección), pero el
    // resumen necesita las cuatro resueltas antes de poder calcular sus métricas — de ahí el
    // Promise.all en vez de simplemente dispararlas sueltas como antes.
    await Promise.all([
      cargarMisProductos(),
      cargarMisChacras(),
      cargarRegistrosProduccionProductor(),
      cargarEntregasDirectasPendientes(),
    ]);
    pintarResumenChacras();
    document.getElementById('productor-resumen').innerHTML = renderResumenProductor();
    animarContadoresResumen(document.getElementById('productor-resumen'));

    // Secciones dedicadas nuevas (ver activarTabProductor) — reusan Estado.registrosProduccion
    // que las 4 cargas de arriba ya dejaron listo, sin pedir nada nuevo al backend. Se vuelve a
    // pintar cada vez que se entra al panel, así que el flag de "ya dibujé los gráficos de
    // economía" se resetea acá para que se redibujen si el productor visita esa pestaña otra vez.
    const registros = Estado.registrosProduccion || [];
    document.getElementById('productor-ultimas-siembras').innerHTML = renderTablaUltimasSiembras(registros);
    document.getElementById('productor-economia-stats').innerHTML = renderEconomiaStatsProductor(registros);
    document.getElementById('productor-economia-historial').innerHTML = renderTablaHistorialCosechas(registros);
    dibujarGraficoCosechasPorMes(registros);
    graficosEconomiaProductorListos = false;
    activarTabProductor('inicio');
  } else {
    setup.classList.remove('hidden');
    panel.classList.add('hidden');
    inicializarMapaUbicacionProductor();
  }
}

// ---- Categorías fijas (mismo catálogo de 8 usado en toda la app) ----
const CATEGORIAS_PRODUCTO = ['fruta', 'verdura', 'tuberculo', 'grano', 'legumbre', 'lacteo', 'huevo', 'hierba'];

// Opciones <option> del selector de categoría de un borrador — mismo catálogo de 8 que ya
// puebla el resto de la app (CATEGORIAS_PRODUCTO), pero como string reusable por tarjeta en vez
// de un <select> único poblado una sola vez (ya no hay un solo formulario, hay uno por borrador).
function opcionesCategoriaHTML(seleccionada) {
  return '<option value="">Elige una categoría</option>' +
    CATEGORIAS_PRODUCTO.map((c) => `<option value="${c}" ${c === seleccionada ? 'selected' : ''}>${ICONOS_CATEGORIA[c]} ${NOMBRE_CATEGORIA[c]}</option>`).join('');
}

// ---- Selector de ubicación (mapa del perfil del productor) ----
let mapaUbicacionProductor = null;
let ubicacionProductorSeleccionada = null; // { lat, lng }

function inicializarMapaUbicacionProductor() {
  if (mapaUbicacionProductor) {
    try { mapaUbicacionProductor.mapa.remove(); } catch { /* ya estaba destruido */ }
    mapaUbicacionProductor = null;
  }
  ubicacionProductorSeleccionada = null;
  const ayuda = document.getElementById('mapa-productor-ayuda');
  if (ayuda) {
    ayuda.textContent = 'Toca el mapa para marcar dónde está tu parcela.';
    ayuda.classList.remove('confirmado');
  }

  mapaUbicacionProductor = crearMapaSeleccionable('mapa-ubicacion-productor', {
    emoji: '<i class="ti ti-map-pin"></i>',
    color: '#2d6a4f',
    onSeleccionar: (lat, lng) => {
      ubicacionProductorSeleccionada = { lat, lng };
      actualizarAyudaUbicacionProductor();
    },
  });
}

function actualizarAyudaUbicacionProductor() {
  const ayuda = document.getElementById('mapa-productor-ayuda');
  if (!ayuda || !ubicacionProductorSeleccionada) return;
  ayuda.innerHTML = `<i class="ti ti-map-pin"></i> Ubicación marcada (${ubicacionProductorSeleccionada.lat.toFixed(5)}, ${ubicacionProductorSeleccionada.lng.toFixed(5)})`;
  ayuda.classList.add('confirmado');
}

async function usarMiUbicacionProductor() {
  const btn = document.getElementById('btn-ubicacion-productor');
  btn.disabled = true;
  btn.textContent = 'Obteniendo ubicación...';
  try {
    const { lat, lng } = await obtenerUbicacionActual();
    mapaUbicacionProductor?.moverMarcador(lat, lng);
    ubicacionProductorSeleccionada = { lat, lng };
    actualizarAyudaUbicacionProductor();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Usar mi ubicación';
  }
}

async function crearPerfilProductor(e) {
  e.preventDefault();
  try {
    const datos = {
      nombre: document.getElementById('productor-nombre').value.trim(),
      comunidad: document.getElementById('productor-comunidad').value.trim() || null,
      contacto: document.getElementById('productor-contacto').value.trim() || null,
      latitud: ubicacionProductorSeleccionada ? String(ubicacionProductorSeleccionada.lat) : null,
      longitud: ubicacionProductorSeleccionada ? String(ubicacionProductorSeleccionada.lng) : null,
    };
    const productor = await Api.productores.crear(datos);
    Estado.productorId = productor.id;
    localStorage.setItem(claveProductorId(), productor.id);
    toast('¡Perfil de productor creado! Ya puedes publicar productos 🌱');
    iniciarPanelProductor();
  } catch (err) {
    manejarError(err, 'crear el perfil de productor');
  }
}

// ---- Gestión Económica: siembra ↔ unidad de medida (mismo patrón que el resto del panel) ----
function actualizarVisibilidadEquivalenciaProduccion() {
  const unidad = document.getElementById('produccion-unidad')?.value;
  const bloque = document.getElementById('produccion-equivalencia-bloque');
  const input = document.getElementById('produccion-equivalencia');
  if (!bloque || !input) return;
  const necesitaEquivalencia = unidad && unidad !== 'kg';
  bloque.classList.toggle('hidden', !necesitaEquivalencia);
  input.required = necesitaEquivalencia;
  if (!necesitaEquivalencia) input.value = '';
}

// Puramente informativo — "sin afectar ningún campo automáticamente" (no toca precio/costos).
async function mostrarPrecioReferenciaCultivo() {
  const cultivo = document.getElementById('produccion-cultivo')?.value.trim();
  const cont = document.getElementById('produccion-precio-referencia');
  if (!cont) return;
  if (!cultivo) { cont.classList.add('hidden'); return; }

  cont.classList.remove('hidden');
  cont.innerHTML = '<i class="ti ti-bulb"></i> Consultando precio de referencia…';
  try {
    const data = await Api.productos.precioReferencia(cultivo);
    // innerHTML por el ícono, así que "cultivo" (texto libre del formulario) va escapado a mano.
    cont.innerHTML = data.precio_promedio != null
      ? `<i class="ti ti-bulb"></i> Precio de referencia en el catálogo: ${formatearMoneda(data.precio_promedio)}/kg (promedio)`
      : `<i class="ti ti-bulb"></i> "${escapeAttr(cultivo)}" no tiene productos en el catálogo todavía — sin precio de referencia.`;
  } catch (err) {
    cont.innerHTML = '<i class="ti ti-bulb"></i> No se pudo consultar el precio de referencia (servicio de Productos no disponible).';
  }
}

async function crearRegistroProduccion(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  const unidad = document.getElementById('produccion-unidad').value;
  const equivalenciaValor = document.getElementById('produccion-equivalencia').value;
  const chacraId = document.getElementById('produccion-chacra').value;

  if (!chacraId) {
    toast('Registra una chacra antes de poder registrar una siembra', 'error');
    return;
  }
  if (unidad !== 'kg' && !equivalenciaValor) {
    toast('Indica la equivalencia a kg para esta unidad de medida', 'error');
    return;
  }

  btn.disabled = true;
  try {
    const datos = {
      cultivo: document.getElementById('produccion-cultivo').value.trim(),
      chacra_id: chacraId,
      numero_parcelas: parseInt(document.getElementById('produccion-parcelas').value, 10),
      ubicacion_cosecha: document.getElementById('produccion-ubicacion').value.trim() || null,
      costo_semillas: parseFloat(document.getElementById('produccion-costo-semillas').value) || 0,
      costo_insumos: parseFloat(document.getElementById('produccion-costo-insumos').value) || 0,
      unidad_medida: unidad,
      equivalencia_kg: unidad !== 'kg' ? parseFloat(equivalenciaValor) : null,
      fecha_siembra: document.getElementById('produccion-fecha-siembra').value,
      fecha_cosecha_estimada: document.getElementById('produccion-fecha-cosecha-estimada').value,
    };
    await Api.productores.produccion.crear(datos);
    toast('Siembra registrada 🌱');
    cargarPerfil();
  } catch (err) {
    manejarError(err, 'registrar la siembra');
  } finally {
    btn.disabled = false;
  }
}

function alternarFormCompletarCosecha(registroId) {
  const form = document.querySelector(`.form-completar-cosecha[data-id="${registroId}"]`);
  form?.classList.toggle('hidden');
}

async function completarCosechaRegistro(e, registroId) {
  e.preventDefault();
  const form = e.target;
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const datos = {
      cantidad_cosechada: parseFloat(form.cantidad_cosechada.value),
      fecha_cosecha_real: form.fecha_cosecha_real.value,
      costo_mano_obra: parseFloat(form.costo_mano_obra.value) || 0,
      costo_envio: parseFloat(form.costo_envio.value) || 0,
    };
    await Api.productores.produccion.completarCosecha(registroId, datos);
    toast('Cosecha completada ✅');
    cargarPerfil();
  } catch (err) {
    manejarError(err, 'completar la cosecha');
  } finally {
    btn.disabled = false;
  }
}

function renderTarjetaProductorProducto(p, indice) {
  const stockBajo = p.stock > 0 && p.stock <= 5;
  const sinStock = p.stock <= 0;
  const tieneCalificacion = p.calificacion_promedio != null && p.total_resenas > 0;
  const retraso = Math.min(indice, 12) * 35;

  return `
    <div class="product-card productor-card" style="animation-delay:${retraso}ms;cursor:default">
      <div class="product-card-media">
        ${renderMediaProducto(p, 'product-banner')}
        ${sinStock ? '<span class="badge-flotante badge-stock-bajo">Sin stock</span>' : ''}
        ${!sinStock && stockBajo ? '<span class="badge-flotante badge-stock-bajo"><i class="ti ti-alert-triangle"></i> Poco stock</span>' : ''}
      </div>
      <div class="product-card-body">
        <div class="product-card-top">
          <span class="pill pill-categoria">${iconoCategoria(p.categoria)} ${escapeAttr(nombreCategoria(p.categoria))}</span>
        </div>
        <h4>${escapeAttr(p.nombre)}</h4>
        <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
        <span class="product-stock ${stockBajo || sinStock ? 'low' : ''}">${p.stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles` : 'Sin stock'}</span>
        <div class="stock-barra"><div class="stock-barra-fill ${stockBajo || sinStock ? 'low' : ''}" style="width:${Math.min(100, (Number(p.stock) / 50) * 100)}%"></div></div>

        <div class="productor-card-acciones">
          <button type="button" class="btn btn-outline btn-sm btn-toggle-resenas" data-id="${p.id}">
            <i class="ti ti-star"></i> ${tieneCalificacion ? `${p.total_resenas} reseña${p.total_resenas === 1 ? '' : 's'}` : 'Reseñas'}
          </button>
          <button type="button" class="btn btn-outline btn-sm btn-toggle-fotos" data-id="${p.id}"><i class="ti ti-camera"></i> Fotos</button>
          <button type="button" class="btn btn-outline btn-sm btn-ver-certificacion" data-id="${p.id}"><i class="ti ti-link"></i> Certificación</button>
          <button type="button" class="btn btn-outline btn-sm btn-toggle-unidades" data-id="${p.id}"><i class="ti ti-scale"></i> Unidades</button>
        </div>

        <div class="panel-expandible hidden" data-panel="resenas" data-id="${p.id}"></div>
        <div class="panel-expandible hidden" data-panel="fotos" data-id="${p.id}"></div>
        <div class="panel-expandible hidden" data-panel="unidades" data-id="${p.id}"></div>
      </div>
    </div>`;
}

async function alternarResenasProductor(id) {
  const panel = document.querySelector(`.panel-expandible[data-panel="resenas"][data-id="${id}"]`);
  if (!panel) return;
  document.querySelector(`.panel-expandible[data-panel="fotos"][data-id="${id}"]`)?.classList.add('hidden');

  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  if (panel.dataset.cargado) return;

  panel.innerHTML = renderSkeletonFilas(2);
  try {
    const resenas = await Api.productos.listarResenas(id);
    panel.innerHTML = renderResenasSoloLectura(resenas);
    panel.dataset.cargado = '1';
  } catch {
    panel.innerHTML = '<p class="muted">No se pudieron cargar las reseñas.</p>';
  }
}

async function alternarFotosProductor(id) {
  const panel = document.querySelector(`.panel-expandible[data-panel="fotos"][data-id="${id}"]`);
  if (!panel) return;
  document.querySelector(`.panel-expandible[data-panel="resenas"][data-id="${id}"]`)?.classList.add('hidden');

  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');

  if (!Estado.imagenesPorProducto[id]) {
    panel.innerHTML = '<p class="muted">Cargando fotos...</p>';
    try {
      const detalle = await Api.productos.obtener(id);
      Estado.imagenesPorProducto[id] = (detalle.imagenes || []).slice().sort((a, b) => a.orden - b.orden);
    } catch {
      Estado.imagenesPorProducto[id] = [];
    }
  }
  panel.innerHTML = renderGestorFotosHTML(id, Estado.imagenesPorProducto[id]);
}

// ---- Unidades de venta alternativas — editable en cualquier estado del producto (borrador,
// en_transito, disponible), a diferencia de categoría/precio que solo se editan en borrador. Mismo
// patrón de panel-expandible + carga perezosa + caché que Fotos/Reseñas arriba, pero reusable
// tanto en la tarjeta de publicados (que ya tiene ese mecanismo) como en la de borradores (que
// hoy no tiene ninguno, se agrega acá por primera vez). ----

function renderUnidadAltFila(u, unidadBase, modoEdicion = false) {
  if (modoEdicion) {
    return `
      <div class="unidades-alt-fila unidades-alt-fila-edicion" data-unidad-id="${u.id}">
        <div class="unidades-alt-info">
          <strong>${escapeAttr(infoUnidad(u.unidad).label)}</strong>
          <label>Precio (S/)
            <input type="number" class="unidad-alt-editar-precio" min="0.01" step="0.01" value="${u.precio}">
          </label>
          <label>Factor (1 ${unidadCorta(u.unidad)} = X ${unidadPlural(unidadBase, 2)})
            <input type="number" class="unidad-alt-editar-factor" min="0.0001" step="0.0001" value="${u.factor_a_base}">
          </label>
        </div>
        <div class="unidades-alt-acciones">
          <button type="button" class="btn btn-primary btn-sm btn-guardar-unidad-alt" data-unidad-id="${u.id}">Guardar</button>
          <button type="button" class="btn btn-outline btn-sm btn-cancelar-edicion-unidad-alt">Cancelar</button>
        </div>
      </div>`;
  }
  return `
    <div class="unidades-alt-fila" data-unidad-id="${u.id}">
      <div class="unidades-alt-info">
        <strong>${escapeAttr(infoUnidad(u.unidad).label)}</strong>
        <span>${formatearMoneda(u.precio)} / ${unidadCorta(u.unidad)}</span>
        <span class="muted">1 ${unidadCorta(u.unidad)} = ${Number(u.factor_a_base)} ${unidadPlural(unidadBase, Number(u.factor_a_base))}</span>
      </div>
      <div class="unidades-alt-acciones">
        <button type="button" class="btn btn-outline btn-sm btn-editar-unidad-alt" data-unidad-id="${u.id}" aria-label="Editar"><i class="ti ti-pencil"></i></button>
        <button type="button" class="btn btn-danger btn-sm btn-eliminar-unidad-alt" data-unidad-id="${u.id}" aria-label="Eliminar"><i class="ti ti-trash"></i></button>
      </div>
    </div>`;
}

function renderUnidadesAlternativasHTML(producto, unidades) {
  const unidadBase = producto.unidad_medida;
  // Excluye del selector la unidad base y las que ya están agregadas — no tiene sentido
  // ofrecerlas de nuevo (la base ya sale 400 del backend, las repetidas también).
  const opciones = opcionesUnidadMedidaHtml([unidadBase, ...unidades.map((u) => u.unidad)]);

  return `
    <div class="unidades-alt-gestor" data-producto-id="${escapeAttr(producto.id)}" data-unidad-base="${escapeAttr(unidadBase)}">
      <div class="unidades-alt-lista">
        ${unidades.length
          ? unidades.map((u) => renderUnidadAltFila(u, unidadBase)).join('')
          : '<p class="muted">Todavía no agregaste unidades de venta adicionales.</p>'}
      </div>
      ${opciones ? `
      <form class="unidad-alt-form form-grid">
        <label>Nueva unidad
          <select class="unidad-alt-nueva-select">${opciones}</select>
        </label>
        <label>Precio (S/)
          <input type="number" class="unidad-alt-nuevo-precio" min="0.01" step="0.01" required placeholder="0.00">
        </label>
        <label>Factor de conversión
          <input type="number" class="unidad-alt-nuevo-factor" min="0.0001" step="0.0001" required placeholder="Ej. 10">
        </label>
        <p class="unidad-alt-factor-ayuda muted"></p>
        <button type="submit" class="btn btn-outline btn-sm">Agregar unidad</button>
      </form>` : '<p class="muted">Ya agregaste todas las unidades de medida disponibles para este producto.</p>'}
    </div>`;
}

// "¿Cuántos {unidad base} equivalen a 1 {unidad elegida}?" — se recalcula cada vez que el
// productor cambia la unidad en el <select>, para que el factor nunca se sienta un número
// abstracto.
function actualizarAyudaFactorUnidadAlt(form) {
  const ayuda = form?.querySelector('.unidad-alt-factor-ayuda');
  const unidadBase = form?.closest('.unidades-alt-gestor')?.dataset.unidadBase;
  const nuevaUnidad = form?.querySelector('.unidad-alt-nueva-select')?.value;
  if (!ayuda || !unidadBase || !nuevaUnidad) return;
  ayuda.textContent = `¿Cuántos ${unidadPlural(unidadBase, 2)} equivalen a 1 ${unidadCorta(nuevaUnidad)}?`;
}

function renderGestorUnidadesAlt(productoId) {
  const producto = (Estado.misProductos || []).find((p) => p.id === productoId);
  if (!producto) return '<p class="muted">No se pudo cargar la información del producto.</p>';
  return renderUnidadesAlternativasHTML(producto, Estado.unidadesAlternativasPorProducto[productoId] || []);
}

// Reemplaza solo el gestor (lista + form), no todo el panel ni la tarjeta — mismo criterio que
// rerenderGestorFotos.
function rerenderUnidadesAlternativas(productoId) {
  document.querySelectorAll(`.unidades-alt-gestor[data-producto-id="${productoId}"]`).forEach((el) => {
    el.outerHTML = renderGestorUnidadesAlt(productoId);
    actualizarAyudaFactorUnidadAlt(document.querySelector(`.unidades-alt-gestor[data-producto-id="${productoId}"] .unidad-alt-form`));
  });
}

async function alternarUnidadesProductor(id) {
  const panel = document.querySelector(`.panel-expandible[data-panel="unidades"][data-id="${id}"]`);
  if (!panel) return;
  // Cierra cualquier otro panel-expandible hermano dentro de la misma tarjeta — mismo acordeón
  // que ya usan Reseñas/Fotos, generalizado para funcionar también en la tarjeta de borradores
  // (que hoy no tiene ningún otro panel, pero por si el día de mañana lo tiene).
  panel.closest('.product-card, .pedido-card')?.querySelectorAll('.panel-expandible').forEach((p) => {
    if (p !== panel) p.classList.add('hidden');
  });

  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');

  if (!Estado.unidadesAlternativasPorProducto[id]) {
    panel.innerHTML = '<p class="muted">Cargando unidades de venta...</p>';
    try {
      Estado.unidadesAlternativasPorProducto[id] = await Api.productos.unidades.listar(id);
    } catch {
      Estado.unidadesAlternativasPorProducto[id] = [];
    }
  }
  panel.innerHTML = renderGestorUnidadesAlt(id);
  actualizarAyudaFactorUnidadAlt(panel.querySelector('.unidad-alt-form'));
}

async function agregarUnidadAlternativa(productoId, form) {
  const unidad = form.querySelector('.unidad-alt-nueva-select').value;
  const precio = parseFloat(form.querySelector('.unidad-alt-nuevo-precio').value);
  const factor_a_base = parseFloat(form.querySelector('.unidad-alt-nuevo-factor').value);
  const btn = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const nueva = await Api.productos.unidades.crear(productoId, { unidad, precio, factor_a_base });
    Estado.unidadesAlternativasPorProducto[productoId] = [...(Estado.unidadesAlternativasPorProducto[productoId] || []), nueva];
    rerenderUnidadesAlternativas(productoId);
    toast('Unidad de venta agregada');
  } catch (err) {
    manejarError(err, 'agregar la unidad de venta');
    btn.disabled = false;
  }
}

function iniciarEdicionUnidadAlternativa(productoId, unidadId) {
  const unidad = (Estado.unidadesAlternativasPorProducto[productoId] || []).find((u) => u.id === unidadId);
  const fila = document.querySelector(`.unidades-alt-fila[data-unidad-id="${unidadId}"]`);
  const producto = (Estado.misProductos || []).find((p) => p.id === productoId);
  if (!unidad || !fila || !producto) return;
  fila.outerHTML = renderUnidadAltFila(unidad, producto.unidad_medida, true);
}

async function guardarEdicionUnidadAlternativa(productoId, unidadId) {
  const fila = document.querySelector(`.unidades-alt-fila[data-unidad-id="${unidadId}"]`);
  if (!fila) return;
  const precio = parseFloat(fila.querySelector('.unidad-alt-editar-precio').value);
  const factor_a_base = parseFloat(fila.querySelector('.unidad-alt-editar-factor').value);
  fila.querySelectorAll('button, input').forEach((el) => { el.disabled = true; });
  try {
    const actualizada = await Api.productos.unidades.editar(unidadId, { precio, factor_a_base });
    const unidades = Estado.unidadesAlternativasPorProducto[productoId] || [];
    Estado.unidadesAlternativasPorProducto[productoId] = unidades.map((u) => (u.id === unidadId ? actualizada : u));
    rerenderUnidadesAlternativas(productoId);
    toast('Unidad de venta actualizada');
  } catch (err) {
    manejarError(err, 'actualizar la unidad de venta');
    fila.querySelectorAll('button, input').forEach((el) => { el.disabled = false; });
  }
}

async function eliminarUnidadAlternativa(productoId, unidadId) {
  const fila = document.querySelector(`.unidades-alt-fila[data-unidad-id="${unidadId}"]`);
  fila?.querySelectorAll('button').forEach((el) => { el.disabled = true; });
  try {
    await Api.productos.unidades.eliminar(unidadId);
    const unidades = Estado.unidadesAlternativasPorProducto[productoId] || [];
    Estado.unidadesAlternativasPorProducto[productoId] = unidades.filter((u) => u.id !== unidadId);
    rerenderUnidadesAlternativas(productoId);
    toast('Unidad de venta eliminada');
  } catch (err) {
    manejarError(err, 'eliminar la unidad de venta');
    fila?.querySelectorAll('button').forEach((el) => { el.disabled = false; });
  }
}

// ---- Borradores (nacen de una cosecha aprobada por el Verificador — ver
// crear_producto_desde_cosecha en productos/main.py) ----
function renderTarjetaBorrador(p, indice, imagenes) {
  const retraso = Math.min(indice, 12) * 35;
  const puedePublicar = p.precio != null && Number(p.precio) > 0 && imagenes.length > 0;
  // Progreso puramente visual (categoría / precio / al menos 1 foto = 3 pasos) — no es un campo
  // del backend, solo ayuda a priorizar qué borrador completar primero en la vista dedicada.
  const pasosCompletos = (p.categoria ? 1 : 0) + (p.precio != null && Number(p.precio) > 0 ? 1 : 0) + (imagenes.length > 0 ? 1 : 0);
  const progreso = Math.round((pasosCompletos / 3) * 100);
  return `
    <div class="pedido-card borrador-card" style="animation-delay:${retraso}ms" data-producto-id="${escapeAttr(p.id)}">
      <div class="borrador-card-header">
        <h4>${escapeAttr(p.nombre)} <span class="pill pill-estado pill-pendiente"><i class="ti ti-pencil"></i> Borrador</span></h4>
        <div class="borrador-progreso-wrap">${progreso}% completo <div class="borrador-progreso-barra"><div class="borrador-progreso-fill" style="width:${progreso}%"></div></div></div>
      </div>
      <div class="borrador-card-body">
      <p class="muted">${Number(p.stock)} ${escapeAttr(p.unidad_medida)} de tu cosecha aprobada — completa lo que falta para publicarlo.</p>

      <div class="publicar-layout">
        <div class="form-grid">
          <label>Categoría
            <select class="borrador-categoria">${opcionesCategoriaHTML(p.categoria)}</select>
          </label>
          <label>Precio (S/)
            <input type="number" class="borrador-precio" min="0" step="0.01" placeholder="0.00" value="${p.precio != null ? p.precio : ''}">
          </label>
        </div>
        <div class="publicar-preview">
          <span class="publicar-preview-label"><i class="ti ti-eye"></i> Vista previa</span>
          <div class="borrador-preview-card"></div>
        </div>
      </div>

      <div class="fotos-upload-bloque">
        <div class="fotos-upload-header">
          <h4><i class="ti ti-camera"></i> Fotos del producto</h4>
        </div>
        <p class="muted">La primera foto será la principal en el catálogo. Formatos: JPG, PNG o WEBP.</p>
        <div class="gestor-fotos-borrador">${renderGestorFotosHTML(p.id, imagenes)}</div>
      </div>

      <div class="fotos-upload-bloque">
        <div class="fotos-upload-header">
          <h4><i class="ti ti-scale"></i> Unidades de venta adicionales</h4>
          <button type="button" class="btn btn-outline btn-sm btn-toggle-unidades" data-id="${p.id}">Ver / editar</button>
        </div>
        <p class="muted">Editable en cualquier momento, publicado o no.</p>
        <div class="panel-expandible hidden" data-panel="unidades" data-id="${p.id}"></div>
      </div>

      <button type="button" class="btn btn-primary btn-block btn-publicar-borrador" data-producto-id="${escapeAttr(p.id)}" ${puedePublicar ? '' : 'disabled'}>Publicar</button>
      </div>
    </div>`;
}

// Recalcula la vista previa de un borrador con lo que hay ahora mismo en pantalla (categoría,
// precio, primera foto ya subida) — mismo componente que usaba el formulario viejo
// (renderTarjetaProductoCatalogo), solo que ahora lee de un borrador real en vez de inputs vacíos.
function actualizarPreviewBorrador(productoId) {
  const card = document.querySelector(`.pedido-card[data-producto-id="${productoId}"]`);
  const cont = card?.querySelector('.borrador-preview-card');
  const producto = (Estado.misProductos || []).find((p) => p.id === productoId);
  if (!cont || !producto) return;

  const categoria = card.querySelector('.borrador-categoria')?.value || producto.categoria;
  const precio = parseFloat(card.querySelector('.borrador-precio')?.value);
  const imagenes = Estado.imagenesPorProducto[productoId] || [];
  const primeraImagen = [...imagenes].sort((a, b) => a.orden - b.orden)[0]?.url;

  cont.innerHTML = renderTarjetaProductoCatalogo({
    ...producto,
    categoria,
    precio: Number.isFinite(precio) ? precio : 0,
    imagen_principal: primeraImagen || producto.imagen_principal,
  }, { clickable: false, indice: 0 });
}

// Habilita "Publicar" solo cuando precio > 0 y hay al menos 1 foto — ayuda visual, el backend
// igual valida esto mismo en PATCH /productos/{id}/publicar.
function sincronizarBotonPublicarBorrador(productoId) {
  const btn = document.querySelector(`.btn-publicar-borrador[data-producto-id="${productoId}"]`);
  if (!btn) return; // no es (o ya no es) un borrador visible en pantalla
  const card = btn.closest('.pedido-card');
  const precio = parseFloat(card?.querySelector('.borrador-precio')?.value);
  const categoria = card?.querySelector('.borrador-categoria')?.value;
  const tieneImagen = (Estado.imagenesPorProducto[productoId] || []).length > 0;
  btn.disabled = !(Number.isFinite(precio) && precio > 0 && tieneImagen);

  // Barra de progreso visual del header (mismos 3 pasos que al pintar la tarjeta) —
  // se recalcula en vivo mientras el productor completa el borrador.
  const pasos = (categoria ? 1 : 0) + (Number.isFinite(precio) && precio > 0 ? 1 : 0) + (tieneImagen ? 1 : 0);
  const progreso = Math.round((pasos / 3) * 100);
  const wrap = card?.querySelector('.borrador-progreso-wrap');
  if (wrap) {
    wrap.firstChild.textContent = `${progreso}% completo `;
    wrap.querySelector('.borrador-progreso-fill').style.width = `${progreso}%`;
  }
}

async function publicarBorrador(productoId) {
  const card = document.querySelector(`.pedido-card[data-producto-id="${productoId}"]`);
  if (!card) return;
  const categoria = card.querySelector('.borrador-categoria').value || null;
  const precio = parseFloat(card.querySelector('.borrador-precio').value);
  card.querySelectorAll('button, select, input').forEach((el) => { el.disabled = true; });
  try {
    await Api.productos.actualizar(productoId, { categoria, precio: Number.isFinite(precio) ? precio : null });
    await Api.productos.publicar(productoId);
    toast('Producto publicado — ya está visible en el catálogo 🎉');
    // Recarga la sección completa (no toda la vista) — mismo criterio que cargarInvitaciones()
    // en Panel Admin: más simple y correcto que mover la tarjeta a mano, porque "publicados"
    // necesita datos que este borrador no tenía (imagen_principal calculada del lado del
    // backend, por ejemplo).
    cargarMisProductos();
  } catch (err) {
    manejarError(err, 'publicar el producto');
    card.querySelectorAll('button, select, input').forEach((el) => { el.disabled = false; });
  }
}

async function cargarMisProductos() {
  const contBorradores = document.getElementById('productor-borradores-lista');
  const vacioBorradores = document.getElementById('productor-borradores-empty');
  const grid = document.getElementById('mis-productos-grid');
  const vacio = document.getElementById('mis-productos-empty');
  vacioBorradores.classList.add('hidden');
  vacio.classList.add('hidden');
  contBorradores.innerHTML = renderSkeletonFilas(1);
  grid.innerHTML = renderSkeletonProductos(3);
  try {
    const productos = await Api.productos.mios();
    Estado.misProductos = productos;
    const borradores = productos.filter((p) => p.estado === 'borrador');
    // TODO(sub-entrega de UI de reservas): agrupar "en_transito" junto con "disponible" acá es
    // temporal — el ciclo real ahora tiene 3 estados, y esta sección debería separarse en
    // Borradores / En tránsito / Disponibles (con su propia UI de reservas). Por ahora, con tal
    // de no romper el filtro silenciosamente, "publicados" sigue siendo todo lo que no es
    // borrador.
    const publicados = productos.filter((p) => p.estado !== 'borrador');

    if (!borradores.length) {
      contBorradores.innerHTML = '';
      vacioBorradores.classList.remove('hidden');
    } else {
      // GET /productos/mios trae imagen_principal (una sola url) pero no la lista completa con
      // id/orden que necesita el gestor de fotos para eliminar/reordenar — eso solo lo trae el
      // detalle de cada producto (GET /productos/{id}), igual que ya usa alternarFotosProductor.
      const detalles = await Promise.all(
        borradores.map((p) => Api.productos.obtener(p.id).catch(() => null))
      );
      borradores.forEach((p, i) => {
        Estado.imagenesPorProducto[p.id] = detalles[i]?.imagenes || [];
      });
      contBorradores.innerHTML = borradores.map((p, i) => renderTarjetaBorrador(p, i, Estado.imagenesPorProducto[p.id])).join('');
      borradores.forEach((p) => actualizarPreviewBorrador(p.id));
    }

    if (!publicados.length) {
      grid.innerHTML = '';
      vacio.classList.remove('hidden');
    } else {
      grid.innerHTML = publicados.map((p, i) => renderTarjetaProductorProducto(p, i)).join('');
    }
  } catch (err) {
    manejarError(err, 'cargar tus productos');
  }
}

// ---- Mis chacras (panel-productor) — mismo patrón de mapa seleccionable que el perfil de
// productor (inicializarMapaUbicacionProductor). Cada RegistroProduccion exige un chacra_id, así
// que esta sección es un prerrequisito real de "Registrar nueva siembra" en Mi Perfil. ----
let mapaUbicacionChacra = null;
let ubicacionChacraSeleccionada = null; // { lat, lng }

function inicializarMapaUbicacionChacra() {
  if (mapaUbicacionChacra) {
    try { mapaUbicacionChacra.mapa.remove(); } catch { /* ya estaba destruido */ }
    mapaUbicacionChacra = null;
  }
  ubicacionChacraSeleccionada = null;
  const ayuda = document.getElementById('mapa-chacra-ayuda');
  if (ayuda) {
    ayuda.textContent = 'Toca el mapa para marcar dónde está la chacra.';
    ayuda.classList.remove('confirmado');
  }

  mapaUbicacionChacra = crearMapaSeleccionable('mapa-ubicacion-chacra', {
    emoji: '<i class="ti ti-map-pin"></i>',
    color: '#2d6a4f',
    onSeleccionar: (lat, lng) => {
      ubicacionChacraSeleccionada = { lat, lng };
      actualizarAyudaUbicacionChacra();
    },
  });
}

function actualizarAyudaUbicacionChacra() {
  const ayuda = document.getElementById('mapa-chacra-ayuda');
  if (!ayuda || !ubicacionChacraSeleccionada) return;
  ayuda.innerHTML = `<i class="ti ti-map-pin"></i> Ubicación marcada (${ubicacionChacraSeleccionada.lat.toFixed(5)}, ${ubicacionChacraSeleccionada.lng.toFixed(5)})`;
  ayuda.classList.add('confirmado');
}

async function usarMiUbicacionChacra() {
  const btn = document.getElementById('btn-ubicacion-chacra');
  btn.disabled = true;
  btn.textContent = 'Obteniendo ubicación...';
  try {
    const { lat, lng } = await obtenerUbicacionActual();
    mapaUbicacionChacra?.moverMarcador(lat, lng);
    ubicacionChacraSeleccionada = { lat, lng };
    actualizarAyudaUbicacionChacra();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Usar mi ubicación';
  }
}

async function crearChacra(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  if (!ubicacionChacraSeleccionada) {
    toast('Marca la ubicación de la chacra en el mapa', 'error');
    return;
  }
  btn.disabled = true;
  try {
    const datos = {
      codigo: document.getElementById('chacra-codigo').value.trim(),
      nombre: document.getElementById('chacra-nombre').value.trim() || null,
      ubicacion_latitud: ubicacionChacraSeleccionada.lat,
      ubicacion_longitud: ubicacionChacraSeleccionada.lng,
    };
    await Api.chacras.crear(datos);
    toast('Chacra registrada 🌱');
    document.getElementById('form-chacra').reset();
    inicializarMapaUbicacionChacra();
    await cargarMisChacras();
    pintarResumenChacras();
  } catch (err) {
    manejarError(err, 'registrar la chacra');
  } finally {
    btn.disabled = false;
  }
}

function renderTarjetaChacra(c, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  return `
    <div class="chacra-card" style="animation-delay:${retraso}ms">
      <div class="chacra-card-banner">
        <span class="chacra-codigo-badge">${escapeAttr(c.codigo)}</span>
        <span class="chacra-siembras" data-chacra-siembras="${c.id}"></span>
      </div>
      <div class="chacra-card-body">
        <h4>${c.nombre ? escapeAttr(c.nombre) : escapeAttr(c.codigo)}</h4>
        <div class="chacra-card-cultivos" data-chacra-cultivos="${c.id}"></div>
        <div id="mapa-chacra-mini-${c.id}" class="chacra-card-mapa-mini"></div>
        <div class="chacra-card-ubicacion"><i class="ti ti-map-pin"></i> ${Number(c.ubicacion_latitud).toFixed(5)}, ${Number(c.ubicacion_longitud).toFixed(5)}</div>
      </div>
    </div>`;
}

// Cuántas siembras y qué cultivos tiene cada chacra. Las chacras y los registros se cargan en
// paralelo, así que esto se llama cuando ya están las dos cosas (tras el Promise.all de
// iniciarPanelProductor y tras registrar una chacra nueva); antes de eso los elementos quedan vacíos.
function pintarResumenChacras() {
  const registros = Estado.registrosProduccion || [];
  (Estado.chacras || []).forEach((c) => {
    const propias = registros.filter((r) => r.chacra_id === c.id);
    const cultivos = [...new Set(propias.map((r) => r.cultivo))];
    const id = CSS.escape(String(c.id));
    const contador = document.querySelector(`[data-chacra-siembras="${id}"]`);
    const lista = document.querySelector(`[data-chacra-cultivos="${id}"]`);
    if (contador) contador.textContent = `${propias.length} ${propias.length === 1 ? 'siembra' : 'siembras'}`;
    if (lista) {
      lista.innerHTML = cultivos.length
        ? cultivos.map((nombre) => `<span class="chacra-cultivo-chip">${escapeAttr(nombre)}</span>`).join('')
        : '<span class="muted">Sin siembras todavía</span>';
    }
  });
}

// Los mapas de "Mis Chacras" se crean con la pestaña oculta (tamaño 0) y Leaflet no se entera de que
// después se muestra: hay que pedirle que recalcule su tamaño cada vez que se activa la pestaña.
let mapasChacras = [];

async function cargarMisChacras() {
  const cont = document.getElementById('chacras-lista');
  const vacio = document.getElementById('chacras-empty');
  vacio.classList.add('hidden');
  cont.innerHTML = renderSkeletonFilas(2);
  try {
    const chacras = await Api.chacras.misChacras();
    Estado.chacras = chacras;
    if (!chacras.length) {
      cont.innerHTML = '';
      vacio.classList.remove('hidden');
      return;
    }
    cont.innerHTML = chacras.map(renderTarjetaChacra).join('');
    // Mini-mapa de solo lectura por chacra (mismo helper que ya usa Mi Perfil para
    // #mapa-perfil-productor) — se inicializa después de pintar el HTML porque Leaflet
    // necesita que el contenedor ya exista en el DOM.
    mapasChacras.forEach((m) => { try { m.remove(); } catch { /* ya estaba destruido */ } });
    mapasChacras = [];
    chacras.forEach((c) => {
      if (hayCoordenadas(c.ubicacion_latitud, c.ubicacion_longitud)) {
        const mini = crearMapaSoloLectura(`mapa-chacra-mini-${c.id}`, c.ubicacion_latitud, c.ubicacion_longitud, '<i class="ti ti-map-pin"></i>', '#2d6a4f', escapeAttr(c.nombre || c.codigo));
        if (mini) mapasChacras.push(mini);
      }
    });
  } catch (err) {
    manejarError(err, 'cargar tus chacras');
  }
}

// No pinta nada por sí sola — solo alimenta Estado.registrosProduccion, que consume el resumen
// del panel (renderResumenProductor). "Mis siembras" en sí se ve y se gestiona en Mi Perfil
// (cargarPerfil), que también deja los mismos datos en Estado.registrosProduccion al visitarla.
async function cargarRegistrosProduccionProductor() {
  try {
    Estado.registrosProduccion = await Api.productores.produccion.listarMe();
  } catch (err) {
    manejarError(err, 'cargar tus siembras');
    Estado.registrosProduccion = [];
  }
}

// ---- Resumen / dashboard de "Panel del Productor" — lee de Estado.productos (ya filtrado por
// productor_id, cargado por cargarMisProductos), Estado.chacras y Estado.registrosProduccion. ----
function renderResumenProductor() {
  // Estado.misProductos (Api.productos.mios()) trae borradores Y publicados — a diferencia de
  // Estado.productos (el catálogo público, solo lo publicado por TODOS los productores), que ya
  // no hace falta filtrar por productor_id acá.
  // Solo "disponible" cuenta acá — "en_transito" ya se publicó pero todavía no es una venta
  // real (solo reservable), contarlo como "disponible" sería engañoso para el productor.
  const misProductosDisponibles = (Estado.misProductos || []).filter((p) => p.estado === 'disponible');
  const chacras = Estado.chacras || [];
  const registros = Estado.registrosProduccion || [];
  // Máquina de estados de un registro (ver productores/models.py): planificado ->
  // pendiente_verificacion -> aprobado | rechazado. "En curso" es todo lo que aún no terminó
  // (aprobado ya está cosechado y verificado; "cosechado" es un valor legado equivalente).
  const enCurso = registros.filter((r) => r.estado !== 'aprobado' && r.estado !== 'cosechado');

  // La próxima cosecha solo puede ser de una siembra que todavía no se cosechó.
  const proximaCosecha = registros
    .filter((r) => r.estado === 'planificado' && r.fecha_cosecha_estimada)
    .sort((a, b) => new Date(a.fecha_cosecha_estimada) - new Date(b.fecha_cosecha_estimada))[0];

  const entregasDirectas = Estado.entregasDirectasPendientes || [];

  return `
    <div class="card-panel resumen-panel">
      <div class="resumen-stats">
        <div class="resumen-stat resumen-stat-destacado">
          <span class="resumen-stat-icono"><i class="ti ti-seedling"></i></span>
          <div>
            <span class="resumen-stat-valor">${enCurso.length}</span>
            <span class="resumen-stat-label">Cosecha${enCurso.length === 1 ? '' : 's'} en curso</span>
          </div>
        </div>
        <div class="resumen-stat">
          <span class="resumen-stat-icono"><i class="ti ti-map-pin"></i></span>
          <div>
            <span class="resumen-stat-valor">${chacras.length}</span>
            <span class="resumen-stat-label">Chacra${chacras.length === 1 ? '' : 's'}</span>
          </div>
        </div>
        <div class="resumen-stat">
          <span class="resumen-stat-icono"><i class="ti ti-shopping-bag"></i></span>
          <div>
            <span class="resumen-stat-valor">${misProductosDisponibles.length}</span>
            <span class="resumen-stat-label">${misProductosDisponibles.length === 1 ? 'Producto disponible' : 'Productos disponibles'}</span>
          </div>
        </div>
      </div>
      ${proximaCosecha ? `
      <div class="resumen-destacado">
        <i class="ti ti-calendar-event"></i>
        Próxima cosecha estimada: <strong>${escapeAttr(proximaCosecha.cultivo)}</strong> el ${formatearFechaCorta(proximaCosecha.fecha_cosecha_estimada)}
      </div>` : ''}
      ${entregasDirectas.length ? `
      <div class="resumen-destacado resumen-destacado-urgente resumen-destacado-clicable" id="btn-resumen-entregas-directas">
        <i class="ti ti-truck-delivery"></i>
        Tienes ${entregasDirectas.length} entrega${entregasDirectas.length === 1 ? '' : 's'} directa${entregasDirectas.length === 1 ? '' : 's'} pendiente${entregasDirectas.length === 1 ? '' : 's'} — ningún repartidor tuvo capacidad suficiente
      </div>` : ''}
      <div class="resumen-acciones">
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-publicar"><i class="ti ti-pencil"></i> Completar borradores</button>
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-siembra"><i class="ti ti-plant-2"></i> Registrar siembra</button>
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-chacra"><i class="ti ti-map-pin"></i> Registrar chacra</button>
      </div>
    </div>`;
}

// ---- Entregas directas pendientes (caso poco frecuente: ningún repartidor con capacidad
// suficiente — ver requiere_vehiculo_grande/pendiente_entrega_directa en pedidos/transporte) ----

function renderTarjetaEntregaDirecta(envio, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  // Sin datos del pedido (destino, items): Envio no los trae y pedirlos de a uno por tarjeta
  // sería una llamada extra por cada envío listado — lo único disponible sin costo es su propia
  // fecha_creacion y el pedido_id, que alcanza como referencia para que el productor identifique
  // cuál es cuál entre sus propios pedidos.
  return `
    <div class="pedido-card" style="animation-delay:${retraso}ms" data-envio-id="${escapeAttr(envio.id)}">
      <div class="pedido-card-header">
        <h4>Pedido #${escapeAttr(String(envio.pedido_id).slice(0, 8))}</h4>
      </div>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Envío creado</span><span class="perfil-dato-valor">${formatearFechaServidor(envio.fecha_creacion)}</span></div>
      </div>
      <p class="muted">No se encontró un repartidor con capacidad suficiente para este pedido — te toca entregarlo directamente.</p>
      <div class="verificador-cosecha-acciones">
        <button type="button" class="btn btn-primary btn-sm btn-marcar-entregado" data-envio-id="${escapeAttr(envio.id)}"><i class="ti ti-check"></i> Marcar como entregado</button>
      </div>
    </div>`;
}

async function cargarEntregasDirectasPendientes() {
  const seccion = document.getElementById('entregas-directas-section');
  const cont = document.getElementById('entregas-directas-lista');
  try {
    const envios = await Api.transporte.pendientesEntregaDirecta();
    Estado.entregasDirectasPendientes = envios;
    if (!envios.length) {
      seccion.classList.add('hidden');
      cont.innerHTML = '';
      return;
    }
    seccion.classList.remove('hidden');
    cont.innerHTML = envios.map(renderTarjetaEntregaDirecta).join('');
  } catch {
    // Silencioso a propósito, mismo criterio que Api.transporte.propuestas() en
    // cargarListaEnvios(): es una sección discreta y poco frecuente — un error acá no debería
    // interrumpir el resto del panel del productor (borradores, productos, chacras siguen
    // cargando igual).
    Estado.entregasDirectasPendientes = [];
    seccion.classList.add('hidden');
  }
}

// Saca la tarjeta ya entregada de la lista sin recargar toda la vista — mismo criterio que
// quitarTarjetaCosechaPendiente() en Panel Verificador.
function quitarTarjetaEntregaDirecta(envioId) {
  document.querySelector(`.pedido-card[data-envio-id="${envioId}"]`)?.remove();
  Estado.entregasDirectasPendientes = Estado.entregasDirectasPendientes.filter((e) => String(e.id) !== String(envioId));
  const cont = document.getElementById('entregas-directas-lista');
  if (cont && !cont.children.length) {
    document.getElementById('entregas-directas-section')?.classList.add('hidden');
  }
}

async function marcarEntregaDirectaComoEntregada(envioId) {
  const card = document.querySelector(`.pedido-card[data-envio-id="${envioId}"]`);
  card?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    await Api.transporte.marcarEntregado(envioId);
    toast('¡Entrega confirmada! 🚚');
    quitarTarjetaEntregaDirecta(envioId);
  } catch (err) {
    manejarError(err, 'marcar la entrega como completada');
    card?.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}

// ============ GESTIÓN DE ENVÍOS (repartidor) ============
// icono queda como un punto de color dibujado en CSS (.disponibilidad-punto), no un ícono de
// Tabler: el set outline no trae una variante de círculo sólido/relleno, y un anillo hueco
// comunica mucho más débil el "semáforo de estado" que un punto de color lleno.
const DISPONIBILIDAD_INFO = {
  disponible: { texto: 'Disponible', clase: 'disponibilidad-verde', detalle: 'Puedes recibir nuevas propuestas de envío.' },
  ocupado: { texto: 'Ocupado', clase: 'disponibilidad-ambar', detalle: 'Estás atendiendo un envío en este momento.' },
  desconectado: { texto: 'Desconectado', clase: 'disponibilidad-gris', detalle: 'No recibirás nuevas propuestas hasta que te conectes.' },
};

function renderBannerDisponibilidad(estado, { propuestas = 0, activos = 0 } = {}) {
  const clave = (estado || 'desconectado').toLowerCase();
  let info = DISPONIBILIDAD_INFO[clave] || DISPONIBILIDAD_INFO.desconectado;
  // "Ocupado" también es lo que queda mientras hay una propuesta sin responder (el servidor reserva al
  // repartidor al proponérsela): decir "atendiendo un envío" en ese caso confundía.
  if (clave === 'ocupado' && activos === 0 && propuestas > 0) {
    info = { ...info, detalle: `Tienes ${propuestas === 1 ? 'una propuesta' : `${propuestas} propuestas`} de envío por responder: acéptala o recházala.` };
  }
  return `
    <div class="disponibilidad-banner ${info.clase}">
      <span class="disponibilidad-icono"><span class="disponibilidad-punto"></span></span>
      <div>
        <strong>${info.texto}</strong>
        <span>${info.detalle}</span>
      </div>
    </div>`;
}

async function cargarGestionEnvios() {
  if (!Estado.token || Estado.rol !== 'repartidor') {
    cambiarVista('inicio');
    return;
  }

  detenerSeguimientoRepartidor();
  detenerPollingRutas();

  const setup = document.getElementById('repartidor-setup');
  const panel = document.getElementById('repartidor-panel');

  let miRepartidor;
  try {
    miRepartidor = await Api.repartidores.miPerfil();
  } catch (err) {
    if (err.status === 404) {
      setup.classList.remove('hidden');
      panel.classList.add('hidden');
      return;
    }
    manejarError(err, 'cargar tu perfil de repartidor');
    return;
  }

  setup.classList.add('hidden');
  panel.classList.remove('hidden');
  Estado.repartidorId = miRepartidor.id;
  document.getElementById('disponibilidad-banner').innerHTML = renderBannerDisponibilidad(miRepartidor.estado_disponibilidad);
  document.getElementById('repartidor-resumen').innerHTML = renderSkeletonResumen(2);

  await cargarListaEnvios(miRepartidor.id);
  pintarResumenYDisponibilidadRepartidor(miRepartidor.estado_disponibilidad);
  iniciarSeguimientoRepartidor();
}

// Pinta el banner de disponibilidad, los contadores y el gráfico a partir de lo ya cargado (Estado.enviosRepartidor
// y Estado.propuestasRepartidor, que llena cargarListaEnvios).
function pintarResumenYDisponibilidadRepartidor(estadoDisponibilidad) {
  if (estadoDisponibilidad !== undefined) Estado.disponibilidadRepartidor = estadoDisponibilidad;
  const activos = (Estado.enviosRepartidor || []).filter((e) => ['asignado', 'en_camino'].includes(e.estado)).length;
  document.getElementById('disponibilidad-banner').innerHTML = renderBannerDisponibilidad(
    Estado.disponibilidadRepartidor,
    { propuestas: (Estado.propuestasRepartidor || []).length, activos },
  );
  document.getElementById('repartidor-resumen').innerHTML = renderResumenRepartidor();
  animarContadoresResumen(document.getElementById('repartidor-resumen'));
  dibujarGraficoEnviosRepartidor();
}

// Tras aceptar/rechazar una propuesta, cambiar el estado de un envío o confirmar una entrega hay que repintar
// TODO el panel: antes solo se recargaba la lista y los contadores, el gráfico y el banner de disponibilidad
// quedaban desactualizados ("0 pendientes" con un envío recién aceptado) hasta pulsar "Refrescar".
async function refrescarPanelRepartidor() {
  if (!Estado.repartidorId) return;
  let disponibilidad;
  try {
    disponibilidad = (await Api.repartidores.miPerfil()).estado_disponibilidad;
  } catch { /* el banner queda como estaba */ }
  await cargarListaEnvios(Estado.repartidorId);
  pintarResumenYDisponibilidadRepartidor(disponibilidad);
}

let chartEnviosRepartidor = null;

// Distribución de los envíos ya cargados por cargarListaEnvios() (Estado.enviosRepartidor) —
// no dispara ninguna llamada nueva al backend. El canvas vive fijo en index.html, así que hay
// que destruir la instancia anterior antes de redibujar (mismo motivo que en Panel Productor).
function dibujarGraficoEnviosRepartidor() {
  const canvas = document.getElementById('grafico-envios-estado');
  if (!canvas || typeof Chart === 'undefined') return;

  const envios = Estado.enviosRepartidor || [];
  const pendientes = envios.filter((e) => ['asignado', 'en_camino'].includes(e.estado)).length;
  const entregados = envios.filter((e) => e.estado === 'entregado').length;
  const otros = envios.filter((e) => ['cancelado', 'rechazado'].includes(e.estado)).length;

  chartEnviosRepartidor?.destroy();
  chartEnviosRepartidor = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: ['Pendientes', 'Entregados', 'Cancelados/Rechazados'],
      datasets: [{ data: [pendientes, entregados, otros], backgroundColor: ['#2D3B2A', '#82907A', '#A23B22'] }],
    },
    options: { plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } } },
  });
}

// ---- Resumen / dashboard de "Gestionar Envíos" — lee de Estado.enviosRepartidor, ya cargado
// por cargarListaEnvios() (no incluye las propuestas pendientes, que son una cosa aparte). ----
function renderResumenRepartidor() {
  const envios = Estado.enviosRepartidor || [];
  const pendientes = envios.filter((e) => ['asignado', 'en_camino'].includes(e.estado));
  const entregados = envios.filter((e) => e.estado === 'entregado');
  const enCamino = envios.filter((e) => e.estado === 'en_camino');

  return `
    <div class="card-panel resumen-panel">
      <div class="resumen-stats">
        <div class="resumen-stat resumen-stat-destacado">
          <span class="resumen-stat-icono"><i class="ti ti-truck-delivery"></i></span>
          <div>
            <span class="resumen-stat-valor">${pendientes.length}</span>
            <span class="resumen-stat-label">Pendiente${pendientes.length === 1 ? '' : 's'} de entrega</span>
          </div>
        </div>
        <div class="resumen-stat">
          <span class="resumen-stat-icono"><i class="ti ti-circle-check"></i></span>
          <div>
            <span class="resumen-stat-valor">${entregados.length}</span>
            <span class="resumen-stat-label">Entregado${entregados.length === 1 ? '' : 's'}</span>
          </div>
        </div>
      </div>
      ${enCamino.length ? `
      <div class="resumen-destacado resumen-destacado-urgente">
        <i class="ti ti-navigation"></i> Tienes ${enCamino.length} entrega${enCamino.length === 1 ? '' : 's'} en camino ahora
      </div>` : ''}
      <button type="button" class="btn btn-ghost btn-sm resumen-ver-mas" id="btn-resumen-ver-envios"><i class="ti ti-chevron-down"></i> Ver mis envíos</button>
    </div>`;
}

// ============ PANEL VERIFICADOR ============
// Scaffolding: guard de rol + "Mi cuenta" (nombre del verificador). Las secciones "Cosechas
// pendientes" e "Historial" son placeholders — su contenido va en entregas separadas.
// Mismo guard que iniciarPanelProductor / cargarGestionEnvios: sin token o rol != verificador
// -> se manda a inicio (no hay pantalla 403 propia en esta SPA, redirigir es el patrón).
async function iniciarPanelVerificador() {
  if (!Estado.token || Estado.rol !== 'verificador') {
    cambiarVista('inicio');
    return;
  }

  // Al entrar al panel siempre se muestra la primera sección (por si quedó otra activa de antes).
  seleccionarSeccionVerificador('cosechas');
  cargarCosechasPendientes();

  const nombreEl = document.getElementById('verificador-cuenta-nombre');
  const detalleEl = document.getElementById('verificador-cuenta-detalle');
  nombreEl.textContent = Estado.nombre || 'Mi cuenta';
  detalleEl.textContent = 'Cargando tu perfil de verificador…';

  try {
    const perfil = await Api.verificadores.miPerfil();
    nombreEl.textContent = perfil.nombre || Estado.nombre || 'Verificador';
    detalleEl.textContent = perfil.credencial
      ? `Credencial: ${perfil.credencial}`
      : 'Verificador de Chakra Shop';
  } catch (err) {
    // 404 = tiene el rol pero aún no creó su perfil de verificador (POST /verificadores).
    // El alta de ese perfil es una entrega aparte; acá solo se informa.
    detalleEl.textContent = err.status === 404
      ? 'Todavía no completaste tu perfil de verificador.'
      : 'No se pudo cargar tu perfil de verificador.';
  }
}

// Botón de sección (pestañas de los paneles) activo o no: la clase pinta el estado y aria-current se lo
// dice a los lectores de pantalla.
function marcarBotonActivo(boton, activo) {
  boton.classList.toggle('active', activo);
  if (activo) boton.setAttribute('aria-current', 'true'); else boton.removeAttribute('aria-current');
}

function seleccionarSeccionVerificador(seccion) {
  document.querySelectorAll('#view-panel-verificador .verificador-tab').forEach((t) => {
    marcarBotonActivo(t, t.dataset.vseccion === seccion);
  });
  document.querySelectorAll('#view-panel-verificador .verificador-seccion').forEach((s) => {
    s.classList.toggle('hidden', s.dataset.vseccion !== seccion);
  });
  // El historial se recarga cada vez que se entra a la pestaña (no solo la primera vez): una
  // aprobación/rechazo hecha en "Cosechas pendientes" en la misma sesión agrega una fila nueva.
  if (seccion === 'historial') cargarHistorialVerificacion();
}

// ---- Cosechas pendientes ----
function renderTarjetaCosechaPendiente(r, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  return `
    <div class="pedido-card" style="animation-delay:${retraso}ms" data-registro-id="${escapeAttr(r.id)}">
      <div class="pedido-card-header">
        <h4>${escapeAttr(r.cultivo)}</h4>
        ${r.fue_rechazado_antes ? '<span class="pill pill-estado pill-reenviado"><i class="ti ti-recycle"></i> Reenviado</span>' : ''}
      </div>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Cosechado</span><span class="perfil-dato-valor">${Number(r.cantidad_cosechada).toFixed(2)} ${escapeAttr(r.unidad_medida)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Productor</span><span class="perfil-dato-valor">${escapeAttr(r.productor_nombre || '—')}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Chacra</span><span class="perfil-dato-valor">${escapeAttr(r.chacra_codigo || '—')}${r.chacra_nombre ? ' — ' + escapeAttr(r.chacra_nombre) : ''}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Cosecha real</span><span class="perfil-dato-valor">${formatearFechaCorta(r.fecha_cosecha_real)}</span></div>
      </div>
      <div class="verificador-cosecha-acciones">
        <button type="button" class="btn btn-primary btn-sm btn-aprobar-cosecha" data-registro-id="${escapeAttr(r.id)}"><i class="ti ti-check"></i> Aprobar</button>
        <button type="button" class="btn btn-danger btn-sm btn-rechazar-cosecha" data-registro-id="${escapeAttr(r.id)}"><i class="ti ti-x"></i> Rechazar</button>
      </div>
    </div>`;
}

async function cargarCosechasPendientes() {
  const cont = document.getElementById('verificador-cosechas-lista');
  const vacio = document.getElementById('verificador-cosechas-empty');
  vacio.classList.add('hidden');
  cont.innerHTML = renderSkeletonFilas(2);
  try {
    const registros = await Api.productores.produccion.verificacion.pendientes();
    if (!registros.length) {
      cont.innerHTML = '';
      vacio.classList.remove('hidden');
      return;
    }
    cont.innerHTML = registros.map(renderTarjetaCosechaPendiente).join('');
  } catch (err) {
    manejarError(err, 'cargar las cosechas pendientes');
  }
}

// Saca la tarjeta ya resuelta de la lista sin recargar toda la vista — mismo criterio que
// cargarInvitaciones() en Panel Admin, pero acá alcanza con quitar un nodo del DOM (no hace
// falta volver a pedir la lista completa, la tarjeta resuelta ya no pertenece a "pendientes").
function quitarTarjetaCosechaPendiente(registroId) {
  document.querySelector(`.pedido-card[data-registro-id="${registroId}"]`)?.remove();
  const cont = document.getElementById('verificador-cosechas-lista');
  if (cont && !cont.children.length) {
    document.getElementById('verificador-cosechas-empty').classList.remove('hidden');
  }
}

async function aprobarCosecha(registroId) {
  const card = document.querySelector(`.pedido-card[data-registro-id="${registroId}"]`);
  card?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    const aprobada = await Api.productores.produccion.verificacion.aprobar(registroId);
    // certificacion_registrada: el backend deja en la cadena del producto los eventos
    // cosecha_registrada + certificado_productor (lo que ve quien escanea el QR). Si esa llamada
    // falló, la aprobación igual se completó — se avisa para que no pase desapercibido.
    if (aprobada?.certificacion_registrada === false) {
      toast('Cosecha aprobada, pero el certificado de trazabilidad no se pudo registrar todavía. Avisa a un administrador.', 'error');
    } else {
      toast('Cosecha aprobada y certificada — el producto ya está en el catálogo del productor, en borrador 🌱');
    }
    quitarTarjetaCosechaPendiente(registroId);
  } catch (err) {
    // Acá cae, entre otros, el 502 del circuit breaker si Productos no pudo crear el producto
    // (ver aprobar_cosecha en productores/main.py) — manejarError muestra err.message tal cual
    // vino del backend, y la tarjeta queda en la lista (no se llama a quitarTarjetaCosechaPendiente)
    // para que el Verificador pueda simplemente tocar "Aprobar" de nuevo.
    manejarError(err, 'aprobar la cosecha');
    card?.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}

let registroIdParaRechazar = null;

function abrirModalRechazarCosecha(registroId) {
  registroIdParaRechazar = registroId;
  document.getElementById('form-rechazar-cosecha').reset();
  abrirModal('modal-rechazar-cosecha');
}

async function confirmarRechazoCosecha(e) {
  e.preventDefault();
  const motivo = document.getElementById('rechazar-cosecha-motivo').value.trim();
  if (!motivo) {
    toast('Indica el motivo del rechazo', 'error');
    return;
  }
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    await Api.productores.produccion.verificacion.rechazar(registroIdParaRechazar, motivo);
    toast('Cosecha rechazada.');
    cerrarModal('modal-rechazar-cosecha');
    quitarTarjetaCosechaPendiente(registroIdParaRechazar);
    registroIdParaRechazar = null;
  } catch (err) {
    manejarError(err, 'rechazar la cosecha');
  } finally {
    btn.disabled = false;
  }
}

// ---- Historial ----
function renderTarjetaHistorialVerificacion(h, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  const aprobado = h.accion === 'aprobado';
  const badge = aprobado
    ? '<span class="pill pill-estado pill-vigente"><i class="ti ti-circle-check"></i> Aprobado</span>'
    : '<span class="pill pill-estado pill-expirada"><i class="ti ti-circle-x"></i> Rechazado</span>';
  return `
    <div class="pedido-card" style="animation-delay:${retraso}ms">
      <div class="pedido-card-header">
        <h4>${escapeAttr(h.cultivo || 'Cultivo no disponible')}</h4>
        ${badge}
      </div>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Productor</span><span class="perfil-dato-valor">${escapeAttr(h.productor_nombre || '—')}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Chacra</span><span class="perfil-dato-valor">${escapeAttr(h.chacra_codigo || '—')}${h.chacra_nombre ? ' — ' + escapeAttr(h.chacra_nombre) : ''}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Fecha</span><span class="perfil-dato-valor">${formatearFechaServidor(h.fecha)}</span></div>
      </div>
      ${!aprobado && h.motivo ? `<p class="verificador-historial-motivo"><i class="ti ti-message-circle"></i> ${escapeAttr(h.motivo)}</p>` : ''}
    </div>`;
}

async function cargarHistorialVerificacion() {
  const cont = document.getElementById('verificador-historial-lista');
  const vacio = document.getElementById('verificador-historial-empty');
  vacio.classList.add('hidden');
  cont.innerHTML = renderSkeletonFilas(2);
  try {
    const historial = await Api.productores.produccion.verificacion.historial();
    if (!historial.length) {
      cont.innerHTML = '';
      vacio.classList.remove('hidden');
      return;
    }
    cont.innerHTML = historial.map(renderTarjetaHistorialVerificacion).join('');
  } catch (err) {
    manejarError(err, 'cargar tu historial de verificación');
  }
}

// ============ PANEL ADMIN ============
// Generación y gestión de códigos de invitación. Mismo guard que el resto de paneles por rol.

// Roles restringidos que un admin puede invitar — agregar uno acá habilita la opción en el
// <select> del formulario (poblarSelectRolDestino) sin tocar el HTML ni el resto de esta lógica.
const ROLES_INVITABLES = [
  { value: 'verificador', label: 'Verificador' },
];

// Única fuente de verdad de "¿este rol necesita código de invitación?" — la usan tanto el
// formulario de registro (actualizarCampoCodigoInvitacion, más arriba) como este panel.
function rolRequiereInvitacion(rol) {
  return ROLES_INVITABLES.some((r) => r.value === rol);
}

function poblarSelectRolDestino() {
  const select = document.getElementById('invitacion-rol-destino');
  if (!select || select.dataset.poblado) return;
  select.innerHTML = ROLES_INVITABLES.map((r) => `<option value="${escapeAttr(r.value)}">${escapeAttr(r.label)}</option>`).join('');
  select.dataset.poblado = '1';
}

async function iniciarPanelAdmin() {
  if (!Estado.token || Estado.rol !== 'admin') {
    cambiarVista('inicio');
    return;
  }
  poblarSelectRolDestino();
  document.getElementById('invitacion-resultado').classList.add('hidden');
  document.getElementById('form-invitacion').reset();
  document.getElementById('admin-resumen').innerHTML = renderSkeletonResumen(3);
  activarTabAdmin('inicio');
  await Promise.all([cargarInvitaciones(), cargarProductosEnTransito(), cargarEnviosPendientesPorCapacidad(), cargarProductoresAdmin()]);
}

// ---- Productores (vista de solo lectura para el Admin) ----
// Fuente: GET /productores (público) + GET /productos (catálogo público) — este último solo para
// contar cuántos productos publicados tiene cada uno (el catálogo trae productor_id por producto).
// No hay endpoint de "productos por productor" para el Admin, y para una demo alcanza con agrupar
// acá (mismo criterio que cargarProductosEnTransito con las reservas).
let productoresAdmin = [];

function renderTarjetaProductorAdmin(p, totalProductos, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  const tel = String(p.contacto || '').replace(/\D/g, '');
  return `
    <div class="pedido-card productor-admin-card" style="animation-delay:${retraso}ms" data-productor-id="${escapeAttr(p.id)}">
      <div class="pedido-card-header">
        <h4>${escapeAttr(p.nombre)}</h4>
        <span class="pill pill-estado ${totalProductos ? 'pill-vigente' : 'pill-default'}"><i class="ti ti-shopping-bag"></i> ${totalProductos} producto${totalProductos === 1 ? '' : 's'}</span>
      </div>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Comunidad / Región</span><span class="perfil-dato-valor">${escapeAttr(p.comunidad || '—')}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Contacto</span><span class="perfil-dato-valor">${tel ? `<a class="productor-admin-tel" href="tel:${tel}">${escapeAttr(p.contacto)}</a>` : '—'}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Registrado</span><span class="perfil-dato-valor">${formatearFechaServidor(p.fecha_registro)}</span></div>
      </div>
      ${hayCoordenadas(p.latitud, p.longitud)
        ? `<div id="mapa-productor-admin-${escapeAttr(p.id)}" class="chacra-card-mapa-mini"></div>`
        : `<div class="mapa-bloque-neutro"><span class="icon"><i class="ti ti-map-pin"></i></span> Sin ubicación registrada.</div>`}
    </div>`;
}

function pintarProductoresAdmin() {
  const termino = (document.getElementById('admin-productores-buscar')?.value || '').trim().toLowerCase();
  const filtrados = productoresAdmin.filter((item) =>
    !termino || [item.p.nombre, item.p.comunidad, item.p.contacto].some((v) => String(v || '').toLowerCase().includes(termino)));
  const cont = document.getElementById('admin-productores-lista');
  document.getElementById('admin-productores-empty').classList.toggle('hidden', filtrados.length > 0);
  cont.innerHTML = filtrados.map((item, i) => renderTarjetaProductorAdmin(item.p, item.total, i)).join('');
  filtrados.forEach((item) => {
    const p = item.p;
    if (hayCoordenadas(p.latitud, p.longitud)) {
      crearMapaSoloLectura(`mapa-productor-admin-${p.id}`, p.latitud, p.longitud, '<i class="ti ti-tractor"></i>', '#2d6a4f', escapeAttr(p.nombre));
    }
  });
}

async function cargarProductoresAdmin() {
  const cont = document.getElementById('admin-productores-lista');
  cont.innerHTML = renderSkeletonFilas(2);
  try {
    const [productores, productos] = await Promise.all([
      Api.productores.listarAdmin(),
      Api.productos.listar().catch(() => []),
    ]);
    const totalPorProductor = {};
    productos.forEach((pr) => { totalPorProductor[pr.productor_id] = (totalPorProductor[pr.productor_id] || 0) + 1; });
    // Más productos publicados primero; a igualdad, alfabético.
    productoresAdmin = productores
      .map((p) => ({ p, total: totalPorProductor[p.id] || 0 }))
      .sort((a, b) => b.total - a.total || String(a.p.nombre).localeCompare(String(b.p.nombre), 'es'));
    const badge = document.getElementById('admin-productores-total');
    badge.textContent = productores.length;
    badge.classList.toggle('hidden', !productores.length);
    pintarProductoresAdmin();
  } catch (err) {
    cont.innerHTML = '';
    manejarError(err, 'cargar los productores');
  }
}

// Secciones dedicadas del Panel Admin (Inicio / Invitaciones / Pendientes por Capacidad /
// Productos en Tránsito) — mismo patrón que activarTabProductor.
function activarTabAdmin(nombreTab) {
  document.querySelectorAll('.admin-tab-btn').forEach((b) => marcarBotonActivo(b, b.dataset.admintab === nombreTab));
  document.querySelectorAll('.admin-tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.adminPanel === nombreTab));
}

// El backend solo manda `usado` (bool) y `fecha_expiracion` — "Expirada" no viaja ya resuelta
// desde /admin/invitaciones (a diferencia de /admin/invitaciones/validar/{codigo}, que sí la
// calcula), así que se deriva acá comparando contra la hora actual del navegador.
function estadoInvitacion(inv) {
  if (inv.usado) return { clave: 'usada', etiqueta: 'Usada' };
  if (parsearFechaServidor(inv.fecha_expiracion) < new Date()) return { clave: 'expirada', etiqueta: 'Expirada' };
  return { clave: 'vigente', etiqueta: 'Vigente' };
}

function renderInvitacionCard(inv) {
  const estado = estadoInvitacion(inv);
  return `
    <div class="pedido-card" data-invitacion-id="${escapeAttr(inv.id)}">
      <div class="pedido-card-header">
        <code class="pedido-id">${escapeAttr(inv.codigo)}</code>
        <span class="pill pill-estado pill-${estado.clave}">${estado.etiqueta}</span>
      </div>
      <p><strong>Rol:</strong> ${escapeAttr(inv.rol_destino)}</p>
      <p class="pedido-fecha">Creada: ${formatearFechaServidor(inv.fecha_creacion)} · Vence: ${formatearFechaServidor(inv.fecha_expiracion)}</p>
      ${estado.clave === 'vigente'
        ? `<button type="button" class="btn btn-outline btn-sm btn-revocar-invitacion" data-invitacion-id="${escapeAttr(inv.id)}">Revocar</button>`
        : ''}
    </div>`;
}

// cargarInvitaciones(), cargarProductosEnTransito() y cargarEnviosPendientesPorCapacidad() corren
// en paralelo (ver iniciarPanelAdmin) y las tres alimentan el mismo resumen — cada una guarda acá
// lo último que trajo y dispara un re-render con los tres datos, sin importar cuál termine primero.
let invitacionesActualesAdmin = [];
let productosTransitoActualesAdmin = [];
let enviosPorCapacidadActualesAdmin = [];

function refrescarResumenAdmin() {
  document.getElementById('admin-resumen').innerHTML = renderResumenAdmin(
    invitacionesActualesAdmin,
    productosTransitoActualesAdmin.length,
    enviosPorCapacidadActualesAdmin.length,
  );
  animarContadoresResumen(document.getElementById('admin-resumen'));
}

async function cargarInvitaciones() {
  const cont = document.getElementById('invitaciones-lista');
  const vacio = document.getElementById('invitaciones-empty');
  try {
    const invitaciones = await Api.admin.listarInvitaciones();
    invitacionesActualesAdmin = invitaciones;
    vacio.classList.toggle('hidden', invitaciones.length > 0);
    cont.innerHTML = invitaciones.map(renderInvitacionCard).join('');
    refrescarResumenAdmin();
    dibujarGraficoInvitacionesEstado(invitaciones);
  } catch (err) {
    manejarError(err, 'cargar las invitaciones');
  }
}

let chartInvitacionesEstado = null;

// Mismo canvas fijo en index.html (nunca se recrea por innerHTML) que ya obligó a este patrón
// en Panel Productor/Repartidor — destruir la instancia anterior antes de redibujar.
function dibujarGraficoInvitacionesEstado(invitaciones) {
  const canvas = document.getElementById('grafico-invitaciones-estado');
  if (!canvas || typeof Chart === 'undefined') return;

  const porEstado = { vigente: 0, usada: 0, expirada: 0 };
  invitaciones.forEach((inv) => { porEstado[estadoInvitacion(inv).clave]++; });

  chartInvitacionesEstado?.destroy();
  chartInvitacionesEstado = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: ['Vigentes', 'Usadas', 'Expiradas'],
      datasets: [{ data: [porEstado.vigente, porEstado.usada, porEstado.expirada], backgroundColor: ['#2D3B2A', '#82907A', '#A23B22'] }],
    },
    options: { plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } } },
  });
}

// ---- Resumen / dashboard de "Panel Admin" — reusa estadoInvitacion() (la misma función que ya
// clasifica cada tarjeta de la lista) sobre el array que acaba de traer cargarInvitaciones(). ----
function renderResumenAdmin(invitaciones, totalProductosTransito = 0, totalPendientesPorCapacidad = 0) {
  const porEstado = { vigente: 0, usada: 0, expirada: 0 };
  let masProximaAVencer = null;
  invitaciones.forEach((inv) => {
    const estado = estadoInvitacion(inv);
    porEstado[estado.clave]++;
    if (estado.clave === 'vigente' && (!masProximaAVencer || parsearFechaServidor(inv.fecha_expiracion) < parsearFechaServidor(masProximaAVencer.fecha_expiracion))) {
      masProximaAVencer = inv;
    }
  });

  let avisoVencimiento = '';
  if (masProximaAVencer) {
    const diasRestantes = Math.max(0, Math.ceil((parsearFechaServidor(masProximaAVencer.fecha_expiracion) - new Date()) / 86400000));
    const texto = diasRestantes === 0 ? 'vence hoy' : `vence en ${diasRestantes} día${diasRestantes === 1 ? '' : 's'}`;
    avisoVencimiento = `<i class="ti ti-alert-triangle"></i> El código <code>${escapeAttr(masProximaAVencer.codigo)}</code> ${texto}`;
  }

  return `
    <div class="card-panel resumen-panel">
      <div class="resumen-stats">
        <div class="resumen-stat resumen-stat-destacado">
          <span class="resumen-stat-icono"><i class="ti ti-ticket"></i></span>
          <div>
            <span class="resumen-stat-valor">${porEstado.vigente}</span>
            <span class="resumen-stat-label">Vigente${porEstado.vigente === 1 ? '' : 's'}</span>
          </div>
        </div>
        <div class="resumen-stat">
          <span class="resumen-stat-icono"><i class="ti ti-user-check"></i></span>
          <div>
            <span class="resumen-stat-valor">${porEstado.usada}</span>
            <span class="resumen-stat-label">Usada${porEstado.usada === 1 ? '' : 's'}</span>
          </div>
        </div>
        <div class="resumen-stat">
          <span class="resumen-stat-icono"><i class="ti ti-clock-x"></i></span>
          <div>
            <span class="resumen-stat-valor">${porEstado.expirada}</span>
            <span class="resumen-stat-label">Expirada${porEstado.expirada === 1 ? '' : 's'}</span>
          </div>
        </div>
        <div class="resumen-stat">
          <span class="resumen-stat-icono"><i class="ti ti-building-warehouse"></i></span>
          <div>
            <span class="resumen-stat-valor">${totalProductosTransito}</span>
            <span class="resumen-stat-label">Producto${totalProductosTransito === 1 ? '' : 's'} en tránsito</span>
          </div>
        </div>
      </div>
      ${avisoVencimiento ? `<div class="resumen-destacado resumen-destacado-urgente">${avisoVencimiento}</div>` : ''}
      ${totalPendientesPorCapacidad ? `
      <div class="resumen-destacado resumen-destacado-urgente resumen-destacado-clicable" id="btn-resumen-pendientes-capacidad">
        <i class="ti ti-truck"></i>
        ${totalPendientesPorCapacidad} pedido${totalPendientesPorCapacidad === 1 ? '' : 's'} esperando un vehículo con más capacidad
      </div>` : ''}
      <div class="resumen-acciones">
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-generar-codigo"><i class="ti ti-plus"></i> Generar nuevo código</button>
      </div>
    </div>`;
}

function mostrarInvitacionGenerada(inv) {
  document.getElementById('invitacion-codigo-texto').textContent = inv.codigo;
  document.getElementById('invitacion-expira-texto').textContent = `Vence: ${formatearFechaServidor(inv.fecha_expiracion)}`;
  document.getElementById('invitacion-resultado').classList.remove('hidden');
}

async function generarInvitacion(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  const rolDestino = document.getElementById('invitacion-rol-destino').value;
  btn.disabled = true;
  try {
    const invitacion = await Api.admin.crearInvitacion(rolDestino);
    mostrarInvitacionGenerada(invitacion);
    await cargarInvitaciones(); // la nueva invitación aparece en la lista sin recargar la página
    toast('Código de invitación generado');
  } catch (err) {
    manejarError(err, 'generar la invitación');
  } finally {
    btn.disabled = false;
  }
}

async function copiarCodigoInvitacion() {
  const codigo = document.getElementById('invitacion-codigo-texto').textContent;
  if (!codigo) return;
  try {
    await navigator.clipboard.writeText(codigo);
    toast('Código copiado al portapapeles');
  } catch {
    toast('No se pudo copiar. Selecciona el código manualmente.', 'error');
  }
}

async function revocarInvitacionDesdeLista(invitacionId) {
  try {
    await Api.admin.revocarInvitacion(invitacionId);
    toast('Invitación revocada');
    await cargarInvitaciones(); // saca la fila revocada sin recargar toda la página
  } catch (err) {
    manejarError(err, 'revocar la invitación');
  }
}

// ---- Pedidos esperando vehículo con más capacidad (caso poco frecuente: motivo_pendiente ==
// "sin_capacidad_suficiente" — ver GET /envios/pendientes-por-capacidad en transporte) ----

function renderTarjetaPendienteCapacidad(envio, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  // Sin peso del pedido (Envio no lo trae, y pedirlo de a uno por tarjeta sería una llamada
  // extra por cada envío listado — mismo criterio que "Entregas directas pendientes" en
  // panel-productor): la instrucción queda en términos generales, sin un número de kg concreto.
  return `
    <div class="pedido-card" style="animation-delay:${retraso}ms" data-envio-id="${escapeAttr(envio.id)}">
      <div class="pedido-card-header">
        <h4>Pedido #${escapeAttr(String(envio.pedido_id).slice(0, 8))}</h4>
      </div>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Envío creado</span><span class="perfil-dato-valor">${formatearFechaServidor(envio.fecha_creacion)}</span></div>
      </div>
      <p class="muted">Este pedido necesita un vehículo con más capacidad — ningún repartidor disponible alcanzaba. Contrata o coordina uno, y pide a quien lo va a manejar que se registre como Repartidor desde su propia cuenta (pantalla de inicio → "Soy repartidor" → completar perfil con capacidad y tipo de vehículo).</p>
    </div>`;
}

async function cargarEnviosPendientesPorCapacidad() {
  const vacio = document.getElementById('pendientes-capacidad-empty');
  const cont = document.getElementById('pendientes-capacidad-lista');
  try {
    const envios = await Api.transporte.pendientesPorCapacidad();
    enviosPorCapacidadActualesAdmin = envios;
    vacio.classList.toggle('hidden', envios.length > 0);
    cont.innerHTML = envios.map(renderTarjetaPendienteCapacidad).join('');
  } catch (err) {
    // Silencioso a propósito, mismo criterio que cargarEntregasDirectasPendientes() en
    // panel-productor: es una sección discreta y poco frecuente — un error acá no debería
    // interrumpir el resto del panel (invitaciones, productos en tránsito siguen cargando igual).
    enviosPorCapacidadActualesAdmin = [];
    cont.innerHTML = '';
    vacio.classList.remove('hidden');
  }
  refrescarResumenAdmin();
}

// ---- Productos en tránsito (confirmar llegada al almacén) ----
function renderTarjetaProductoTransito(p, totalReservas, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  const unidad = UNIDADES_MEDIDA[p.unidad_medida]?.etiqueta || p.unidad_medida || 'kg';
  const declarada = Math.max(Number(p.stock) || 0, 0);
  const idCantidad = `llegada-cantidad-${escapeAttr(p.id)}`;
  return `
    <div class="pedido-card" style="animation-delay:${retraso}ms" data-producto-id="${escapeAttr(p.id)}">
      <div class="pedido-card-header">
        <h4>${escapeAttr(p.nombre)}</h4>
        <span class="pill pill-estado pill-en_transito"><i class="ti ti-truck"></i> En tránsito</span>
      </div>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Productor</span><span class="perfil-dato-valor">${escapeAttr(p.productor_nombre || '—')}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Categoría</span><span class="perfil-dato-valor">${escapeAttr(nombreCategoria(p.categoria))}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Precio</span><span class="perfil-dato-valor">${formatearMoneda(p.precio)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Declarado</span><span class="perfil-dato-valor">${declarada} ${escapeAttr(unidad)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Reservas</span><span class="perfil-dato-valor">${totalReservas}</span></div>
      </div>
      <form class="form-llegada" data-producto-id="${escapeAttr(p.id)}" data-declarada="${declarada}" data-unidad="${escapeAttr(unidad)}" novalidate>
        <div class="form-llegada-campos">
          <label for="${idCantidad}">Cantidad recibida (${escapeAttr(unidad)})
            <input id="${idCantidad}" type="number" name="cantidad" inputmode="numeric" min="1" max="${declarada}" step="1" value="${declarada}" required aria-describedby="${idCantidad}-ayuda">
            <span class="form-llegada-ayuda ok" id="${idCantidad}-ayuda" role="status" aria-live="polite">Llegó todo lo declarado.</span>
          </label>
          <label>Observaciones (opcional)
            <input type="text" name="observaciones" maxlength="300" placeholder="Ej.: 2 cajas golpeadas" autocomplete="off">
          </label>
        </div>
        <div class="form-llegada-acciones">
          <button type="submit" class="btn btn-primary btn-sm btn-confirmar-llegada"><i class="ti ti-building-warehouse"></i> Confirmar llegada al almacén</button>
        </div>
      </form>
    </div>`;
}

async function cargarProductosEnTransito() {
  const cont = document.getElementById('productos-transito-lista');
  const vacio = document.getElementById('productos-transito-empty');
  vacio.classList.add('hidden');
  cont.innerHTML = renderSkeletonFilas(2);
  try {
    // GET /productos (catálogo público) ya trae en_transito + disponible juntos — el filtro por
    // estado se hace acá porque no existe (ni hace falta) un endpoint separado solo para esto.
    const productos = await Api.productos.listar();
    const enTransito = productos.filter((p) => p.estado === 'en_transito');
    productosTransitoActualesAdmin = enTransito;

    if (!enTransito.length) {
      cont.innerHTML = '';
      vacio.classList.remove('hidden');
    } else {
      // Una llamada a GET /productos/{id}/reservas por tarjeta — aceptable para el volumen de
      // una demo (mismo criterio que cargarMisProductos trayendo el detalle de cada borrador por
      // separado). Si el catálogo creciera de verdad, esto se resuelve mejor agregando un conteo
      // de reservas directo en la respuesta de GET /productos en vez de N llamadas adicionales.
      const reservasPorProducto = await Promise.all(
        enTransito.map((p) => Api.productos.reservas(p.id).catch(() => []))
      );
      cont.innerHTML = enTransito.map((p, i) => renderTarjetaProductoTransito(p, reservasPorProducto[i].length, i)).join('');
    }
    refrescarResumenAdmin();
  } catch (err) {
    manejarError(err, 'cargar los productos en tránsito');
  }
}

// Saca la tarjeta ya confirmada de la lista sin recargar toda la vista — mismo criterio que
// quitarTarjetaCosechaPendiente() en Panel Verificador.
function quitarTarjetaProductoTransito(productoId) {
  document.querySelector(`.pedido-card[data-producto-id="${productoId}"]`)?.remove();
  productosTransitoActualesAdmin = productosTransitoActualesAdmin.filter((p) => String(p.id) !== String(productoId));
  const cont = document.getElementById('productos-transito-lista');
  if (cont && !cont.children.length) {
    document.getElementById('productos-transito-empty').classList.remove('hidden');
  }
  refrescarResumenAdmin();
}

// Revisa la cantidad escrita contra lo declarado por el productor, muestra el mensaje junto al campo y
// habilita o no el botón. Devuelve la cantidad si es válida, o null. El servidor vuelve a comprobarlo
// todo: esto solo evita el viaje de ida y vuelta y explica el porqué al instante.
function evaluarCantidadLlegada(form) {
  const declarada = Number(form.dataset.declarada);
  const unidad = form.dataset.unidad;
  const campo = form.elements.cantidad;
  const ayuda = form.querySelector('.form-llegada-ayuda');
  const texto = campo.value.trim();
  const cantidad = Number(texto);

  let estado;
  let mensaje;
  let valida = false;
  if (!texto || !Number.isInteger(cantidad) || cantidad < 1) {
    estado = 'error';
    mensaje = `Escribe un número entero de 1 a ${declarada}.`;
  } else if (cantidad > declarada) {
    estado = 'error';
    mensaje = `No puede llegar más de lo declarado (${declarada} ${unidad}): esa es la cantidad que certificó la cosecha.`;
  } else if (cantidad < declarada) {
    estado = 'aviso';
    mensaje = `Faltan ${declarada - cantidad} ${unidad} de los ${declarada} declarados. La diferencia queda escrita en la cadena de certificación.`;
    valida = true;
  } else {
    estado = 'ok';
    mensaje = 'Llegó todo lo declarado.';
    valida = true;
  }

  ayuda.className = `form-llegada-ayuda ${estado}`;
  ayuda.textContent = mensaje;
  campo.setAttribute('aria-invalid', estado === 'error' ? 'true' : 'false');
  form.querySelector('button[type="submit"]').disabled = !valida;
  return valida ? cantidad : null;
}

async function confirmarLlegadaAlmacen(form) {
  const productoId = form.dataset.productoId;
  const cantidad = evaluarCantidadLlegada(form);
  if (cantidad === null) {
    form.elements.cantidad.focus();
    return;
  }
  const observaciones = form.elements.observaciones.value.trim();
  const controles = form.querySelectorAll('button, input');
  controles.forEach((c) => { c.disabled = true; });
  try {
    await Api.productos.confirmarLlegadaAlmacen(productoId, cantidad, observaciones);
    toast(`Llegada confirmada: ${cantidad} ${form.dataset.unidad} ya se pueden comprar y se avisó a quienes lo reservaron 📦`);
    quitarTarjetaProductoTransito(productoId);
  } catch (err) {
    manejarError(err, 'confirmar la llegada al almacén');
    controles.forEach((c) => { c.disabled = false; });
    evaluarCantidadLlegada(form);
  }
}

async function crearPerfilRepartidor(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const datos = {
      nombre: document.getElementById('repartidor-nombre').value.trim(),
      dni: document.getElementById('repartidor-dni').value.trim(),
      tipo_vehiculo: document.getElementById('repartidor-tipo-vehiculo').value,
      capacidad_maxima_kg: Number(document.getElementById('repartidor-capacidad-maxima').value),
    };
    await Api.repartidores.crear(datos);
    toast('¡Perfil de repartidor creado! 🚚');
    document.getElementById('form-repartidor').reset();
    cargarGestionEnvios();
  } catch (err) {
    manejarError(err, 'crear tu perfil de repartidor');
  } finally {
    btn.disabled = false;
  }
}

// Lo que el repartidor necesita para decidir si acepta: cuánto hay que recorrer, cuánto pesa y cuánto se paga
// (Pedidos → Transporte, sin datos del comprador: nombre y teléfono solo se ven una vez aceptado el envío). Si
// Pedidos no respondió, `resumen` viene null y la propuesta se muestra sin esta franja.
function renderMetricasPropuesta(resumen) {
  if (!resumen) return '';
  const numero = (valor) => Number(valor).toLocaleString('es-PE', { maximumFractionDigits: 1 });
  const dato = (icono, valor, etiqueta) => `
    <div class="propuesta-metrica">
      <i class="ti ti-${icono}" aria-hidden="true"></i>
      <strong>${valor}</strong>
      <span>${etiqueta}</span>
    </div>`;
  const distancia = resumen.distancia_km != null ? `${numero(resumen.distancia_km)} km` : '—';
  const peso = resumen.peso_total_kg != null ? `${numero(resumen.peso_total_kg)} kg` : 'Sin dato';
  const tarifa = resumen.costo_envio != null ? formatearMoneda(resumen.costo_envio) : '—';
  return `
    <div class="propuesta-metricas" role="group" aria-label="Datos del envío">
      ${dato('route', distancia, 'Desde el almacén')}
      ${dato('weight', peso, 'Peso')}
      ${dato('cash', tarifa, 'Tarifa')}
    </div>
    ${resumen.requiere_vehiculo_grande ? '<p class="propuesta-aviso"><i class="ti ti-truck" aria-hidden="true"></i> Pedido grande: pide un vehículo con más capacidad.</p>' : ''}`;
}

function renderTarjetaPropuesta(envio, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  return `
    <div class="propuesta-card" style="animation-delay:${retraso}ms">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Envío #${String(envio.id).slice(0, 8)} · Pedido #${String(envio.pedido_id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFechaServidor(envio.fecha_creacion)}</div>
        </div>
        ${badgeEstadoEnvio(envio.estado || 'propuesto')}
      </div>
      ${renderMetricasPropuesta(envio.resumen)}
      <div class="propuesta-actions">
        <button type="button" class="btn btn-outline btn-rechazar-propuesta" data-envio-id="${envio.id}"><i class="ti ti-x"></i> Rechazar</button>
        <button type="button" class="btn btn-primary btn-aceptar-propuesta" data-envio-id="${envio.id}"><i class="ti ti-check"></i> Aceptar envío</button>
      </div>
    </div>`;
}

// Contacto del comprador para el repartidor: el teléfono ya es obligatorio al pedir (ver
// PedidoCrear en pedidos/main.py) y este es el único lugar donde alguien lo usa. Los pedidos
// anteriores a esa validación pueden no tenerlo — en ese caso no se pinta nada.
function renderContactoComprador(pedido) {
  const telefono = String(pedido?.comprador_telefono || '').replace(/\D/g, '');
  if (!pedido || !telefono) return '';
  return `
      <div class="envio-contacto">
        <span class="envio-contacto-nombre"><i class="ti ti-user"></i> ${escapeAttr(pedido.comprador_nombre || 'Comprador')}</span>
        <a class="btn btn-outline btn-sm envio-contacto-llamar" href="tel:${telefono}"><i class="ti ti-phone"></i> ${telefono}</a>
      </div>`;
}

function renderTarjetaEnvio(envio, ruta, indice = 0, pedido = null) {
  const activo = ['asignado', 'en_camino'].includes(envio.estado);
  const finalizado = ['entregado', 'cancelado', 'rechazado'].includes(envio.estado);
  const retraso = Math.min(indice, 12) * 35;
  return `
    <div class="pedido-card ${activo ? 'pedido-card-activo' : ''}" style="animation-delay:${retraso}ms">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Envío #${String(envio.id).slice(0, 8)} · Pedido #${String(envio.pedido_id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFechaServidor(envio.fecha_creacion)}</div>
        </div>
        ${badgeEstadoEnvio(envio.estado)}
      </div>
      ${activo ? renderContactoComprador(pedido) : ''}
      ${activo ? renderBloqueRuta(envio, ruta) : ''}
      ${!finalizado ? `
      <div class="envio-actualizar">
        <select class="select-estado-envio" data-envio-id="${envio.id}">
          <option value="asignado" ${envio.estado === 'asignado' ? 'selected' : ''}>Asignado</option>
          <option value="en_camino" ${envio.estado === 'en_camino' ? 'selected' : ''}>En camino</option>
          <option value="entregado" ${envio.estado === 'entregado' ? 'selected' : ''}>Entregado</option>
        </select>
        <button class="btn btn-primary btn-actualizar-envio" data-envio-id="${envio.id}">Actualizar estado</button>
      </div>
      <form class="form-confirmar-entrega hidden" data-envio-id="${envio.id}">
        <p class="muted"><i class="ti ti-shield-check"></i> Pídele al comprador el código de 4 dígitos de su pedido.</p>
        <div class="form-confirmar-entrega-campos">
          <label>Código del comprador
            <input type="text" name="pin" inputmode="numeric" maxlength="4" pattern="[0-9]{4}" required placeholder="0000">
          </label>
          <div class="foto-entrega" data-estado="vacia">
            <span class="foto-entrega-titulo" id="foto-titulo-${envio.id}">Foto de la entrega</span>
            <!-- El input nativo queda oculto pero operable con teclado (el foco se ve en la zona, ver :focus-within);
                 en el celular "capture" abre la cámara directamente. Sin "required": la falta de foto se avisa en
                 el propio formulario (confirmarEntregaConPin), no con la burbuja del navegador. -->
            <input class="foto-entrega-input" id="foto-${envio.id}" type="file" name="foto" accept="image/jpeg,image/png,image/webp" capture="environment" aria-labelledby="foto-titulo-${envio.id}" aria-describedby="foto-ayuda-${envio.id}">
            <label class="foto-entrega-zona" for="foto-${envio.id}">
              <span class="foto-entrega-icono"><i class="ti ti-camera" aria-hidden="true"></i></span>
              <span class="foto-entrega-texto">
                <strong>Tomar o elegir foto</strong>
                <small>Del paquete entregado · JPG, PNG o WEBP · máx. 5 MB</small>
              </span>
            </label>
            <div class="foto-entrega-previa">
              <img alt="Vista previa de la foto de la entrega">
              <div class="foto-entrega-datos">
                <strong class="foto-nombre"></strong>
                <small class="foto-peso"></small>
              </div>
              <div class="foto-entrega-botones">
                <button type="button" class="btn btn-ghost btn-sm foto-cambiar"><i class="ti ti-refresh" aria-hidden="true"></i> Cambiar</button>
                <button type="button" class="btn btn-ghost btn-sm foto-quitar"><i class="ti ti-trash" aria-hidden="true"></i> Quitar</button>
              </div>
            </div>
            <p class="foto-entrega-ayuda" id="foto-ayuda-${envio.id}" role="status" aria-live="polite"></p>
          </div>
        </div>
        <p class="form-entrega-mensaje hidden" role="alert"></p>
        <div class="form-confirmar-entrega-acciones">
          <button type="button" class="btn btn-ghost btn-sm btn-cancelar-entrega">Cancelar</button>
          <button type="submit" class="btn btn-primary btn-sm">Confirmar entrega</button>
        </div>
      </form>` : ''}
    </div>
  `;
}

async function cargarListaEnvios(miRepartidorId) {
  detenerPollingRutas();

  const propuestasSeccion = document.getElementById('propuestas-section');
  const propuestasCont = document.getElementById('propuestas-list');
  const propuestasBadge = document.getElementById('propuestas-badge');
  const cont = document.getElementById('envios-list');
  const vacio = document.getElementById('envios-empty');

  vacio.classList.add('hidden');
  propuestasSeccion.classList.add('hidden');
  cont.innerHTML = renderSkeletonFilas(2);

  let propuestas = [];
  try {
    propuestas = await Api.transporte.propuestas();
  } catch { /* si falla, seguimos mostrando al menos los envíos ya asignados */ }
  Estado.propuestasRepartidor = propuestas;

  let mios = [];
  try {
    const todos = await Api.transporte.listarTodos();
    const idsPropuestas = new Set(propuestas.map((p) => p.id));
    mios = todos.filter((e) => String(e.repartidor_id) === String(miRepartidorId) && !idsPropuestas.has(e.id));
    Estado.enviosRepartidor = mios;
  } catch (err) {
    manejarError(err, 'cargar tus envíos');
    cont.innerHTML = '';
    return;
  }

  if (propuestas.length) {
    propuestasSeccion.classList.remove('hidden');
    propuestasCont.innerHTML = propuestas.map((e, i) => renderTarjetaPropuesta(e, i)).join('');
    propuestasBadge.textContent = propuestas.length;
    propuestasBadge.classList.toggle('hidden', propuestas.length <= 1);
  }

  if (!mios.length && !propuestas.length) {
    cont.innerHTML = '';
    vacio.classList.remove('hidden');
    return;
  }
  vacio.classList.add('hidden');

  if (!mios.length) {
    cont.innerHTML = '';
    return;
  }

  const activos = mios.filter((e) => ['asignado', 'en_camino'].includes(e.estado));
  const rutasPorEnvio = {};
  const pedidosPorEnvio = {};
  // Ruta (seguimiento en mapa) y pedido (nombre + teléfono de contacto del comprador) son
  // independientes y opcionales: si alguna falla, la tarjeta se pinta igual sin ese bloque.
  await Promise.all(activos.map(async (e) => {
    const [ruta, pedido] = await Promise.allSettled([Api.transporte.ruta(e.id), Api.pedidos.obtener(e.pedido_id)]);
    if (ruta.status === 'fulfilled') rutasPorEnvio[e.id] = ruta.value;
    if (pedido.status === 'fulfilled') pedidosPorEnvio[e.id] = pedido.value;
  }));

  cont.innerHTML = mios.map((e, i) => renderTarjetaEnvio(e, rutasPorEnvio[e.id], i, pedidosPorEnvio[e.id])).join('');
  activos.forEach((e) => {
    const ruta = rutasPorEnvio[e.id];
    if (ruta?.disponible) iniciarMapaRutaPedido(e.id, ruta);
  });
}

async function actualizarEstadoEnvio(envioId, nuevoEstado) {
  try {
    await Api.transporte.actualizarEstado(envioId, nuevoEstado);
    toast(`Estado del envío actualizado a "${nuevoEstado}" ✅`);
    refrescarPanelRepartidor();
  } catch (err) {
    manejarError(err, 'actualizar el estado del envío');
  }
}

// "Entregado" ya no se marca con el select genérico de arriba — necesita el PIN que el
// comprador le dicta al repartidor en la puerta más una foto, ver POST /envios/{id}/entregar.
async function confirmarEntregaConPin(form) {
  const envioId = form.dataset.envioId;
  const pin = form.pin.value.trim();
  const archivoFoto = form.foto.files[0];
  if (!archivoFoto) {
    mostrarMensajeEntrega(form, 'Agrega la foto de la entrega antes de confirmar.', 'error');
    form.foto.focus();
    return;
  }

  const btnConfirmar = form.querySelector('button[type="submit"]');
  form.querySelector('.form-entrega-mensaje')?.classList.add('hidden');
  form.querySelectorAll('button, input').forEach((el) => { el.disabled = true; });
  btnConfirmar.textContent = 'Confirmando...';
  try {
    await Api.transporte.confirmarEntrega(envioId, pin, archivoFoto);
    toast('Entrega confirmada ✅');
    refrescarPanelRepartidor();
  } catch (err) {
    manejarError(err, 'confirmar la entrega');
    mostrarMensajeEntrega(form, err.message, err.status === 429 ? 'bloqueado' : 'error');
    if (err.status === 429) {
      // Envío bloqueado por demasiados códigos incorrectos: el formulario queda deshabilitado (con el
      // mensaje a la vista, no solo en un aviso que desaparece) hasta que pase el bloqueo. Al volver a
      // abrir la lista se vuelve a intentar y el servidor dice cuántos minutos faltan.
      btnConfirmar.textContent = 'Bloqueado temporalmente';
      form.querySelector('.btn-cancelar-entrega').disabled = false;
    } else {
      form.querySelectorAll('button, input').forEach((el) => { el.disabled = false; });
      btnConfirmar.textContent = 'Confirmar entrega';
      // Solo un código incorrecto (400) se borra; si falló la foto (no es una imagen, pesa de más) el código
      // escrito sigue bueno y el foco va al campo de la foto.
      if (err.status === 400) {
        form.pin.value = '';
        form.pin.focus();
      } else {
        form.foto.focus();
      }
    }
  }
}

// ---- Selector de la foto de entrega: vista previa, nombre y peso, y validación al momento (mismo tope y tipos que
// el servidor, ver imagenes.py; el servidor vuelve a comprobarlo todo). ----
const FOTO_TIPOS_VALIDOS = ['image/jpeg', 'image/png', 'image/webp'];
const FOTO_TAMANO_MAXIMO = 5 * 1024 * 1024;

function formatearTamanoArchivo(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// estado: 'vacia' (zona para elegir), 'lista' (vista previa con Cambiar/Quitar) o 'error' (la zona con el motivo)
function pintarFotoEntrega(contenedor, estado, { archivo = null, mensaje = '' } = {}) {
  contenedor.dataset.estado = estado;
  const ayuda = contenedor.querySelector('.foto-entrega-ayuda');
  ayuda.className = `foto-entrega-ayuda${estado === 'error' ? ' error' : estado === 'lista' ? ' ok' : ''}`;
  ayuda.setAttribute('role', estado === 'error' ? 'alert' : 'status');
  ayuda.textContent = mensaje;

  const imagen = contenedor.querySelector('.foto-entrega-previa img');
  if (imagen.dataset.url) URL.revokeObjectURL(imagen.dataset.url);
  if (archivo) {
    imagen.dataset.url = URL.createObjectURL(archivo);
    imagen.src = imagen.dataset.url;
    contenedor.querySelector('.foto-nombre').textContent = archivo.name;
    contenedor.querySelector('.foto-peso').textContent = formatearTamanoArchivo(archivo.size);
  } else {
    delete imagen.dataset.url;
    imagen.removeAttribute('src');
  }
}

function alElegirFotoEntrega(input) {
  const contenedor = input.closest('.foto-entrega');
  const archivo = input.files[0];
  if (!archivo) return pintarFotoEntrega(contenedor, 'vacia');
  if (!FOTO_TIPOS_VALIDOS.includes(archivo.type)) {
    input.value = '';
    return pintarFotoEntrega(contenedor, 'error', { mensaje: 'Ese archivo no es una imagen JPG, PNG o WEBP. Elige otra foto.' });
  }
  if (archivo.size > FOTO_TAMANO_MAXIMO) {
    input.value = '';
    return pintarFotoEntrega(contenedor, 'error', { mensaje: `La foto pesa ${formatearTamanoArchivo(archivo.size)} y el máximo es 5 MB. Toma otra más liviana.` });
  }
  pintarFotoEntrega(contenedor, 'lista', { archivo, mensaje: 'Foto lista para enviar.' });
  contenedor.querySelector('.foto-cambiar').focus({ preventScroll: true }); // el foco no se pierde con la zona oculta
}

function quitarFotoEntrega(contenedor) {
  contenedor.querySelector('.foto-entrega-input').value = '';
  pintarFotoEntrega(contenedor, 'vacia');
  contenedor.querySelector('.foto-entrega-input').focus({ preventScroll: true });
}

// Mensaje de error fijo dentro del formulario de confirmar entrega (role="alert": se anuncia solo).
function mostrarMensajeEntrega(form, mensaje, tipo) {
  const el = form.querySelector('.form-entrega-mensaje');
  if (!el) return;
  const icono = tipo === 'bloqueado' ? 'ti-lock' : 'ti-alert-circle';
  el.className = `form-entrega-mensaje ${tipo}`;
  el.innerHTML = `<i class="ti ${icono}"></i><span>${escapeAttr(mensaje)}</span>`;
}

async function responderPropuesta(envioId, accion) {
  const boton = document.querySelector(`.btn-${accion}-propuesta[data-envio-id="${envioId}"]`);
  const card = boton?.closest('.propuesta-card');
  card?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    if (accion === 'aceptar') await Api.transporte.aceptar(envioId);
    else await Api.transporte.rechazar(envioId);
    toast(accion === 'aceptar' ? 'Envío aceptado, ¡buen viaje! 🚚' : 'Envío rechazado.');
    refrescarPanelRepartidor();
  } catch (err) {
    manejarError(err, `${accion} el envío`);
    card?.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}

// ---- Seguimiento de ubicación en vivo (geolocalización del repartidor) ----
let watchIdRepartidor = null;
let intervalUbicacionRepartidor = null;
let ultimaPosicionRepartidor = null;

function renderEstadoUbicacion(tipo, mensaje) {
  const iconos = { ok: 'map-pin', error: 'alert-triangle', espera: 'refresh' };
  return `<span class="ubicacion-chip ubicacion-${tipo}"><i class="ti ti-${iconos[tipo] || 'map-pin'}"></i> ${escapeAttr(mensaje)}</span>`;
}

function iniciarSeguimientoRepartidor() {
  detenerSeguimientoRepartidor();

  const estadoEl = document.getElementById('ubicacion-estado');
  if (!estadoEl) return;

  if (!navigator.geolocation) {
    estadoEl.innerHTML = renderEstadoUbicacion('error', 'Tu navegador no soporta geolocalización.');
    return;
  }

  estadoEl.innerHTML = renderEstadoUbicacion('espera', 'Buscando tu ubicación...');

  watchIdRepartidor = navigator.geolocation.watchPosition(
    (pos) => {
      ultimaPosicionRepartidor = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      const el = document.getElementById('ubicacion-estado');
      if (el) el.innerHTML = renderEstadoUbicacion('ok', 'Compartiendo tu ubicación en tiempo real.');
    },
    (err) => {
      const el = document.getElementById('ubicacion-estado');
      if (el) el.innerHTML = renderEstadoUbicacion('error', mensajeErrorGeolocalizacion(err));
    },
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 }
  );

  intervalUbicacionRepartidor = setInterval(enviarUbicacionActual, 15000);
}

async function enviarUbicacionActual() {
  if (!ultimaPosicionRepartidor) return;
  const trackeables = (Estado.enviosRepartidor || []).filter((e) => ['asignado', 'en_camino'].includes(e.estado));
  if (!trackeables.length) return;

  for (const envio of trackeables) {
    try {
      await Api.transporte.actualizarUbicacion(envio.id, ultimaPosicionRepartidor.lat, ultimaPosicionRepartidor.lng);
    } catch { /* se reintenta en el siguiente ciclo de 15s */ }
  }
}

function detenerSeguimientoRepartidor() {
  if (watchIdRepartidor != null) {
    navigator.geolocation?.clearWatch(watchIdRepartidor);
    watchIdRepartidor = null;
  }
  if (intervalUbicacionRepartidor != null) {
    clearInterval(intervalUbicacionRepartidor);
    intervalUbicacionRepartidor = null;
  }
  ultimaPosicionRepartidor = null;
}

// ============ MI PERFIL ============
const ROL_INFO_PERFIL = {
  comprador: { icono: '<i class="ti ti-shopping-cart"></i>', texto: 'Comprador', descripcion: 'Explora el catálogo y haz seguimiento de tus compras.' },
  productor: { icono: '<i class="ti ti-tractor"></i>', texto: 'Productor', descripcion: 'Publica tus productos sin intermediarios y lleva tu gestión económica.' },
  repartidor: { icono: '<i class="ti ti-truck"></i>', texto: 'Repartidor', descripcion: 'Recibe propuestas de envío y entrega los pedidos.' },
  verificador: { icono: '<i class="ti ti-shield-check"></i>', texto: 'Verificador', descripcion: 'Revisa y certifica las cosechas de los productores.' },
  admin: { icono: '<i class="ti ti-settings"></i>', texto: 'Administrador', descripcion: 'Administra la plataforma.' },
};

// Acceso al panel propio de cada rol (la vista es la misma a la que aterriza al iniciar sesión).
const ACCESO_PANEL_POR_ROL = {
  productor: { icono: 'tractor', etiqueta: 'Panel de productor' },
  repartidor: { icono: 'truck', etiqueta: 'Gestionar envíos' },
  verificador: { icono: 'shield-check', etiqueta: 'Panel de verificación' },
  admin: { icono: 'settings', etiqueta: 'Panel de administración' },
};

function ocultarDni(dni) {
  if (!dni) return '—';
  if (dni.length < 6) return dni;
  const centro = Math.max(0, dni.length - 6);
  return `${dni.slice(0, 4)}${'*'.repeat(centro)}${dni.slice(-2)}`;
}

function renderSkeletonPerfil() {
  return `
    <div class="perfil-hero panel-header-hero" aria-hidden="true">
      <div class="skeleton" style="width:104px;height:104px;border-radius:50%;flex-shrink:0;"></div>
      <div style="flex:1">
        <div class="skeleton skeleton-line w-40" style="margin-bottom:12px;"></div>
        <div class="skeleton skeleton-line tall w-60"></div>
      </div>
    </div>
    <div class="perfil-layout" aria-hidden="true">
      <div class="card-panel">
        <div class="skeleton skeleton-line w-40" style="margin-bottom:14px;"></div>
        <div class="skeleton skeleton-line tall w-70" style="margin-bottom:10px;"></div>
        <div class="skeleton skeleton-line w-60"></div>
      </div>
      <div class="card-panel">
        <div class="skeleton skeleton-line w-40" style="margin-bottom:14px;"></div>
        <div class="skeleton skeleton-line tall w-70"></div>
      </div>
    </div>`;
}

// Cuenta de quien no tiene datos propios de rol (comprador, verificador, administrador): lo que sabemos de
// ella y qué hace ese rol. Productor y repartidor muestran en su lugar sus datos de rol.
function renderPerfilCuenta(usuario, rolInfo) {
  return `
    <section class="card-panel perfil-tarjeta">
      <h3><i class="ti ti-id-badge-2"></i> Tu cuenta</h3>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Nombre</span><span class="perfil-dato-valor">${escapeAttr(usuario.nombre)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Correo</span><span class="perfil-dato-valor">${escapeAttr(usuario.email)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Tipo de cuenta</span><span class="perfil-dato-valor">${rolInfo.texto}</span></div>
      </div>
      <p class="perfil-cuenta-descripcion">${rolInfo.descripcion || ''}</p>
    </section>`;
}

// Accesos a lo que más se usa; el panel propio del rol va primero. Se resuelven por delegación en
// #perfil-contenido (data-perfil-ir): "catalogo" abre el buscador, cualquier otro valor es una vista.
function renderPerfilAccesos(rol) {
  const panel = ACCESO_PANEL_POR_ROL[rol];
  const accesos = [
    ...(panel ? [{ icono: panel.icono, etiqueta: panel.etiqueta, ir: VISTA_INICIAL_POR_ROL[rol] }] : []),
    { icono: 'package', etiqueta: 'Mis pedidos', ir: 'mis-pedidos' },
    { icono: 'bell', etiqueta: 'Notificaciones', ir: 'notificaciones' },
    { icono: 'search', etiqueta: 'Explorar el catálogo', ir: 'catalogo' },
  ];
  return `
    <nav class="card-panel perfil-accesos" aria-label="Accesos rápidos">
      <h3>Accesos rápidos</h3>
      ${accesos.map((a) => `
        <button type="button" class="perfil-acceso" data-perfil-ir="${a.ir}">
          <span class="perfil-acceso-icono"><i class="ti ti-${a.icono}" aria-hidden="true"></i></span>
          <span>${a.etiqueta}</span>
          <i class="ti ti-chevron-right" aria-hidden="true"></i>
        </button>`).join('')}
    </nav>`;
}

// Datos de productor (columna principal de Mi Perfil): las cifras de gestión económica arriba y, debajo, sus
// datos con el mapa de la chakra. "Mis siembras" va aparte, a todo el ancho (ver renderPerfil).
function renderPerfilProductor(extra, registrosProduccion) {
  if (!extra) {
    return `
      <div class="perfil-cta-completar">
        <span class="empty-state-icon"><i class="ti ti-tractor"></i></span>
        <p><strong>Aún no completaste tu perfil de productor</strong></p>
        <p class="muted">Créalo para empezar a publicar productos sin intermediarios.</p>
        <button type="button" class="btn btn-primary" id="btn-perfil-ir-panel">Completar perfil de productor</button>
      </div>`;
  }
  const dato = (etiqueta, valor) => `<div class="perfil-dato"><span class="perfil-dato-label">${etiqueta}</span><span class="perfil-dato-valor">${valor}</span></div>`;
  return `
    ${renderEstadisticasProductor(registrosProduccion || [])}
    <section class="card-panel perfil-tarjeta">
      <h3><i class="ti ti-tractor"></i> Datos de productor</h3>
      <div class="perfil-datos-grid">
        ${extra.nombre ? dato('Nombre', escapeAttr(extra.nombre)) : ''}
        ${dato('Comunidad / Región', escapeAttr(extra.comunidad || '—'))}
        ${dato('Contacto', escapeAttr(extra.contacto || '—'))}
        ${extra.fecha_registro ? dato('Registrado', formatearFechaServidor(extra.fecha_registro)) : ''}
      </div>
      ${hayCoordenadas(extra.latitud, extra.longitud) ? `
      <div class="mapa-bloque">
        <h4 class="mapa-titulo"><i class="ti ti-map-pin"></i> Ubicación de tu chakra</h4>
        <div id="mapa-perfil-productor" class="mapa-mini"></div>
      </div>` : `<div class="mapa-bloque-neutro"><span class="icon"><i class="ti ti-map-pin"></i></span> No has marcado la ubicación de tu chakra todavía.</div>`}
    </section>`;
}

// ============ GESTIÓN ECONÓMICA (Módulo 1) ============

// Cálculo puro reusado por renderEstadisticasProductor (Mi Perfil) y renderEconomiaStatsProductor
// (pestaña "Gestión Económica" del Panel Productor) — mismos números, dos presentaciones.
function calcularResumenEconomico(registros) {
  const totalInvertido = registros.reduce((suma, r) => suma + (Number(r.costo_total) || 0), 0);

  // "cosechado" es un valor legado (ver comentario en productores/models.py) — el flujo actual
  // termina en "aprobado" con cantidad_cosechada ya presente; completar_cosecha() nunca deja un
  // registro en reposo como "cosechado". Sin este OR, esta sección quedaba en "—" para casi
  // cualquier dato real.
  const cosechados = registros.filter((r) => r.estado === 'aprobado' || r.estado === 'cosechado');
  const totalCosechadoKg = cosechados.reduce((suma, r) => {
    if (r.cantidad_cosechada == null) return suma;
    const enKg = r.unidad_medida === 'kg'
      ? Number(r.cantidad_cosechada)
      : Number(r.cantidad_cosechada) * Number(r.equivalencia_kg || 0);
    return suma + enKg;
  }, 0);

  const conGanancia = cosechados.filter((r) => r.ganancia_estimada != null);
  const gananciaAcumulada = conGanancia.reduce((suma, r) => suma + Number(r.ganancia_estimada), 0);
  const sinCalcular = cosechados.length - conGanancia.length;

  return { totalInvertido, cosechados, totalCosechadoKg, conGanancia, gananciaAcumulada, sinCalcular };
}

function renderEstadisticasProductor(registros) {
  const { totalInvertido, cosechados, totalCosechadoKg, conGanancia, gananciaAcumulada, sinCalcular } = calcularResumenEconomico(registros);
  const tarjeta = (icono, etiqueta, valor, clase = '') => `
    <div class="perfil-stat">
      <span class="perfil-stat-icono"><i class="ti ti-${icono}" aria-hidden="true"></i></span>
      <div><span class="perfil-stat-valor ${clase}">${valor}</span><span class="perfil-stat-etiqueta">${etiqueta}</span></div>
    </div>`;

  return `
    <section class="perfil-economia" aria-label="Gestión económica">
      <div class="perfil-stats">
        ${tarjeta('coin', 'Total invertido', formatearMoneda(totalInvertido))}
        ${tarjeta('scale', 'Total cosechado', cosechados.length ? `${totalCosechadoKg.toFixed(2)} kg` : '—')}
        ${tarjeta('trending-up', 'Ganancia acumulada', conGanancia.length ? formatearMoneda(gananciaAcumulada) : '—', gananciaAcumulada >= 0 ? 'ganancia-positiva' : 'ganancia-negativa')}
      </div>
      ${sinCalcular > 0 ? `<p class="muted produccion-nota-resumen"><i class="ti ti-info-circle"></i> ${sinCalcular} registro${sinCalcular > 1 ? 's' : ''} cosechado${sinCalcular > 1 ? 's' : ''} sin datos suficientes para calcular ganancia — no se incluye${sinCalcular > 1 ? 'n' : ''} en el total.</p>` : ''}
    </section>`;
}

// ============ GESTIÓN ECONÓMICA DENTRO DE PANEL PRODUCTOR (pestaña dedicada) ============
// Misma fuente de datos que ya carga iniciarPanelProductor (Estado.registrosProduccion vía
// cargarRegistrosProduccionProductor) — no dispara ninguna llamada nueva al backend.

function renderEconomiaStatsProductor(registros) {
  const { totalInvertido, cosechados, totalCosechadoKg, conGanancia, gananciaAcumulada } = calcularResumenEconomico(registros);
  // Margen sobre el ingreso estimado: misma definición que margen_porcentaje de cada registro (Productores).
  const ingresoTotal = conGanancia.reduce((suma, r) => suma + (Number(r.ingreso_estimado) || 0), 0);
  const margen = conGanancia.length && ingresoTotal > 0 ? (gananciaAcumulada / ingresoTotal) * 100 : null;
  const plural = (n, uno, varios) => `${n} ${n === 1 ? uno : varios}`;
  const tarjeta = (icono, etiqueta, valor, pie, claseValor = '') => `
    <div class="economia-stat">
      <div class="economia-stat-cabeza">
        <span class="economia-stat-icono"><i class="ti ti-${icono}" aria-hidden="true"></i></span>
        <span class="economia-stat-label">${etiqueta}</span>
      </div>
      <span class="economia-stat-valor ${claseValor}">${valor}</span>
      ${pie}
    </div>`;
  const pieGanancia = margen === null
    ? '<span class="economia-stat-sub">Aún sin cosechas verificadas</span>'
    : `<span class="economia-badge ${margen >= 0 ? 'positivo' : 'negativo'}"><i class="ti ti-trending-${margen >= 0 ? 'up' : 'down'}" aria-hidden="true"></i> ${margen >= 0 ? '+' : '-'}${Math.abs(margen).toFixed(1)}% de margen</span>`;
  return `
    <div class="economia-stats">
      ${tarjeta('coin', 'Total invertido', formatearMoneda(totalInvertido), `<span class="economia-stat-sub">en ${plural(registros.length, 'siembra', 'siembras')}</span>`)}
      ${tarjeta('scale', 'Total cosechado', cosechados.length ? `${totalCosechadoKg.toFixed(2)} kg` : '—', `<span class="economia-stat-sub">${cosechados.length ? `de ${plural(cosechados.length, 'cosecha', 'cosechas')}` : 'Aún sin cosechas'}</span>`)}
      ${tarjeta(gananciaAcumulada >= 0 ? 'trending-up' : 'trending-down', 'Ganancia acumulada', conGanancia.length ? formatearMoneda(gananciaAcumulada) : '—', pieGanancia, gananciaAcumulada >= 0 ? 'ganancia-positiva' : 'ganancia-negativa')}
    </div>`;
}

function renderTablaHistorialCosechas(registros) {
  if (!registros.length) {
    return '<p class="muted">Todavía no registraste ninguna siembra.</p>';
  }
  const filas = registros.map((r) => {
    const infoEstado = BADGES_ESTADO_REGISTRO[r.estado] || BADGES_ESTADO_REGISTRO.planificado;
    const cosechado = r.cantidad_cosechada != null ? `${Number(r.cantidad_cosechada).toFixed(2)} ${escapeAttr(r.unidad_medida)}` : '—';
    const ingreso = r.ingreso_estimado != null ? formatearMoneda(r.ingreso_estimado) : '—';
    const ganancia = r.ganancia_estimada != null
      ? `<span class="${r.ganancia_estimada >= 0 ? 'ganancia-positiva' : 'ganancia-negativa'}">${formatearMoneda(r.ganancia_estimada)}</span>`
      : '—';
    const margen = r.margen_porcentaje != null
      ? `<span class="${r.margen_porcentaje >= 0 ? 'ganancia-positiva' : 'ganancia-negativa'}">${r.margen_porcentaje < 0 ? '-' : ''}${Math.abs(Number(r.margen_porcentaje)).toFixed(1)}%</span>`
      : '—';
    return `
      <tr>
        <td>${escapeAttr(r.cultivo)}</td>
        <td class="num">${formatearMoneda(r.costo_total)}</td>
        <td class="num">${cosechado}</td>
        <td class="num">${ingreso}</td>
        <td class="num">${ganancia}</td>
        <td class="num">${margen}</td>
        <td><span class="pill pill-estado ${infoEstado.clase}"><i class="ti ti-${infoEstado.icono}"></i> ${infoEstado.etiqueta}</span></td>
      </tr>`;
  }).join('');
  return `
    <table class="tabla-simple tabla-numerica">
      <thead><tr><th>Cultivo</th><th class="num">Costo total</th><th class="num">Cosechado</th><th class="num">Ingreso est.</th><th class="num">Ganancia</th><th class="num">Margen</th><th>Estado</th></tr></thead>
      <tbody>${filas}</tbody>
    </table>`;
}

// Ganancia acumulada por cultivo (solo registros con ganancia_estimada calculada) — varias
// siembras del mismo cultivo se suman en una sola barra.
function agruparGananciaPorCultivo(registros) {
  const mapa = new Map();
  registros.filter((r) => r.ganancia_estimada != null).forEach((r) => {
    mapa.set(r.cultivo, (mapa.get(r.cultivo) || 0) + Number(r.ganancia_estimada));
  });
  return { labels: [...mapa.keys()], valores: [...mapa.values()] };
}

// Suma de los 4 componentes de costo across todos los registros (independiente del estado).
function calcularDistribucionCostos(registros) {
  const totales = { semillas: 0, insumos: 0, manoObra: 0, envio: 0 };
  registros.forEach((r) => {
    totales.semillas += Number(r.costo_semillas) || 0;
    totales.insumos += Number(r.costo_insumos) || 0;
    totales.manoObra += Number(r.costo_mano_obra) || 0;
    totales.envio += Number(r.costo_envio) || 0;
  });
  return totales;
}

let chartCosechasMes = null;
let chartGananciaCultivo = null;
let chartCostos = null;
// Semillas, insumos, mano de obra, envío. El beige va más oscuro que el fondo de la tarjeta para que
// la porción se distinga.
const COLORES_COSTOS = ['#2D3B2A', '#82907A', '#CDBF9F', '#8A6D1A'];

// Cuenta registros por mes de fecha_siembra, últimos 6 meses (incluye el actual). Los canvases
// viven fijos en index.html (no se recrean por innerHTML), así que hay que destruir la instancia
// anterior antes de dibujar de nuevo cada vez que se revisita el panel — si no, Chart.js lanza
// "Canvas is already in use".
function dibujarGraficoCosechasPorMes(registros) {
  const canvas = document.getElementById('grafico-cosechas-mes');
  if (!canvas || typeof Chart === 'undefined') return;

  const meses = [];
  const conteos = [];
  const ahora = new Date();
  for (let i = 5; i >= 0; i--) {
    const fecha = new Date(ahora.getFullYear(), ahora.getMonth() - i, 1);
    meses.push(fecha);
    conteos.push(0);
  }
  registros.forEach((r) => {
    if (!r.fecha_siembra) return;
    const f = new Date(r.fecha_siembra);
    const idx = meses.findIndex((m) => m.getFullYear() === f.getFullYear() && m.getMonth() === f.getMonth());
    if (idx !== -1) conteos[idx]++;
  });
  const etiquetas = meses.map((m) => m.toLocaleDateString('es-PE', { month: 'short' }));

  chartCosechasMes?.destroy();
  chartCosechasMes = new Chart(canvas, {
    type: 'bar',
    data: { labels: etiquetas, datasets: [{ label: 'Siembras', data: conteos, backgroundColor: '#2D3B2A', borderRadius: 6, maxBarThickness: 44 }] },
    options: {
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.parsed.y} ${c.parsed.y === 1 ? 'siembra' : 'siembras'}` } } },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0, stepSize: 1 } } },
    },
  });
}

function dibujarGraficosEconomiaProductor(registros) {
  const canvasGanancia = document.getElementById('grafico-ganancia-cultivo');
  const canvasCostos = document.getElementById('grafico-costos');
  if (!canvasGanancia || !canvasCostos || typeof Chart === 'undefined') return;

  const { labels, valores } = agruparGananciaPorCultivo(registros);
  const datosGanancia = valores.length ? valores : [0];
  // Las pérdidas en el mismo rojo que .ganancia-negativa de la tabla, para verlas de un vistazo.
  const colorBarra = (v) => (v < 0 ? '#b3401f' : '#2D3B2A');
  chartGananciaCultivo?.destroy();
  chartGananciaCultivo = new Chart(canvasGanancia, {
    type: 'bar',
    data: { labels: labels.length ? labels : ['Sin datos'], datasets: [{ label: 'Ganancia (S/)', data: datosGanancia, backgroundColor: datosGanancia.map(colorBarra), borderRadius: 6 }] },
    options: {
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => formatearMoneda(c.parsed.y) } } },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { callback: (v) => `${v < 0 ? '-' : ''}S/ ${Math.abs(v)}` } } },
    },
  });

  // Donut + lista de detalle: el color solo no dice cuánto es cada costo, así que la leyenda lleva
  // monto y porcentaje (y sirve de alternativa de texto al gráfico).
  const costos = calcularDistribucionCostos(registros);
  const nombresCostos = ['Semillas', 'Insumos', 'Mano de obra', 'Envío'];
  const valoresCostos = [costos.semillas, costos.insumos, costos.manoObra, costos.envio];
  const totalCostos = valoresCostos.reduce((suma, v) => suma + v, 0);
  chartCostos?.destroy();
  chartCostos = new Chart(canvasCostos, {
    type: 'doughnut',
    data: { labels: nombresCostos, datasets: [{ data: valoresCostos, backgroundColor: COLORES_COSTOS, borderColor: '#fff', borderWidth: 2 }] },
    options: {
      cutout: '62%',
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${formatearMoneda(c.parsed)} (${totalCostos ? Math.round((c.parsed / totalCostos) * 100) : 0}%)` } },
      },
    },
  });
  renderLeyendaCostos(nombresCostos, valoresCostos);
}

function renderLeyendaCostos(nombres, valores) {
  const cont = document.getElementById('costos-leyenda');
  if (!cont) return;
  const total = valores.reduce((suma, v) => suma + v, 0);
  if (!total) {
    cont.innerHTML = '<li class="muted">Aún no hay costos registrados.</li>';
    return;
  }
  cont.innerHTML = nombres.map((nombre, i) => `
    <li>
      <span class="costos-chip" style="background:${COLORES_COSTOS[i]}" aria-hidden="true"></span>
      <span class="costos-nombre">${nombre}</span>
      <span class="costos-monto">${formatearMoneda(valores[i])}</span>
      <span class="costos-pct">${Math.round((valores[i] / total) * 100)}%</span>
    </li>`).join('') + `
    <li class="costos-total"><span class="costos-nombre">Total</span><span class="costos-monto">${formatearMoneda(total)}</span></li>`;
}

function renderTablaUltimasSiembras(registros) {
  if (!registros.length) {
    return '<p class="muted">Todavía no registraste ninguna siembra.</p>';
  }
  const chacras = Estado.chacras || [];
  const ultimas = [...registros]
    .filter((r) => r.fecha_siembra)
    .sort((a, b) => new Date(b.fecha_siembra) - new Date(a.fecha_siembra))
    .slice(0, 5);
  if (!ultimas.length) return '<p class="muted">Todavía no registraste ninguna siembra.</p>';
  const filas = ultimas.map((r) => {
    const chacra = chacras.find((c) => c.id === r.chacra_id);
    const infoEstado = BADGES_ESTADO_REGISTRO[r.estado] || BADGES_ESTADO_REGISTRO.planificado;
    return `
      <tr>
        <td>${formatearFechaCorta(r.fecha_siembra)}</td>
        <td>${escapeAttr(r.cultivo)}</td>
        <td>${chacra ? escapeAttr(chacra.codigo) : '—'}</td>
        <td><span class="pill pill-estado ${infoEstado.clase}"><i class="ti ti-${infoEstado.icono}"></i> ${infoEstado.etiqueta}</span></td>
      </tr>`;
  }).join('');
  return `
    <table class="tabla-simple">
      <thead><tr><th>Fecha</th><th>Cultivo</th><th>Chacra</th><th>Estado</th></tr></thead>
      <tbody>${filas}</tbody>
    </table>`;
}

// Activa una de las 5 secciones dedicadas del Panel Productor (Inicio / Mis Chacras / Borradores
// / Mis Productos / Gestión Económica). Los gráficos de "economia" se dibujan recién la primera
// vez que se abre esa pestaña (si se dibujaran de entrada, mientras el panel está con
// display:none, Chart.js los mediría con 0x0 y quedarían rotos).
let graficosEconomiaProductorListos = false;
function activarTabProductor(nombreTab) {
  document.querySelectorAll('.productor-tab-btn').forEach((b) => marcarBotonActivo(b, b.dataset.tab === nombreTab));
  document.querySelectorAll('.productor-tab-panel').forEach((p) => p.classList.toggle('active', p.dataset.tabPanel === nombreTab));
  if (nombreTab === 'chacras') {
    setTimeout(() => {
      mapasChacras.forEach((m) => m.invalidateSize());
      mapaUbicacionChacra?.mapa.invalidateSize();
    }, 60);
  }
  if (nombreTab === 'economia') {
    if (!graficosEconomiaProductorListos) {
      dibujarGraficosEconomiaProductor(Estado.registrosProduccion || []);
      graficosEconomiaProductorListos = true;
    } else {
      chartGananciaCultivo?.resize();
      chartCostos?.resize();
    }
  }
}

function renderFormRegistroProduccion() {
  const chacras = Estado.chacras || [];
  const opcionesChacra = chacras.length
    ? `<option value="">Elige una chacra</option>` + chacras.map((c) => `<option value="${escapeAttr(c.id)}">${escapeAttr(c.codigo)}${c.nombre ? ' — ' + escapeAttr(c.nombre) : ''}</option>`).join('')
    : '';
  return `
    <div class="card-panel">
      <h4><i class="ti ti-seedling"></i> Registrar nueva siembra</h4>
      <form id="form-registro-produccion" class="form-grid">
        <label>Cultivo
          <input type="text" id="produccion-cultivo" required maxlength="80" placeholder="Ej. Papa Nativa">
        </label>
        <div id="produccion-precio-referencia" class="produccion-precio-ref hidden"></div>
        <label>Chacra
          <select id="produccion-chacra" required ${chacras.length ? '' : 'disabled'}>${opcionesChacra}</select>
        </label>
        ${chacras.length ? '' : `
        <p class="mapa-ayuda" id="produccion-sin-chacras-aviso"><i class="ti ti-alert-circle"></i> No tienes chacras registradas.
          <button type="button" class="btn btn-outline btn-sm" id="btn-ir-a-chacras">Registra una chacra</button> antes de continuar.
        </p>`}
        <label>Número de parcelas
          <input type="number" id="produccion-parcelas" min="1" max="1000" step="1" required placeholder="1">
        </label>
        <label>Ubicación de la cosecha (opcional)
          <input type="text" id="produccion-ubicacion" placeholder="Ej. Parcela norte">
        </label>
        <label>Costo de semillas (S/)
          <input type="number" id="produccion-costo-semillas" min="0" max="9999999.99" step="0.01" value="0">
        </label>
        <label>Costo de insumos (S/)
          <input type="number" id="produccion-costo-insumos" min="0" max="9999999.99" step="0.01" value="0">
        </label>
        <label>Unidad de medida
          <select id="produccion-unidad">${opcionesUnidadMedidaHtml([], { soloBase: true })}</select>
        </label>
        <label id="produccion-equivalencia-bloque" class="hidden">Equivalencia a kg (1 unidad = X kg)
          <input type="number" id="produccion-equivalencia" min="0.0001" max="5000" step="0.0001" placeholder="Ej. 11.5">
        </label>
        <label>Fecha de siembra
          <input type="date" id="produccion-fecha-siembra" required min="2000-01-01">
        </label>
        <label>Fecha estimada de cosecha
          <input type="date" id="produccion-fecha-cosecha-estimada" required>
        </label>
        <button type="submit" class="btn btn-primary">Registrar siembra</button>
      </form>
    </div>`;
}

// Máquina de estados real de RegistroProduccion (ver productores/models.py): planificado ->
// pendiente_verificacion -> aprobado | rechazado -> pendiente_verificacion (reenvío). "cosechado"
// nunca debería llegar acá (completar_cosecha salta directo a pendiente_verificacion), pero
// queda como fallback defensivo si algún dato viejo lo tuviera.
const BADGES_ESTADO_REGISTRO = {
  planificado: { clase: 'pill-pendiente', icono: 'seedling', etiqueta: 'Planificado' },
  pendiente_verificacion: { clase: 'pill-pendiente', icono: 'hourglass', etiqueta: 'En revisión' },
  aprobado: { clase: 'pill-aprobado', icono: 'circle-check', etiqueta: 'Aprobado' },
  rechazado: { clase: 'pill-rechazado', icono: 'circle-x', etiqueta: 'Rechazado' },
};

function renderTarjetaRegistroProduccion(r, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  const esPlanificado = r.estado === 'planificado';
  const esRechazado = r.estado === 'rechazado';
  const infoEstado = BADGES_ESTADO_REGISTRO[r.estado] || BADGES_ESTADO_REGISTRO.planificado;
  const badge = `<span class="pill pill-estado ${infoEstado.clase}"><i class="ti ti-${infoEstado.icono}"></i> ${infoEstado.etiqueta}</span>`;

  const datosBase = `
    <div class="perfil-datos-grid">
      <div class="perfil-dato"><span class="perfil-dato-label">Parcelas</span><span class="perfil-dato-valor">${r.numero_parcelas}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Ubicación</span><span class="perfil-dato-valor">${escapeAttr(r.ubicacion_cosecha || '—')}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Siembra</span><span class="perfil-dato-valor">${formatearFechaCorta(r.fecha_siembra)}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Cosecha estimada</span><span class="perfil-dato-valor">${formatearFechaCorta(r.fecha_cosecha_estimada)}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Costo total</span><span class="perfil-dato-valor">${formatearMoneda(r.costo_total)}</span></div>
    </div>`;

  let bloqueEstado;
  if (esPlanificado || esRechazado) {
    // Reenvío tras rechazo = el MISMO mini-form y el MISMO endpoint (completar-cosecha) que ya
    // usa "planificado" — el backend ya acepta estado in ('planificado', 'rechazado') para esta
    // transición (ver productores/main.py). Precargado con lo ya guardado para que el productor
    // solo corrija lo que causó el rechazo, no vuelva a escribir todo desde cero.
    const valorCantidad = r.cantidad_cosechada != null ? Number(r.cantidad_cosechada) : '';
    const valorFecha = r.fecha_cosecha_real ? new Date(r.fecha_cosecha_real).toISOString().slice(0, 10) : '';
    const valorManoObra = r.costo_mano_obra != null ? Number(r.costo_mano_obra) : 0;
    const valorEnvio = r.costo_envio != null ? Number(r.costo_envio) : 0;

    bloqueEstado = `
      ${esRechazado && r.motivo_rechazo ? `<p class="verificador-historial-motivo"><i class="ti ti-message-circle"></i> ${escapeAttr(r.motivo_rechazo)}</p>` : ''}
      <button type="button" class="btn btn-outline btn-sm btn-toggle-completar-cosecha" data-id="${r.id}">${esRechazado ? 'Corregir y reenviar' : 'Completar cosecha'}</button>
      <form class="form-grid form-completar-cosecha hidden" data-id="${r.id}">
        <label>Cantidad cosechada (${escapeAttr(r.unidad_medida)})
          <input type="number" name="cantidad_cosechada" min="0.01" max="1000000" step="0.01" required value="${valorCantidad}">
        </label>
        <label>Fecha real de cosecha
          <input type="date" name="fecha_cosecha_real" required min="${String(r.fecha_siembra || '').slice(0, 10)}" max="${new Date().toISOString().slice(0, 10)}" value="${valorFecha}">
        </label>
        <label>Costo de mano de obra final (S/)
          <input type="number" name="costo_mano_obra" min="0" max="9999999.99" step="0.01" value="${valorManoObra}" required>
        </label>
        <label>Costo de envío final (S/)
          <input type="number" name="costo_envio" min="0" max="9999999.99" step="0.01" value="${valorEnvio}" required>
        </label>
        <button type="submit" class="btn btn-primary btn-sm">${esRechazado ? 'Reenviar a verificación' : 'Confirmar cosecha'}</button>
      </form>`;
  } else if (r.ingreso_estimado != null) {
    bloqueEstado = `
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Cosechado</span><span class="perfil-dato-valor">${Number(r.cantidad_cosechada).toFixed(2)} ${escapeAttr(r.unidad_medida)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Precio de referencia</span><span class="perfil-dato-valor">${formatearMoneda(r.precio_referencia_kg)}/kg</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Ingreso estimado</span><span class="perfil-dato-valor">${formatearMoneda(r.ingreso_estimado)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Ganancia</span><span class="perfil-dato-valor ${r.ganancia_estimada >= 0 ? 'ganancia-positiva' : 'ganancia-negativa'}">${formatearMoneda(r.ganancia_estimada)} (${r.margen_porcentaje}%)</span></div>
      </div>`;
  } else {
    bloqueEstado = `
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Cosechado</span><span class="perfil-dato-valor">${Number(r.cantidad_cosechada).toFixed(2)} ${escapeAttr(r.unidad_medida)}</span></div>
      </div>
      <p class="produccion-sin-calculo"><i class="ti ti-info-circle"></i> No se pudo calcular la ganancia: ${escapeAttr(r.motivo_sin_calculo || 'motivo desconocido')}.</p>`;
  }

  return `
    <div class="card-panel produccion-card" style="animation-delay:${retraso}ms">
      <div class="produccion-card-header">
        <h4>${escapeAttr(r.cultivo)}</h4>
        ${badge}
      </div>
      ${datosBase}
      ${bloqueEstado}
    </div>`;
}

function renderProduccionSeccion(registros) {
  const lista = registros.length
    ? registros.map(renderTarjetaRegistroProduccion).join('')
    : '<p class="muted" style="text-align:center;padding:16px 0;">Aún no registraste ninguna siembra.</p>';

  return `
    <div class="produccion-layout">
      ${renderFormRegistroProduccion()}
      <div id="produccion-lista" class="produccion-lista">${lista}</div>
    </div>`;
}

function renderPerfilRepartidor(extra) {
  if (!extra) {
    return `
      <div class="perfil-cta-completar">
        <span class="empty-state-icon"><i class="ti ti-truck"></i></span>
        <p><strong>Aún no completaste tu perfil de repartidor</strong></p>
        <p class="muted">Créalo para empezar a recibir propuestas de envío.</p>
        <button type="button" class="btn btn-primary" id="btn-perfil-ir-envios">Completar perfil de repartidor</button>
      </div>`;
  }
  return `
    <section class="card-panel perfil-tarjeta">
      <h3><i class="ti ti-truck"></i> Datos de repartidor</h3>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">DNI</span><span class="perfil-dato-valor perfil-dato-mono">${escapeAttr(ocultarDni(extra.dni))}</span></div>
      </div>
      ${renderBannerDisponibilidad(extra.estado_disponibilidad)}
    </section>`;
}

function renderPerfil(usuario, extra, registrosProduccion) {
  const rolInfo = ROL_INFO_PERFIL[usuario.rol] || { icono: '<i class="ti ti-user"></i>', texto: usuario.rol || 'Usuario', descripcion: '' };

  const cabecera = `
    <header class="perfil-hero panel-header-hero">
      <div class="perfil-avatar" aria-hidden="true">${escapeAttr((usuario.nombre || '?').charAt(0).toUpperCase())}</div>
      <div class="perfil-hero-info">
        <span class="perfil-hero-etiqueta">Mi perfil</span>
        <h2>${escapeAttr(usuario.nombre)}</h2>
        <div class="perfil-hero-meta">
          <span class="perfil-email"><i class="ti ti-mail" aria-hidden="true"></i> ${escapeAttr(usuario.email)}</span>
          <span class="perfil-rol-badge">${rolInfo.icono} ${rolInfo.texto}</span>
        </div>
      </div>
      <button type="button" class="btn btn-outline" id="btn-perfil-logout"><i class="ti ti-logout"></i> Cerrar sesión</button>
    </header>`;

  let principal;
  let siembras = '';
  if (usuario.rol === 'productor') {
    principal = renderPerfilProductor(extra, registrosProduccion);
    if (extra) {
      siembras = `
        <section class="perfil-siembras">
          <h3 class="perfil-titulo-seccion"><i class="ti ti-plant-2" aria-hidden="true"></i> Mis siembras</h3>
          ${renderProduccionSeccion(registrosProduccion || [])}
        </section>`;
    }
  } else if (usuario.rol === 'repartidor') {
    principal = renderPerfilRepartidor(extra);
  } else {
    principal = renderPerfilCuenta(usuario, rolInfo);
  }

  return `
    ${cabecera}
    <div class="perfil-layout">
      <div class="perfil-principal">${principal}</div>
      <aside class="perfil-lateral">${renderPerfilAccesos(usuario.rol)}</aside>
    </div>
    ${siembras}`;
}

async function cargarPerfil() {
  if (!Estado.token) {
    cambiarVista('inicio');
    return;
  }
  const cont = document.getElementById('perfil-contenido');
  cont.innerHTML = renderSkeletonPerfil();

  let usuario;
  try {
    usuario = await Api.usuarios.obtener(Estado.usuarioId);
  } catch (err) {
    manejarError(err, 'cargar tu perfil');
    cont.innerHTML = '<p class="muted">No se pudo cargar tu perfil.</p>';
    return;
  }

  let extra = null;
  let registrosProduccion = [];
  if (usuario.rol === 'productor') {
    try { extra = await Api.productores.miPerfil(); } catch { /* aún no tiene perfil de productor */ }
    if (extra) {
      try { registrosProduccion = await Api.productores.produccion.listarMe(); Estado.registrosProduccion = registrosProduccion; } catch (err) { manejarError(err, 'cargar tu gestión económica'); }
      try { Estado.chacras = await Api.chacras.misChacras(); } catch (err) { manejarError(err, 'cargar tus chacras'); }
    }
  } else if (usuario.rol === 'repartidor') {
    try { extra = await Api.repartidores.miPerfil(); } catch { /* aún no tiene perfil de repartidor */ }
  }

  cont.innerHTML = renderPerfil(usuario, extra, registrosProduccion);

  if (usuario.rol === 'productor' && extra && hayCoordenadas(extra.latitud, extra.longitud)) {
    crearMapaSoloLectura('mapa-perfil-productor', extra.latitud, extra.longitud, '<i class="ti ti-map-pin"></i>', '#2d6a4f', escapeAttr(extra.nombre || 'Tu chakra'));
  }
}

// ============ AUTENTICACIÓN ============
// ---- Modal de acceso: errores dentro del formulario, estado de carga, foco y teclado ----
function mostrarErrorAuth(idAlerta, mensaje) {
  const alerta = document.getElementById(idAlerta);
  if (!alerta) return;
  alerta.innerHTML = `<i class="ti ti-alert-circle" aria-hidden="true"></i><span>${escapeAttr(mensaje)}</span>`;
  alerta.classList.remove('hidden'); // role="alert": los lectores de pantalla lo anuncian al aparecer
}

function limpiarErroresAuth() {
  document.querySelectorAll('.auth-alerta').forEach((alerta) => {
    alerta.classList.add('hidden');
    alerta.textContent = '';
  });
}

// El mensaje de la API va tal cual si es legible; un JSON crudo (validación de FastAPI) se cambia por
// un texto genérico.
function mensajeErrorAuth(err, respaldo) {
  const mensaje = String(err?.message || '').trim();
  return mensaje && !/^[[{]/.test(mensaje) ? mensaje : respaldo;
}

function marcarEnviandoAuth(btn, enviando, textoOcupado) {
  const texto = btn.querySelector('.auth-enviar-texto');
  if (enviando) {
    btn.dataset.texto = texto.textContent;
    texto.textContent = textoOcupado;
  } else if (btn.dataset.texto) {
    texto.textContent = btn.dataset.texto;
  }
  btn.disabled = enviando;
  btn.classList.toggle('cargando', enviando);
  btn.setAttribute('aria-busy', String(enviando));
}

function enfocarPrimerCampoAuth() {
  document.querySelector('#modal-auth .auth-form:not(.hidden) input:not([type="radio"]):not([hidden])')?.focus();
}

// Tab no se escapa de un diálogo abierto: en el último elemento enfocable vuelve al primero, y con
// Mayús+Tab en el primero salta al último. Lo comparten el modal de acceso y el panel de búsqueda.
function atraparFoco(e, raiz) {
  if (e.key !== 'Tab') return;
  const enfocables = [...raiz.querySelectorAll('button, input, select, textarea, [href]')]
    .filter((el) => !el.disabled && !el.hidden && el.offsetParent !== null && !(el.type === 'radio' && !el.checked));
  if (!enfocables.length) return;
  const primero = enfocables[0];
  const ultimo = enfocables[enfocables.length - 1];
  if (e.shiftKey && document.activeElement === primero) {
    e.preventDefault();
    ultimo.focus();
  } else if (!e.shiftKey && document.activeElement === ultimo) {
    e.preventDefault();
    primero.focus();
  }
}

// Escape cierra el modal de acceso y Tab no se escapa de él.
function manejarTecladoModalAuth(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    cerrarModal('modal-auth');
    return;
  }
  atraparFoco(e, document.getElementById('modal-auth'));
}

async function manejarLogin(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  limpiarErroresAuth();
  marcarEnviandoAuth(btn, true, 'Ingresando…');
  try {
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    const resp = await Api.usuarios.login({ email, password });
    await iniciarSesionConToken(resp.access_token);
    document.getElementById('form-login').reset();
    cerrarModal('modal-auth');
    cambiarVista(vistaInicialPara(Estado.rol));
    toast(`¡Bienvenido de nuevo, ${Estado.nombre}! 🌱`);
  } catch (err) {
    console.error('iniciar sesión', err);
    mostrarErrorAuth('login-alerta', mensajeErrorAuth(err, 'No se pudo iniciar sesión. Inténtalo de nuevo.'));
  } finally {
    marcarEnviandoAuth(btn, false);
  }
}

async function manejarRegistro(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  limpiarErroresAuth();
  marcarEnviandoAuth(btn, true, 'Creando cuenta…');
  try {
    const nombre = document.getElementById('registro-nombre').value.trim();
    const email = document.getElementById('registro-email').value.trim();
    const password = document.getElementById('registro-password').value;
    const rol = document.getElementById('registro-rol').value;

    const datos = { nombre, email, password, rol };
    if (rolRequiereInvitacion(rol)) {
      datos.codigo_invitacion = document.getElementById('registro-codigo-invitacion').value.trim();
    }

    await Api.usuarios.registrar(datos);
    const resp = await Api.usuarios.login({ email, password });
    Estado.nombre = nombre;
    await iniciarSesionConToken(resp.access_token);

    document.getElementById('form-registro').reset();
    actualizarCampoCodigoInvitacion(); // el reset vuelve el <select> a "comprador" — resincroniza el campo
    cerrarModal('modal-auth');
    cambiarVista(vistaInicialPara(Estado.rol));
    toast(`¡Cuenta creada! Bienvenido a Chakra Shop, ${nombre} 🎉`);
  } catch (err) {
    console.error('crear la cuenta', err);
    mostrarErrorAuth('registro-alerta', mensajeErrorAuth(err, 'No se pudo crear la cuenta. Revisa los datos e inténtalo de nuevo.'));
  } finally {
    marcarEnviandoAuth(btn, false);
  }
}

// ---- Login/registro con Google (POST /usuarios/oauth/google) ----
// Un solo botón de Google por tab (login/registro), ambos apuntando al mismo callback: la API
// de Identity Services no distingue "de qué botón vino" el credential, así que
// manejarCredencialGoogle() mira qué tab está activo en ESE momento para decidir qué hacer con
// él — evita inicializar dos veces o mantener dos callbacks separados para el mismo id_token.
function inicializarGoogleSignIn() {
  if (!window.google?.accounts?.id) return; // script de Google no cargó (bloqueado, sin red, etc.) — sin esto, no hay botón, pero el resto del modal sigue funcionando igual
  google.accounts.id.initialize({ client_id: GOOGLE_CLIENT_ID, callback: manejarCredencialGoogle });

  // Ancho fijo, no medido en el momento: el modal está oculto (display:none) cuando esto corre
  // (llamado una sola vez desde iniciar(), antes de que el usuario abra nada), así que
  // getBoundingClientRect() daría 0 — 380px encaja bien dentro del ancho de contenido real del
  // modal (.modal tiene max-width:480px con 28px de padding a cada lado).
  ['google-btn-login', 'google-btn-registro'].forEach((id) => {
    const contenedor = document.getElementById(id);
    if (!contenedor) return;
    google.accounts.id.renderButton(contenedor, {
      theme: 'outline', size: 'large', shape: 'rectangular', text: 'continue_with', width: 380,
    });
  });
}

// El botón de Google se dibujó una sola vez al arrancar, con el modal oculto y un ancho fijo de 380px
// (que en un móvil angosto se sale del formulario). Al abrir el modal o cambiar de pestaña se vuelve
// a dibujar con el ancho real disponible (Google admite entre 200 y 400px), solo si ese ancho cambió.
function ajustarBotonesGoogle() {
  if (!window.google?.accounts?.id) return;
  ['google-btn-login', 'google-btn-registro'].forEach((id) => {
    const contenedor = document.getElementById(id);
    if (!contenedor || !contenedor.offsetWidth) return; // pestaña oculta: se ajusta cuando se muestre
    const ancho = Math.max(200, Math.min(400, Math.floor(contenedor.clientWidth)));
    if (contenedor.dataset.ancho === String(ancho)) return;
    contenedor.dataset.ancho = String(ancho);
    contenedor.innerHTML = '';
    google.accounts.id.renderButton(contenedor, {
      theme: 'outline', size: 'large', shape: 'rectangular', text: 'continue_with', width: ancho,
    });
  });
}

function manejarCredencialGoogle(response) {
  const tabActivo = document.querySelector('.auth-tab.active')?.dataset.tab;
  if (tabActivo === 'registro') {
    manejarRegistroGoogle(response.credential);
  } else {
    manejarLoginGoogle(response.credential);
  }
}

async function manejarLoginGoogle(credential) {
  try {
    const resp = await Api.usuarios.oauthGoogle({ id_token: credential });
    await iniciarSesionConToken(resp.access_token);
    cerrarModal('modal-auth');
    cambiarVista(vistaInicialPara(Estado.rol));
    toast(`¡Bienvenido, ${Estado.nombre}! 🌱`);
  } catch (err) {
    if (err.status === 400) {
      // Sin cuenta previa (ni por Google ni por email) y el tab de Login no pide un rol — el
      // backend lo rechaza en vez de registrar a ciegas. Se invita a usar Registrarse en vez de
      // mostrar el 400 crudo ("Selecciona un rol antes de continuar con Google").
      toast('No encontramos una cuenta con ese Google. Usa "Registrarse" para crear una.', 'error');
      cambiarTabAuth('registro');
      return;
    }
    manejarError(err, 'iniciar sesión con Google');
  }
}

async function manejarRegistroGoogle(credential) {
  const rol = document.getElementById('registro-rol').value;
  const datos = { id_token: credential, rol };
  if (rolRequiereInvitacion(rol)) {
    datos.codigo_invitacion = document.getElementById('registro-codigo-invitacion').value.trim();
  }

  try {
    const resp = await Api.usuarios.oauthGoogle(datos);
    await iniciarSesionConToken(resp.access_token);
    document.getElementById('form-registro').reset();
    actualizarCampoCodigoInvitacion(); // el reset vuelve el <select> a "comprador" — resincroniza el campo
    cerrarModal('modal-auth');
    cambiarVista(vistaInicialPara(Estado.rol));
    toast(`¡Cuenta creada! Bienvenido a Chakra Shop, ${Estado.nombre} 🎉`);
  } catch (err) {
    manejarError(err, 'crear la cuenta con Google');
  }
}

// ============ EVENTOS ============
function inicializarEventos() {
  document.querySelectorAll('[data-nav]').forEach((el) => {
    el.addEventListener('click', () => cambiarVista(el.dataset.nav));
  });

  document.getElementById('hamburger').addEventListener('click', () => {
    document.getElementById('nav-links').classList.toggle('open');
  });

  // ---- Navbar dinámico: transparente sobre el hero, blanco sólido al pasarlo ----
  actualizarUmbralNavbarScroll();
  actualizarNavbarScroll();
  window.addEventListener('scroll', manejarScrollNavbar, { passive: true });
  window.addEventListener('resize', actualizarUmbralNavbarScroll);

  // ---- Barra de categorías + ícono de cuenta: hover en escritorio, tap en táctil ----
  // (nunca ambos a la vez, se pisarían; mismo criterio que ya usaba el mega-dropdown anterior)
  const esTactil = !window.matchMedia('(hover: hover)').matches;

  document.querySelectorAll('.nav-cat').forEach((navCat) => {
    const categoria = navCat.dataset.categoria;
    if (!esTactil) {
      navCat.addEventListener('mouseenter', () => abrirNavCat(categoria));
      navCat.addEventListener('mouseleave', () => cerrarNavCatConDelay(categoria));
    } else {
      navCat.querySelector('.nav-cat-trigger').addEventListener('click', (e) => {
        e.stopPropagation();
        if (navCat.classList.contains('open')) cerrarNavCat(categoria);
        else abrirNavCat(categoria);
      });
    }
  });
  document.getElementById('nav-categorias').addEventListener('click', (e) => {
    const btnProducto = e.target.closest('.nav-mega-producto');
    if (btnProducto) {
      cerrarTodosLosNavCat();
      document.getElementById('nav-links').classList.remove('open');
      cambiarVista('detalle-producto', btnProducto.dataset.id);
      return;
    }

    // "Ver todos" de la cabecera de una categoría: abre el panel de búsqueda ya filtrado a ella.
    const btnVerTodos = e.target.closest('.nav-cat-vertodo');
    if (btnVerTodos) {
      cerrarTodosLosNavCat();
      document.getElementById('nav-links').classList.remove('open');
      abrirPanelBusqueda(btnVerTodos.dataset.categoria);
      return;
    }

    const btnDescubre = e.target.closest('.nav-cat-descubre-link');
    if (btnDescubre) {
      cerrarTodosLosNavCat();
      document.getElementById('nav-links').classList.remove('open');
      const accion = btnDescubre.dataset.accion;
      if (accion === 'buscar') {
        abrirPanelBusqueda('', { explorar: true });
      } else if (accion === 'blockchain') {
        // No existe una vista dedicada a "cómo funciona" — se ancla a la tarjeta real ya existente
        // en el landing (confirmado con el usuario en vez de inventar un destino).
        cambiarVista('landing');
        document.getElementById('landing-confianza-blockchain')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  });

  const navAccount = document.getElementById('nav-account');
  if (!esTactil) {
    navAccount.addEventListener('mouseenter', abrirNavAccount);
    navAccount.addEventListener('mouseleave', cerrarNavAccountConDelay);
  } else {
    document.getElementById('btn-account').addEventListener('click', (e) => {
      e.stopPropagation();
      if (navAccount.classList.contains('open')) cerrarNavAccount();
      else abrirNavAccount();
    });
  }
  document.getElementById('nav-account-panel').addEventListener('click', (e) => {
    if (e.target.closest('.nav-account-item')) cerrarNavAccount();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.nav-cat')) cerrarTodosLosNavCat();
    if (!e.target.closest('.nav-account')) cerrarNavAccount();
  });

  document.getElementById('btn-open-login').addEventListener('click', () => {
    cambiarTabAuth('login');
    abrirModal('modal-auth');
  });
  document.getElementById('btn-open-registro').addEventListener('click', () => {
    cambiarTabAuth('registro');
    abrirModal('modal-auth');
  });
  document.getElementById('btn-logout').addEventListener('click', cerrarSesion);

  document.querySelectorAll('.modal-close').forEach((btn) => {
    btn.addEventListener('click', () => cerrarModal(btn.dataset.close));
  });
  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) cerrarModal(overlay.id);
    });
  });

  document.querySelectorAll('.auth-tab').forEach((tab) => {
    tab.addEventListener('click', () => cambiarTabAuth(tab.dataset.tab));
  });

  document.getElementById('form-login').addEventListener('submit', manejarLogin);
  document.getElementById('form-registro').addEventListener('submit', manejarRegistro);
  document.getElementById('registro-rol').addEventListener('change', actualizarCampoCodigoInvitacion);

  // Modal de acceso: mostrar/ocultar contraseña, enlaces entre pestañas, tarjetas de rol y teclado.
  const modalAuth = document.getElementById('modal-auth');
  modalAuth.addEventListener('click', (e) => {
    const ojo = e.target.closest('.auth-ojo');
    if (ojo) {
      const campo = document.getElementById(ojo.dataset.ojo);
      const mostrar = campo.type === 'password';
      campo.type = mostrar ? 'text' : 'password';
      ojo.setAttribute('aria-pressed', String(mostrar));
      ojo.setAttribute('aria-label', mostrar ? 'Ocultar contraseña' : 'Mostrar contraseña');
      ojo.innerHTML = `<i class="ti ti-eye${mostrar ? '-off' : ''}"></i>`;
      return;
    }
    const cambio = e.target.closest('[data-cambiar-tab]');
    if (cambio) {
      cambiarTabAuth(cambio.dataset.cambiarTab);
      enfocarPrimerCampoAuth();
    }
  });
  document.querySelectorAll('input[name="registro-rol-opcion"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      document.getElementById('registro-rol').value = radio.value;
      actualizarCampoCodigoInvitacion();
    });
  });
  modalAuth.addEventListener('input', limpiarErroresAuth);
  modalAuth.addEventListener('keydown', manejarTecladoModalAuth);

  document.getElementById('btn-abrir-busqueda').addEventListener('click', () => abrirPanelBusqueda());
  document.getElementById('panel-busqueda').addEventListener('click', (e) => {
    if (e.target.id === 'panel-busqueda') cerrarPanelBusqueda();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !document.getElementById('panel-busqueda').classList.contains('hidden')) {
      cerrarPanelBusqueda();
    }
  });
  document.getElementById('input-busqueda-panel').addEventListener('input', buscarEnPanel);
  // Teclado del panel de búsqueda: Enter abre el primer resultado, la flecha hacia abajo baja a los
  // resultados y Tab no se escapa del panel mientras está abierto.
  document.getElementById('input-busqueda-panel').addEventListener('keydown', (e) => {
    const primero = document.querySelector('#search-panel-grid .nav-mega-producto');
    if (e.key === 'Enter' && primero) { e.preventDefault(); primero.click(); }
    if (e.key === 'ArrowDown' && primero) { e.preventDefault(); primero.focus(); }
  });
  document.getElementById('panel-busqueda').addEventListener('keydown', (e) => atraparFoco(e, e.currentTarget));
  document.getElementById('btn-limpiar-busqueda').addEventListener('click', () => {
    const input = document.getElementById('input-busqueda-panel');
    input.value = '';
    input.focus();
    buscarEnPanel();
  });
  document.getElementById('search-panel-chips').addEventListener('click', (e) => {
    const chip = e.target.closest('.search-chip');
    if (!chip) return;
    busquedaCategoria = chip.dataset.categoria;
    busquedaExplorando = true; // elegir una categoría (o "Todo") es explorar el catálogo, no ver recomendaciones
    marcarChipsBusqueda();
    actualizarResultadosBusqueda();
  });
  document.getElementById('search-panel-orden').addEventListener('change', (e) => {
    busquedaOrden = e.target.value;
    actualizarResultadosBusqueda();
  });
  document.getElementById('search-panel-empty').addEventListener('click', (e) => {
    if (!e.target.closest('#btn-ver-todo-catalogo')) return;
    document.getElementById('input-busqueda-panel').value = '';
    document.getElementById('btn-limpiar-busqueda').classList.add('hidden');
    busquedaCategoria = '';
    busquedaExplorando = true;
    marcarChipsBusqueda();
    actualizarResultadosBusqueda();
  });
  document.getElementById('search-panel-grid').addEventListener('click', (e) => {
    const tarjeta = e.target.closest('.nav-mega-producto');
    if (tarjeta) {
      cerrarPanelBusqueda();
      cambiarVista('detalle-producto', tarjeta.dataset.id);
    }
  });
  document.getElementById('search-panel-populares-lista').addEventListener('click', (e) => {
    const item = e.target.closest('.search-panel-popular-item');
    if (!item) return;
    const input = document.getElementById('input-busqueda-panel');
    input.value = item.dataset.termino;
    input.focus();
    buscarEnPanel();
  });

  document.querySelectorAll('.landing-banner-tarjetas').forEach((cont) => {
    cont.addEventListener('click', (e) => {
      const tarjeta = e.target.closest('.landing-banner-tarjeta');
      if (tarjeta && tarjeta.dataset.id) cambiarVista('detalle-producto', tarjeta.dataset.id);
    });
  });

  document.querySelectorAll('[data-landing-cta]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.landingCta === 'registro') abrirRegistroConRol();
      if (btn.dataset.landingCta === 'explorar') abrirPanelBusqueda();
    });
  });
  inicializarBannerCinematico();
  inicializarControlVideoLanding();
  document.querySelectorAll('[data-landing-registro]').forEach((btn) => {
    btn.addEventListener('click', () => abrirRegistroConRol(btn.dataset.landingRegistro));
  });

  // El mega-dropdown de categorías (barra de navegación) ya resuelve esta navegación con productos
  // reales — en vez de duplicar esa lógica, estos botones simplemente abren ese mismo dropdown.
  document.querySelectorAll('[data-landing-categoria]').forEach((btn) => {
    btn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
      setTimeout(() => abrirNavCat(btn.dataset.landingCategoria), 350);
    });
  });

  document.getElementById('landing-carousel-prev').addEventListener('click', () => desplazarCarrusel(-1));
  document.getElementById('landing-carousel-next').addEventListener('click', () => desplazarCarrusel(1));
  document.getElementById('landing-productos-carousel').addEventListener('scroll', actualizarProgresoCarrusel, { passive: true });
  document.getElementById('landing-productos-carousel').addEventListener('click', (e) => {
    const tarjeta = e.target.closest('.landing-carousel-tarjeta');
    if (tarjeta) cambiarVista('detalle-producto', tarjeta.dataset.id);
  });

  document.getElementById('btn-cart').addEventListener('click', () => {
    abrirModal('modal-pedido');
    resetCheckout();
  });
  document.getElementById('carrito-items').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-accion]');
    if (!btn) return;
    const { accion, id, unidad } = btn.dataset;
    const unidadNormalizada = unidad || null; // dataset siempre da string; '' -> null (unidad base)
    if (accion === 'mas') cambiarCantidadCarrito(id, 1, unidadNormalizada);
    if (accion === 'menos') cambiarCantidadCarrito(id, -1, unidadNormalizada);
    if (accion === 'quitar') quitarDelCarrito(id, unidadNormalizada);
  });

  document.getElementById('btn-usar-mi-ubicacion').addEventListener('click', usarMiUbicacionDestino);
  document.getElementById('btn-checkout-editar-entrega').addEventListener('click', () => irPasoCheckout(1));
  document.getElementById('btn-carrito-explorar').addEventListener('click', () => {
    cerrarModal('modal-pedido');
    abrirPanelBusqueda('', { explorar: true });
  });
  document.getElementById('modal-pedido').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      cerrarModal('modal-pedido');
      return;
    }
    atraparFoco(e, e.currentTarget);
  });
  document.getElementById('btn-checkout-paso1-siguiente').addEventListener('click', irAPaso2);
  document.getElementById('btn-checkout-paso2-volver').addEventListener('click', () => irPasoCheckout(1));
  document.getElementById('btn-checkout-pagar').addEventListener('click', manejarClickPagar);
  document.getElementById('btn-checkout-finalizar').addEventListener('click', () => {
    cerrarModal('modal-pedido');
    resetCheckout();
    cambiarVista('mis-pedidos');
  });
  document.querySelectorAll('input[name="metodo-pago"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      document.querySelectorAll('.metodo-pago-card').forEach((c) => c.classList.remove('seleccionado'));
      radio.closest('.metodo-pago-card').classList.add('seleccionado');
    });
  });

  document.getElementById('btn-refrescar-pedidos').addEventListener('click', cargarMisPedidos);
  document.getElementById('btn-buscar-pedido').addEventListener('click', buscarPedidoPorId);
  document.getElementById('input-buscar-pedido').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); buscarPedidoPorId(); }
  });
  document.getElementById('pedidos-tabs').addEventListener('click', (e) => {
    const tab = e.target.closest('.pedidos-tab');
    if (!tab) return;
    filtroPedidoActual = tab.dataset.filtro;
    filtrarYRenderizarPedidos();
  });
  document.getElementById('pedidos-list').addEventListener('click', (e) => {
    const calificar = e.target.closest('.btn-calificar-producto');
    if (calificar) { cambiarVista('detalle-producto', calificar.dataset.productoId); return; }
    const reintentar = e.target.closest('.btn-reintentar-pago');
    if (reintentar) { reintentarPago(reintentar.dataset.pedidoId, Number(reintentar.dataset.monto), Number(reintentar.dataset.envio)); return; }
    const certPedido = e.target.closest('.btn-ver-certificado-pedido');
    if (certPedido) { alternarCertificadoPedido(certPedido.dataset.pedidoId); return; }
    const copiarPin = e.target.closest('.btn-copiar-pin');
    if (copiarPin) { copiarPinEntrega(copiarPin); return; }
  });
  // Aviso "va en camino" del resumen: lleva a la tarjeta (clic o Enter/Espacio, es un role="button").
  const resumenPedidos = document.getElementById('pedidos-resumen');
  resumenPedidos.addEventListener('click', (e) => {
    const aviso = e.target.closest('.resumen-destacado-clicable');
    if (aviso) resaltarPedido(aviso.dataset.pedidoId);
  });
  resumenPedidos.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const aviso = e.target.closest('.resumen-destacado-clicable');
    if (aviso) { e.preventDefault(); resaltarPedido(aviso.dataset.pedidoId); }
  });

  document.getElementById('btn-refrescar-notificaciones').addEventListener('click', cargarNotificaciones);
  document.getElementById('btn-notif-cargar-mas').addEventListener('click', () => {
    notifMostrar += TAMANO_PAGINA_NOTIF;
    renderNotificacionesPagina();
  });
  document.getElementById('notif-filtros').addEventListener('click', (e) => {
    const boton = e.target.closest('[data-filtro]');
    if (!boton) return;
    notifFiltro = boton.dataset.filtro;
    notifMostrar = TAMANO_PAGINA_NOTIF;
    renderNotificacionesPagina();
    document.querySelector(`#notif-filtros [data-filtro="${notifFiltro}"]`)?.focus(); // el repintado reemplaza los botones
  });
  document.getElementById('notificaciones-list').addEventListener('click', (e) => {
    const boton = e.target.closest('[data-accion]');
    if (!boton) return;
    if (boton.dataset.accion === 'ver-pedido') cambiarVista('mis-pedidos');
    if (boton.dataset.accion === 'ver-producto') cambiarVista('detalle-producto', boton.dataset.id);
  });
  document.getElementById('notificaciones-empty').addEventListener('click', (e) => {
    const boton = e.target.closest('[data-notif-accion]');
    if (!boton) return;
    const accion = boton.dataset.notifAccion;
    if (accion === 'login') abrirModal('modal-auth');
    if (accion === 'explorar') abrirPanelBusqueda('', { explorar: true });
    if (accion === 'todas') {
      notifFiltro = 'todas';
      notifMostrar = TAMANO_PAGINA_NOTIF;
      renderNotificacionesPagina();
    }
  });

  document.getElementById('form-productor').addEventListener('submit', crearPerfilProductor);
  document.getElementById('btn-ubicacion-productor').addEventListener('click', usarMiUbicacionProductor);
  document.getElementById('form-chacra').addEventListener('submit', crearChacra);
  document.getElementById('btn-ubicacion-chacra').addEventListener('click', usarMiUbicacionChacra);
  document.getElementById('btn-refrescar-mis-productos').addEventListener('click', cargarMisProductos);

  // Nav de secciones dedicadas del Panel Productor — delegado sobre .productor-tabs (markup
  // estático en index.html, nunca se reemplaza por innerHTML, así que un solo listener alcanza
  // para toda la vida de la página).
  document.querySelector('.productor-tabs')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.productor-tab-btn');
    if (btn) activarTabProductor(btn.dataset.tab);
  });

  // Accesos rápidos del resumen de "Panel del Productor" — delegados sobre #productor-resumen
  // (su contenido se reemplaza por completo en cada iniciarPanelProductor(), así que un
  // addEventListener directo sobre los botones no sobreviviría al primer refresco).
  document.getElementById('productor-resumen').addEventListener('click', (e) => {
    if (e.target.closest('#btn-resumen-publicar')) {
      activarTabProductor('borradores');
      document.querySelector('.productor-tabs')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (e.target.closest('#btn-resumen-chacra')) {
      activarTabProductor('chacras');
      document.querySelector('.productor-tabs')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (e.target.closest('#btn-resumen-siembra')) {
      // "Registrar siembra" vive en Mi Perfil, no en este panel — cambiamos de vista y
      // esperamos a que cargarPerfil() termine de pintar antes de hacer scroll (ver
      // esperarElementoYScroll).
      cambiarVista('perfil');
      esperarElementoYScroll('form-registro-produccion');
      return;
    }
    if (e.target.closest('#btn-resumen-entregas-directas')) {
      document.getElementById('entregas-directas-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
  });

  // Entregas directas pendientes — delegado sobre #entregas-directas-lista (el contenido se
  // reemplaza por completo en cada cargarEntregasDirectasPendientes(), mismo criterio que
  // productor-borradores-lista de abajo).
  document.getElementById('entregas-directas-lista').addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-marcar-entregado');
    if (btn) marcarEntregaDirectaComoEntregada(btn.dataset.envioId);
  });

  // Borradores — delegado sobre #productor-borradores-lista (el contenido se reemplaza por
  // completo en cada cargarMisProductos(), así que listeners directos sobre cada tarjeta no
  // sobrevivirían a un refresco).
  document.getElementById('productor-borradores-lista').addEventListener('input', (e) => {
    if (!e.target.matches('.borrador-precio')) return;
    const productoId = e.target.closest('.pedido-card')?.dataset.productoId;
    if (!productoId) return;
    sincronizarBotonPublicarBorrador(productoId);
    actualizarPreviewBorrador(productoId);
  });
  document.getElementById('productor-borradores-lista').addEventListener('change', (e) => {
    if (!e.target.matches('.borrador-categoria')) return;
    const productoId = e.target.closest('.pedido-card')?.dataset.productoId;
    if (productoId) actualizarPreviewBorrador(productoId);
  });
  document.getElementById('productor-borradores-lista').addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-publicar-borrador');
    if (btn) { publicarBorrador(btn.dataset.productoId); return; }
    const btnUnidades = e.target.closest('.btn-toggle-unidades');
    if (btnUnidades) alternarUnidadesProductor(btnUnidades.dataset.id);
  });

  document.getElementById('mis-productos-grid').addEventListener('click', (e) => {
    const btnResenas = e.target.closest('.btn-toggle-resenas');
    if (btnResenas) { alternarResenasProductor(btnResenas.dataset.id); return; }
    const btnFotos = e.target.closest('.btn-toggle-fotos');
    if (btnFotos) { alternarFotosProductor(btnFotos.dataset.id); return; }
    const btnCert = e.target.closest('.btn-ver-certificacion');
    if (btnCert) { abrirModalCertificacion(btnCert.dataset.id); return; }
    const btnUnidades = e.target.closest('.btn-toggle-unidades');
    if (btnUnidades) { alternarUnidadesProductor(btnUnidades.dataset.id); return; }
  });

  // ---- Gestor de fotos: subida/eliminación delegadas (se usan tanto al publicar como en "Mis productos") ----
  document.addEventListener('click', (e) => {
    const mover = e.target.closest('.miniatura-mover-btn');
    if (mover) {
      if (mover.disabled) return;
      const productoId = mover.closest('.gestor-fotos')?.dataset.productoId;
      if (productoId) moverFotoProducto(productoId, mover.dataset.imagenId, mover.dataset.direccion);
      return;
    }
    const quitar = e.target.closest('.upload-miniatura-quitar');
    if (quitar) {
      const productoId = quitar.closest('.gestor-fotos')?.dataset.productoId;
      if (productoId) eliminarFotoProducto(productoId, quitar.dataset.imagenId);
      return;
    }
    const zona = e.target.closest('.upload-zona');
    if (zona && !zona.classList.contains('deshabilitada') && !e.target.closest('.upload-input')) {
      zona.querySelector('.upload-input')?.click();
    }
  });

  document.addEventListener('change', (e) => {
    if (!e.target.matches('.upload-input')) return;
    const productoId = e.target.closest('.gestor-fotos')?.dataset.productoId;
    if (productoId && e.target.files.length) subirFotosProducto(productoId, e.target.files);
    e.target.value = '';
  });

  // ---- Unidades de venta alternativas: delegado globalmente, mismo criterio que el gestor de
  // fotos de arriba (el contenido del panel se reemplaza por completo en cada toggle/refresco,
  // así que un listener puesto directo sobre un botón no sobreviviría). ----
  document.addEventListener('click', (e) => {
    const editar = e.target.closest('.btn-editar-unidad-alt');
    if (editar) {
      const productoId = editar.closest('.unidades-alt-gestor')?.dataset.productoId;
      if (productoId) iniciarEdicionUnidadAlternativa(productoId, editar.dataset.unidadId);
      return;
    }
    const cancelar = e.target.closest('.btn-cancelar-edicion-unidad-alt');
    if (cancelar) {
      const productoId = cancelar.closest('.unidades-alt-gestor')?.dataset.productoId;
      if (productoId) rerenderUnidadesAlternativas(productoId);
      return;
    }
    const guardar = e.target.closest('.btn-guardar-unidad-alt');
    if (guardar) {
      const productoId = guardar.closest('.unidades-alt-gestor')?.dataset.productoId;
      if (productoId) guardarEdicionUnidadAlternativa(productoId, guardar.dataset.unidadId);
      return;
    }
    const eliminar = e.target.closest('.btn-eliminar-unidad-alt');
    if (eliminar) {
      const productoId = eliminar.closest('.unidades-alt-gestor')?.dataset.productoId;
      if (productoId) eliminarUnidadAlternativa(productoId, eliminar.dataset.unidadId);
    }
  });

  document.addEventListener('submit', (e) => {
    const form = e.target.closest('.unidad-alt-form');
    if (!form) return;
    e.preventDefault();
    const productoId = form.closest('.unidades-alt-gestor')?.dataset.productoId;
    if (productoId) agregarUnidadAlternativa(productoId, form);
  });

  document.addEventListener('change', (e) => {
    const select = e.target.closest('.unidad-alt-nueva-select');
    if (select) actualizarAyudaFactorUnidadAlt(select.closest('.unidad-alt-form'));
  });

  document.addEventListener('dragover', (e) => {
    const zona = e.target.closest('.upload-zona');
    if (zona && !zona.classList.contains('deshabilitada')) {
      e.preventDefault();
      zona.classList.add('dragover');
    }
  });
  document.addEventListener('dragleave', (e) => {
    e.target.closest('.upload-zona')?.classList.remove('dragover');
  });
  document.addEventListener('drop', (e) => {
    const zona = e.target.closest('.upload-zona');
    if (!zona || zona.classList.contains('deshabilitada')) return;
    e.preventDefault();
    zona.classList.remove('dragover');
    const productoId = zona.closest('.gestor-fotos')?.dataset.productoId;
    if (productoId && e.dataTransfer.files.length) subirFotosProducto(productoId, e.dataTransfer.files);
  });

  document.getElementById('form-repartidor').addEventListener('submit', crearPerfilRepartidor);
  document.getElementById('btn-refrescar-envios').addEventListener('click', cargarGestionEnvios);
  document.getElementById('repartidor-resumen').addEventListener('click', (e) => {
    if (e.target.closest('#btn-resumen-ver-envios')) {
      document.getElementById('envios-list')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });

  // ---- Panel Verificador: navegación entre secciones + logout ----
  document.getElementById('verificador-tabs').addEventListener('click', (e) => {
    const tab = e.target.closest('.verificador-tab');
    if (tab) seleccionarSeccionVerificador(tab.dataset.vseccion);
  });
  document.getElementById('btn-verificador-logout').addEventListener('click', cerrarSesion);

  document.getElementById('verificador-cosechas-lista').addEventListener('click', (e) => {
    const aprobar = e.target.closest('.btn-aprobar-cosecha');
    if (aprobar) { aprobarCosecha(aprobar.dataset.registroId); return; }
    const rechazar = e.target.closest('.btn-rechazar-cosecha');
    if (rechazar) { abrirModalRechazarCosecha(rechazar.dataset.registroId); return; }
  });
  document.getElementById('form-rechazar-cosecha').addEventListener('submit', confirmarRechazoCosecha);

  // ---- Panel Admin: nav de secciones dedicadas + generar invitación, copiar código, refrescar, revocar ----
  document.querySelector('.admin-tabs')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.admin-tab-btn');
    if (btn) activarTabAdmin(btn.dataset.admintab);
  });
  document.getElementById('form-invitacion').addEventListener('submit', generarInvitacion);
  document.getElementById('btn-copiar-codigo').addEventListener('click', copiarCodigoInvitacion);
  document.getElementById('btn-refrescar-invitaciones').addEventListener('click', cargarInvitaciones);
  document.getElementById('admin-resumen').addEventListener('click', (e) => {
    if (e.target.closest('#btn-resumen-generar-codigo')) {
      activarTabAdmin('invitaciones');
      document.getElementById('invitacion-rol-destino')?.focus({ preventScroll: true });
      return;
    }
    if (e.target.closest('#btn-resumen-pendientes-capacidad')) {
      activarTabAdmin('capacidad');
    }
  });
  document.getElementById('invitaciones-lista').addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-revocar-invitacion');
    if (btn) revocarInvitacionDesdeLista(btn.dataset.invitacionId);
  });
  document.getElementById('btn-refrescar-productores').addEventListener('click', cargarProductoresAdmin);
  document.getElementById('admin-productores-buscar').addEventListener('input', pintarProductoresAdmin);
  document.getElementById('btn-refrescar-productos-transito').addEventListener('click', cargarProductosEnTransito);
  const listaTransito = document.getElementById('productos-transito-lista');
  listaTransito.addEventListener('input', (e) => {
    const form = e.target.closest('.form-llegada');
    if (form && e.target.name === 'cantidad') evaluarCantidadLlegada(form);
  });
  listaTransito.addEventListener('submit', (e) => {
    const form = e.target.closest('.form-llegada');
    if (!form) return;
    e.preventDefault();
    confirmarLlegadaAlmacen(form);
  });
  document.getElementById('envios-list').addEventListener('change', (e) => {
    if (e.target.matches('.foto-entrega-input')) alElegirFotoEntrega(e.target);
  });
  document.getElementById('envios-list').addEventListener('click', (e) => {
    const btnQuitarFoto = e.target.closest('.foto-quitar');
    if (btnQuitarFoto) { quitarFotoEntrega(btnQuitarFoto.closest('.foto-entrega')); return; }
    const btnCambiarFoto = e.target.closest('.foto-cambiar');
    if (btnCambiarFoto) { btnCambiarFoto.closest('.foto-entrega').querySelector('.foto-entrega-input').click(); return; }

    const btnCancelar = e.target.closest('.btn-cancelar-entrega');
    if (btnCancelar) { btnCancelar.closest('.form-confirmar-entrega').classList.add('hidden'); return; }

    const btn = e.target.closest('.btn-actualizar-envio');
    if (!btn) return;
    const envioId = btn.dataset.envioId;
    const select = document.querySelector(`.select-estado-envio[data-envio-id="${envioId}"]`);
    if (select.value === 'entregado') {
      // "Entregado" ya no es un simple cambio de estado — muestra el formulario de PIN + foto
      // en vez de llamar a actualizarEstadoEnvio (ver confirmarEntregaConPin).
      document.querySelector(`.form-confirmar-entrega[data-envio-id="${envioId}"]`)?.classList.remove('hidden');
      return;
    }
    actualizarEstadoEnvio(envioId, select.value);
  });
  document.getElementById('envios-list').addEventListener('submit', (e) => {
    const form = e.target.closest('.form-confirmar-entrega');
    if (!form) return;
    e.preventDefault();
    confirmarEntregaConPin(form);
  });
  document.getElementById('propuestas-list').addEventListener('click', (e) => {
    const aceptar = e.target.closest('.btn-aceptar-propuesta');
    if (aceptar) { responderPropuesta(aceptar.dataset.envioId, 'aceptar'); return; }
    const rechazar = e.target.closest('.btn-rechazar-propuesta');
    if (rechazar) { responderPropuesta(rechazar.dataset.envioId, 'rechazar'); return; }
  });

  document.getElementById('perfil-contenido').addEventListener('click', (e) => {
    if (e.target.closest('#btn-perfil-logout')) { cerrarSesion(); return; }
    if (e.target.closest('#btn-perfil-ir-panel')) { cambiarVista('panel-productor'); return; }
    if (e.target.closest('#btn-perfil-ir-envios')) { cambiarVista('gestion-envios'); return; }
    const acceso = e.target.closest('[data-perfil-ir]');
    if (acceso) {
      if (acceso.dataset.perfilIr === 'catalogo') abrirPanelBusqueda('', { explorar: true });
      else cambiarVista(acceso.dataset.perfilIr);
      return;
    }
    if (e.target.closest('#btn-ir-a-chacras')) {
      cambiarVista('panel-productor');
      document.getElementById('form-chacra')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    const btnCompletar = e.target.closest('.btn-toggle-completar-cosecha');
    if (btnCompletar) { alternarFormCompletarCosecha(btnCompletar.dataset.id); return; }
  });

  // Gestión Económica: el formulario de siembra y las mini-formas de "completar cosecha" (una
  // por tarjeta) se re-renderizan en cada cargarPerfil(), así que van delegados sobre el
  // contenedor estable en vez de engancharse directo — igual que los botones de arriba.
  document.getElementById('perfil-contenido').addEventListener('submit', (e) => {
    if (e.target.id === 'form-registro-produccion') { crearRegistroProduccion(e); return; }
    if (e.target.matches('.form-completar-cosecha')) { completarCosechaRegistro(e, e.target.dataset.id); return; }
  });

  document.getElementById('perfil-contenido').addEventListener('change', (e) => {
    if (e.target.id === 'produccion-unidad') actualizarVisibilidadEquivalenciaProduccion();
  });
  document.getElementById('perfil-contenido').addEventListener('blur', (e) => {
    if (e.target.id === 'produccion-cultivo') mostrarPrecioReferenciaCultivo();
  }, true);
}

function cambiarTabAuth(tab) {
  document.querySelectorAll('.auth-tab').forEach((t) => {
    const activa = t.dataset.tab === tab;
    t.classList.toggle('active', activa);
    t.setAttribute('aria-selected', String(activa));
  });
  document.getElementById('form-login').classList.toggle('hidden', tab !== 'login');
  document.getElementById('form-registro').classList.toggle('hidden', tab !== 'registro');
  document.querySelector('.modal-auth')?.setAttribute('data-tab', tab); // el panel de marca cambia de mensaje según la pestaña
  limpiarErroresAuth();
  // Con el modal ya abierto, el botón de Google del formulario recién mostrado se vuelve a medir.
  if (!document.getElementById('modal-auth').classList.contains('hidden')) requestAnimationFrame(ajustarBotonesGoogle);
}

// ============ INICIO ============
function iniciar() {
  alExpirarSesion = manejarSesionExpirada;
  cargarSesionGuardada();
  actualizarUIAuth();
  renderNavCategorias();
  inicializarEventos();
  inicializarGoogleSignIn();
  cambiarVista(vistaInicialPara(Estado.rol));
  if (sesionVencidaAlIniciar) toast('Tu sesión anterior venció. Inicia sesión de nuevo para continuar.');
}

document.addEventListener('DOMContentLoaded', iniciar);
