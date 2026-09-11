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

async function requestFormData(baseKey, path, formData) {
  const token = Estado.token;
  if (!token) throw new ApiError('Debes iniciar sesión para continuar.', 401);

  let resp;
  try {
    resp = await fetch(`${API_BASE[baseKey]}${path}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
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
    obtener: (id) => request('usuarios', `/usuarios/${id}`, { auth: true }),
  },
  admin: {
    // El rol Admin vive en el servicio de usuarios (mismo servicio del login/registro) —
    // mismo caso que Api.verificadores, que apunta a un servicio propio del rol.
    crearInvitacion: (rolDestino) => request('usuarios', '/admin/invitaciones', { method: 'POST', body: { rol_destino: rolDestino }, auth: true }),
    listarInvitaciones: () => request('usuarios', '/admin/invitaciones', { auth: true }),
    revocarInvitacion: (id) => request('usuarios', `/admin/invitaciones/${id}`, { method: 'DELETE', auth: true }),
  },
  productores: {
    listar: () => request('productores', '/productores'),
    obtener: (id) => request('productores', `/productores/${id}`),
    miPerfil: () => request('productores', '/productores/me', { auth: true }),
    crear: (datos) => request('productores', '/productores', { method: 'POST', body: datos, auth: true }),
    produccion: {
      crear: (datos) => request('productores', '/productores/produccion', { method: 'POST', body: datos, auth: true }),
      listarMe: () => request('productores', '/productores/produccion/me', { auth: true }),
      completarCosecha: (id, datos) => request('productores', `/productores/produccion/${id}/completar-cosecha`, { method: 'PATCH', body: datos, auth: true }),
    },
  },
  chacras: {
    misChacras: () => request('productores', '/chacras/me', { auth: true }),
    crear: (datos) => request('productores', '/chacras', { method: 'POST', body: datos, auth: true }),
  },
  productos: {
    listar: () => request('productos', '/productos'),
    obtener: (id) => request('productos', `/productos/${id}`),
    precioReferencia: (nombre) => request('productos', `/productos/precio-referencia/${encodeURIComponent(nombre)}`),
    crear: (datos) => request('productos', '/productos', { method: 'POST', body: datos, auth: true }),
    listarResenas: (id) => request('productos', `/productos/${id}/resenas`),
    crearResena: (id, datos) => request('productos', `/productos/${id}/resenas`, { method: 'POST', body: datos, auth: true }),
    subirImagen: (id, archivo, orden = 0) => {
      const formData = new FormData();
      formData.append('archivo', archivo);
      return requestFormData('productos', `/productos/${id}/imagenes/subir?orden=${orden}`, formData);
    },
    eliminarImagen: (imagenId) => request('productos', `/productos/imagenes/${imagenId}`, { method: 'DELETE', auth: true }),
    actualizarOrdenImagen: (imagenId, orden) => request('productos', `/productos/imagenes/${imagenId}/orden`, { method: 'PATCH', body: { orden }, auth: true }),
    registrarBusqueda: (termino) => request('productos', '/productos/busquedas/registrar', { method: 'POST', body: { termino } }),
    busquedasPopulares: () => request('productos', '/productos/busquedas/populares'),
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
    propuestas: () => request('transporte', '/envios/propuestas', { auth: true }),
    aceptar: (envioId) => request('transporte', `/envios/${envioId}/aceptar`, { method: 'POST', auth: true }),
    rechazar: (envioId) => request('transporte', `/envios/${envioId}/rechazar`, { method: 'POST', auth: true }),
  },
  repartidores: {
    miPerfil: () => request('transporte', '/repartidores/me', { auth: true }),
    crear: (datos) => request('transporte', '/repartidores', { method: 'POST', body: datos, auth: true }),
  },
  verificadores: {
    // El rol Verificador vive en el servicio de certificación (8008) — mismo caso que
    // Api.repartidores, que usa el servicio de transporte y no uno propio.
    miPerfil: () => request('certificacion', '/verificadores/me', { auth: true }),
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
    certificadoPedido: (pedidoId) => request('certificacion', `/certificacion/pedido/${pedidoId}/certificado`),
  },
};
