// Configuración de endpoints de los microservicios
const API_BASE = {
  usuarios: 'http://localhost:8006',
  productores: 'http://localhost:8001',
  productos: 'http://localhost:8002',
  pedidos: 'http://localhost:8003',
  transporte: 'http://localhost:8004',
  notificaciones: 'http://localhost:8005',
  pagos: 'http://localhost:8007',
  certificacion: 'http://localhost:8008',
};

// Llave pública de Culqi (modo test, segura de exponer en frontend)
const CULQI_PUBLIC_KEY = 'pk_test_gGgpDDFDAt5HjYwI';

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(baseKey, path, { method = 'GET', body, auth = false } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth) {
    const token = Estado.token;
    if (!token) throw new ApiError('Debes iniciar sesión para continuar.', 401);
    headers['Authorization'] = `Bearer ${token}`;
  }

  let resp;
  try {
    resp = await fetch(`${API_BASE[baseKey]}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(`No se pudo conectar con el servicio de ${baseKey}. ¿Está corriendo?`, 0);
  }

  let data = null;
  const text = await resp.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }

  if (!resp.ok) {
    if (resp.status === 403) {
      throw new ApiError('No tienes permiso para esta acción.', 403);
    }
    const detail = (data && data.detail) ? data.detail : `Error ${resp.status} en ${baseKey}`;
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), resp.status);
  }

  return data;
}

const Api = {
  usuarios: {
    registrar: (datos) => request('usuarios', '/usuarios/registro', { method: 'POST', body: datos }),
    login: (datos) => request('usuarios', '/usuarios/login', { method: 'POST', body: datos }),
    obtener: (id) => request('usuarios', `/usuarios/${id}`),
  },
  productores: {
    listar: () => request('productores', '/productores'),
    obtener: (id) => request('productores', `/productores/${id}`),
    crear: (datos) => request('productores', '/productores', { method: 'POST', body: datos, auth: true }),
  },
  productos: {
    listar: () => request('productos', '/productos'),
    obtener: (id) => request('productos', `/productos/${id}`),
    crear: (datos) => request('productos', '/productos', { method: 'POST', body: datos, auth: true }),
    listarResenas: (id) => request('productos', `/productos/${id}/resenas`),
    crearResena: (id, datos) => request('productos', `/productos/${id}/resenas`, { method: 'POST', body: datos, auth: true }),
  },
  pedidos: {
    crear: (datos) => request('pedidos', '/pedidos', { method: 'POST', body: datos, auth: true }),
    obtener: (id) => request('pedidos', `/pedidos/${id}`),
  },
  transporte: {
    obtenerEnvio: (pedidoId) => request('transporte', `/envios/${pedidoId}`),
    listarTodos: () => request('transporte', '/envios', { auth: true }),
    actualizarEstado: (envioId, estado) => request('transporte', `/envios/${envioId}/estado`, { method: 'PATCH', body: { estado }, auth: true }),
    ruta: (envioId) => request('transporte', `/envios/${envioId}/ruta`),
    actualizarUbicacion: (envioId, latitud, longitud) => request('transporte', `/envios/${envioId}/ubicacion`, { method: 'PATCH', body: { latitud: String(latitud), longitud: String(longitud) }, auth: true }),
  },
  repartidores: {
    miPerfil: () => request('transporte', '/repartidores/me', { auth: true }),
    crear: (datos) => request('transporte', '/repartidores', { method: 'POST', body: datos, auth: true }),
  },
  notificaciones: {
    porPedido: (pedidoId) => request('notificaciones', `/notificaciones/${pedidoId}`, { auth: true }),
    listarTodas: () => request('notificaciones', '/notificaciones', { auth: true }),
  },
  pagos: {
    procesar: (datos) => request('pagos', '/pagos/procesar', { method: 'POST', body: datos, auth: true }),
    obtener: (pedidoId) => request('pagos', `/pagos/${pedidoId}`, { auth: true }),
  },
  certificacion: {
    verificar: (productoId) => request('certificacion', `/certificacion/${productoId}/verificar`),
    historial: (productoId) => request('certificacion', `/certificacion/${productoId}/historial`),
    qrUrl: (productoId) => `${API_BASE.certificacion}/certificacion/${productoId}/qr`,
  },
};
