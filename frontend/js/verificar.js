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
  const d = new Date(fechaStr);
  if (isNaN(d)) return fechaStr;
  return d.toLocaleString('es-PE', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function hashCorto(hash) {
  if (!hash) return '—';
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

const EVENTOS_LABEL = {
  cosecha_registrada: { emoji: '🌾', texto: 'Cosecha registrada' },
  certificado_productor: { emoji: '✅', texto: 'Certificado por el productor' },
  verificado_punto_venta: { emoji: '🏪', texto: 'Verificado en punto de venta' },
};

function labelEvento(evento) {
  return EVENTOS_LABEL[evento] || { emoji: '📦', texto: evento };
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
    <div class="verificar-banner banner-error">
      <span class="banner-icon">⚠️</span>
      <div>
        <strong>No se pudo verificar el producto</strong>
        <p>${escapeHtml(mensaje)}</p>
      </div>
    </div>`;
}

function renderBanner(verificacion) {
  if (verificacion.total_bloques === 0) {
    return `
      <div class="verificar-banner banner-neutro">
        <span class="banner-icon">ℹ️</span>
        <div>
          <strong>Sin historial de certificación aún</strong>
          <p>${escapeHtml(verificacion.mensaje)}</p>
        </div>
      </div>`;
  }
  if (verificacion.valido) {
    return `
      <div class="verificar-banner banner-ok">
        <span class="banner-icon">✅</span>
        <div>
          <strong>Cadena verificada, sin alteraciones</strong>
          <p>${escapeHtml(verificacion.mensaje)}</p>
        </div>
      </div>`;
  }
  return `
    <div class="verificar-banner banner-error">
      <span class="banner-icon">🚨</span>
      <div>
        <strong>Alteración detectada</strong>
        <p>${escapeHtml(verificacion.mensaje)}</p>
      </div>
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
            <div class="timeline-marker">${info.emoji}</div>
            <div class="timeline-content">
              <div class="timeline-header">
                <strong>${escapeHtml(info.texto)}</strong>
                <span class="timeline-indice">Bloque #${b.indice}</span>
              </div>
              <div class="timeline-fecha">${formatearFecha(b.fecha)}</div>
              ${b.datos ? `<p class="timeline-datos">${escapeHtml(b.datos)}</p>` : ''}
              <div class="timeline-hash">Hash: <code>${escapeHtml(hashCorto(b.hash_actual))}</code></div>
            </div>
          </div>`;
      }).join('')}
    </div>`;
}

function renderPagina(producto, verificacion, historial) {
  document.getElementById('contenido').innerHTML = `
    <div class="verificar-producto-header">
      <h1>${escapeHtml(producto.nombre)}</h1>
      <p class="muted">👨‍🌾 ${escapeHtml(producto.productor_nombre || 'Productor no especificado')}</p>
    </div>
    ${renderBanner(verificacion)}
    <h2 class="timeline-titulo">🔗 Historial de la cadena</h2>
    ${renderLineaTiempo(historial)}
    <div class="verificar-nota">
      🔒 <strong>¿Qué significa esto?</strong> Cada evento queda enlazado matemáticamente al anterior mediante un hash — si alguien intentara alterar un registro pasado, la cadena completa dejaría de coincidir y la alteración quedaría expuesta automáticamente.
    </div>
    <div class="verificar-footer">
      <span class="brand-icon">🌱</span> Verificado por AgroMercado
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
