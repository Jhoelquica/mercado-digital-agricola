// ============ ESTADO GLOBAL ============
const Estado = {
  token: null,
  usuarioId: null,
  nombre: null,
  email: null,
  rol: null,
  productos: [],
  productores: {},   // id -> { nombre, comunidad, contacto, ubicacion }
  carrito: [],        // {producto_id, nombre, precio, cantidad, stockDisponible}
  productorId: null,
  repartidorId: null,
  enviosRepartidor: [],
  imagenesPorProducto: {}, // producto_id -> [{id, url, orden}], caché en memoria para el gestor de fotos
};

const SESSION_KEY = 'agro_sesion';

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

function formatearMoneda(valor) {
  const n = Number(valor) || 0;
  return `S/ ${n.toFixed(2)}`;
}

function hashCorto(hash) {
  if (!hash) return '—';
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

const EVENTOS_CERTIFICACION = {
  cosecha_registrada: { emoji: '🌾', texto: 'Cosecha registrada' },
  certificado_productor: { emoji: '✅', texto: 'Certificado por el productor' },
  verificado_punto_venta: { emoji: '🏪', texto: 'Verificado en punto de venta' },
};

function labelEventoCertificacion(evento) {
  return EVENTOS_CERTIFICACION[evento] || { emoji: '📦', texto: evento };
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
              <div class="timeline-fecha">${formatearFecha(b.fecha)}</div>
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
  return `<p class="timeline-verificador">👤 Certificado por: ${escapeAttr(nombre)}</p>`;
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
      <h3>🔗 Certificación de trazabilidad</h3>
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
        <span class="banner-icon">⚠️</span>
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
  if (boton.classList.contains('btn-favorito-detalle')) {
    boton.innerHTML = ahoraFavorito ? '❤️ Guardado' : '🤍 Guardar';
  } else {
    boton.textContent = ahoraFavorito ? '❤️' : '🤍';
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
        <span class="upload-zona-icono">📤</span>
        <span>${lleno ? 'Alcanzaste el máximo de 5 fotos' : 'Arrastra tus fotos aquí o <strong>haz clic para elegir</strong>'}</span>
      </div>
      ${n ? `
      <div class="upload-miniaturas">
        ${ordenadas.map((img, i) => `
          <div class="upload-miniatura">
            ${i === 0 ? '<span class="miniatura-badge-principal">Principal</span>' : ''}
            <img src="${escapeAttr(img.url)}" alt="" loading="lazy">
            <button type="button" class="upload-miniatura-quitar" data-imagen-id="${img.id}" aria-label="Quitar foto">✕</button>
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
    btn.textContent = `📷 Fotos (${imagenes.length}/5)`;
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
    toast('Foto eliminada.');
  } catch (err) {
    manejarError(err, 'eliminar la foto');
  }
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

  try {
    await Promise.all([
      Api.productos.actualizarOrdenImagen(actual.id, actual.orden),
      Api.productos.actualizarOrdenImagen(vecino.id, vecino.orden),
    ]);
  } catch (err) {
    Estado.imagenesPorProducto[productoId] = snapshot;
    rerenderGestorFotos(productoId);
    manejarError(err, 'reordenar las fotos');
  }
}

const ICONOS_ESTADO = {
  pendiente: '⏳',
  pendiente_asignacion: '⏳',
  propuesto: '📨',
  asignado: '📋',
  en_camino: '🚚',
  en_transito: '🚚',
  en_ruta: '🚚',
  entregado: '✅',
  cancelado: '✖️',
  rechazado: '✖️',
  aprobado: '✅',
  error: '⚠️',
};

const ESTADOS_BADGE_CONOCIDOS = new Set([
  'pendiente', 'pendiente_asignacion', 'propuesto', 'asignado',
  'en_camino', 'en_transito', 'en_ruta', 'entregado', 'aprobado',
  'cancelado', 'rechazado', 'error',
]);

function badgeEstadoEnvio(estado) {
  const clave = (estado || 'pendiente').toLowerCase().replace(/\s+/g, '_');
  const clase = ESTADOS_BADGE_CONOCIDOS.has(clave) ? `badge-${clave}` : 'badge-default';
  const icono = ICONOS_ESTADO[clave] || '•';
  return `<span class="badge ${clase}">${icono} ${estado || 'pendiente'}</span>`;
}

const UNIDADES_MEDIDA = {
  kg: { singular: 'kg', plural: 'kg', etiqueta: 'kg' },
  unidad: { singular: 'unidad', plural: 'unidades', etiqueta: 'unidades' },
  saco: { singular: 'saco', plural: 'sacos', etiqueta: 'sacos' },
  arroba: { singular: 'arroba', plural: 'arrobas', etiqueta: 'arrobas' },
};

function infoUnidad(codigo) {
  return UNIDADES_MEDIDA[codigo] || UNIDADES_MEDIDA.unidad;
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

function renderEstrellas(promedio) {
  const llenas = Math.round(promedio || 0);
  let html = '';
  for (let i = 1; i <= 5; i++) html += i <= llenas ? '★' : '☆';
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
    <div class="skeleton" style="height:220px;border-radius:20px;margin-bottom:16px;"></div>
    <div class="skeleton skeleton-line w-40" style="margin-bottom:10px;"></div>
    <div class="skeleton skeleton-line tall w-70" style="margin-bottom:10px;"></div>
    <div class="skeleton skeleton-line w-60" style="margin-bottom:16px;"></div>
    <div class="skeleton skeleton-btn"></div>`;
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

function crearMapaSeleccionable(contenedorId, { latInicial, lngInicial, emoji = '📍', color = '#e76f51', onSeleccionar } = {}) {
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

  const marcadorOrigen = L.marker(puntoOrigen, { icon: crearIconoMarcador('🛵', '#2d6a4f') }).addTo(mapa).bindPopup('Repartidor');
  L.marker(puntoDestino, { icon: crearIconoMarcador('📍', '#e76f51') }).addTo(mapa).bindPopup('Destino');
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

function cargarSesionGuardada() {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) return;
  try {
    const datos = JSON.parse(raw);
    Object.assign(Estado, datos);
  } catch { /* sesión corrupta, se ignora */ }
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
  document.getElementById('auth-area').classList.toggle('hidden', logueado);
  document.getElementById('user-area').classList.toggle('hidden', !logueado);
  document.querySelector('.nav-productor').classList.toggle('hidden', !(logueado && Estado.rol === 'productor'));
  document.querySelector('.nav-repartidor').classList.toggle('hidden', !(logueado && Estado.rol === 'repartidor'));
  document.getElementById('footer-ctas').classList.toggle('hidden', logueado);

  if (logueado) {
    const nombre = Estado.nombre || 'Usuario';
    document.getElementById('user-name').textContent = nombre;
    document.getElementById('user-avatar').textContent = nombre.charAt(0).toUpperCase();
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

// ============ NAVEGACIÓN ============
function cambiarVista(nombre) {
  if (nombre === 'inicio') nombre = Estado.token ? 'catalogo' : 'landing';

  detenerPollingRutas();
  detenerSeguimientoRepartidor();
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  document.getElementById(`view-${nombre}`).classList.add('active');
  document.querySelectorAll('.nav-link').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.nav === nombre);
  });
  document.getElementById('nav-links').classList.remove('open');

  if (nombre === 'landing') cargarLanding();
  if (nombre === 'catalogo') cargarCatalogo();
  if (nombre === 'mis-pedidos') cargarMisPedidos();
  if (nombre === 'notificaciones') cargarNotificaciones();
  if (nombre === 'panel-productor') iniciarPanelProductor();
  if (nombre === 'gestion-envios') cargarGestionEnvios();
  if (nombre === 'perfil') cargarPerfil();
}

function abrirRegistroConRol(rol) {
  cambiarTabAuth('registro');
  const select = document.getElementById('registro-rol');
  if (rol) select.value = rol;
  abrirModal('modal-auth');
}

// ============ MODALES ============
function abrirModal(id) { document.getElementById(id).classList.remove('hidden'); }
function cerrarModal(id) { document.getElementById(id).classList.add('hidden'); }

// ============ LANDING (INVITADOS) ============
async function cargarLanding() {
  const grid = document.getElementById('landing-productos-grid');
  grid.innerHTML = renderSkeletonProductos(6);
  try {
    const productos = await Api.productos.listar();
    Estado.productos = productos;

    document.getElementById('landing-stat-productos').textContent = productos.length;
    const productoresUnicos = new Set(productos.map((p) => p.productor_id)).size;
    document.getElementById('landing-stat-productores').textContent = productoresUnicos;

    const esProductoDePrueba = (p) => /\btest\b|\bqa\b/i.test(`${p.nombre} ${p.categoria}`);
    const candidatos = productos.filter((p) => !esProductoDePrueba(p));
    const fuente = candidatos.length >= 3 ? candidatos : productos;

    const destacados = [...fuente]
      .sort((a, b) => (b.calificacion_promedio || 0) - (a.calificacion_promedio || 0) || (b.total_resenas || 0) - (a.total_resenas || 0))
      .slice(0, 6);

    grid.innerHTML = destacados.length
      ? destacados.map((p, i) => renderTarjetaProductoCatalogo(p, { indice: i })).join('')
      : '<p class="empty-state">🌾 Todavía no hay productos publicados.</p>';
  } catch (err) {
    grid.innerHTML = '';
    manejarError(err, 'cargar los productos destacados');
  }
}

// ============ CATÁLOGO ============
async function cargarCatalogo() {
  const grid = document.getElementById('productos-grid');
  document.getElementById('productos-empty').classList.add('hidden');
  grid.innerHTML = renderSkeletonProductos();
  try {
    const [productos, productores] = await Promise.all([
      Api.productos.listar(),
      Api.productores.listar(),
    ]);
    Estado.productos = productos;
    Estado.productores = {};
    productores.forEach((p) => { Estado.productores[p.id] = p; });

    poblarSelectCategorias(productos);
    poblarSelectUbicaciones(productos);
    renderProductos(productos);
  } catch (err) {
    manejarError(err, 'cargar el catálogo');
  }
}

// ============ MEGA-DROPDOWN DE CATEGORÍAS (NAVBAR) ============
// Reutiliza CATEGORIAS_PRODUCTO (definida más abajo, junto al Panel Productor) — mismas 8 categorías fijas de toda la app.
let megaCategoriaActiva = null;
let megaCerrarTimeout = null;
const megaCacheCategorias = {}; // categoria -> productos[] (evita refetch al reabrir la misma pestaña)

function renderMegaTabs() {
  const cont = document.getElementById('mega-tabs-categorias');
  cont.innerHTML = CATEGORIAS_PRODUCTO.map((c) => `
    <button type="button" class="nav-mega-tab ${c === megaCategoriaActiva ? 'active' : ''}" data-categoria="${c}">
      ${ICONOS_CATEGORIA[c]} ${NOMBRE_CATEGORIA[c]}
    </button>`).join('');
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

function renderProductoMega(p) {
  return `
    <button type="button" class="nav-mega-producto" data-id="${p.id}">
      ${renderMediaProducto(p, 'nav-mega-producto-img')}
      <span class="nav-mega-producto-nombre">${escapeAttr(p.nombre)}</span>
      <span class="nav-mega-producto-precio">${formatearMoneda(p.precio)}</span>
    </button>`;
}

function pintarProductosMega(productos) {
  const cont = document.getElementById('mega-productos-categorias');
  cont.innerHTML = productos.length
    ? productos.map(renderProductoMega).join('')
    : '<p class="muted" style="grid-column:1/-1;margin:0;">Sin productos en esta categoría por ahora.</p>';
}

async function mostrarCategoriaMega(categoria) {
  megaCategoriaActiva = categoria;
  renderMegaTabs();

  if (megaCacheCategorias[categoria]) {
    pintarProductosMega(megaCacheCategorias[categoria]);
    return;
  }

  document.getElementById('mega-productos-categorias').innerHTML = renderSkeletonMega();
  if (!Estado.productos.length) {
    try { Estado.productos = await Api.productos.listar(); } catch { /* seguimos sin preview si falla */ }
  }
  const delCategoria = Estado.productos.filter((p) => normalizarCategoria(p.categoria) === categoria).slice(0, 4);
  megaCacheCategorias[categoria] = delCategoria;

  if (megaCategoriaActiva === categoria) pintarProductosMega(delCategoria);
}

function abrirMegaCategorias() {
  clearTimeout(megaCerrarTimeout);
  document.getElementById('nav-mega-categorias').classList.add('open');
  document.getElementById('btn-mega-categorias').setAttribute('aria-expanded', 'true');
  if (!megaCategoriaActiva) mostrarCategoriaMega(CATEGORIAS_PRODUCTO[0]);
}

function cerrarMegaCategorias() {
  document.getElementById('nav-mega-categorias').classList.remove('open');
  document.getElementById('btn-mega-categorias').setAttribute('aria-expanded', 'false');
}

function cerrarMegaCategoriasConDelay() {
  clearTimeout(megaCerrarTimeout);
  megaCerrarTimeout = setTimeout(cerrarMegaCategorias, 180);
}

function poblarSelectCategorias(productos) {
  const select = document.getElementById('select-categoria');
  const actual = select.value;
  const categorias = [...new Set(productos.map((p) => p.categoria).filter(Boolean))].sort();
  select.innerHTML = '<option value="">🗂️ Todas las categorías</option>' +
    categorias.map((c) => `<option value="${escapeAttr(c)}">${iconoCategoria(c)} ${nombreCategoria(c)}</option>`).join('');
  select.value = actual;
}

function poblarSelectUbicaciones(productos) {
  const select = document.getElementById('select-ubicacion');
  const actual = select.value;
  const ubicaciones = [...new Set(
    productos
      .map((p) => Estado.productores[p.productor_id]?.ubicacion)
      .filter(Boolean)
  )].sort();
  select.innerHTML = '<option value="">Todas las ubicaciones</option>' +
    ubicaciones.map((u) => `<option value="${escapeAttr(u)}">${u}</option>`).join('');
  select.value = actual;
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
          ${favorito ? '❤️' : '🤍'}
        </button>` : ''}
      </div>
      <div class="product-card-body">
        <div class="product-card-top">
          <span class="product-tag">${iconoCategoria(p.categoria)} ${nombreCategoria(p.categoria)}</span>
          ${tieneCalificacion ? `<span class="product-rating"><span class="rating-stars">★</span> ${Number(p.calificacion_promedio).toFixed(1)} <span class="rating-count-mini">(${p.total_resenas})</span></span>` : ''}
        </div>
        <h4>${p.nombre}</h4>
        <span class="product-productor">👨‍🌾 ${p.productor_nombre || 'Productor local'}</span>
        <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
        <span class="product-stock ${stockBajo ? 'low' : ''}">${p.stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles` : 'Sin stock'}</span>
        ${clickable ? `
        <button class="btn btn-primary btn-agregar" data-id="${p.id}" ${p.stock <= 0 ? 'disabled' : ''}>
          ${p.stock <= 0 ? 'Agotado' : '+ Agregar al pedido'}
        </button>` : ''}
      </div>
    </div>`;
}

function manejarClickGrid(e) {
  const btnFavorito = e.target.closest('.btn-favorito');
  if (btnFavorito) {
    e.stopPropagation();
    manejarClickFavorito(btnFavorito);
    return;
  }
  const btnAgregar = e.target.closest('.btn-agregar');
  if (btnAgregar) {
    e.stopPropagation();
    agregarAlCarrito(btnAgregar.dataset.id);
    destellarBoton(btnAgregar, '✓ Agregado');
    return;
  }
  const card = e.target.closest('.product-card');
  if (card) abrirDetalleProducto(card.dataset.id);
}

function renderProductos(productos) {
  const grid = document.getElementById('productos-grid');
  const vacio = document.getElementById('productos-empty');

  if (!productos.length) {
    grid.innerHTML = '';
    vacio.classList.remove('hidden');
    return;
  }
  vacio.classList.add('hidden');
  grid.innerHTML = productos.map((p, i) => renderTarjetaProductoCatalogo(p, { indice: i })).join('');
}

function filtrarYRenderizar() {
  const texto = document.getElementById('input-buscar').value.trim().toLowerCase();
  const categoria = document.getElementById('select-categoria').value;
  const ubicacion = document.getElementById('select-ubicacion').value;
  const precioMin = parseFloat(document.getElementById('input-precio-min').value);
  const precioMax = parseFloat(document.getElementById('input-precio-max').value);

  const filtrados = Estado.productos.filter((p) => {
    const coincideTexto = !texto || `${p.nombre} ${p.categoria || ''} ${p.productor_nombre || ''}`.toLowerCase().includes(texto);
    const coincideCategoria = !categoria || p.categoria === categoria;
    const ubicacionProducto = Estado.productores[p.productor_id]?.ubicacion;
    const coincideUbicacion = !ubicacion || ubicacionProducto === ubicacion;
    const precio = Number(p.precio);
    const coincideMin = isNaN(precioMin) || precio >= precioMin;
    const coincideMax = isNaN(precioMax) || precio <= precioMax;
    return coincideTexto && coincideCategoria && coincideUbicacion && coincideMin && coincideMax;
  });
  renderProductos(filtrados);
}

function limpiarFiltros() {
  document.getElementById('input-buscar').value = '';
  document.getElementById('select-categoria').value = '';
  document.getElementById('select-ubicacion').value = '';
  document.getElementById('input-precio-min').value = '';
  document.getElementById('input-precio-max').value = '';
  filtrarYRenderizar();
}

async function abrirDetalleProducto(id) {
  const contenido = document.getElementById('detalle-producto-content');
  contenido.innerHTML = renderSkeletonDetalle();
  abrirModal('modal-detalle-producto');

  let p;
  try {
    p = await Api.productos.obtener(id);
  } catch (err) {
    manejarError(err, 'cargar el producto');
    cerrarModal('modal-detalle-producto');
    return;
  }

  let resenas = [];
  let productor = null;
  try {
    resenas = await Api.productos.listarResenas(id);
  } catch { /* sin reseñas disponibles por ahora */ }
  try {
    productor = await Api.productores.obtener(p.productor_id);
  } catch { /* no se pudo cargar el productor, seguimos sin mapa */ }

  renderDetalleProducto(p, resenas, productor);
}

function renderBloqueProductor(p, productor) {
  const nombre = productor?.nombre || p.productor_nombre || 'Productor local';
  const ubicacionTexto = productor?.comunidad || productor?.ubicacion || null;
  const tieneMapa = hayCoordenadas(productor?.latitud, productor?.longitud);

  return `
    <div class="detalle-productor-card">
      <div class="detalle-productor-header">
        <span class="detalle-productor-icono">🧑‍🌾</span>
        <div class="detalle-productor-info">
          <span class="detalle-productor-label">Cultivado por</span>
          <h3 class="detalle-productor-nombre">${escapeAttr(nombre)}</h3>
          ${ubicacionTexto ? `<span class="detalle-productor-ubicacion">📍 ${escapeAttr(ubicacionTexto)}</span>` : ''}
        </div>
      </div>
      ${tieneMapa ? `<div id="mapa-productor-detalle" class="mapa-mini"></div>` : ''}
    </div>`;
}

function renderGaleriaProducto(p) {
  const imagenes = Array.isArray(p.imagenes) ? [...p.imagenes].sort((a, b) => a.orden - b.orden) : [];
  if (imagenes.length <= 1) {
    return renderMediaProducto(p, 'producto-emoji-lg');
  }

  const emoji = emojiParaProducto(p);
  const miniaturas = imagenes.map((img, i) => `
    <div class="galeria-miniatura ${i === 0 ? 'active' : ''}" data-url="${escapeAttr(img.url)}">
      <img src="${escapeAttr(img.url)}" alt="${escapeAttr(p.nombre || '')}" loading="lazy" onerror="this.style.opacity='0.25'">
    </div>`).join('');

  return `
    <div class="galeria-producto">
      <div class="galeria-principal" id="galeria-imagen-principal">
        <img src="${escapeAttr(imagenes[0].url)}" alt="${escapeAttr(p.nombre || '')}" onerror="this.parentElement.textContent='${emoji}'">
      </div>
      <div class="galeria-miniaturas">${miniaturas}</div>
    </div>`;
}

function cambiarImagenGaleria(url, emoji, miniaturas, seleccionada) {
  const principal = document.getElementById('galeria-imagen-principal');
  principal.innerHTML = `<img src="${escapeAttr(url)}" alt="" onerror="this.parentElement.textContent='${emoji}'">`;
  miniaturas.forEach((m) => m.classList.toggle('active', m === seleccionada));
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

function renderSeccionResenas(resenas) {
  const listaHtml = resenas.length
    ? resenas.map((r) => `
        <div class="resena-card">
          <div class="resena-header">
            <span class="resena-usuario">${escapeAttr(r.usuario_nombre)}</span>
            <span class="rating-stars">${renderEstrellas(r.calificacion)}</span>
          </div>
          <div class="resena-fecha">${formatearFecha(r.fecha_creacion)}</div>
          ${r.comentario ? `<p class="resena-comentario">${escapeAttr(r.comentario)}</p>` : ''}
        </div>`).join('')
    : '<p class="muted">Aún no hay reseñas para este producto.</p>';

  const formularioHtml = Estado.token ? `
    <form id="form-resena" class="resena-form">
      <h4>Deja tu reseña</h4>
      <div class="estrellas-selector">
        ${[1, 2, 3, 4, 5].map((v) => `<button type="button" data-valor="${v}">★</button>`).join('')}
      </div>
      <textarea placeholder="Cuéntanos qué te pareció (opcional)"></textarea>
      <button type="submit" class="btn btn-primary">Publicar reseña</button>
    </form>`
    : '<p class="muted">🔒 Inicia sesión para dejar tu propia reseña.</p>';

  return `
    <div class="resenas-section">
      <h3>⭐ Reseñas ${resenas.length ? `(${resenas.length})` : ''}</h3>
      <div class="resenas-lista">${listaHtml}</div>
      ${formularioHtml}
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
          <div class="resena-fecha">${formatearFecha(r.fecha_creacion)}</div>
          ${r.comentario ? `<p class="resena-comentario">${escapeAttr(r.comentario)}</p>` : ''}
        </div>`).join('')}
    </div>`;
}

function renderDetalleProducto(p, resenas, productor) {
  const contenido = document.getElementById('detalle-producto-content');
  const favorito = esFavorito(p.id);
  const stockBajo = p.stock > 0 && p.stock <= 5;
  const sinStock = p.stock <= 0;

  contenido.innerHTML = `
    ${renderGaleriaProducto(p)}
    <div class="detalle-encabezado">
      <span class="product-tag">${iconoCategoria(p.categoria)} ${nombreCategoria(p.categoria)}</span>
      <button type="button" class="btn-favorito btn-favorito-detalle ${favorito ? 'activo' : ''}" data-id="${p.id}" aria-label="Favorito" aria-pressed="${favorito}">
        ${favorito ? '❤️ Guardado' : '🤍 Guardar'}
      </button>
    </div>

    <div class="detalle-cabecera">
      <h2>${escapeAttr(p.nombre)}</h2>
      ${renderResumenCalificacion(p)}
      <div class="detalle-precio-row">
        <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
        <span class="product-stock ${stockBajo ? 'low' : ''}">${sinStock ? 'Sin stock disponible' : `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles`}</span>
      </div>
    </div>

    <div class="detalle-accion-principal">
      ${!sinStock ? `
      <div class="carrito-qty detalle-cantidad-selector">
        <button type="button" id="btn-cantidad-menos" aria-label="Disminuir cantidad">−</button>
        <span id="detalle-cantidad-valor">1</span>
        <button type="button" id="btn-cantidad-mas" aria-label="Aumentar cantidad">+</button>
      </div>` : ''}
      <button class="btn btn-primary btn-lg btn-block" data-id="${p.id}" id="btn-agregar-detalle" ${sinStock ? 'disabled' : ''}>
        ${sinStock ? 'Agotado' : '+ Agregar al pedido'}
      </button>
    </div>

    ${renderBloqueProductor(p, productor)}
    ${renderSeccionResenas(resenas)}
  `;

  let cantidadSeleccionada = 1;
  const cantidadValorEl = document.getElementById('detalle-cantidad-valor');
  document.getElementById('btn-cantidad-menos')?.addEventListener('click', () => {
    if (cantidadSeleccionada > 1) {
      cantidadSeleccionada--;
      cantidadValorEl.textContent = cantidadSeleccionada;
    }
  });
  document.getElementById('btn-cantidad-mas')?.addEventListener('click', () => {
    if (cantidadSeleccionada < p.stock) {
      cantidadSeleccionada++;
      cantidadValorEl.textContent = cantidadSeleccionada;
    } else {
      toast('Alcanzaste el stock disponible.', 'error');
    }
  });

  document.getElementById('btn-agregar-detalle')?.addEventListener('click', (e) => {
    agregarAlCarrito(p.id, cantidadSeleccionada);
    destellarBoton(e.currentTarget, '✓ Agregado');
    setTimeout(() => cerrarModal('modal-detalle-producto'), 700);
  });

  contenido.querySelector('.btn-favorito-detalle')?.addEventListener('click', (e) => {
    manejarClickFavorito(e.currentTarget);
  });

  if (hayCoordenadas(productor?.latitud, productor?.longitud)) {
    crearMapaSoloLectura('mapa-productor-detalle', productor.latitud, productor.longitud, '🧺', '#2d6a4f', escapeAttr(productor.nombre || 'Productor'));
  }

  const miniaturas = contenido.querySelectorAll('.galeria-miniatura');
  const emoji = emojiParaProducto(p);
  miniaturas.forEach((mini) => {
    mini.addEventListener('click', () => cambiarImagenGaleria(mini.dataset.url, emoji, miniaturas, mini));
  });

  const formResena = document.getElementById('form-resena');
  if (formResena) {
    let calificacionSeleccionada = 0;
    const botones = formResena.querySelectorAll('.estrellas-selector button');
    botones.forEach((btn) => {
      btn.addEventListener('click', () => {
        calificacionSeleccionada = Number(btn.dataset.valor);
        botones.forEach((b) => b.classList.toggle('activa', Number(b.dataset.valor) <= calificacionSeleccionada));
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
    renderDetalleProducto(p, resenas, productor);
  } catch (err) {
    manejarError(err, 'publicar tu reseña');
    btn.disabled = false;
    btn.textContent = 'Publicar reseña';
  }
}

// ============ CARRITO / PEDIDO ============
function agregarAlCarrito(productoId, cantidad = 1) {
  if (!Estado.token) {
    toast('Inicia sesión para armar tu pedido 🌱', 'error');
    abrirModal('modal-auth');
    return;
  }
  const producto = Estado.productos.find((p) => p.id === productoId);
  if (!producto) return;

  const existente = Estado.carrito.find((i) => i.producto_id === productoId);
  if (existente) {
    const nuevaCantidad = Math.min(existente.cantidad + cantidad, producto.stock);
    if (nuevaCantidad === existente.cantidad) toast('No hay más stock disponible de este producto.', 'error');
    existente.cantidad = nuevaCantidad;
  } else {
    Estado.carrito.push({
      producto_id: producto.id,
      nombre: producto.nombre,
      precio: Number(producto.precio),
      cantidad: Math.min(cantidad, producto.stock),
      stockDisponible: producto.stock,
      unidad_medida: producto.unidad_medida,
    });
  }
  renderCarrito();
  toast(`${producto.nombre} agregado al pedido 🧺`);
}

function cambiarCantidadCarrito(productoId, delta) {
  const item = Estado.carrito.find((i) => i.producto_id === productoId);
  if (!item) return;
  item.cantidad += delta;
  if (item.cantidad <= 0) {
    Estado.carrito = Estado.carrito.filter((i) => i.producto_id !== productoId);
  } else if (item.cantidad > item.stockDisponible) {
    item.cantidad = item.stockDisponible;
    toast('Alcanzaste el stock máximo disponible.', 'error');
  }
  renderCarrito();
}

function quitarDelCarrito(productoId) {
  Estado.carrito = Estado.carrito.filter((i) => i.producto_id !== productoId);
  renderCarrito();
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

  if (!Estado.carrito.length) {
    cont.innerHTML = '';
    vacio.classList.remove('hidden');
    totalEl.textContent = formatearMoneda(0);
    btnConfirmar.disabled = true;
    return;
  }
  vacio.classList.add('hidden');
  btnConfirmar.disabled = false;

  cont.innerHTML = Estado.carrito.map((i) => `
    <div class="carrito-item">
      <div class="carrito-item-info">
        <strong>${i.nombre}</strong>
        <span>${formatearMoneda(i.precio)} / ${unidadCorta(i.unidad_medida)}</span>
        <span class="qty-label">Cantidad en ${unidadEtiqueta(i.unidad_medida)}</span>
      </div>
      <div class="carrito-qty">
        <button data-accion="menos" data-id="${i.producto_id}">−</button>
        <span>${i.cantidad} ${unidadPlural(i.unidad_medida, i.cantidad)}</span>
        <button data-accion="mas" data-id="${i.producto_id}">+</button>
      </div>
      <button class="carrito-remove" data-accion="quitar" data-id="${i.producto_id}">🗑️</button>
    </div>
  `).join('');

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
    const circulo = stepEl.querySelector('.checkout-step-circle');
    circulo.textContent = n < paso ? '✓' : String(n);
  });
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
let pedidoReintento = null; // { id, monto }

function reintentarPago(pedidoId, monto) {
  pedidoReintento = { id: pedidoId, monto };
  abrirModal('modal-pedido');
  document.getElementById('checkout-total-paso2').textContent = formatearMoneda(monto);
  irPasoCheckout(2);
}

async function manejarClickPagar() {
  if (pedidoReintento) {
    const { id, monto } = pedidoReintento;
    const metodo = document.querySelector('input[name="metodo-pago"]:checked')?.value || 'tarjeta';
    const btn = document.getElementById('btn-checkout-pagar');
    btn.disabled = true;
    btn.textContent = 'Enviando pedido...';
    pedidoReintento = null;
    abrirCheckoutCulqi(id, monto, metodo);
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
  const ayuda = document.getElementById('mapa-destino-ayuda');
  if (ayuda) {
    ayuda.textContent = 'Toca el mapa para marcar dónde quieres recibir tu pedido.';
    ayuda.classList.remove('confirmado');
  }

  mapaDestinoPedido = crearMapaSeleccionable('mapa-destino-pedido', {
    emoji: '📍',
    color: '#e76f51',
    onSeleccionar: (lat, lng) => {
      destinoSeleccionado = { lat, lng };
      actualizarAyudaDestino();
    },
  });
}

function actualizarAyudaDestino() {
  const ayuda = document.getElementById('mapa-destino-ayuda');
  if (!ayuda || !destinoSeleccionado) return;
  ayuda.textContent = `📍 Destino marcado (${destinoSeleccionado.lat.toFixed(5)}, ${destinoSeleccionado.lng.toFixed(5)})`;
  ayuda.classList.add('confirmado');
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
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Usar mi ubicación';
  }
}

function irAPaso2() {
  if (!Estado.carrito.length) return;
  const total = Estado.carrito.reduce((acc, i) => acc + i.precio * i.cantidad, 0);
  document.getElementById('checkout-total-paso2').textContent = formatearMoneda(total);
  irPasoCheckout(2);
}

function resetearBotonPago() {
  const btn = document.getElementById('btn-checkout-pagar');
  btn.disabled = false;
  btn.textContent = 'Confirmar y pagar';
}

async function confirmarYPagar() {
  const btn = document.getElementById('btn-checkout-pagar');
  const metodo = document.querySelector('input[name="metodo-pago"]:checked')?.value || 'tarjeta';
  btn.disabled = true;
  btn.textContent = 'Enviando pedido...';

  try {
    const total = Estado.carrito.reduce((acc, i) => acc + i.precio * i.cantidad, 0);
    const datos = {
      comprador_nombre: Estado.nombre || 'Cliente Chakra Shop',
      comprador_telefono: document.getElementById('pedido-telefono').value.trim() || null,
      destino_latitud: destinoSeleccionado ? String(destinoSeleccionado.lat) : null,
      destino_longitud: destinoSeleccionado ? String(destinoSeleccionado.lng) : null,
      items: Estado.carrito.map((i) => ({ producto_id: i.producto_id, cantidad: i.cantidad })),
    };
    const pedido = await Api.pedidos.crear(datos);
    guardarPedidoTrackeado(pedido.id);

    Estado.carrito = [];
    renderCarrito();
    document.getElementById('pedido-telefono').value = '';
    cargarCatalogo();

    abrirCheckoutCulqi(pedido.id, total, metodo);
  } catch (err) {
    manejarError(err, 'crear el pedido');
    resetearBotonPago();
  }
}

// ============ PAGO (CULQI) ============
let pagoEnCurso = null; // { pedidoId, email, monto }

function abrirCheckoutCulqi(pedidoId, montoTotal, metodo = 'tarjeta') {
  if (typeof Culqi === 'undefined') {
    toast('No se pudo cargar la pasarela de pagos. Vuelve a intentar el pago desde "Mis Pedidos".', 'error');
    resetearBotonPago();
    return;
  }

  pagoEnCurso = { pedidoId, email: Estado.email, monto: montoTotal };
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
    procesarPagoCulqi(window.Culqi.token.id);
  } else if (window.Culqi.error) {
    toast(window.Culqi.error.user_message || 'Revisa los datos de tu tarjeta e intenta de nuevo.', 'error');
    resetearBotonPago();
  }
};

async function procesarPagoCulqi(tokenCulqi) {
  const { pedidoId, email, monto } = pagoEnCurso;
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

  mostrarConfirmacionCheckout({ pedidoId, monto, resultado, errorMsg });
  pagoEnCurso = null;
  resetearBotonPago();
  irPasoCheckout(3);
}

function mostrarConfirmacionCheckout({ pedidoId, monto, resultado, errorMsg }) {
  const cont = document.getElementById('checkout-confirmacion');
  const aprobado = resultado?.estado === 'aprobado';
  const idCorto = String(pedidoId).slice(0, 8);

  if (aprobado) {
    cont.innerHTML = `
      <div class="icono-grande">🎉</div>
      <h3>¡Pago aprobado!</h3>
      <p>Tu pedido <strong>#${idCorto}</strong> está confirmado.</p>
      <p>Total pagado: <strong>${formatearMoneda(monto)}</strong></p>`;
  } else {
    const pareceJson = errorMsg && (errorMsg.trim().startsWith('[') || errorMsg.trim().startsWith('{'));
    const motivo = (errorMsg && !pareceJson)
      ? errorMsg
      : `El pago quedó en estado "${resultado?.estado || 'pendiente'}".`;
    cont.innerHTML = `
      <div class="icono-grande">⚠️</div>
      <h3>No se pudo confirmar el pago</h3>
      <p>${escapeAttr(motivo)}</p>
      <p>Tu pedido <strong>#${idCorto}</strong> quedó registrado — puedes reintentar el pago más tarde.</p>`;
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

function renderEmptyPedidos(tipo) {
  const vacio = document.getElementById('pedidos-empty');
  const plantillas = {
    'no-sesion': `
      <span class="empty-state-icon">🔒</span>
      <p><strong>Inicia sesión para ver tus pedidos</strong></p>
      <p class="muted">Necesitas una cuenta para hacer seguimiento de tus compras.</p>
      <button type="button" class="btn btn-primary" id="btn-vacio-login">Iniciar sesión</button>`,
    'sin-pedidos': `
      <span class="empty-state-icon">🛍️</span>
      <p><strong>Aún no tienes pedidos</strong></p>
      <p class="muted">Explora el catálogo y arma tu primer pedido directo de productores locales.</p>
      <button type="button" class="btn btn-primary" id="btn-vacio-catalogo">Explorar catálogo</button>`,
    'sin-en-curso': `
      <span class="empty-state-icon">✅</span>
      <p><strong>No tienes pedidos en curso</strong></p>
      <p class="muted">Todos tus pedidos ya fueron entregados.</p>`,
    'sin-entregados': `
      <span class="empty-state-icon">📭</span>
      <p><strong>Aún no tienes pedidos entregados</strong></p>
      <p class="muted">Aquí verás el historial una vez que se complete una entrega.</p>`,
  };
  vacio.innerHTML = plantillas[tipo] || plantillas['sin-pedidos'];
  vacio.classList.remove('hidden');
  document.getElementById('btn-vacio-login')?.addEventListener('click', () => {
    cambiarTabAuth('login');
    abrirModal('modal-auth');
  });
  document.getElementById('btn-vacio-catalogo')?.addEventListener('click', () => cambiarVista('catalogo'));
}

async function cargarMisPedidos() {
  detenerPollingRutas();
  const cont = document.getElementById('pedidos-list');
  const vacio = document.getElementById('pedidos-empty');
  vacio.classList.add('hidden');

  if (!Estado.token) {
    cont.innerHTML = '';
    renderEmptyPedidos('no-sesion');
    return;
  }

  const ids = obtenerPedidosTrackeados();
  if (!ids.length) {
    cont.innerHTML = '';
    renderEmptyPedidos('sin-pedidos');
    return;
  }
  cont.innerHTML = renderSkeletonFilas(Math.min(ids.length, 3));

  if (!Estado.productos.length) {
    try { Estado.productos = await Api.productos.listar(); } catch { /* seguimos sin fotos si falla */ }
  }

  const datos = await Promise.all(ids.map((id) => cargarDatosPedido(id)));
  pedidosCargados = datos.filter(Boolean);

  if (!pedidosCargados.length) {
    cont.innerHTML = '<p class="muted">No se pudieron cargar los pedidos.</p>';
    return;
  }

  filtrarYRenderizarPedidos();
}

function filtrarYRenderizarPedidos() {
  const cont = document.getElementById('pedidos-list');

  document.querySelectorAll('.pedidos-tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.filtro === filtroPedidoActual);
  });

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
  cont.innerHTML = filtrados.map((d) => renderTarjetaPedido(d.pedido, d.envio, d.pago, d.ruta)).join('');

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
        return `<button type="button" class="btn btn-outline btn-sm btn-calificar-producto" data-producto-id="${pid}">⭐ Califica ${escapeAttr(nombre)}</button>`;
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
    <div class="ruta-stat"><span class="ruta-stat-icon">📏</span><div><strong>${ruta.distancia_km} km</strong><span>distancia</span></div></div>
    <div class="ruta-stat"><span class="ruta-stat-icon">⏱️</span><div><strong>~${ruta.tiempo_estimado_min} min</strong><span>tiempo estimado</span></div></div>`;
}

function renderBloqueRuta(envio, ruta) {
  if (!envio) return '';
  if (!ruta || !ruta.disponible) {
    const mensaje = ruta?.mensaje || 'El seguimiento en mapa aún no está disponible para este envío.';
    return `<div class="mapa-bloque-neutro"><span class="icon">🗺️</span> ${escapeAttr(mensaje)}</div>`;
  }
  return `
    <div class="mapa-bloque">
      <div id="mapa-ruta-${envio.id}" class="mapa-mini"></div>
      <div class="ruta-stats" id="ruta-datos-${envio.id}">${renderStatsRuta(ruta)}</div>
    </div>`;
}

function renderTarjetaPedido(pedido, envio, pago, ruta) {
  const items = (pedido.items || []).map((i) => ({ ...i, producto: Estado.productos.find((p) => p.id === i.producto_id) }));
  const total = items.reduce((acc, i) => acc + Number(i.precio_unitario) * i.cantidad, 0);

  const itemsHtml = items.map((i) => `
    <li class="pedido-item-linea">
      <div class="pedido-item-media">${renderMediaProducto(i.producto || { categoria: null, nombre: '' }, 'pedido-item-thumb')}</div>
      <span class="pedido-item-nombre">${i.cantidad} × ${escapeAttr(i.producto?.nombre || `Producto ${String(i.producto_id).slice(0, 8)}`)}</span>
      <span class="pedido-item-precio">${formatearMoneda(i.precio_unitario * i.cantidad)}</span>
    </li>`).join('');

  const activo = envio && ['asignado', 'en_camino'].includes(envio.estado);
  const entregado = esPedidoEntregado({ pedido, envio });
  const pagoRechazado = pago?.estado === 'rechazado';

  return `
    <div class="pedido-card" data-pedido-id="${pedido.id}">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Pedido #${String(pedido.id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFecha(pedido.fecha_creacion)}</div>
        </div>
        <div class="pedido-total-header">${formatearMoneda(total)}</div>
      </div>

      <div class="estado-badges-row">
        <div class="estado-badge-item"><span class="estado-badge-label">Pedido</span>${badgeEstadoEnvio(pedido.estado)}</div>
        <div class="estado-badge-item"><span class="estado-badge-label">Pago</span>${pago ? badgeEstadoEnvio(pago.estado) : '<span class="badge badge-default">—</span>'}</div>
        <div class="estado-badge-item"><span class="estado-badge-label">Envío</span>${envio ? badgeEstadoEnvio(envio.estado) : '<span class="badge badge-default">—</span>'}</div>
      </div>

      <ul class="pedido-items">${itemsHtml}</ul>

      ${pagoRechazado ? `
      <div class="pago-rechazado-aviso">
        <span>⚠️ Tu pago fue rechazado.</span>
        <button type="button" class="btn btn-outline btn-sm btn-reintentar-pago" data-pedido-id="${pedido.id}" data-monto="${total}">Reintentar pago</button>
      </div>` : ''}

      ${activo ? renderBloqueRuta(envio, ruta) : ''}
      ${entregado ? '<div class="resena-cta"></div>' : ''}
      ${entregado ? `
      <div class="pedido-certificado-cta">
        <button type="button" class="btn btn-outline btn-sm btn-ver-certificado-pedido" data-pedido-id="${pedido.id}">🔗 Ver certificado de compra</button>
        <div class="productor-expandible hidden" data-panel="certificado-pedido" data-pedido-id="${pedido.id}"></div>
      </div>` : ''}
    </div>
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
      <p class="muted certificado-pedido-nota">🔒 Verificado mediante blockchain</p>
    </div>`;
}

async function alternarCertificadoPedido(pedidoId) {
  const panel = document.querySelector(`.productor-expandible[data-panel="certificado-pedido"][data-pedido-id="${pedidoId}"]`);
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
  cont.innerHTML = '<p class="muted">Buscando pedido...</p>';

  try {
    const d = await cargarDatosPedido(id);
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
    manejarError(err, 'buscar el pedido');
    cargarMisPedidos();
  }
}

// ============ NOTIFICACIONES ============
const NOTIF_ICONOS = {
  pedido_creado: '📦',
  envio_actualizado: '🚚',
};

function iconoNotificacion(tipo) {
  return NOTIF_ICONOS[tipo] || '🔔';
}

function renderVacioNotificaciones(tipo) {
  const vacio = document.getElementById('notificaciones-empty');
  const plantillas = {
    'no-sesion': `
      <span class="empty-state-icon">🔒</span>
      <p><strong>Inicia sesión para ver tus notificaciones</strong></p>`,
    'sin-notificaciones': `
      <span class="empty-state-icon">🔔</span>
      <p><strong>No tienes notificaciones todavía</strong></p>
      <p class="muted">Aquí verás avisos sobre tus pedidos y envíos apenas ocurran.</p>`,
  };
  vacio.innerHTML = plantillas[tipo] || plantillas['sin-notificaciones'];
  vacio.classList.remove('hidden');
}

const TAMANO_PAGINA_NOTIF = 10;
let notificacionesCargadas = [];
let notifMostrar = TAMANO_PAGINA_NOTIF;

function renderNotificacionesPagina() {
  const cont = document.getElementById('notificaciones-list');
  const btnMas = document.getElementById('btn-notif-cargar-mas');
  const visibles = notificacionesCargadas.slice(0, notifMostrar);

  cont.innerHTML = visibles.map((n, i) => `
    <div class="notif-card" style="animation-delay:${Math.min(i, 10) * 35}ms">
      <div class="notif-icon tipo-${escapeAttr(n.tipo)}">${iconoNotificacion(n.tipo)}</div>
      <div>
        <div class="notif-msg">${escapeAttr(n.mensaje)}</div>
        <div class="notif-meta">Pedido #${String(n.pedido_id).slice(0, 8)} · ${formatearFecha(n.fecha_envio)}</div>
      </div>
    </div>
  `).join('');

  btnMas.classList.toggle('hidden', notifMostrar >= notificacionesCargadas.length);
}

async function cargarNotificaciones() {
  const cont = document.getElementById('notificaciones-list');
  const vacio = document.getElementById('notificaciones-empty');
  const btnMas = document.getElementById('btn-notif-cargar-mas');
  vacio.classList.add('hidden');
  btnMas.classList.add('hidden');
  notifMostrar = TAMANO_PAGINA_NOTIF;

  if (!Estado.token) {
    cont.innerHTML = '';
    renderVacioNotificaciones('no-sesion');
    return;
  }

  cont.innerHTML = renderSkeletonFilas(3);
  try {
    const ids = new Set(obtenerPedidosTrackeados());
    const todas = await Api.notificaciones.listarTodas();
    notificacionesCargadas = ids.size ? todas.filter((n) => ids.has(String(n.pedido_id))) : todas;

    if (!notificacionesCargadas.length) {
      cont.innerHTML = '';
      renderVacioNotificaciones('sin-notificaciones');
      return;
    }
    renderNotificacionesPagina();
  } catch (err) {
    manejarError(err, 'cargar notificaciones');
  }
}

// ============ PANEL PRODUCTOR ============
function claveProductorId() {
  return `agro_productor_id_${Estado.usuarioId}`;
}

async function iniciarPanelProductor() {
  if (!Estado.token || Estado.rol !== 'productor') {
    cambiarVista('catalogo');
    return;
  }

  Estado.productorId = localStorage.getItem(claveProductorId());

  if (Estado.productorId) {
    try {
      await Api.productores.obtener(Estado.productorId);
    } catch {
      Estado.productorId = null;
      localStorage.removeItem(claveProductorId());
    }
  }

  const setup = document.getElementById('productor-setup');
  const panel = document.getElementById('productor-panel');

  if (Estado.productorId) {
    setup.classList.add('hidden');
    panel.classList.remove('hidden');
    poblarSelectCategoriaProducto();
    reiniciarFormularioProducto();
    cargarMisProductos();
  } else {
    setup.classList.remove('hidden');
    panel.classList.add('hidden');
    inicializarMapaUbicacionProductor();
  }
}

// ---- Categorías fijas (mismo catálogo de 8 usado en toda la app) ----
const CATEGORIAS_PRODUCTO = ['fruta', 'verdura', 'tuberculo', 'grano', 'legumbre', 'lacteo', 'huevo', 'hierba'];

function poblarSelectCategoriaProducto() {
  const select = document.getElementById('producto-categoria');
  if (!select || select.dataset.poblado) return;
  select.innerHTML = '<option value="">Elige una categoría</option>' +
    CATEGORIAS_PRODUCTO.map((c) => `<option value="${c}">${ICONOS_CATEGORIA[c]} ${NOMBRE_CATEGORIA[c]}</option>`).join('');
  select.dataset.poblado = '1';
}

// ---- Vista previa en vivo de la tarjeta mientras se llena el formulario ----
function leerBorradorProducto() {
  return {
    nombre: document.getElementById('producto-nombre').value.trim() || 'Nombre del producto',
    categoria: document.getElementById('producto-categoria').value,
    unidad_medida: document.getElementById('producto-unidad').value,
    precio: parseFloat(document.getElementById('producto-precio').value) || 0,
    stock: parseInt(document.getElementById('producto-stock').value, 10) || 0,
    productor_nombre: Estado.nombre || 'Tu emprendimiento',
  };
}

function actualizarVistaPreviaProducto() {
  const cont = document.getElementById('preview-producto-card');
  if (!cont) return;
  cont.innerHTML = renderTarjetaProductoCatalogo(leerBorradorProducto(), { clickable: false, indice: 0 });
}

function reiniciarFormularioProducto() {
  document.getElementById('form-producto')?.reset();
  document.getElementById('producto-fotos-bloque')?.classList.add('hidden');
  document.getElementById('form-producto')?.classList.remove('hidden');
  document.getElementById('titulo-form-producto').textContent = '🌱 Publicar nuevo producto';
  actualizarVistaPreviaProducto();
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
    emoji: '🧺',
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
  ayuda.textContent = `📍 Ubicación marcada (${ubicacionProductorSeleccionada.lat.toFixed(5)}, ${ubicacionProductorSeleccionada.lng.toFixed(5)})`;
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

async function publicarProducto(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const datos = {
      productor_id: Estado.productorId,
      nombre: document.getElementById('producto-nombre').value.trim(),
      categoria: document.getElementById('producto-categoria').value || null,
      unidad_medida: document.getElementById('producto-unidad').value,
      precio: parseFloat(document.getElementById('producto-precio').value),
      stock: parseInt(document.getElementById('producto-stock').value, 10),
    };
    const nuevoProducto = await Api.productos.crear(datos);
    toast('Producto publicado con éxito 🎉');

    document.getElementById('form-producto').classList.add('hidden');
    document.getElementById('titulo-form-producto').textContent = `✅ ${nuevoProducto.nombre}`;
    Estado.imagenesPorProducto[nuevoProducto.id] = [];
    const bloqueFotos = document.getElementById('producto-fotos-bloque');
    bloqueFotos.classList.remove('hidden');
    bloqueFotos.dataset.productoId = nuevoProducto.id;
    document.getElementById('gestor-fotos-nuevo').innerHTML = renderGestorFotosHTML(nuevoProducto.id, []);

    cargarMisProductos();
  } catch (err) {
    manejarError(err, 'publicar el producto');
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
        ${!sinStock && stockBajo ? '<span class="badge-flotante badge-stock-bajo">⚠️ Poco stock</span>' : ''}
      </div>
      <div class="product-card-body">
        <div class="product-card-top">
          <span class="product-tag">${iconoCategoria(p.categoria)} ${nombreCategoria(p.categoria)}</span>
        </div>
        <h4>${p.nombre}</h4>
        <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
        <span class="product-stock ${stockBajo || sinStock ? 'low' : ''}">${p.stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles` : 'Sin stock'}</span>

        <div class="productor-card-acciones">
          <button type="button" class="btn btn-outline btn-sm btn-toggle-resenas" data-id="${p.id}">
            ⭐ ${tieneCalificacion ? `${p.total_resenas} reseña${p.total_resenas === 1 ? '' : 's'}` : 'Reseñas'}
          </button>
          <button type="button" class="btn btn-outline btn-sm btn-toggle-fotos" data-id="${p.id}">📷 Fotos</button>
          <button type="button" class="btn btn-outline btn-sm btn-ver-certificacion" data-id="${p.id}">🔗 Certificación</button>
        </div>

        <div class="productor-expandible hidden" data-panel="resenas" data-id="${p.id}"></div>
        <div class="productor-expandible hidden" data-panel="fotos" data-id="${p.id}"></div>
      </div>
    </div>`;
}

async function alternarResenasProductor(id) {
  const panel = document.querySelector(`.productor-expandible[data-panel="resenas"][data-id="${id}"]`);
  if (!panel) return;
  document.querySelector(`.productor-expandible[data-panel="fotos"][data-id="${id}"]`)?.classList.add('hidden');

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
  const panel = document.querySelector(`.productor-expandible[data-panel="fotos"][data-id="${id}"]`);
  if (!panel) return;
  document.querySelector(`.productor-expandible[data-panel="resenas"][data-id="${id}"]`)?.classList.add('hidden');

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

async function cargarMisProductos() {
  const grid = document.getElementById('mis-productos-grid');
  const vacio = document.getElementById('mis-productos-empty');
  vacio.classList.add('hidden');
  grid.innerHTML = renderSkeletonProductos(3);
  try {
    const todos = await Api.productos.listar();
    const mios = todos.filter((p) => p.productor_id === Estado.productorId);
    Estado.productos = todos;

    if (!mios.length) {
      grid.innerHTML = '';
      vacio.classList.remove('hidden');
      return;
    }
    vacio.classList.add('hidden');
    grid.innerHTML = mios.map((p, i) => renderTarjetaProductorProducto(p, i)).join('');
  } catch (err) {
    manejarError(err, 'cargar tus productos');
  }
}

// ============ GESTIÓN DE ENVÍOS (repartidor) ============
const DISPONIBILIDAD_INFO = {
  disponible: { texto: 'Disponible', clase: 'disponibilidad-verde', icono: '🟢', detalle: 'Puedes recibir nuevas propuestas de envío.' },
  ocupado: { texto: 'Ocupado', clase: 'disponibilidad-ambar', icono: '🟠', detalle: 'Estás atendiendo un envío en este momento.' },
  desconectado: { texto: 'Desconectado', clase: 'disponibilidad-gris', icono: '⚪', detalle: 'No recibirás nuevas propuestas hasta que te conectes.' },
};

function renderBannerDisponibilidad(estado) {
  const clave = (estado || 'desconectado').toLowerCase();
  const info = DISPONIBILIDAD_INFO[clave] || DISPONIBILIDAD_INFO.desconectado;
  return `
    <div class="disponibilidad-banner ${info.clase}">
      <span class="disponibilidad-icono">${info.icono}</span>
      <div>
        <strong>${info.texto}</strong>
        <span>${info.detalle}</span>
      </div>
    </div>`;
}

async function cargarGestionEnvios() {
  if (!Estado.token || Estado.rol !== 'repartidor') {
    cambiarVista('catalogo');
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

  await cargarListaEnvios(miRepartidor.id);
  iniciarSeguimientoRepartidor();
}

async function crearPerfilRepartidor(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const datos = {
      nombre: document.getElementById('repartidor-nombre').value.trim(),
      dni: document.getElementById('repartidor-dni').value.trim(),
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

function renderTarjetaPropuesta(envio) {
  return `
    <div class="propuesta-card">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Envío #${String(envio.id).slice(0, 8)} · Pedido #${String(envio.pedido_id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFecha(envio.fecha_creacion)}</div>
        </div>
        ${badgeEstadoEnvio(envio.estado || 'propuesto')}
      </div>
      <div class="propuesta-actions">
        <button type="button" class="btn btn-outline btn-rechazar-propuesta" data-envio-id="${envio.id}">✕ Rechazar</button>
        <button type="button" class="btn btn-primary btn-aceptar-propuesta" data-envio-id="${envio.id}">✓ Aceptar envío</button>
      </div>
    </div>`;
}

function renderTarjetaEnvio(envio, ruta) {
  const activo = ['asignado', 'en_camino'].includes(envio.estado);
  const finalizado = ['entregado', 'cancelado', 'rechazado'].includes(envio.estado);
  return `
    <div class="pedido-card ${activo ? 'pedido-card-activo' : ''}">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Envío #${String(envio.id).slice(0, 8)} · Pedido #${String(envio.pedido_id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFecha(envio.fecha_creacion)}</div>
        </div>
        ${badgeEstadoEnvio(envio.estado)}
      </div>
      ${activo ? renderBloqueRuta(envio, ruta) : ''}
      ${!finalizado ? `
      <div class="envio-actualizar">
        <select class="select-estado-envio" data-envio-id="${envio.id}">
          <option value="asignado" ${envio.estado === 'asignado' ? 'selected' : ''}>Asignado</option>
          <option value="en_camino" ${envio.estado === 'en_camino' ? 'selected' : ''}>En camino</option>
          <option value="entregado" ${envio.estado === 'entregado' ? 'selected' : ''}>Entregado</option>
        </select>
        <button class="btn btn-primary btn-actualizar-envio" data-envio-id="${envio.id}">Actualizar estado</button>
      </div>` : ''}
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
    propuestasCont.innerHTML = propuestas.map(renderTarjetaPropuesta).join('');
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
  await Promise.all(activos.map(async (e) => {
    try {
      rutasPorEnvio[e.id] = await Api.transporte.ruta(e.id);
    } catch { /* seguimiento de ruta no disponible por ahora */ }
  }));

  cont.innerHTML = mios.map((e) => renderTarjetaEnvio(e, rutasPorEnvio[e.id])).join('');
  activos.forEach((e) => {
    const ruta = rutasPorEnvio[e.id];
    if (ruta?.disponible) iniciarMapaRutaPedido(e.id, ruta);
  });
}

async function actualizarEstadoEnvio(envioId, nuevoEstado) {
  try {
    await Api.transporte.actualizarEstado(envioId, nuevoEstado);
    toast(`Estado del envío actualizado a "${nuevoEstado}" ✅`);
    if (Estado.repartidorId) cargarListaEnvios(Estado.repartidorId);
  } catch (err) {
    manejarError(err, 'actualizar el estado del envío');
  }
}

async function responderPropuesta(envioId, accion) {
  const boton = document.querySelector(`.btn-${accion}-propuesta[data-envio-id="${envioId}"]`);
  const card = boton?.closest('.propuesta-card');
  card?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    if (accion === 'aceptar') await Api.transporte.aceptar(envioId);
    else await Api.transporte.rechazar(envioId);
    toast(accion === 'aceptar' ? 'Envío aceptado, ¡buen viaje! 🚚' : 'Envío rechazado.');
    if (Estado.repartidorId) cargarListaEnvios(Estado.repartidorId);
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
  const iconos = { ok: '📍', error: '⚠️', espera: '🔄' };
  return `<span class="ubicacion-chip ubicacion-${tipo}">${iconos[tipo] || '📍'} ${escapeAttr(mensaje)}</span>`;
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
  comprador: { icono: '🛒', texto: 'Comprador' },
  productor: { icono: '🚜', texto: 'Productor' },
  repartidor: { icono: '🚚', texto: 'Repartidor' },
};

function ocultarDni(dni) {
  if (!dni) return '—';
  if (dni.length < 6) return dni;
  const centro = Math.max(0, dni.length - 6);
  return `${dni.slice(0, 4)}${'*'.repeat(centro)}${dni.slice(-2)}`;
}

function renderSkeletonPerfil() {
  return `
    <div class="perfil-header card-panel">
      <div class="skeleton" style="width:64px;height:64px;border-radius:50%;flex-shrink:0;"></div>
      <div style="flex:1">
        <div class="skeleton skeleton-line w-40" style="margin-bottom:10px;"></div>
        <div class="skeleton skeleton-line w-60"></div>
      </div>
    </div>
    <div class="card-panel">
      <div class="skeleton skeleton-line w-40" style="margin-bottom:14px;"></div>
      <div class="skeleton skeleton-line tall w-70" style="margin-bottom:10px;"></div>
      <div class="skeleton skeleton-line w-60"></div>
    </div>`;
}

function renderPerfilComprador() {
  return `
    <div class="card-panel">
      <h3>🛒 Comprador</h3>
      <p class="muted">Explora el catálogo y haz seguimiento de tus compras.</p>
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-ir-pedidos">Ir a Mis Pedidos →</button>
    </div>`;
}

function renderPerfilProductor(extra) {
  if (!extra) {
    return `
      <div class="perfil-cta-completar">
        <span class="empty-state-icon">🚜</span>
        <p><strong>Aún no completaste tu perfil de productor</strong></p>
        <p class="muted">Créalo para empezar a publicar productos sin intermediarios.</p>
        <button type="button" class="btn btn-primary" id="btn-perfil-ir-panel">Completar perfil de productor</button>
      </div>`;
  }
  return `
    <div class="card-panel">
      <h3>🚜 Datos de productor</h3>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Comunidad / Región</span><span class="perfil-dato-valor">${escapeAttr(extra.comunidad || '—')}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Contacto</span><span class="perfil-dato-valor">${escapeAttr(extra.contacto || '—')}</span></div>
      </div>
      ${hayCoordenadas(extra.latitud, extra.longitud) ? `
      <div class="mapa-bloque">
        <h4 class="mapa-titulo">📍 Ubicación de tu chakra</h4>
        <div id="mapa-perfil-productor" class="mapa-mini"></div>
      </div>` : `<div class="mapa-bloque-neutro"><span class="icon">📍</span> No has marcado la ubicación de tu chakra todavía.</div>`}
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-ir-panel" style="margin-top:16px;">Ir a Mis Productos →</button>
    </div>`;
}

function renderPerfilRepartidor(extra) {
  if (!extra) {
    return `
      <div class="perfil-cta-completar">
        <span class="empty-state-icon">🚚</span>
        <p><strong>Aún no completaste tu perfil de repartidor</strong></p>
        <p class="muted">Créalo para empezar a recibir propuestas de envío.</p>
        <button type="button" class="btn btn-primary" id="btn-perfil-ir-envios">Completar perfil de repartidor</button>
      </div>`;
  }
  return `
    <div class="card-panel">
      <h3>🚚 Datos de repartidor</h3>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">DNI</span><span class="perfil-dato-valor perfil-dato-mono">${escapeAttr(ocultarDni(extra.dni))}</span></div>
      </div>
      ${renderBannerDisponibilidad(extra.estado_disponibilidad)}
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-ir-envios" style="margin-top:16px;">Ir a Gestionar Envíos →</button>
    </div>`;
}

function renderPerfil(usuario, extra) {
  const rolInfo = ROL_INFO_PERFIL[usuario.rol] || { icono: '👤', texto: usuario.rol || 'Usuario' };

  const headerHtml = `
    <div class="perfil-header card-panel">
      <div class="perfil-avatar">${escapeAttr((usuario.nombre || '?').charAt(0).toUpperCase())}</div>
      <div class="perfil-header-info">
        <h3>${escapeAttr(usuario.nombre)}</h3>
        <span class="perfil-email">${escapeAttr(usuario.email)}</span>
        <span class="perfil-rol-badge">${rolInfo.icono} ${rolInfo.texto}</span>
      </div>
    </div>`;

  let seccionRol = renderPerfilComprador();
  if (usuario.rol === 'productor') seccionRol = renderPerfilProductor(extra);
  else if (usuario.rol === 'repartidor') seccionRol = renderPerfilRepartidor(extra);

  return `
    ${headerHtml}
    ${seccionRol}
    <div class="card-panel">
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-logout">🚪 Cerrar sesión</button>
    </div>`;
}

async function cargarPerfil() {
  if (!Estado.token) {
    cambiarVista('catalogo');
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
  if (usuario.rol === 'productor') {
    try { extra = await Api.productores.miPerfil(); } catch { /* aún no tiene perfil de productor */ }
  } else if (usuario.rol === 'repartidor') {
    try { extra = await Api.repartidores.miPerfil(); } catch { /* aún no tiene perfil de repartidor */ }
  }

  cont.innerHTML = renderPerfil(usuario, extra);

  if (usuario.rol === 'productor' && extra && hayCoordenadas(extra.latitud, extra.longitud)) {
    crearMapaSoloLectura('mapa-perfil-productor', extra.latitud, extra.longitud, '🧺', '#2d6a4f', escapeAttr(extra.nombre || 'Tu chakra'));
  }
}

// ============ AUTENTICACIÓN ============
async function manejarLogin(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    const resp = await Api.usuarios.login({ email, password });
    await iniciarSesionConToken(resp.access_token);
    document.getElementById('form-login').reset();
    cerrarModal('modal-auth');
    cambiarVista('catalogo');
    toast(`¡Bienvenido de nuevo, ${Estado.nombre}! 🌱`);
  } catch (err) {
    manejarError(err, 'iniciar sesión');
  } finally {
    btn.disabled = false;
  }
}

async function manejarRegistro(e) {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    const nombre = document.getElementById('registro-nombre').value.trim();
    const email = document.getElementById('registro-email').value.trim();
    const password = document.getElementById('registro-password').value;
    const rol = document.getElementById('registro-rol').value;

    await Api.usuarios.registrar({ nombre, email, password, rol });
    const resp = await Api.usuarios.login({ email, password });
    Estado.nombre = nombre;
    await iniciarSesionConToken(resp.access_token);

    document.getElementById('form-registro').reset();
    cerrarModal('modal-auth');
    cambiarVista('catalogo');
    toast(`¡Cuenta creada! Bienvenido a Chakra Shop, ${nombre} 🎉`);
  } catch (err) {
    manejarError(err, 'crear la cuenta');
  } finally {
    btn.disabled = false;
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

  // ---- Mega-dropdown de categorías: hover en escritorio, tap en táctil (nunca ambos a la vez, se pisarían) ----
  const navMega = document.getElementById('nav-mega-categorias');
  if (window.matchMedia('(hover: hover)').matches) {
    navMega.addEventListener('mouseenter', abrirMegaCategorias);
    navMega.addEventListener('mouseleave', cerrarMegaCategoriasConDelay);
  } else {
    document.getElementById('btn-mega-categorias').addEventListener('click', (e) => {
      e.stopPropagation();
      if (navMega.classList.contains('open')) cerrarMegaCategorias();
      else abrirMegaCategorias();
    });
  }
  document.getElementById('mega-tabs-categorias').addEventListener('click', (e) => {
    const tab = e.target.closest('.nav-mega-tab');
    if (tab) mostrarCategoriaMega(tab.dataset.categoria);
  });
  document.getElementById('mega-productos-categorias').addEventListener('click', (e) => {
    const btn = e.target.closest('.nav-mega-producto');
    if (!btn) return;
    cerrarMegaCategorias();
    document.getElementById('nav-links').classList.remove('open');
    cambiarVista('catalogo');
    abrirDetalleProducto(btn.dataset.id);
  });
  document.addEventListener('click', (e) => {
    if (!navMega.contains(e.target)) cerrarMegaCategorias();
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
      if (e.target === overlay) overlay.classList.add('hidden');
    });
  });

  document.querySelectorAll('.auth-tab').forEach((tab) => {
    tab.addEventListener('click', () => cambiarTabAuth(tab.dataset.tab));
  });

  document.getElementById('form-login').addEventListener('submit', manejarLogin);
  document.getElementById('form-registro').addEventListener('submit', manejarRegistro);

  document.getElementById('input-buscar').addEventListener('input', filtrarYRenderizar);
  document.getElementById('select-categoria').addEventListener('change', filtrarYRenderizar);
  document.getElementById('select-ubicacion').addEventListener('change', filtrarYRenderizar);
  document.getElementById('input-precio-min').addEventListener('input', filtrarYRenderizar);
  document.getElementById('input-precio-max').addEventListener('input', filtrarYRenderizar);
  document.getElementById('btn-limpiar-filtros').addEventListener('click', limpiarFiltros);
  document.getElementById('btn-refrescar-catalogo').addEventListener('click', cargarCatalogo);

  document.getElementById('productos-grid').addEventListener('click', manejarClickGrid);
  document.getElementById('landing-productos-grid').addEventListener('click', manejarClickGrid);

  document.querySelectorAll('[data-landing-cta]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.dataset.landingCta === 'registro') abrirRegistroConRol();
      if (btn.dataset.landingCta === 'explorar') cambiarVista('catalogo');
    });
  });
  document.querySelectorAll('[data-landing-registro]').forEach((btn) => {
    btn.addEventListener('click', () => abrirRegistroConRol(btn.dataset.landingRegistro));
  });

  document.getElementById('btn-cart').addEventListener('click', () => {
    abrirModal('modal-pedido');
    resetCheckout();
  });
  document.getElementById('carrito-items').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-accion]');
    if (!btn) return;
    const { accion, id } = btn.dataset;
    if (accion === 'mas') cambiarCantidadCarrito(id, 1);
    if (accion === 'menos') cambiarCantidadCarrito(id, -1);
    if (accion === 'quitar') quitarDelCarrito(id);
  });

  document.getElementById('btn-usar-mi-ubicacion').addEventListener('click', usarMiUbicacionDestino);
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
    if (calificar) { abrirDetalleProducto(calificar.dataset.productoId); return; }
    const reintentar = e.target.closest('.btn-reintentar-pago');
    if (reintentar) { reintentarPago(reintentar.dataset.pedidoId, Number(reintentar.dataset.monto)); return; }
    const certPedido = e.target.closest('.btn-ver-certificado-pedido');
    if (certPedido) { alternarCertificadoPedido(certPedido.dataset.pedidoId); return; }
  });

  document.getElementById('btn-refrescar-notificaciones').addEventListener('click', cargarNotificaciones);
  document.getElementById('btn-notif-cargar-mas').addEventListener('click', () => {
    notifMostrar += TAMANO_PAGINA_NOTIF;
    renderNotificacionesPagina();
  });

  document.getElementById('form-productor').addEventListener('submit', crearPerfilProductor);
  document.getElementById('btn-ubicacion-productor').addEventListener('click', usarMiUbicacionProductor);
  document.getElementById('form-producto').addEventListener('submit', publicarProducto);
  document.getElementById('btn-refrescar-mis-productos').addEventListener('click', cargarMisProductos);

  ['producto-nombre', 'producto-categoria', 'producto-unidad', 'producto-precio', 'producto-stock'].forEach((id) => {
    document.getElementById(id).addEventListener('input', actualizarVistaPreviaProducto);
    document.getElementById(id).addEventListener('change', actualizarVistaPreviaProducto);
  });

  document.getElementById('btn-terminar-publicacion').addEventListener('click', reiniciarFormularioProducto);
  document.getElementById('btn-empezar-publicar').addEventListener('click', () => {
    const campo = document.getElementById('producto-nombre');
    campo.scrollIntoView({ behavior: 'smooth', block: 'center' });
    campo.focus({ preventScroll: true });
  });

  document.getElementById('mis-productos-grid').addEventListener('click', (e) => {
    const btnResenas = e.target.closest('.btn-toggle-resenas');
    if (btnResenas) { alternarResenasProductor(btnResenas.dataset.id); return; }
    const btnFotos = e.target.closest('.btn-toggle-fotos');
    if (btnFotos) { alternarFotosProductor(btnFotos.dataset.id); return; }
    const btnCert = e.target.closest('.btn-ver-certificacion');
    if (btnCert) { abrirModalCertificacion(btnCert.dataset.id); return; }
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
  document.getElementById('envios-list').addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-actualizar-envio');
    if (!btn) return;
    const envioId = btn.dataset.envioId;
    const select = document.querySelector(`.select-estado-envio[data-envio-id="${envioId}"]`);
    actualizarEstadoEnvio(envioId, select.value);
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
    if (e.target.closest('#btn-perfil-ir-pedidos')) { cambiarVista('mis-pedidos'); return; }
  });
}

function cambiarTabAuth(tab) {
  document.querySelectorAll('.auth-tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
  document.getElementById('form-login').classList.toggle('hidden', tab !== 'login');
  document.getElementById('form-registro').classList.toggle('hidden', tab !== 'registro');
}

// ============ INICIO ============
function iniciar() {
  cargarSesionGuardada();
  actualizarUIAuth();
  inicializarEventos();
  cambiarVista(Estado.token ? 'catalogo' : 'landing');
}

document.addEventListener('DOMContentLoaded', iniciar);
