// ============ ESTADO GLOBAL ============
const Estado = {
  token: null,
  usuarioId: null,
  nombre: null,
  email: null,
  rol: null,
  productos: [],
  productores: {},   // id -> nombre
  carrito: [],        // {producto_id, nombre, precio, cantidad, stockDisponible}
  productorId: null,
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

const EMOJIS_CATEGORIA = [
  { claves: ['palta', 'fruta', 'manzana', 'platano', 'plátano', 'mango', 'naranja', 'fresa'], emoji: '🍎' },
  { claves: ['verdura', 'lechuga', 'brócoli', 'brocoli', 'espinaca', 'zanahoria'], emoji: '🥦' },
  { claves: ['papa', 'tuber', 'yuca', 'camote'], emoji: '🥔' },
  { claves: ['maiz', 'maíz', 'grano', 'quinua', 'trigo', 'cereal'], emoji: '🌽' },
  { claves: ['leche', 'queso', 'lacteo', 'lácteo', 'yogurt'], emoji: '🥛' },
  { claves: ['cafe', 'café'], emoji: '☕' },
  { claves: ['miel'], emoji: '🍯' },
  { claves: ['huevo'], emoji: '🥚' },
  { claves: ['tomate', 'jitomate'], emoji: '🍅' },
  { claves: ['flor'], emoji: '🌷' },
];

function emojiParaProducto(producto) {
  const texto = `${producto.nombre || ''} ${producto.categoria || ''}`.toLowerCase();
  for (const grupo of EMOJIS_CATEGORIA) {
    if (grupo.claves.some((c) => texto.includes(c))) return grupo.emoji;
  }
  return '🌿';
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
  const url = (producto.imagen_url || '').trim();
  if (!url) return `<div class="${contenedorClase}">${emoji}</div>`;

  return `
    <div class="${contenedorClase} product-media">
      <img src="${escapeAttr(url)}" alt="${escapeAttr(producto.nombre || '')}" loading="lazy"
           onerror="this.parentElement.textContent='${emoji}'">
    </div>`;
}

function badgeEstadoEnvio(estado) {
  const clase = `badge-${(estado || 'pendiente').toLowerCase().replace(/\s+/g, '_')}`;
  return `<span class="badge ${clase} badge-default">${estado || 'pendiente'}</span>`;
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
  cambiarVista('catalogo');
  toast('Sesión cerrada. ¡Hasta pronto! 👋');
}

function actualizarUIAuth() {
  const logueado = !!Estado.token;
  document.getElementById('auth-area').classList.toggle('hidden', logueado);
  document.getElementById('user-area').classList.toggle('hidden', !logueado);
  document.querySelector('.nav-productor').classList.toggle('hidden', !(logueado && Estado.rol === 'productor'));
  document.querySelector('.nav-repartidor').classList.toggle('hidden', !(logueado && Estado.rol === 'repartidor'));

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
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  document.getElementById(`view-${nombre}`).classList.add('active');
  document.querySelectorAll('.nav-link').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.nav === nombre);
  });
  document.getElementById('nav-links').classList.remove('open');

  if (nombre === 'catalogo') cargarCatalogo();
  if (nombre === 'mis-pedidos') cargarMisPedidos();
  if (nombre === 'notificaciones') cargarNotificaciones();
  if (nombre === 'panel-productor') iniciarPanelProductor();
  if (nombre === 'gestion-envios') cargarGestionEnvios();
}

// ============ MODALES ============
function abrirModal(id) { document.getElementById(id).classList.remove('hidden'); }
function cerrarModal(id) { document.getElementById(id).classList.add('hidden'); }

// ============ CATÁLOGO ============
async function cargarCatalogo() {
  try {
    const [productos, productores] = await Promise.all([
      Api.productos.listar(),
      Api.productores.listar(),
    ]);
    Estado.productos = productos;
    Estado.productores = {};
    productores.forEach((p) => { Estado.productores[p.id] = p.nombre; });

    poblarSelectCategorias(productos);
    renderProductos(productos);
  } catch (err) {
    manejarError(err, 'cargar el catálogo');
  }
}

function poblarSelectCategorias(productos) {
  const select = document.getElementById('select-categoria');
  const actual = select.value;
  const categorias = [...new Set(productos.map((p) => p.categoria).filter(Boolean))].sort();
  select.innerHTML = '<option value="">Todas las categorías</option>' +
    categorias.map((c) => `<option value="${c}">${c}</option>`).join('');
  select.value = actual;
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

  grid.innerHTML = productos.map((p) => {
    const stockBajo = p.stock <= 5;
    return `
    <div class="product-card" data-id="${p.id}">
      ${renderMediaProducto(p)}
      <span class="product-tag">${p.categoria || 'General'}</span>
      <h4>${p.nombre}</h4>
      <span class="product-productor">👨‍🌾 ${p.productor_nombre || 'Productor local'}</span>
      <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
      <span class="product-stock ${stockBajo ? 'low' : ''}">${p.stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles` : 'Sin stock'}</span>
      <button class="btn btn-primary btn-agregar" data-id="${p.id}" ${p.stock <= 0 ? 'disabled' : ''}>
        ${p.stock <= 0 ? 'Agotado' : '+ Agregar al pedido'}
      </button>
    </div>`;
  }).join('');
}

function filtrarYRenderizar() {
  const texto = document.getElementById('input-buscar').value.trim().toLowerCase();
  const categoria = document.getElementById('select-categoria').value;

  const filtrados = Estado.productos.filter((p) => {
    const coincideTexto = !texto || `${p.nombre} ${p.categoria || ''} ${p.productor_nombre || ''}`.toLowerCase().includes(texto);
    const coincideCategoria = !categoria || p.categoria === categoria;
    return coincideTexto && coincideCategoria;
  });
  renderProductos(filtrados);
}

function abrirDetalleProducto(id) {
  const p = Estado.productos.find((x) => x.id === id);
  if (!p) return;
  const contenido = document.getElementById('detalle-producto-content');
  contenido.innerHTML = `
    ${renderMediaProducto(p, 'producto-emoji-lg')}
    <span class="product-tag">${p.categoria || 'General'}</span>
    <h2>${p.nombre}</h2>
    <p class="muted">Publicado por ${p.productor_nombre || 'Productor local'} · ${formatearFecha(p.fecha_publicacion)}</p>
    <p class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></p>
    <p class="product-stock ${p.stock <= 5 ? 'low' : ''}">${p.stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} disponibles` : 'Sin stock disponible'}</p>
    <button class="btn btn-primary btn-block" style="margin-top:16px" data-id="${p.id}" id="btn-agregar-detalle" ${p.stock <= 0 ? 'disabled' : ''}>
      ${p.stock <= 0 ? 'Agotado' : '+ Agregar al pedido'}
    </button>
  `;
  abrirModal('modal-detalle-producto');
  document.getElementById('btn-agregar-detalle')?.addEventListener('click', () => {
    agregarAlCarrito(p.id);
    cerrarModal('modal-detalle-producto');
  });
}

// ============ CARRITO / PEDIDO ============
function agregarAlCarrito(productoId) {
  if (!Estado.token) {
    toast('Inicia sesión para armar tu pedido 🌱', 'error');
    abrirModal('modal-auth');
    return;
  }
  const producto = Estado.productos.find((p) => p.id === productoId);
  if (!producto) return;

  const existente = Estado.carrito.find((i) => i.producto_id === productoId);
  if (existente) {
    if (existente.cantidad < producto.stock) existente.cantidad += 1;
    else toast('No hay más stock disponible de este producto.', 'error');
  } else {
    Estado.carrito.push({
      producto_id: producto.id,
      nombre: producto.nombre,
      precio: Number(producto.precio),
      cantidad: 1,
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
  badge.textContent = cantidadTotal;
  badge.classList.toggle('hidden', cantidadTotal === 0);

  const cont = document.getElementById('carrito-items');
  const vacio = document.getElementById('carrito-vacio');
  const totalEl = document.getElementById('carrito-total');
  const btnConfirmar = document.getElementById('btn-confirmar-pedido');

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

async function confirmarPedido(e) {
  e.preventDefault();
  if (!Estado.carrito.length) return;

  const btn = document.getElementById('btn-confirmar-pedido');
  btn.disabled = true;
  btn.textContent = 'Enviando pedido...';

  try {
    const total = Estado.carrito.reduce((acc, i) => acc + i.precio * i.cantidad, 0);
    const datos = {
      comprador_nombre: Estado.nombre || 'Cliente AgroMercado',
      comprador_telefono: document.getElementById('pedido-telefono').value.trim() || null,
      items: Estado.carrito.map((i) => ({ producto_id: i.producto_id, cantidad: i.cantidad })),
    };
    const pedido = await Api.pedidos.crear(datos);
    guardarPedidoTrackeado(pedido.id);

    Estado.carrito = [];
    renderCarrito();
    document.getElementById('form-pedido').reset();
    cerrarModal('modal-pedido');
    toast('¡Pedido creado con éxito! 🎉 Completa el pago para confirmarlo.');
    cambiarVista('catalogo');

    abrirCheckoutCulqi(pedido.id, total);
  } catch (err) {
    manejarError(err, 'crear el pedido');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Confirmar y pagar';
  }
}

// ============ PAGO (CULQI) ============
let pagoEnCurso = null; // { pedidoId, email }

function abrirCheckoutCulqi(pedidoId, montoTotal) {
  if (typeof Culqi === 'undefined') {
    toast('No se pudo cargar la pasarela de pagos. Vuelve a intentar el pago desde "Mis Pedidos".', 'error');
    return;
  }

  pagoEnCurso = { pedidoId, email: Estado.email };
  Culqi.publicKey = CULQI_PUBLIC_KEY;
  Culqi.settings({
    title: 'AgroMercado',
    currency: 'PEN',
    amount: Math.round(montoTotal * 100),
  });
  Culqi.open();
}

window.culqi = function () {
  if (!pagoEnCurso) return;

  if (window.Culqi.token) {
    procesarPagoCulqi(window.Culqi.token.id);
  } else if (window.Culqi.error) {
    toast(window.Culqi.error.user_message || 'Revisa los datos de tu tarjeta e intenta de nuevo.', 'error');
  }
};

async function procesarPagoCulqi(tokenCulqi) {
  const { pedidoId, email } = pagoEnCurso;
  try {
    const resultado = await Api.pagos.procesar({
      pedido_id: pedidoId,
      token_culqi: tokenCulqi,
      email,
    });
    if (resultado.estado === 'aprobado') {
      toast('¡Pago aprobado! 🎉 Tu pedido está confirmado.');
    } else {
      toast(`El pago no fue aprobado (estado: ${resultado.estado}).`, 'error');
    }
  } catch (err) {
    manejarError(err, 'procesar el pago');
  } finally {
    pagoEnCurso = null;
    cambiarVista('mis-pedidos');
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

async function cargarMisPedidos() {
  const cont = document.getElementById('pedidos-list');
  const vacio = document.getElementById('pedidos-empty');

  if (!Estado.token) {
    cont.innerHTML = '';
    vacio.classList.remove('hidden');
    vacio.querySelector('p').textContent = '🔒 Inicia sesión para ver tus pedidos.';
    return;
  }

  const ids = obtenerPedidosTrackeados();
  if (!ids.length) {
    cont.innerHTML = '';
    vacio.classList.remove('hidden');
    vacio.querySelector('p').textContent = '🛍️ Aún no tienes pedidos registrados. ¡Explora el catálogo!';
    return;
  }
  vacio.classList.add('hidden');
  cont.innerHTML = '<p class="muted">Cargando pedidos...</p>';

  const tarjetas = await Promise.all(ids.map((id) => cargarTarjetaPedido(id)));
  cont.innerHTML = tarjetas.filter(Boolean).join('') || '<p class="muted">No se pudieron cargar los pedidos.</p>';
}

async function cargarTarjetaPedido(pedidoId) {
  let pedido;
  try {
    pedido = await Api.pedidos.obtener(pedidoId);
  } catch {
    return '';
  }

  let envio = null;
  try {
    envio = await Api.transporte.obtenerEnvio(pedidoId);
  } catch { /* aún no tiene envío asignado */ }

  let pago = null;
  try {
    pago = await Api.pagos.obtener(pedidoId);
  } catch { /* aún no hay un pago registrado para este pedido */ }

  return renderTarjetaPedido(pedido, envio, pago);
}

function renderTarjetaPedido(pedido, envio, pago) {
  const items = (pedido.items || []).map((i) => {
    const nombre = Estado.productos.find((p) => p.id === i.producto_id)?.nombre || `Producto ${String(i.producto_id).slice(0, 8)}`;
    return `<li><span>${i.cantidad} × ${nombre}</span><span>${formatearMoneda(i.precio_unitario * i.cantidad)}</span></li>`;
  }).join('');

  const total = (pedido.items || []).reduce((acc, i) => acc + Number(i.precio_unitario) * i.cantidad, 0);

  const envioHtml = envio
    ? `<div class="envio-track"><span class="icon">🚚</span> Envío: ${badgeEstadoEnvio(envio.estado)} ${envio.transportista ? `· Transportista: ${envio.transportista}` : ''}</div>`
    : `<div class="envio-track"><span class="icon">📦</span> Aún no se asignó transporte para este pedido.</div>`;

  const pagoHtml = pago
    ? `<div class="envio-track"><span class="icon">💳</span> Pago: ${badgeEstadoEnvio(pago.estado)}</div>`
    : `<div class="envio-track"><span class="icon">💳</span> Pago: <span class="badge badge-default">pendiente</span></div>`;

  return `
    <div class="pedido-card">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Pedido #${String(pedido.id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFecha(pedido.fecha_creacion)}</div>
        </div>
        ${badgeEstadoEnvio(pedido.estado)}
      </div>
      <ul class="pedido-items">${items}</ul>
      <div class="pedido-total">Total: ${formatearMoneda(total)}</div>
      ${pagoHtml}
      ${envioHtml}
    </div>
  `;
}

async function buscarPedidoPorId() {
  const input = document.getElementById('input-buscar-pedido');
  const id = input.value.trim();
  if (!id) return;

  const cont = document.getElementById('pedidos-list');
  document.getElementById('pedidos-empty').classList.add('hidden');
  cont.innerHTML = '<p class="muted">Buscando pedido...</p>';

  try {
    const tarjeta = await cargarTarjetaPedido(id);
    if (!tarjeta) throw new Error('No se encontró un pedido con ese ID.');
    cont.innerHTML = tarjeta;
    guardarPedidoTrackeado(id);
  } catch (err) {
    manejarError(err, 'buscar el pedido');
    cargarMisPedidos();
  }
}

// ============ NOTIFICACIONES ============
async function cargarNotificaciones() {
  const cont = document.getElementById('notificaciones-list');
  const vacio = document.getElementById('notificaciones-empty');

  if (!Estado.token) {
    cont.innerHTML = '';
    vacio.classList.remove('hidden');
    vacio.querySelector('p').textContent = '🔒 Inicia sesión para ver tus notificaciones.';
    return;
  }

  try {
    const ids = new Set(obtenerPedidosTrackeados());
    const todas = await Api.notificaciones.listarTodas();
    const relevantes = ids.size ? todas.filter((n) => ids.has(String(n.pedido_id))) : todas;

    if (!relevantes.length) {
      cont.innerHTML = '';
      vacio.classList.remove('hidden');
      return;
    }
    vacio.classList.add('hidden');
    cont.innerHTML = relevantes.map((n) => `
      <div class="notif-card">
        <div class="notif-icon">${n.tipo === 'envio_actualizado' ? '🚚' : '🧾'}</div>
        <div>
          <div class="notif-msg">${n.mensaje}</div>
          <div class="notif-meta">Pedido #${String(n.pedido_id).slice(0, 8)} · ${formatearFecha(n.fecha_envio)}</div>
        </div>
      </div>
    `).join('');
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
    cargarMisProductos();
  } else {
    setup.classList.remove('hidden');
    panel.classList.add('hidden');
  }
}

async function crearPerfilProductor(e) {
  e.preventDefault();
  try {
    const datos = {
      nombre: document.getElementById('productor-nombre').value.trim(),
      comunidad: document.getElementById('productor-comunidad').value.trim() || null,
      contacto: document.getElementById('productor-contacto').value.trim() || null,
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
      categoria: document.getElementById('producto-categoria').value.trim() || null,
      unidad_medida: document.getElementById('producto-unidad').value,
      precio: parseFloat(document.getElementById('producto-precio').value),
      stock: parseInt(document.getElementById('producto-stock').value, 10),
      imagen_url: document.getElementById('producto-imagen').value.trim() || null,
    };
    await Api.productos.crear(datos);
    document.getElementById('form-producto').reset();
    toast('Producto publicado con éxito 🎉');
    cargarMisProductos();
  } catch (err) {
    manejarError(err, 'publicar el producto');
  } finally {
    btn.disabled = false;
  }
}

async function cargarMisProductos() {
  const grid = document.getElementById('mis-productos-grid');
  const vacio = document.getElementById('mis-productos-empty');
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
    grid.innerHTML = mios.map((p) => `
      <div class="product-card" style="cursor:default">
        ${renderMediaProducto(p)}
        <span class="product-tag">${p.categoria || 'General'}</span>
        <h4>${p.nombre}</h4>
        <span class="product-price">${formatearMoneda(p.precio)} <span class="price-unit">/ ${unidadCorta(p.unidad_medida)}</span></span>
        <span class="product-stock ${p.stock <= 5 ? 'low' : ''}">${p.stock} ${unidadPlural(p.unidad_medida, p.stock)} en stock</span>
      </div>
    `).join('');
  } catch (err) {
    manejarError(err, 'cargar tus productos');
  }
}

// ============ GESTIÓN DE ENVÍOS (repartidor) ============
async function cargarGestionEnvios() {
  if (!Estado.token || Estado.rol !== 'repartidor') {
    cambiarVista('catalogo');
    return;
  }

  const cont = document.getElementById('envios-list');
  const vacio = document.getElementById('envios-empty');
  try {
    const envios = await Api.transporte.listarTodos();
    if (!envios.length) {
      cont.innerHTML = '';
      vacio.classList.remove('hidden');
      return;
    }
    vacio.classList.add('hidden');
    cont.innerHTML = envios.map(renderTarjetaEnvio).join('');
  } catch (err) {
    manejarError(err, 'cargar los envíos');
  }
}

function renderTarjetaEnvio(envio) {
  return `
    <div class="pedido-card">
      <div class="pedido-card-header">
        <div>
          <div class="pedido-id">Envío #${String(envio.id).slice(0, 8)} · Pedido #${String(envio.pedido_id).slice(0, 8)}</div>
          <div class="pedido-fecha">${formatearFecha(envio.fecha_creacion)}</div>
        </div>
        ${badgeEstadoEnvio(envio.estado)}
      </div>
      <div class="envio-track"><span class="icon">🧑‍✈️</span> Transportista: ${envio.transportista || 'Sin asignar'}</div>
      <div class="envio-actualizar">
        <select class="select-estado-envio" data-envio-id="${envio.id}">
          <option value="pendiente" ${envio.estado === 'pendiente' ? 'selected' : ''}>Pendiente</option>
          <option value="en_camino" ${envio.estado === 'en_camino' ? 'selected' : ''}>En camino</option>
          <option value="entregado" ${envio.estado === 'entregado' ? 'selected' : ''}>Entregado</option>
        </select>
        <button class="btn btn-primary btn-actualizar-envio" data-envio-id="${envio.id}">Actualizar estado</button>
      </div>
    </div>
  `;
}

async function actualizarEstadoEnvio(envioId, nuevoEstado) {
  try {
    await Api.transporte.actualizarEstado(envioId, nuevoEstado);
    toast(`Estado del envío actualizado a "${nuevoEstado}" ✅`);
    cargarGestionEnvios();
  } catch (err) {
    manejarError(err, 'actualizar el estado del envío');
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
    toast(`¡Cuenta creada! Bienvenido a AgroMercado, ${nombre} 🎉`);
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
  document.getElementById('btn-refrescar-catalogo').addEventListener('click', cargarCatalogo);

  document.getElementById('productos-grid').addEventListener('click', (e) => {
    const btnAgregar = e.target.closest('.btn-agregar');
    if (btnAgregar) {
      agregarAlCarrito(btnAgregar.dataset.id);
      return;
    }
    const card = e.target.closest('.product-card');
    if (card) abrirDetalleProducto(card.dataset.id);
  });

  document.getElementById('btn-cart').addEventListener('click', () => abrirModal('modal-pedido'));
  document.getElementById('form-pedido').addEventListener('submit', confirmarPedido);
  document.getElementById('carrito-items').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-accion]');
    if (!btn) return;
    const { accion, id } = btn.dataset;
    if (accion === 'mas') cambiarCantidadCarrito(id, 1);
    if (accion === 'menos') cambiarCantidadCarrito(id, -1);
    if (accion === 'quitar') quitarDelCarrito(id);
  });

  document.getElementById('btn-refrescar-pedidos').addEventListener('click', cargarMisPedidos);
  document.getElementById('btn-buscar-pedido').addEventListener('click', buscarPedidoPorId);
  document.getElementById('input-buscar-pedido').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); buscarPedidoPorId(); }
  });

  document.getElementById('btn-refrescar-notificaciones').addEventListener('click', cargarNotificaciones);

  document.getElementById('form-productor').addEventListener('submit', crearPerfilProductor);
  document.getElementById('form-producto').addEventListener('submit', publicarProducto);
  document.getElementById('btn-refrescar-mis-productos').addEventListener('click', cargarMisProductos);

  document.getElementById('btn-refrescar-envios').addEventListener('click', cargarGestionEnvios);
  document.getElementById('envios-list').addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-actualizar-envio');
    if (!btn) return;
    const envioId = btn.dataset.envioId;
    const select = document.querySelector(`.select-estado-envio[data-envio-id="${envioId}"]`);
    actualizarEstadoEnvio(envioId, select.value);
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
  cargarCatalogo();
}

document.addEventListener('DOMContentLoaded', iniciar);
