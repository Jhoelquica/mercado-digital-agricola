// ============ UTILIDADES (autónomas, esta página no carga app.js) ============
function escapeHtml(valor) {
  return String(valor ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatearFecha(fechaStr) {
  if (!fechaStr) return '—';
  // La API entrega timestamps UTC sin zona ("2026-09-19T02:42:04.85"): sin la Z el navegador los
  // lee como hora local y muestra la fecha corrida (en Perú, +5 h — a veces el día siguiente).
  const conZona = /(Z|[+-]\d{2}:?\d{2})$/i.test(fechaStr) ? fechaStr : `${fechaStr}Z`;
  const d = new Date(conZona);
  if (isNaN(d)) return fechaStr;
  return d.toLocaleString('es-PE', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function hashCorto(hash) {
  if (!hash) return '—';
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

const EVENTOS_LABEL = {
  // Vale para todo lo que se obtiene: una cosecha, los huevos de un periodo o un queso (el texto del bloque lo detalla).
  cosecha_registrada: { icono: 'ti-seedling', texto: 'Registrado en el origen' },
  certificado_productor: { icono: 'ti-certificate', texto: 'Certificado por el productor' },
  llegada_almacen: { icono: 'ti-building-warehouse', texto: 'Llegada al almacén' },
  verificado_punto_venta: { icono: 'ti-building-store', texto: 'Verificado en punto de venta' },
};

function labelEvento(evento) {
  return EVENTOS_LABEL[evento] || { icono: 'ti-package', texto: evento };
}

// Solo aplica al evento certificado_productor; el backend ya resuelve el nombre en /historial
// (verificador_nombre puede venir null si el verificador fue borrado, ahí caemos al id truncado).
function renderVerificadorLinea(b) {
  if (b.evento !== 'certificado_productor' || !b.verificador_id) return '';
  const nombre = b.verificador_nombre || `verificador ${String(b.verificador_id).slice(0, 8)}`;
  // La credencial la asignó el admin al invitarlo (ver Invitacion.credencial en usuarios); null en perfiles anteriores.
  const credencial = b.verificador_credencial ? ` · Credencial: ${escapeHtml(b.verificador_credencial)}` : '';
  return `<p class="timeline-verificador"><i class="ti ti-user-check"></i> Certificado por: ${escapeHtml(nombre)}${credencial}</p>`;
}

// El texto de un bloque de cosecha trae varias oraciones (cantidad y chacra; dónde estaba el agricultor al sembrar y al
// cosechar; la huella de la foto): se muestran una por línea y las advertencias con su ícono, no solo con color.
function renderDatosBloque(datos, escapar) {
  const frases = String(datos).split(/(?<=\.)\s+(?=[A-ZÁÉÍÓÚÑ])/);
  if (frases.length < 2) return `<p class="timeline-datos">${escapar(datos)}</p>`;
  return `<ul class="timeline-datos timeline-datos-lista">${frases.map((frase) => {
    const advertencia = frase.includes('(advertencia)');
    const icono = advertencia ? 'alert-triangle'
      : /^Al (registrar|completar)/.test(frase) ? 'map-pin'
        : /^Foto de la cosecha/.test(frase) ? 'fingerprint' : 'point';
    return `<li${advertencia ? ' class="es-advertencia"' : ''}><i class="ti ti-${icono}" aria-hidden="true"></i><span>${escapar(frase)}</span></li>`;
  }).join('')}</ul>`;
}

// La foto de la cosecha (solo en cosecha_registrada). Su huella está en el texto del bloque, que sí entra en el hash.
function renderFotoBloque(b, escapar) {
  if (!b.foto_url) return '';
  const url = normalizarUrlImagen(b.foto_url);
  return `
    <a class="timeline-foto" href="${escapar(url)}" target="_blank" rel="noopener">
      <img src="${escapar(url)}" alt="Foto de la cosecha tomada en la chacra" loading="lazy">
      <span><i class="ti ti-camera" aria-hidden="true"></i> Foto tomada en la chacra · ver en grande</span>
    </a>`;
}

function obtenerProductoIdDeUrl() {
  const params = new URLSearchParams(window.location.search);
  return (params.get('producto_id') || '').trim();
}

// ============ RENDER ============
function renderCargando() {
  document.getElementById('contenido').innerHTML = `
    <div class="verificar-cargando">
      <div class="spinner"></div>
      <p>Verificando el producto...</p>
    </div>`;
}

function renderErrorGeneral(mensaje) {
  document.getElementById('contenido').innerHTML = `
    <div class="verificar-sello sello-error">
      <span class="verificar-sello-icono"><i class="ti ti-alert-triangle"></i></span>
      <strong>No se pudo verificar el producto</strong>
      <p>${escapeHtml(mensaje)}</p>
    </div>`;
}

// El "sello" es el elemento de mayor peso visual de toda la página a propósito — es la
// respuesta a la única pregunta que trae a alguien acá: ¿este producto es de fiar?
function renderSello(verificacion) {
  if (verificacion.total_bloques === 0) {
    return `
      <div class="verificar-sello sello-neutro">
        <span class="verificar-sello-icono"><i class="ti ti-info-circle"></i></span>
        <strong>Sin historial de certificación aún</strong>
        <p>${escapeHtml(verificacion.mensaje)}</p>
      </div>`;
  }
  if (verificacion.valido) {
    return `
      <div class="verificar-sello sello-ok">
        <span class="verificar-sello-icono"><i class="ti ti-rosette-discount-check"></i></span>
        <strong>Origen verificado</strong>
        <p>${escapeHtml(verificacion.mensaje)}</p>
        <span class="verificar-sello-meta"><i class="ti ti-link"></i> ${verificacion.total_bloques} evento${verificacion.total_bloques === 1 ? '' : 's'} enlazado${verificacion.total_bloques === 1 ? '' : 's'} criptográficamente</span>
      </div>`;
  }
  return `
    <div class="verificar-sello sello-error">
      <span class="verificar-sello-icono"><i class="ti ti-shield-x"></i></span>
      <strong>Alteración detectada</strong>
      <p>${escapeHtml(verificacion.mensaje)}</p>
    </div>`;
}

function renderLineaTiempo(historial) {
  if (!historial.length) {
    return '<p class="muted" style="text-align:center;padding:12px 0 28px;">Aún no hay eventos registrados para este producto.</p>';
  }
  return `
    <div class="timeline">
      ${historial.map((b) => {
        const info = labelEvento(b.evento);
        return `
          <div class="timeline-item">
            <div class="timeline-marker"><i class="ti ${info.icono}"></i></div>
            <div class="timeline-content">
              <div class="timeline-header">
                <strong>${escapeHtml(info.texto)}</strong>
                <span class="timeline-indice">Bloque #${b.indice}</span>
              </div>
              <div class="timeline-fecha"><i class="ti ti-calendar-event"></i> ${formatearFecha(b.fecha)}</div>
              ${renderVerificadorLinea(b)}
              ${b.datos ? renderDatosBloque(b.datos, escapeHtml) : ''}
              ${renderFotoBloque(b, escapeHtml)}
              <div class="timeline-hash"><i class="ti ti-fingerprint"></i> <code>${escapeHtml(hashCorto(b.hash_actual))}</code></div>
            </div>
          </div>`;
      }).join('')}
    </div>`;
}

function renderFotoProducto(producto) {
  const url = producto.imagenes?.[0]?.url || producto.imagen_principal || producto.imagen_url;
  if (url) return `<img src="${escapeHtml(url)}" alt="${escapeHtml(producto.nombre)}">`;
  return `<span class="verificar-producto-foto-vacia"><i class="ti ti-photo"></i></span>`;
}

function renderPagina(producto, verificacion, historial) {
  document.getElementById('contenido').innerHTML = `
    <div class="verificar-producto-hero">
      <div class="verificar-producto-foto">${renderFotoProducto(producto)}</div>
      <h1>${escapeHtml(producto.nombre)}</h1>
      <p class="verificar-producto-productor"><i class="ti ti-tractor"></i> ${escapeHtml(producto.productor_nombre || 'Productor no especificado')}</p>
    </div>
    ${renderSello(verificacion)}
    <h2 class="timeline-titulo"><i class="ti ti-timeline-event"></i> Historial de la cadena</h2>
    ${renderLineaTiempo(historial)}
    <div class="verificar-nota">
      <i class="ti ti-lock"></i>
      <div><strong>¿Qué significa esto?</strong> Cada evento queda enlazado matemáticamente al anterior mediante un hash — si alguien intentara alterar un registro pasado, la cadena completa dejaría de coincidir y la alteración quedaría expuesta automáticamente.</div>
    </div>
    <div class="verificar-footer">
      <i class="ti ti-shield-check"></i> Verificado por Chakra Shop
    </div>
  `;
}

// ============ CARGA ============
async function iniciarVerificacion() {
  renderCargando();

  const productoId = obtenerProductoIdDeUrl();
  if (!productoId) {
    renderErrorGeneral('Falta el identificador del producto en el enlace. Escanea nuevamente el código QR del producto.');
    return;
  }

  let producto;
  try {
    producto = await Api.productos.obtener(productoId);
  } catch (err) {
    if (err.status === 404) {
      renderErrorGeneral('Este producto no existe o ya no está disponible.');
    } else {
      renderErrorGeneral('No se pudo conectar con el servidor de productos. Verifica tu conexión e intenta de nuevo.');
    }
    return;
  }

  let verificacion;
  let historial = [];
  try {
    [verificacion, historial] = await Promise.all([
      Api.certificacion.verificar(productoId),
      Api.certificacion.historial(productoId),
    ]);
  } catch (err) {
    renderErrorGeneral('No se pudo conectar con el servicio de certificación. Intenta de nuevo más tarde.');
    return;
  }

  renderPagina(producto, verificacion, historial);
}

document.addEventListener('DOMContentLoaded', iniciarVerificacion);
