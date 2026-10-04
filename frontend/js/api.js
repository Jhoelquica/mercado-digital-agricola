// Configuración de endpoints de los microservicios, según dónde se abra la página:
//  - En tu máquina (localhost / 127.0.0.1) cada servicio se llama directo, en su puerto.
//  - En cualquier otro sitio (producción) todo pasa por el gateway (Traefik) del MISMO origen, en /api/<servicio> (ver
//    k8s/traefik-routes.yaml): un solo dominio, sin CORS y sin exponer los puertos de los servicios.
//  - Para probar el modo producción en local: abrir la página servida por el gateway (localhost:8000) o añadir
//    `?api=gateway` a la URL.
//  - Para forzar otra cosa sin tocar este archivo: `window.APP_CONFIG = { apiBase: { usuarios: '…', … } }` antes de cargarlo.
const PUERTOS_LOCALES = {
  usuarios: 8006,
  productores: 8001,
  productos: 8002,
  pedidos: 8003,
  transporte: 8004,
  notificaciones: 8005,
  pagos: 8007,
  certificacion: 8008,
};
const PUERTO_DEL_GATEWAY_LOCAL = '8000';
const ES_LOCALHOST = ['localhost', '127.0.0.1'].includes(location.hostname);
const USA_EL_GATEWAY = !ES_LOCALHOST
  || location.port === PUERTO_DEL_GATEWAY_LOCAL
  || new URLSearchParams(location.search).get('api') === 'gateway';
const API_BASE = (window.APP_CONFIG && window.APP_CONFIG.apiBase) || Object.fromEntries(
  Object.entries(PUERTOS_LOCALES).map(([servicio, puerto]) => [
    servicio,
    USA_EL_GATEWAY ? `${location.origin}/api/${servicio}` : `http://localhost:${puerto}`,
  ]),
);

// Las fotos de producto (y las de las cosechas) se guardan en la base con la URL completa que tenía MinIO al subirlas
// (por defecto http://localhost:9000/productos-imagenes/...): sirve en tu propia máquina ("localhost" te
// apunta a ti), pero no en la de nadie más (a esa persona "localhost" le apunta a SU máquina), ni detrás
// de un túnel/dominio distinto. Por el gateway existe la ruta /media/productos-imagenes/... (ver
// k8s/traefik-routes.yaml) que sirve las mismas fotos por EL MISMO origen de la página: basta con
// reescribir cualquier URL que reconozcamos como de ese bucket para que apunte ahí en vez de al host que
// tenía guardado. En modo "puertos directos" (ES_LOCALHOST sin gateway) se deja tal cual: ahí MinIO ya
// responde directo en localhost:9000.
function normalizarUrlImagen(url) {
  if (!url || !USA_EL_GATEWAY) return url;
  try {
    const u = new URL(url, location.origin);
    if (u.pathname.startsWith('/productos-imagenes/') || u.pathname.startsWith('/cosechas-evidencias/')) {
      return `${location.origin}/media${u.pathname}`;
    }
  } catch { /* url rara o relativa: se deja tal cual */ }
  return url;
}
function normalizarImagenesDeProducto(p) {
  if (!p) return p;
  if (p.imagen_url) p.imagen_url = normalizarUrlImagen(p.imagen_url);
  if (p.imagen_principal) p.imagen_principal = normalizarUrlImagen(p.imagen_principal);
  if (Array.isArray(p.imagenes)) p.imagenes.forEach((img) => { if (img.url) img.url = normalizarUrlImagen(img.url); });
  return p;
}

// Llave pública de Culqi (modo test, segura de exponer en frontend)
const CULQI_PUBLIC_KEY = 'pk_test_gGgpDDFDAt5HjYwI';

// Client ID de Google (login/registro con Google, ver POST /usuarios/oauth/google) — no es
// secreto (viaja público en el HTML, Google lo espera así), pero de todas formas no se
// hardcodea inline donde se usa, mismo criterio que CULQI_PUBLIC_KEY de arriba.
// PLACEHOLDER — reemplazar con el Client ID real generado en Google Cloud Console
// (console.cloud.google.com → APIs & Services → Credentials → OAuth 2.0 Client ID, tipo "Web
// application") antes de usar en cualquier entorno real. Cada entorno (dev/prod) suele tener
// el suyo propio, ya que el Client ID está atado a los orígenes autorizados (localhost vs. el
// dominio real).
const GOOGLE_CLIENT_ID = '546858204347-n72ku2ohsq88er0p297hrd9n2rirsc86.apps.googleusercontent.com';

// La asigna app.js (manejarSesionExpirada). Se llama cuando un servicio responde 401 a una petición hecha
// CON sesión: el token venció (duran 60 minutos) o ya no vale. Recibe el token con el que se hizo la
// petición, para que la respuesta tardía de una sesión anterior no cierre la sesión nueva.
let alExpirarSesion = null;
const MENSAJE_SESION_EXPIRADA = 'Tu sesión expiró. Inicia sesión de nuevo para continuar.';

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// FastAPI devuelve los errores de validación (422) como una lista de {loc, msg}: se convierte en un
// texto legible ("costo_semillas: debe estar entre 0 y 9999999.99") en vez de mostrar el JSON crudo.
// Mismo tope que el servidor (imagenes.py): se avisa antes de subir un archivo que igual sería rechazado.
const TAMANO_MAXIMO_IMAGEN = 5 * 1024 * 1024;

function exigirImagenLiviana(archivo) {
  if (archivo && archivo.size > TAMANO_MAXIMO_IMAGEN) throw new ApiError('La imagen no puede pesar más de 5 MB.', 413);
}

const ETIQUETAS_DE_CAMPO = { password: 'Contraseña', email: 'Correo', nombre: 'Nombre', ruc: 'RUC', nombre_comercial: 'Nombre del negocio' };

function mensajeDeDetalle(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => {
      const campo = Array.isArray(e.loc) ? e.loc.filter((parte) => parte !== 'body').join(' › ') : '';
      const mensaje = String(e.msg || '').replace(/^Value error, /, '');
      return campo ? `${ETIQUETAS_DE_CAMPO[campo] || campo}: ${mensaje}` : mensaje;
    }).join(' · ');
  }
  return JSON.stringify(detail);
}

async function request(baseKey, path, { method = 'GET', body, auth = false } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  let tokenUsado = null;
  if (auth === 'opcional') {
    // Ruta pública que muestra más a su dueño (un borrador): si hay sesión se manda el token, si no se pide como
    // visitante. Un token vencido no cierra la sesión aquí (el servidor lo trata como visitante, sin 401).
    // `Estado` lo define app.js; verificar.html (la página pública del QR) no lo carga: ahí siempre se pide como visitante.
    const token = typeof Estado !== 'undefined' ? Estado.token : null;
    if (token) headers['Authorization'] = `Bearer ${token}`;
  } else if (auth) {
    const token = Estado.token;
    if (!token) throw new ApiError('Debes iniciar sesión para continuar.', 401);
    headers['Authorization'] = `Bearer ${token}`;
    tokenUsado = token;
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
    if (resp.status === 401 && tokenUsado) {
      if (alExpirarSesion) alExpirarSesion(tokenUsado);
      throw new ApiError(MENSAJE_SESION_EXPIRADA, 401);
    }
    if (resp.status === 403) {
      throw new ApiError('No tienes permiso para esta acción.', 403);
    }
    const detail = (data && data.detail) ? data.detail : `Error ${resp.status} en ${baseKey}`;
    throw new ApiError(mensajeDeDetalle(detail), resp.status);
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
    if (resp.status === 401) {
      if (alExpirarSesion) alExpirarSesion(token);
      throw new ApiError(MENSAJE_SESION_EXPIRADA, 401);
    }
    if (resp.status === 403) {
      throw new ApiError('No tienes permiso para esta acción.', 403);
    }
    const detail = (data && data.detail) ? data.detail : `Error ${resp.status} en ${baseKey}`;
    throw new ApiError(mensajeDeDetalle(detail), resp.status);
  }

  return data;
}

// Un archivo protegido (la constancia de una transferencia, que solo ve el admin): se pide con el token y se devuelve
// como Blob para mostrarlo con URL.createObjectURL, sin exponer el almacenamiento.
async function requestBlob(baseKey, path) {
  const token = Estado.token;
  if (!token) throw new ApiError('Debes iniciar sesión para continuar.', 401);
  let resp;
  try {
    resp = await fetch(`${API_BASE[baseKey]}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  } catch (err) {
    throw new ApiError(`No se pudo conectar con el servicio de ${baseKey}. ¿Está corriendo?`, 0);
  }
  if (!resp.ok) {
    if (resp.status === 401) {
      if (alExpirarSesion) alExpirarSesion(token);
      throw new ApiError(MENSAJE_SESION_EXPIRADA, 401);
    }
    let detalle = null;
    try { detalle = (await resp.json()).detail; } catch { /* sin cuerpo JSON */ }
    throw new ApiError(detalle ? mensajeDeDetalle(detalle) : `Error ${resp.status} en ${baseKey}`, resp.status);
  }
  return resp.blob();
}

const Api = {
  usuarios: {
    registrar: (datos) => request('usuarios', '/usuarios/registro', { method: 'POST', body: datos }),
    login: (datos) => request('usuarios', '/usuarios/login', { method: 'POST', body: datos }),
    obtener: (id) => request('usuarios', `/usuarios/${id}`, { auth: true }),
    // El comprador cambia si compra como persona o como negocio (con RUC y nombre comercial opcionales).
    actualizarComprador: (datos) => request('usuarios', '/usuarios/me/comprador', { method: 'PATCH', body: datos, auth: true }),
    // Reclamar un RUC que ya está en otra cuenta: datos del dueño o representante + Ficha RUC + foto del DNI.
    reclamarRuc: ({ ruc, nombreTitular, dni, mensaje, fichaRuc, fotoDni }) => {
      const formData = new FormData();
      formData.append('ruc', ruc);
      formData.append('nombre_titular', nombreTitular);
      formData.append('dni', dni);
      formData.append('mensaje', mensaje || '');
      formData.append('ficha_ruc', fichaRuc);
      formData.append('foto_dni', fotoDni);
      return requestFormData('usuarios', '/usuarios/me/reclamos-ruc', formData);
    },
    misReclamosRuc: () => request('usuarios', '/usuarios/me/reclamos-ruc', { auth: true }),
    admin: {
      reclamosRuc: (estado = 'pendiente') => request('usuarios', `/usuarios/admin/reclamos-ruc?estado=${encodeURIComponent(estado)}`, { auth: true }),
      documentoReclamo: (id, cual) => requestBlob('usuarios', `/usuarios/admin/reclamos-ruc/${id}/documento/${cual}`),
      aprobarReclamo: (id) => request('usuarios', `/usuarios/admin/reclamos-ruc/${id}/aprobar`, { method: 'POST', auth: true }),
      rechazarReclamo: (id, motivo) => request('usuarios', `/usuarios/admin/reclamos-ruc/${id}/rechazar`, { method: 'POST', body: { motivo }, auth: true }),
    },
    // rol solo aplica si el id_token no matchea ninguna cuenta existente (registro nuevo) — mismo
    // filtro de roles que Api.usuarios.registrar(), resuelto del lado del backend (ver POST
    // /usuarios/oauth/google).
    oauthGoogle: (datos) => request('usuarios', '/usuarios/oauth/google', { method: 'POST', body: datos }),
  },
  productores: {
    listar: () => request('productores', '/productores'),
    // Lista completa (contacto y coordenadas exactas): solo la puede pedir un admin.
    listarAdmin: () => request('productores', '/productores/admin/todos', { auth: true }),
    obtener: (id) => request('productores', `/productores/${id}`),
    miPerfil: () => request('productores', '/productores/me', { auth: true }),
    crear: (datos) => request('productores', '/productores', { method: 'POST', body: datos, auth: true }),
    produccion: {
      crear: (datos) => request('productores', '/productores/produccion', { method: 'POST', body: datos, auth: true }),
      sugerirCosecha: (datos) => request('productores', '/productores/produccion/sugerir-cosecha', { method: 'POST', body: datos, auth: true }),
      listarMe: () => request('productores', '/productores/produccion/me', { auth: true }),
      completarCosecha: (id, datos) => request('productores', `/productores/produccion/${id}/completar-cosecha`, { method: 'PATCH', body: datos, auth: true }),
      // Mientras no se cosecha: corregir un dato mal puesto o anular la siembra (no se borra, queda su rastro).
      corregir: (id, datos) => request('productores', `/productores/produccion/${id}`, { method: 'PUT', body: datos, auth: true }),
      anular: (id) => request('productores', `/productores/produccion/${id}/anular`, { method: 'POST', auth: true }),
      // La foto de la cosecha va antes que completarCosecha (que la exige) y aparte, para que esa siga siendo JSON.
      subirFotoCosecha: (id, archivo) => {
        const formData = new FormData();
        formData.append('archivo', archivo);
        return requestFormData('productores', `/productores/produccion/${id}/foto-cosecha`, formData);
      },
    },
  },
  chacras: {
    misChacras: () => request('productores', '/chacras/me', { auth: true }),
    crear: (datos) => request('productores', '/chacras', { method: 'POST', body: datos, auth: true }),
  },
  productos: {
    listar: () => request('productos', '/productos').then((lista) => lista.map(normalizarImagenesDeProducto)),
    mios: () => request('productos', '/productos/mios', { auth: true }).then((lista) => lista.map(normalizarImagenesDeProducto)),
    // Con sesión si la hay: el detalle de un borrador solo lo devuelve a su productor (y al admin).
    obtener: (id) => request('productos', `/productos/${id}`, { auth: 'opcional' }).then(normalizarImagenesDeProducto),
    precioReferencia: (nombre) => request('productos', `/productos/precio-referencia/${encodeURIComponent(nombre)}`),
    actualizar: (id, datos) => request('productos', `/productos/${id}`, { method: 'PATCH', body: datos, auth: true }),
    publicar: (id) => request('productos', `/productos/${id}/publicar`, { method: 'PATCH', auth: true }),
    // El Admin dice cuánto llegó de verdad (entero, no más de lo declarado) y, si quiere, una observación:
    // el servidor deja ambos escritos en la cadena de certificación del producto.
    confirmarLlegadaAlmacen: (id, cantidadRecibida, observaciones = '') => request('productos', `/productos/${id}/confirmar-llegada-almacen`, { method: 'PATCH', body: { cantidad_recibida: cantidadRecibida, observaciones: observaciones || null }, auth: true }),
    reservas: (id) => request('productos', `/productos/${id}/reservas`, { auth: true }),
    listarResenas: (id) => request('productos', `/productos/${id}/resenas`),
    crearResena: (id, datos) => request('productos', `/productos/${id}/resenas`, { method: 'POST', body: datos, auth: true }),
    subirImagen: (id, archivo, orden = 0) => {
      exigirImagenLiviana(archivo);
      const formData = new FormData();
      formData.append('archivo', archivo);
      return requestFormData('productos', `/productos/${id}/imagenes/subir?orden=${orden}`, formData).then((img) => {
        if (img.url) img.url = normalizarUrlImagen(img.url);
        return img;
      });
    },
    eliminarImagen: (imagenId) => request('productos', `/productos/imagenes/${imagenId}`, { method: 'DELETE', auth: true }),
    actualizarOrdenImagen: (imagenId, orden) => request('productos', `/productos/imagenes/${imagenId}/orden`, { method: 'PATCH', body: { orden }, auth: true }),
    unidades: {
      listar: (id) => request('productos', `/productos/${id}/unidades`, { auth: 'opcional' }),
      crear: (id, datos) => request('productos', `/productos/${id}/unidades`, { method: 'POST', body: datos, auth: true }),
      editar: (unidadId, datos) => request('productos', `/productos/unidades/${unidadId}`, { method: 'PATCH', body: datos, auth: true }),
      eliminar: (unidadId) => request('productos', `/productos/unidades/${unidadId}`, { method: 'DELETE', auth: true }),
    },
    registrarBusqueda: (termino) => request('productos', '/productos/busquedas/registrar', { method: 'POST', body: { termino } }),
    busquedasPopulares: () => request('productos', '/productos/busquedas/populares'),
  },
  pedidos: {
    crear: (datos) => request('pedidos', '/pedidos', { method: 'POST', body: datos, auth: true }),
    obtener: (id) => request('pedidos', `/pedidos/${id}`, { auth: true }),
    // Costo de envío según la distancia del destino al almacén (público): permite mostrar el total
    // real antes de crear el pedido.
    // Lo que hay que saber antes de armar el pedido (hoy: { monto_minimo }).
    condiciones: () => request('pedidos', '/pedidos/condiciones'),
    cotizarEnvio: (latitud, longitud) => request('pedidos', `/pedidos/costo-envio?latitud=${encodeURIComponent(latitud)}&longitud=${encodeURIComponent(longitud)}`),
    // El envío exacto de este carrito a este destino (con el recargo por peso si es una compra por mayor).
    cotizar: (datos) => request('pedidos', '/pedidos/cotizar', { method: 'POST', body: datos, auth: true }),
    // Compradores lejanos: las ciudades a las que se llega (público) y el DNI de quien recibe (nombre enmascarado).
    destinosLejanos: () => request('pedidos', '/destinos-lejanos'),
    consultarDni: (dni) => request('pedidos', `/pedidos/dni/${encodeURIComponent(dni)}`, { auth: true }),
    // Dónde deja el repartidor los pedidos que van por agencia de carga (público; lo cambia el admin).
    agenciaDespacho: () => request('pedidos', '/configuracion/agencia-despacho'),
    admin: {
      // Todas las ciudades lejanas (también las ocultas) con sus condiciones, para editarlas.
      destinosLejanos: () => request('pedidos', '/destinos-lejanos/admin', { auth: true }),
      crearDestino: (datos) => request('pedidos', '/destinos-lejanos', { method: 'POST', body: datos, auth: true }),
      actualizarDestino: (id, datos) => request('pedidos', `/destinos-lejanos/${id}`, { method: 'PUT', body: datos, auth: true }),
      actualizarAgenciaDespacho: (latitud, longitud) => request('pedidos', '/configuracion/agencia-despacho', { method: 'PATCH', body: { latitud, longitud }, auth: true }),
    },
  },
  transporte: {
    obtenerEnvio: (pedidoId) => request('transporte', `/envios/${pedidoId}`, { auth: true }),
    // Pedido por agencia: el comprador confirma que lo recogió en la agencia de su ciudad.
    confirmarRecibido: (envioId) => request('transporte', `/envios/${envioId}/recibido`, { method: 'POST', auth: true }),
    listarTodos: () => request('transporte', '/envios', { auth: true }),
    actualizarEstado: (envioId, estado) => request('transporte', `/envios/${envioId}/estado`, { method: 'PATCH', body: { estado }, auth: true }),
    ruta: (envioId) => request('transporte', `/envios/${envioId}/ruta`, { auth: true }),
    actualizarUbicacion: (envioId, latitud, longitud) => request('transporte', `/envios/${envioId}/ubicacion`, { method: 'PATCH', body: { latitud: String(latitud), longitud: String(longitud) }, auth: true }),
    propuestas: () => request('transporte', '/envios/propuestas', { auth: true }),
    aceptar: (envioId) => request('transporte', `/envios/${envioId}/aceptar`, { method: 'POST', auth: true }),
    rechazar: (envioId) => request('transporte', `/envios/${envioId}/rechazar`, { method: 'POST', auth: true }),
    // Rol productor: envíos en "pendiente_entrega_directa" que le corresponden a él (ningún
    // repartidor tuvo capacidad suficiente) y la acción de cerrarlos — reusa actualizarEstado
    // (mismo endpoint que ya usa el repartidor) en vez de duplicar la llamada HTTP.
    pendientesEntregaDirecta: () => request('transporte', '/envios/pendientes-entrega-directa', { auth: true }),
    marcarEntregado: (envioId) => request('transporte', `/envios/${envioId}/estado`, { method: 'PATCH', body: { estado: 'entregado' }, auth: true }),
    // Evidencia de entrega (repartidor): PIN que le dictó el comprador + foto del paquete. El
    // backend copia el GPS del repartidor a ese instante — no hace falta mandarlo desde acá.
    confirmarEntrega: (envioId, pin, archivoFoto) => {
      exigirImagenLiviana(archivoFoto);
      const formData = new FormData();
      formData.append('pin', pin);
      formData.append('foto', archivoFoto);
      return requestFormData('transporte', `/envios/${envioId}/entregar`, formData);
    },
    // Rol admin: envíos "pendiente_asignacion" cuyo motivo es específicamente falta de
    // capacidad (no "nadie disponible en absoluto", que se resuelve solo) — ver
    // motivo_pendiente en transporte/models.py.
    pendientesPorCapacidad: () => request('transporte', '/envios/pendientes-por-capacidad', { auth: true }),
    // Compradores lejanos (repartidor): dejó el pedido en la agencia de carga de Huamanga, con la guía y su foto.
    despacharAgencia: (envioId, agencia, numeroGuia, archivoFoto) => {
      exigirImagenLiviana(archivoFoto);
      const formData = new FormData();
      formData.append('agencia', agencia);
      formData.append('numero_guia', numeroGuia);
      formData.append('foto', archivoFoto);
      return requestFormData('transporte', `/envios/${envioId}/despachar-agencia`, formData);
    },
    // Viajes compartidos en camión del transportista: él marca la salida (cuando el viaje juntó su carga) y la llegada.
    viajesMios: () => request('transporte', '/viajes/mios', { auth: true }),
    salirViaje: (viajeId) => request('transporte', `/viajes/${viajeId}/salir`, { method: 'POST', body: {}, auth: true }),
    llegarViaje: (viajeId) => request('transporte', `/viajes/${viajeId}/llegar`, { method: 'POST', auth: true }),
    admin: {
      // Transportistas de carga por estado de sus documentos ("pendiente", "verificado", "rechazado").
      transportistas: (estado = 'pendiente') => request('transporte', `/repartidores/admin/transportistas?estado=${encodeURIComponent(estado)}`, { auth: true }),
      documentoTransportista: (id, cual) => requestBlob('transporte', `/repartidores/admin/transportistas/${id}/documento/${cual}`),
      verificarTransportista: (id) => request('transporte', `/repartidores/admin/transportistas/${id}/verificar`, { method: 'POST', auth: true }),
      rechazarTransportista: (id, motivo) => request('transporte', `/repartidores/admin/transportistas/${id}/rechazar`, { method: 'POST', body: { motivo }, auth: true }),
      // Viajes en curso; el admin asigna el transportista y puede adelantar la salida (forzar).
      viajes: () => request('transporte', '/viajes', { auth: true }),
      asignarViaje: (viajeId, transportistaId) => request('transporte', `/viajes/${viajeId}/asignar`, { method: 'POST', body: { transportista_id: transportistaId }, auth: true }),
      salirViaje: (viajeId, forzar = false) => request('transporte', `/viajes/${viajeId}/salir`, { method: 'POST', body: { forzar }, auth: true }),
    },
  },
  repartidores: {
    miPerfil: () => request('transporte', '/repartidores/me', { auth: true }),
    crear: (datos) => request('transporte', '/repartidores', { method: 'POST', body: datos, auth: true }),
    // Transportista de carga: placa, licencia y los documentos (licencia, tarjeta de propiedad, SOAT) que revisa el admin.
    enviarDocumentosCarga: ({ placa, licenciaNumero, licencia, tarjetaPropiedad, soat }) => {
      const formData = new FormData();
      formData.append('placa', placa);
      formData.append('licencia_numero', licenciaNumero);
      formData.append('licencia', licencia);
      formData.append('tarjeta_propiedad', tarjetaPropiedad);
      formData.append('soat', soat);
      return requestFormData('transporte', '/repartidores/me/carga', formData);
    },
  },
  notificaciones: {
    porPedido: (pedidoId) => request('notificaciones', `/notificaciones/${pedidoId}`, { auth: true }),
    listarTodas: () => request('notificaciones', '/notificaciones', { auth: true }),
  },
  pagos: {
    procesar: (datos) => request('pagos', '/pagos/procesar', { method: 'POST', body: datos, auth: true }),
    obtener: (pedidoId) => request('pagos', `/pagos/${pedidoId}`, { auth: true }),
    // Público: la comisión de la plataforma, el límite de Yape y la cuenta para transferir (null si no hay).
    condiciones: () => request('pagos', '/pagos/condiciones'),
    // El comprador sube la constancia de su transferencia: el pago queda "en revisión" hasta que un admin la revisa.
    enviarTransferencia: (pedidoId, numeroOperacion, archivo) => {
      const formData = new FormData();
      formData.append('pedido_id', pedidoId);
      formData.append('numero_operacion', numeroOperacion);
      formData.append('archivo', archivo);
      return requestFormData('pagos', '/pagos/transferencia', formData);
    },
    admin: {
      transferencias: () => request('pagos', '/pagos/admin/transferencias', { auth: true }),
      constancia: (pedidoId) => requestBlob('pagos', `/pagos/admin/transferencias/${pedidoId}/constancia`),
      aprobar: (pedidoId) => request('pagos', `/pagos/admin/transferencias/${pedidoId}/aprobar`, { method: 'POST', auth: true }),
      rechazar: (pedidoId, motivo) => request('pagos', `/pagos/admin/transferencias/${pedidoId}/rechazar`, { method: 'POST', body: { motivo }, auth: true }),
    },
  },
  certificacion: {
    verificar: (productoId) => request('certificacion', `/certificacion/${productoId}/verificar`),
    historial: (productoId) => request('certificacion', `/certificacion/${productoId}/historial`),
    qrUrl: (productoId) => `${API_BASE.certificacion}/certificacion/${productoId}/qr`,
    certificadoPedido: (pedidoId) => request('certificacion', `/certificacion/pedido/${pedidoId}/certificado`),
  },
};
