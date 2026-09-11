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
  imagenesPorProducto: {}, // producto_id -> [{id, url, orden}], caché en memoria para el gestor de fotos
};

const SESSION_KEY = 'agro_sesion';

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

function formatearMoneda(valor) {
  const n = Number(valor) || 0;
  return `S/ ${n.toFixed(2)}`;
}

function hashCorto(hash) {
  if (!hash) return '—';
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

const EVENTOS_CERTIFICACION = {
  cosecha_registrada: { emoji: '<i class="ti ti-wheat"></i>', texto: 'Cosecha registrada' },
  certificado_productor: { emoji: '<i class="ti ti-circle-check"></i>', texto: 'Certificado por el productor' },
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
  return `<span class="pill pill-estado ${clase}">${icono} ${estado || 'pendiente'}</span>`;
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
}

// ============ MODALES ============
function abrirModal(id) { document.getElementById(id).classList.remove('hidden'); }
function cerrarModal(id) { document.getElementById(id).classList.add('hidden'); }

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

function renderProductoMega(p) {
  return `
    <button type="button" class="nav-mega-producto" data-id="${p.id}">
      ${renderMediaProducto(p, 'nav-mega-producto-img')}
      <span class="nav-mega-producto-nombre">${escapeAttr(p.nombre)}</span>
      <span class="nav-mega-producto-precio">${formatearMoneda(p.precio)}</span>
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
  const delCategoria = Estado.productos.filter((p) => normalizarCategoria(p.categoria) === categoria).slice(0, 4);
  navCatCache[categoria] = delCategoria;
  pintarProductosNavCat(categoria, delCategoria);
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
// así que esto solo cubre la búsqueda de texto libre. Reutiliza renderProductoMega (misma tarjeta
// del mega-dropdown) y el criterio de "destacados" ya usado en el landing.
async function renderRecomendacionesBusqueda() {
  if (!Estado.productos.length) {
    try { Estado.productos = await Api.productos.listar(); } catch { /* seguimos con lo que haya */ }
  }
  const destacados = ordenarPorDestacado(Estado.productos).slice(0, 4);
  pintarResultadosBusqueda(destacados, 'Recomendaciones', 'sparkles');
}

function pintarResultadosBusqueda(productos, titulo, icono) {
  document.getElementById('search-panel-heading').innerHTML = `<i class="ti ti-${icono}"></i> ${titulo}`;
  const grid = document.getElementById('search-panel-grid');
  const vacio = document.getElementById('search-panel-empty');
  if (!productos.length) {
    grid.innerHTML = '';
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
  const textoOriginal = document.getElementById('input-busqueda-panel').value.trim();
  const texto = textoOriginal.toLowerCase();
  if (!texto) {
    clearTimeout(debounceRegistrarBusqueda);
    renderRecomendacionesBusqueda();
    return;
  }
  registrarBusquedaConDebounce(texto);
  const resultados = Estado.productos.filter((p) =>
    `${p.nombre} ${p.categoria || ''} ${p.productor_nombre || ''}`.toLowerCase().includes(texto));
  pintarResultadosBusqueda(resultados, `Resultados para "${textoOriginal}"`, 'search');
}

function abrirPanelBusqueda() {
  cerrarTodosLosNavCat();
  cerrarNavAccount();
  document.getElementById('nav-links').classList.remove('open');
  const input = document.getElementById('input-busqueda-panel');
  input.value = '';
  document.getElementById('panel-busqueda').classList.remove('hidden');
  renderRecomendacionesBusqueda();
  cargarBusquedasPopulares();
  input.focus();
}

function cerrarPanelBusqueda() {
  document.getElementById('panel-busqueda').classList.add('hidden');
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
          <span class="pill pill-categoria">${iconoCategoria(p.categoria)} ${nombreCategoria(p.categoria)}</span>
          ${tieneCalificacion ? `<span class="product-rating"><span class="rating-stars"><i class="ti ti-star estrella-llena"></i></span> ${Number(p.calificacion_promedio).toFixed(1)} <span class="rating-count-mini">(${p.total_resenas})</span></span>` : ''}
        </div>
        <h4>${p.nombre}</h4>
        <span class="product-productor"><i class="ti ti-plant-2"></i> ${p.productor_nombre || 'Productor local'}</span>
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
        <span class="detalle-productor-icono"><i class="ti ti-plant-2"></i></span>
        <div class="detalle-productor-info">
          <span class="detalle-productor-label">Cultivado por</span>
          <h3 class="detalle-productor-nombre">${escapeAttr(nombre)}</h3>
          ${ubicacionTexto ? `<span class="detalle-productor-ubicacion"><i class="ti ti-map-pin"></i> ${escapeAttr(ubicacionTexto)}</span>` : ''}
        </div>
      </div>
      ${tieneMapa ? `<div id="mapa-productor-detalle" class="mapa-mini"></div>` : ''}
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
        ${[1, 2, 3, 4, 5].map((v) => `<button type="button" data-valor="${v}"><i class="ti ti-star"></i></button>`).join('')}
      </div>
      <textarea placeholder="Cuéntanos qué te pareció (opcional)"></textarea>
      <button type="submit" class="btn btn-primary">Publicar reseña</button>
    </form>`
    : '<p class="muted"><i class="ti ti-lock"></i> Inicia sesión para dejar tu propia reseña.</p>';

  return `
    <div class="resenas-section">
      <h3><i class="ti ti-star"></i> Reseñas ${resenas.length ? `(${resenas.length})` : ''}</h3>
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

function renderHeroYGaleriaDetalle(p) {
  const imagenes = Array.isArray(p.imagenes) ? [...p.imagenes].sort((a, b) => a.orden - b.orden) : [];
  const emoji = emojiParaProducto(p);
  const heroUrl = imagenes[0]?.url || p.imagen_url || null;
  const galeriaExtra = imagenes.slice(1);
  const slug = slugProducto(p.nombre);
  const conVideo = PRODUCTOS_CON_VIDEO.includes(slug);
  const textos = conVideo ? textosVideoGaleria(p) : [];

  // Posiciones en el mismo orden que hoy: 0 = hero, 1+ = fotos extra de la galería. Un video solo
  // reemplaza una posición que YA tiene una foto real ahí (esa foto pasa a ser el poster) — si el
  // producto tiene menos fotos que videos definidos, esa posición sigue sin existir.
  const posiciones = [{ url: heroUrl, esHero: true }, ...galeriaExtra.map((img) => ({ url: img.url, esHero: false }))];

  return posiciones.map((pos, i) => {
    const idAttr = pos.esHero ? ' id="detalle-seccion-descripcion"' : '';
    const claseBase = pos.esHero ? 'detalle-hero' : 'detalle-galeria-item';

    if (conVideo && pos.url && textos[i]) {
      const videoUrl = `assets/videos/${slug}-video-${i + 1}.mp4`;
      const t = textos[i];
      return `
        <div class="${claseBase} detalle-scroll-seccion detalle-video-seccion detalle-video-seccion--${t.tema}"${idAttr} style="background:${t.fondo}">
          <div class="detalle-video-wrap">
            <video class="detalle-video" muted playsinline preload="metadata" poster="${escapeAttr(pos.url)}" data-src="${escapeAttr(videoUrl)}"></video>
            <button type="button" class="detalle-video-replay hidden" aria-label="Reproducir de nuevo"><i class="ti ti-player-play"></i></button>
            <div class="detalle-video-degradado"></div>
            <div class="detalle-video-texto">
              <span class="detalle-video-etiqueta">${escapeAttr(t.etiqueta)}</span>
              <h3>${escapeAttr(t.titulo)}</h3>
              <p>${escapeAttr(t.parrafo)}</p>
            </div>
          </div>
        </div>`;
    }

    return `
      <div class="${claseBase} detalle-scroll-seccion"${idAttr}>
        ${pos.url
          ? `<img src="${escapeAttr(pos.url)}" alt="${escapeAttr(p.nombre || '')}" ${pos.esHero ? '' : 'loading="lazy"'} onerror="this.parentElement.textContent='${emoji}'">`
          : emoji}
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

function renderPaginaDetalleProducto(p, resenas, productor) {
  const contenido = document.getElementById('detalle-producto-content');
  const favorito = esFavorito(p.id);
  const stockBajo = p.stock > 0 && p.stock <= 5;
  const sinStock = p.stock <= 0;
  const precioHtml = `${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span>`;

  contenido.innerHTML = `
    <div class="detalle-sticky-header" id="detalle-sticky-header">
      <button type="button" class="detalle-volver" id="btn-volver-detalle"><i class="ti ti-arrow-left"></i> Volver</button>
      <div class="detalle-sticky-info">
        <span class="detalle-sticky-nombre">${escapeAttr(p.nombre)}</span>
        <span class="detalle-sticky-precio">${precioHtml}</span>
        ${p.calificacion_promedio ? `<span class="detalle-sticky-rating"><span class="rating-stars">${renderEstrellas(p.calificacion_promedio)}</span> ${Number(p.calificacion_promedio).toFixed(1)}</span>` : ''}
      </div>
      <button type="button" class="btn btn-primary" id="btn-agregar-sticky" ${sinStock ? 'disabled' : ''}>
        ${sinStock ? 'Agotado' : '+ Agregar al carrito'}
      </button>
    </div>

    <nav class="detalle-tabs" id="detalle-tabs">
      <button type="button" class="detalle-tab active" data-target="detalle-seccion-descripcion">Descripción</button>
      <button type="button" class="detalle-tab" data-target="detalle-seccion-productor">Productor</button>
      <button type="button" class="detalle-tab" data-target="detalle-seccion-certificacion">Certificación</button>
      <button type="button" class="detalle-tab" data-target="detalle-seccion-resenas">Reseñas</button>
    </nav>

    ${renderHeroYGaleriaDetalle(p)}

    <div class="detalle-info-columna">
      <div class="detalle-encabezado">
        <span class="pill pill-categoria">${iconoCategoria(p.categoria)} ${nombreCategoria(p.categoria)}</span>
        <button type="button" class="btn-favorito btn-favorito-detalle ${favorito ? 'activo' : ''}" data-id="${p.id}" aria-label="Favorito" aria-pressed="${favorito}">
          <i class="ti ti-heart"></i> ${favorito ? 'Guardado' : 'Guardar'}
        </button>
      </div>

      <div class="detalle-cabecera">
        <h2>${escapeAttr(p.nombre)}</h2>
        ${renderResumenCalificacion(p)}
        <div class="detalle-precio-row">
          <span class="product-price">${precioHtml}</span>
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

      <section class="detalle-seccion-bloque detalle-scroll-seccion" id="detalle-seccion-productor">
        <h3 class="detalle-seccion-titulo"><i class="ti ti-plant-2"></i> De la chacra a tu mesa</h3>
        ${renderBloqueProductor(p, productor)}
      </section>

      <section class="detalle-seccion-bloque detalle-scroll-seccion" id="detalle-seccion-certificacion">
        <h3 class="detalle-seccion-titulo"><i class="ti ti-link"></i> Certificación de trazabilidad</h3>
        <div id="detalle-certificacion-contenido"><div class="verificar-cargando"><div class="spinner"></div><p>Cargando certificación...</p></div></div>
      </section>

      <section class="detalle-seccion-bloque detalle-scroll-seccion" id="detalle-seccion-resenas">
        ${renderSeccionResenas(resenas)}
      </section>
    </div>
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

  // Botón grande (bajo el precio) y botón compacto del header fijo comparten la misma cantidad
  // seleccionada — a diferencia del modal viejo, agregar al carrito ya NO navega fuera de la
  // página (no hay "cerrar" en una página propia; el usuario sigue viendo el producto).
  const agregarConFeedback = (btn) => {
    agregarAlCarrito(p.id, cantidadSeleccionada);
    destellarBoton(btn, '<i class="ti ti-check"></i> Agregado');
  };
  document.getElementById('btn-agregar-detalle')?.addEventListener('click', (e) => agregarConFeedback(e.currentTarget));
  document.getElementById('btn-agregar-sticky')?.addEventListener('click', (e) => agregarConFeedback(e.currentTarget));

  contenido.querySelector('.btn-favorito-detalle')?.addEventListener('click', (e) => {
    manejarClickFavorito(e.currentTarget);
  });

  document.getElementById('btn-volver-detalle')?.addEventListener('click', () => cambiarVista(vistaAntesDeDetalle));

  if (hayCoordenadas(productor?.latitud, productor?.longitud)) {
    crearMapaSoloLectura('mapa-productor-detalle', productor.latitud, productor.longitud, '<i class="ti ti-map-pin"></i>', '#2d6a4f', escapeAttr(productor.nombre || 'Productor'));
  }

  inicializarPestanasDetalle();
  inicializarScrollRevealDetalle();
  cargarCertificacionDetalle(p.id);

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

// Pestañas: scroll suave al ancla real, con offset por el header+tabs (ambos sticky) via
// scroll-margin-top — así el destino no queda tapado detrás de las barras fijas.
function inicializarPestanasDetalle() {
  const headerEl = document.getElementById('detalle-sticky-header');
  const tabsEl = document.getElementById('detalle-tabs');
  if (!headerEl || !tabsEl) return;

  tabsEl.style.top = `${headerEl.offsetHeight}px`;
  const offset = headerEl.offsetHeight + tabsEl.offsetHeight + 16;
  document.querySelectorAll('#detalle-producto-content [id^="detalle-seccion-"]').forEach((sec) => {
    sec.style.scrollMarginTop = `${offset}px`;
  });

  tabsEl.querySelectorAll('.detalle-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      tabsEl.querySelectorAll('.detalle-tab').forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(tab.dataset.target)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
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
      <div class="certificacion-qr-bloque">
        <img src="${escapeAttr(Api.certificacion.qrUrl(productoId))}" alt="Código QR de certificación" class="certificacion-qr" id="certificacion-qr-img-detalle">
        <p class="muted certificacion-qr-nota">Comparte este QR con tus compradores para que verifiquen el origen del producto.</p>
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
      <button class="carrito-remove" data-accion="quitar" data-id="${i.producto_id}"><i class="ti ti-trash"></i></button>
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
    circulo.innerHTML = n < paso ? '<i class="ti ti-check"></i>' : String(n);
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
    // Refresca el stock en memoria (recién descontado por el pedido) para que el mega-dropdown y
    // el panel de búsqueda no sigan mostrando cantidades desactualizadas.
    try { Estado.productos = await Api.productos.listar(); } catch { /* no bloquea el checkout si falla */ }

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
      <div class="icono-grande"><i class="ti ti-confetti"></i></div>
      <h3>¡Pago aprobado!</h3>
      <p>Tu pedido <strong>#${idCorto}</strong> está confirmado.</p>
      <p>Total pagado: <strong>${formatearMoneda(monto)}</strong></p>`;
  } else {
    const pareceJson = errorMsg && (errorMsg.trim().startsWith('[') || errorMsg.trim().startsWith('{'));
    const motivo = (errorMsg && !pareceJson)
      ? errorMsg
      : `El pago quedó en estado "${resultado?.estado || 'pendiente'}".`;
    cont.innerHTML = `
      <div class="icono-grande"><i class="ti ti-alert-triangle"></i></div>
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
  cont.innerHTML = filtrados.map((d, i) => renderTarjetaPedido(d.pedido, d.envio, d.pago, d.ruta, i)).join('');

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

function renderTarjetaPedido(pedido, envio, pago, ruta, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
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
    <div class="pedido-card" style="animation-delay:${retraso}ms" data-pedido-id="${pedido.id}">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Pedido #${String(pedido.id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFecha(pedido.fecha_creacion)}</div>
        </div>
        <div class="pedido-total-header">${formatearMoneda(total)}</div>
      </div>

      <div class="estado-badges-row">
        <div class="estado-badge-item"><span class="estado-badge-label">Pedido</span>${badgeEstadoEnvio(pedido.estado)}</div>
        <div class="estado-badge-item"><span class="estado-badge-label">Pago</span>${pago ? badgeEstadoEnvio(pago.estado) : '<span class="pill pill-estado pill-default">—</span>'}</div>
        <div class="estado-badge-item"><span class="estado-badge-label">Envío</span>${envio ? badgeEstadoEnvio(envio.estado) : '<span class="pill pill-estado pill-default">—</span>'}</div>
      </div>

      <ul class="pedido-items">${itemsHtml}</ul>

      ${pagoRechazado ? `
      <div class="pago-rechazado-aviso">
        <span><i class="ti ti-alert-triangle"></i> Tu pago fue rechazado.</span>
        <button type="button" class="btn btn-outline btn-sm btn-reintentar-pago" data-pedido-id="${pedido.id}" data-monto="${total}">Reintentar pago</button>
      </div>` : ''}

      ${activo ? renderBloqueRuta(envio, ruta) : ''}
      ${entregado ? '<div class="resena-cta"></div>' : ''}
      ${entregado ? `
      <div class="pedido-certificado-cta">
        <button type="button" class="btn btn-outline btn-sm btn-ver-certificado-pedido" data-pedido-id="${pedido.id}"><i class="ti ti-link"></i> Ver certificado de compra</button>
        <div class="panel-expandible hidden" data-panel="certificado-pedido" data-pedido-id="${pedido.id}"></div>
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
  pedido_creado: 'package',
  envio_actualizado: 'truck',
};

function iconoNotificacion(tipo) {
  return `<i class="ti ti-${NOTIF_ICONOS[tipo] || 'bell'}"></i>`;
}

function renderVacioNotificaciones(tipo) {
  const vacio = document.getElementById('notificaciones-empty');
  const plantillas = {
    'no-sesion': `
      <span class="empty-state-icon"><i class="ti ti-lock"></i></span>
      <p><strong>Inicia sesión para ver tus notificaciones</strong></p>`,
    'sin-notificaciones': `
      <span class="empty-state-icon"><i class="ti ti-bell"></i></span>
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
    cambiarVista('inicio');
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
    inicializarMapaUbicacionChacra();
    document.getElementById('productor-resumen').innerHTML = renderSkeletonResumen(3);
    // Las 3 cargas son independientes entre sí (cada una pinta su propia sección), pero el
    // resumen necesita las tres resueltas antes de poder calcular sus métricas — de ahí el
    // Promise.all en vez de simplemente dispararlas sueltas como antes.
    await Promise.all([cargarMisProductos(), cargarMisChacras(), cargarRegistrosProduccionProductor()]);
    document.getElementById('productor-resumen').innerHTML = renderResumenProductor();
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
  document.getElementById('titulo-form-producto').innerHTML = '<i class="ti ti-seedling"></i> Publicar nuevo producto';
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
    document.getElementById('titulo-form-producto').innerHTML = `<i class="ti ti-circle-check"></i> ${escapeAttr(nuevoProducto.nombre)}`;
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

// ---- Gestión Económica: siembra ↔ unidad de medida (mismo patrón que "Publicar producto") ----
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
          <span class="pill pill-categoria">${iconoCategoria(p.categoria)} ${nombreCategoria(p.categoria)}</span>
        </div>
        <h4>${p.nombre}</h4>
        <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
        <span class="product-stock ${stockBajo || sinStock ? 'low' : ''}">${p.stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles` : 'Sin stock'}</span>

        <div class="productor-card-acciones">
          <button type="button" class="btn btn-outline btn-sm btn-toggle-resenas" data-id="${p.id}">
            <i class="ti ti-star"></i> ${tieneCalificacion ? `${p.total_resenas} reseña${p.total_resenas === 1 ? '' : 's'}` : 'Reseñas'}
          </button>
          <button type="button" class="btn btn-outline btn-sm btn-toggle-fotos" data-id="${p.id}"><i class="ti ti-camera"></i> Fotos</button>
          <button type="button" class="btn btn-outline btn-sm btn-ver-certificacion" data-id="${p.id}"><i class="ti ti-link"></i> Certificación</button>
        </div>

        <div class="panel-expandible hidden" data-panel="resenas" data-id="${p.id}"></div>
        <div class="panel-expandible hidden" data-panel="fotos" data-id="${p.id}"></div>
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
    cargarMisChacras();
  } catch (err) {
    manejarError(err, 'registrar la chacra');
  } finally {
    btn.disabled = false;
  }
}

function renderTarjetaChacra(c, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  return `
    <div class="pedido-card" style="animation-delay:${retraso}ms">
      <div class="pedido-card-header">
        <code class="pedido-id">${escapeAttr(c.codigo)}</code>
      </div>
      ${c.nombre ? `<p>${escapeAttr(c.nombre)}</p>` : ''}
      <p class="pedido-fecha"><i class="ti ti-map-pin"></i> ${Number(c.ubicacion_latitud).toFixed(5)}, ${Number(c.ubicacion_longitud).toFixed(5)}</p>
    </div>`;
}

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
  const misProductos = (Estado.productos || []).filter((p) => p.productor_id === Estado.productorId);
  const chacras = Estado.chacras || [];
  const registros = Estado.registrosProduccion || [];
  const enCurso = registros.filter((r) => r.estado !== 'cosechado');

  const proximaCosecha = enCurso
    .filter((r) => r.fecha_cosecha_estimada)
    .sort((a, b) => new Date(a.fecha_cosecha_estimada) - new Date(b.fecha_cosecha_estimada))[0];

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
            <span class="resumen-stat-valor">${misProductos.length}</span>
            <span class="resumen-stat-label">${misProductos.length === 1 ? 'Producto publicado' : 'Productos publicados'}</span>
          </div>
        </div>
      </div>
      ${proximaCosecha ? `
      <div class="resumen-destacado">
        <i class="ti ti-calendar-event"></i>
        Próxima cosecha estimada: <strong>${escapeAttr(proximaCosecha.cultivo)}</strong> el ${formatearFecha(proximaCosecha.fecha_cosecha_estimada)}
      </div>` : ''}
      <div class="resumen-acciones">
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-publicar"><i class="ti ti-seedling"></i> Publicar producto</button>
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-siembra"><i class="ti ti-plant-2"></i> Registrar siembra</button>
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-chacra"><i class="ti ti-map-pin"></i> Registrar chacra</button>
      </div>
    </div>`;
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

function renderBannerDisponibilidad(estado) {
  const clave = (estado || 'desconectado').toLowerCase();
  const info = DISPONIBILIDAD_INFO[clave] || DISPONIBILIDAD_INFO.desconectado;
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
  document.getElementById('repartidor-resumen').innerHTML = renderResumenRepartidor();
  iniciarSeguimientoRepartidor();
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

function seleccionarSeccionVerificador(seccion) {
  document.querySelectorAll('#view-panel-verificador .verificador-tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.vseccion === seccion);
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
        <div class="perfil-dato"><span class="perfil-dato-label">Cosecha real</span><span class="perfil-dato-valor">${formatearFecha(r.fecha_cosecha_real)}</span></div>
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
    await Api.productores.produccion.verificacion.aprobar(registroId);
    toast('Cosecha aprobada — el producto ya está en el catálogo del productor, en borrador 🌱');
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
        <div class="perfil-dato"><span class="perfil-dato-label">Fecha</span><span class="perfil-dato-valor">${formatearFecha(h.fecha)}</span></div>
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
  await cargarInvitaciones();
}

// El backend solo manda `usado` (bool) y `fecha_expiracion` — "Expirada" no viaja ya resuelta
// desde /admin/invitaciones (a diferencia de /admin/invitaciones/validar/{codigo}, que sí la
// calcula), así que se deriva acá comparando contra la hora actual del navegador.
function estadoInvitacion(inv) {
  if (inv.usado) return { clave: 'usada', etiqueta: 'Usada' };
  if (new Date(inv.fecha_expiracion) < new Date()) return { clave: 'expirada', etiqueta: 'Expirada' };
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
      <p class="pedido-fecha">Creada: ${formatearFecha(inv.fecha_creacion)} · Vence: ${formatearFecha(inv.fecha_expiracion)}</p>
      ${estado.clave === 'vigente'
        ? `<button type="button" class="btn btn-outline btn-sm btn-revocar-invitacion" data-invitacion-id="${escapeAttr(inv.id)}">Revocar</button>`
        : ''}
    </div>`;
}

async function cargarInvitaciones() {
  const cont = document.getElementById('invitaciones-lista');
  const vacio = document.getElementById('invitaciones-empty');
  try {
    const invitaciones = await Api.admin.listarInvitaciones();
    vacio.classList.toggle('hidden', invitaciones.length > 0);
    cont.innerHTML = invitaciones.map(renderInvitacionCard).join('');
    document.getElementById('admin-resumen').innerHTML = renderResumenAdmin(invitaciones);
  } catch (err) {
    manejarError(err, 'cargar las invitaciones');
  }
}

// ---- Resumen / dashboard de "Panel Admin" — reusa estadoInvitacion() (la misma función que ya
// clasifica cada tarjeta de la lista) sobre el array que acaba de traer cargarInvitaciones(). ----
function renderResumenAdmin(invitaciones) {
  const porEstado = { vigente: 0, usada: 0, expirada: 0 };
  let masProximaAVencer = null;
  invitaciones.forEach((inv) => {
    const estado = estadoInvitacion(inv);
    porEstado[estado.clave]++;
    if (estado.clave === 'vigente' && (!masProximaAVencer || new Date(inv.fecha_expiracion) < new Date(masProximaAVencer.fecha_expiracion))) {
      masProximaAVencer = inv;
    }
  });

  let avisoVencimiento = '';
  if (masProximaAVencer) {
    const diasRestantes = Math.max(0, Math.ceil((new Date(masProximaAVencer.fecha_expiracion) - new Date()) / 86400000));
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
      </div>
      ${avisoVencimiento ? `<div class="resumen-destacado resumen-destacado-urgente">${avisoVencimiento}</div>` : ''}
      <div class="resumen-acciones">
        <button type="button" class="btn btn-primary btn-sm" id="btn-resumen-generar-codigo"><i class="ti ti-plus"></i> Generar nuevo código</button>
      </div>
    </div>`;
}

function mostrarInvitacionGenerada(inv) {
  document.getElementById('invitacion-codigo-texto').textContent = inv.codigo;
  document.getElementById('invitacion-expira-texto').textContent = `Vence: ${formatearFecha(inv.fecha_expiracion)}`;
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

function renderTarjetaPropuesta(envio, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  return `
    <div class="propuesta-card" style="animation-delay:${retraso}ms">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Envío #${String(envio.id).slice(0, 8)} · Pedido #${String(envio.pedido_id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFecha(envio.fecha_creacion)}</div>
        </div>
        ${badgeEstadoEnvio(envio.estado || 'propuesto')}
      </div>
      <div class="propuesta-actions">
        <button type="button" class="btn btn-outline btn-rechazar-propuesta" data-envio-id="${envio.id}"><i class="ti ti-x"></i> Rechazar</button>
        <button type="button" class="btn btn-primary btn-aceptar-propuesta" data-envio-id="${envio.id}"><i class="ti ti-check"></i> Aceptar envío</button>
      </div>
    </div>`;
}

function renderTarjetaEnvio(envio, ruta, indice = 0) {
  const activo = ['asignado', 'en_camino'].includes(envio.estado);
  const finalizado = ['entregado', 'cancelado', 'rechazado'].includes(envio.estado);
  const retraso = Math.min(indice, 12) * 35;
  return `
    <div class="pedido-card ${activo ? 'pedido-card-activo' : ''}" style="animation-delay:${retraso}ms">
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
  await Promise.all(activos.map(async (e) => {
    try {
      rutasPorEnvio[e.id] = await Api.transporte.ruta(e.id);
    } catch { /* seguimiento de ruta no disponible por ahora */ }
  }));

  cont.innerHTML = mios.map((e, i) => renderTarjetaEnvio(e, rutasPorEnvio[e.id], i)).join('');
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
  comprador: { icono: '<i class="ti ti-shopping-cart"></i>', texto: 'Comprador' },
  productor: { icono: '<i class="ti ti-tractor"></i>', texto: 'Productor' },
  repartidor: { icono: '<i class="ti ti-truck"></i>', texto: 'Repartidor' },
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
      <h3><i class="ti ti-shopping-cart"></i> Comprador</h3>
      <p class="muted">Explora el catálogo y haz seguimiento de tus compras.</p>
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-ir-pedidos">Ir a Mis Pedidos →</button>
    </div>`;
}

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
  return `
    <div class="card-panel">
      <h3><i class="ti ti-tractor"></i> Datos de productor</h3>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Comunidad / Región</span><span class="perfil-dato-valor">${escapeAttr(extra.comunidad || '—')}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Contacto</span><span class="perfil-dato-valor">${escapeAttr(extra.contacto || '—')}</span></div>
      </div>
      ${hayCoordenadas(extra.latitud, extra.longitud) ? `
      <div class="mapa-bloque">
        <h4 class="mapa-titulo"><i class="ti ti-map-pin"></i> Ubicación de tu chakra</h4>
        <div id="mapa-perfil-productor" class="mapa-mini"></div>
      </div>` : `<div class="mapa-bloque-neutro"><span class="icon"><i class="ti ti-map-pin"></i></span> No has marcado la ubicación de tu chakra todavía.</div>`}
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-ir-panel" style="margin-top:16px;">Ir a Mis Productos →</button>
    </div>
    ${renderGestionEconomica(registrosProduccion || [])}`;
}

// ============ GESTIÓN ECONÓMICA (Módulo 1) ============
const OPCIONES_UNIDAD_MEDIDA_HTML = `
  <option value="kg">Kilogramo (kg)</option>
  <option value="unidad">Unidad</option>
  <option value="saco">Saco</option>
  <option value="arroba">Arroba</option>`;

function renderResumenEconomico(registros) {
  const totalInvertido = registros.reduce((suma, r) => suma + (Number(r.costo_total) || 0), 0);

  const cosechados = registros.filter((r) => r.estado === 'cosechado');
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

  return `
    <div class="card-panel gestion-economica-resumen">
      <h3><i class="ti ti-chart-bar"></i> Gestión Económica</h3>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">Total invertido</span><span class="perfil-dato-valor">${formatearMoneda(totalInvertido)}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Total cosechado</span><span class="perfil-dato-valor">${cosechados.length ? `${totalCosechadoKg.toFixed(2)} kg` : '—'}</span></div>
        <div class="perfil-dato"><span class="perfil-dato-label">Ganancia acumulada</span><span class="perfil-dato-valor ${gananciaAcumulada >= 0 ? 'ganancia-positiva' : 'ganancia-negativa'}">${conGanancia.length ? formatearMoneda(gananciaAcumulada) : '—'}</span></div>
      </div>
      ${sinCalcular > 0 ? `<p class="muted produccion-nota-resumen"><i class="ti ti-info-circle"></i> ${sinCalcular} registro${sinCalcular > 1 ? 's' : ''} cosechado${sinCalcular > 1 ? 's' : ''} sin datos suficientes para calcular ganancia — no se incluye${sinCalcular > 1 ? 'n' : ''} en el total.</p>` : ''}
    </div>`;
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
          <input type="number" id="produccion-parcelas" min="1" step="1" required placeholder="1">
        </label>
        <label>Ubicación de la cosecha (opcional)
          <input type="text" id="produccion-ubicacion" placeholder="Ej. Parcela norte">
        </label>
        <label>Costo de semillas (S/)
          <input type="number" id="produccion-costo-semillas" min="0" step="0.01" value="0">
        </label>
        <label>Costo de insumos (S/)
          <input type="number" id="produccion-costo-insumos" min="0" step="0.01" value="0">
        </label>
        <label>Unidad de medida
          <select id="produccion-unidad">${OPCIONES_UNIDAD_MEDIDA_HTML}</select>
        </label>
        <label id="produccion-equivalencia-bloque" class="hidden">Equivalencia a kg (1 unidad = X kg)
          <input type="number" id="produccion-equivalencia" min="0.0001" step="0.0001" placeholder="Ej. 11.5">
        </label>
        <label>Fecha de siembra
          <input type="date" id="produccion-fecha-siembra" required>
        </label>
        <label>Fecha estimada de cosecha
          <input type="date" id="produccion-fecha-cosecha-estimada" required>
        </label>
        <button type="submit" class="btn btn-primary">Registrar siembra</button>
      </form>
    </div>`;
}

function renderTarjetaRegistroProduccion(r, indice = 0) {
  const retraso = Math.min(indice, 12) * 35;
  const esPlanificado = r.estado === 'planificado';
  const badge = esPlanificado
    ? '<span class="pill pill-estado pill-pendiente"><i class="ti ti-seedling"></i> Planificado</span>'
    : '<span class="pill pill-estado pill-entregado"><i class="ti ti-circle-check"></i> Cosechado</span>';

  const datosBase = `
    <div class="perfil-datos-grid">
      <div class="perfil-dato"><span class="perfil-dato-label">Parcelas</span><span class="perfil-dato-valor">${r.numero_parcelas}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Ubicación</span><span class="perfil-dato-valor">${escapeAttr(r.ubicacion_cosecha || '—')}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Siembra</span><span class="perfil-dato-valor">${formatearFecha(r.fecha_siembra)}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Cosecha estimada</span><span class="perfil-dato-valor">${formatearFecha(r.fecha_cosecha_estimada)}</span></div>
      <div class="perfil-dato"><span class="perfil-dato-label">Costo total</span><span class="perfil-dato-valor">${formatearMoneda(r.costo_total)}</span></div>
    </div>`;

  let bloqueEstado;
  if (esPlanificado) {
    bloqueEstado = `
      <button type="button" class="btn btn-outline btn-sm btn-toggle-completar-cosecha" data-id="${r.id}">Completar cosecha</button>
      <form class="form-grid form-completar-cosecha hidden" data-id="${r.id}">
        <label>Cantidad cosechada (${escapeAttr(r.unidad_medida)})
          <input type="number" name="cantidad_cosechada" min="0.01" step="0.01" required>
        </label>
        <label>Fecha real de cosecha
          <input type="date" name="fecha_cosecha_real" required>
        </label>
        <label>Costo de mano de obra final (S/)
          <input type="number" name="costo_mano_obra" min="0" step="0.01" value="0" required>
        </label>
        <label>Costo de envío final (S/)
          <input type="number" name="costo_envio" min="0" step="0.01" value="0" required>
        </label>
        <button type="submit" class="btn btn-primary btn-sm">Confirmar cosecha</button>
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

function renderGestionEconomica(registros) {
  const lista = registros.length
    ? registros.map(renderTarjetaRegistroProduccion).join('')
    : '<p class="muted" style="text-align:center;padding:16px 0;">Aún no registraste ninguna siembra.</p>';

  return `
    ${renderResumenEconomico(registros)}
    ${renderFormRegistroProduccion()}
    <div id="produccion-lista">${lista}</div>`;
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
    <div class="card-panel">
      <h3><i class="ti ti-truck"></i> Datos de repartidor</h3>
      <div class="perfil-datos-grid">
        <div class="perfil-dato"><span class="perfil-dato-label">DNI</span><span class="perfil-dato-valor perfil-dato-mono">${escapeAttr(ocultarDni(extra.dni))}</span></div>
      </div>
      ${renderBannerDisponibilidad(extra.estado_disponibilidad)}
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-ir-envios" style="margin-top:16px;">Ir a Gestionar Envíos →</button>
    </div>`;
}

function renderPerfil(usuario, extra, registrosProduccion) {
  const rolInfo = ROL_INFO_PERFIL[usuario.rol] || { icono: '<i class="ti ti-user"></i>', texto: usuario.rol || 'Usuario' };

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
  if (usuario.rol === 'productor') seccionRol = renderPerfilProductor(extra, registrosProduccion);
  else if (usuario.rol === 'repartidor') seccionRol = renderPerfilRepartidor(extra);

  return `
    ${headerHtml}
    ${seccionRol}
    <div class="card-panel">
      <button type="button" class="btn btn-outline btn-block" id="btn-perfil-logout"><i class="ti ti-logout"></i> Cerrar sesión</button>
    </div>`;
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
    cambiarVista(vistaInicialPara(Estado.rol));
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

    const btnDescubre = e.target.closest('.nav-cat-descubre-link');
    if (btnDescubre) {
      cerrarTodosLosNavCat();
      document.getElementById('nav-links').classList.remove('open');
      const accion = btnDescubre.dataset.accion;
      if (accion === 'buscar') {
        abrirPanelBusqueda();
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
      if (e.target === overlay) overlay.classList.add('hidden');
    });
  });

  document.querySelectorAll('.auth-tab').forEach((tab) => {
    tab.addEventListener('click', () => cambiarTabAuth(tab.dataset.tab));
  });

  document.getElementById('form-login').addEventListener('submit', manejarLogin);
  document.getElementById('form-registro').addEventListener('submit', manejarRegistro);
  document.getElementById('registro-rol').addEventListener('change', actualizarCampoCodigoInvitacion);

  document.getElementById('btn-abrir-busqueda').addEventListener('click', abrirPanelBusqueda);
  document.getElementById('panel-busqueda').addEventListener('click', (e) => {
    if (e.target.id === 'panel-busqueda') cerrarPanelBusqueda();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !document.getElementById('panel-busqueda').classList.contains('hidden')) {
      cerrarPanelBusqueda();
    }
  });
  document.getElementById('input-busqueda-panel').addEventListener('input', buscarEnPanel);
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
    if (calificar) { cambiarVista('detalle-producto', calificar.dataset.productoId); return; }
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
  document.getElementById('form-chacra').addEventListener('submit', crearChacra);
  document.getElementById('btn-ubicacion-chacra').addEventListener('click', usarMiUbicacionChacra);
  document.getElementById('form-producto').addEventListener('submit', publicarProducto);
  document.getElementById('btn-refrescar-mis-productos').addEventListener('click', cargarMisProductos);

  // Accesos rápidos del resumen de "Panel del Productor" — delegados sobre #productor-resumen
  // (su contenido se reemplaza por completo en cada iniciarPanelProductor(), así que un
  // addEventListener directo sobre los botones no sobreviviría al primer refresco).
  document.getElementById('productor-resumen').addEventListener('click', (e) => {
    if (e.target.closest('#btn-resumen-publicar')) {
      document.getElementById('producto-nombre')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      document.getElementById('producto-nombre')?.focus({ preventScroll: true });
      return;
    }
    if (e.target.closest('#btn-resumen-chacra')) {
      document.getElementById('form-chacra')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
  });

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

  // ---- Panel Admin: generar invitación, copiar código, refrescar, revocar ----
  document.getElementById('form-invitacion').addEventListener('submit', generarInvitacion);
  document.getElementById('btn-copiar-codigo').addEventListener('click', copiarCodigoInvitacion);
  document.getElementById('btn-refrescar-invitaciones').addEventListener('click', cargarInvitaciones);
  document.getElementById('admin-resumen').addEventListener('click', (e) => {
    if (e.target.closest('#btn-resumen-generar-codigo')) {
      document.getElementById('invitacion-rol-destino')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      document.getElementById('invitacion-rol-destino')?.focus({ preventScroll: true });
    }
  });
  document.getElementById('invitaciones-lista').addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-revocar-invitacion');
    if (btn) revocarInvitacionDesdeLista(btn.dataset.invitacionId);
  });
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
  document.querySelectorAll('.auth-tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
  document.getElementById('form-login').classList.toggle('hidden', tab !== 'login');
  document.getElementById('form-registro').classList.toggle('hidden', tab !== 'registro');
}

// ============ INICIO ============
function iniciar() {
  cargarSesionGuardada();
  actualizarUIAuth();
  renderNavCategorias();
  inicializarEventos();
  cambiarVista(vistaInicialPara(Estado.rol));
}

document.addEventListener('DOMContentLoaded', iniciar);
