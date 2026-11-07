// ---- Borradores (nacen al completar una cosecha con su foto: crear_producto_desde_cosecha en productos/main.py) ----
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { Chacra } from '../../api/chacras';
import type { RegistroProduccion } from '../../api/productores';
import { productos, type ProductoMio } from '../../api/productos';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { manejarError, toast } from '../../components/Toast';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearMoneda } from '../../lib/formato';
import { CATEGORIAS_PRODUCTO, ICONOS_CATEGORIA, NOMBRE_CATEGORIA } from '../../lib/productos';
import { descripcionSugerida, precioSugerido } from '../../lib/reglas';
import { useChacras, useMisProductos, useRegistros } from './comun';
import { GestorFotos, useImagenesProducto } from './productos/GestorFotos';
import { GestorUnidades } from './productos/GestorUnidades';
import { TarjetaProductoVistaPrevia } from './productos/VistaPrevia';

// Mismo tope que el servidor (DESCRIPCION_MAXIMA en productos/main.py).
const DESCRIPCION_MAXIMA = 600;

interface Sugerencias { precio: number | null; referenciaKg: number | null; descripcion: string | null }

// H6 (validación con usuarios del 2026-10-14): el precio y la descripción sugeridos salen de la siembra de la que nació el
// borrador. Vienen escritos en los campos si el agricultor todavía no puso nada, y los puede cambiar antes de publicar.
function sugerenciasDeBorrador(p: ProductoMio, registros: RegistroProduccion[], chacras: Chacra[]): Sugerencias {
  const registro = registros.find((r) => String(r.id) === String(p.registro_produccion_id));
  if (!registro) return { precio: null, referenciaKg: null, descripcion: null };
  const chacra = chacras.find((c) => String(c.id) === String(registro.chacra_id));
  return {
    referenciaKg: registro.precio_referencia_kg,
    precio: precioSugerido({ precioReferenciaKg: registro.precio_referencia_kg, unidad: p.unidad_medida, equivalenciaKg: registro.equivalencia_kg }),
    descripcion: descripcionSugerida({
      cultivo: registro.cultivo, chacra: chacra?.nombre || chacra?.codigo, fechaSiembra: registro.fecha_siembra,
      fechaCosecha: registro.fecha_cosecha_real, cantidad: registro.cantidad_cosechada, unidad: registro.unidad_medida,
    }),
  };
}

function TextoPrecioSugerido({ p, sug }: { p: ProductoMio; sug: Sugerencias }) {
  if (sug.precio == null) {
    return <><i className="ti ti-info-circle" aria-hidden="true" /><span>Todavía no hay «{p.nombre}» con precio en la tienda para comparar: pon el precio que te parezca justo.</span></>;
  }
  const porKg = p.unidad_medida !== 'kg' ? ` (${formatearMoneda(sug.referenciaKg)} el kg)` : '';
  return <><i className="ti ti-bulb" aria-hidden="true" /><span><strong>Sugerido: {formatearMoneda(sug.precio)} por {p.unidad_medida}</strong>, el precio de referencia de «{p.nombre}» en la tienda{porKg}. Puedes cambiarlo.</span></>;
}

function TarjetaBorrador({ p, indice, sug }: { p: ProductoMio; indice: number; sug: Sugerencias }) {
  const queryClient = useQueryClient();
  const { data: imagenes = [] } = useImagenesProducto(p.id);
  const [categoria, setCategoria] = useState(p.categoria ?? '');
  const [precio, setPrecio] = useState(p.precio != null ? String(p.precio) : sug.precio != null ? String(sug.precio) : '');
  const [descripcion, setDescripcion] = useState(p.descripcion || sug.descripcion || '');
  const [verUnidades, setVerUnidades] = useState(false);
  const [unidadesAbiertas, setUnidadesAbiertas] = useState(false);
  const [publicando, setPublicando] = useState(false);
  const descripcionFueSugerida = !p.descripcion && Boolean(sug.descripcion);
  const precioNumero = parseFloat(precio);
  const conPrecio = Number.isFinite(precioNumero) && precioNumero > 0;
  const puedePublicar = conPrecio && imagenes.length > 0;
  // Progreso solo visual (categoría / precio / al menos 1 foto = 3 pasos): ayuda a ver qué falta.
  const progreso = Math.round((((categoria ? 1 : 0) + (conPrecio ? 1 : 0) + (imagenes.length > 0 ? 1 : 0)) / 3) * 100);
  const primeraImagen = [...imagenes].sort((a, b) => (a.orden ?? 0) - (b.orden ?? 0))[0]?.url;

  async function publicar() {
    setPublicando(true);
    try {
      await productos.actualizar(p.id, { categoria: categoria || null, precio: Number.isFinite(precioNumero) ? precioNumero : null, descripcion: descripcion.trim() });
      await productos.publicar(p.id);
      toast('Producto publicado — ya está visible en el catálogo 🎉');
      // Se recarga la sección: "publicados" necesita datos que este borrador no tenía (la imagen principal, p. ej.).
      await queryClient.invalidateQueries({ queryKey: ['productor', 'mis-productos'] });
      void queryClient.invalidateQueries({ queryKey: ['productor', 'recojos'] });
      void queryClient.invalidateQueries({ queryKey: ['catalogo'] });
    } catch (err) {
      manejarError(err, 'publicar el producto');
      setPublicando(false);
    }
  }

  return (
    <div className="pedido-card borrador-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-producto-id={p.id}>
      <div className="borrador-card-header">
        <h4>{p.nombre} <span className="pill pill-estado pill-pendiente"><i className="ti ti-pencil" /> Borrador</span></h4>
        <div className="borrador-progreso-wrap">{progreso}% completo <div className="borrador-progreso-barra"><div className="borrador-progreso-fill" style={{ width: `${progreso}%` }} /></div></div>
      </div>
      <div className="borrador-card-body">
        <p className="muted">{Number(p.stock)} {p.unidad_medida} de tu cosecha aprobada — completa lo que falta para publicarlo.</p>

        <div className="publicar-layout">
          <div className="form-grid">
            <label>Categoría
              <select className="borrador-categoria" value={categoria} disabled={publicando} onChange={(e) => setCategoria(e.target.value)}>
                <option value="">Elige una categoría</option>
                {CATEGORIAS_PRODUCTO.map((c) => <option key={c} value={c}>{ICONOS_CATEGORIA[c]} {NOMBRE_CATEGORIA[c]}</option>)}
              </select>
            </label>
            <label>Precio (S/ por {p.unidad_medida})
              <input type="number" className="borrador-precio" min="0" step="0.01" placeholder="0.00" value={precio} disabled={publicando} aria-describedby={`borrador-precio-ayuda-${p.id}`} onChange={(e) => setPrecio(e.target.value)} />
              <span className="borrador-sugerencia" id={`borrador-precio-ayuda-${p.id}`}><TextoPrecioSugerido p={p} sug={sug} /></span>
            </label>
            <label><span>Descripción <span className="borrador-opcional">(opcional)</span></span>
              <textarea className="borrador-descripcion" rows={4} maxLength={DESCRIPCION_MAXIMA} aria-describedby={`borrador-desc-ayuda-${p.id}`} disabled={publicando}
                placeholder="Ej.: Papa amarilla de altura, cosechada a mano en Quinua. Ideal para puré y sancochado." value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
              <span className="borrador-descripcion-pie" id={`borrador-desc-ayuda-${p.id}`}>
                <span>{descripcionFueSugerida
                  ? 'Te la escribimos con los datos de tu cosecha: cámbiala como quieras o agrégale la variedad y para qué sirve.'
                  : 'Cuenta la variedad, cómo la cultivaste o para qué sirve. Sale en la página del producto.'}</span>
                <span className={`borrador-descripcion-contador${descripcion.length >= DESCRIPCION_MAXIMA * 0.9 ? ' cerca' : ''}`} aria-live="polite">{descripcion.length}/{DESCRIPCION_MAXIMA}</span>
              </span>
            </label>
          </div>
          <div className="publicar-preview">
            <span className="publicar-preview-label"><i className="ti ti-eye" /> Vista previa</span>
            <div className="borrador-preview-card">
              <TarjetaProductoVistaPrevia producto={{ ...p, categoria: categoria || p.categoria, precio: Number.isFinite(precioNumero) ? precioNumero : 0, imagen_principal: primeraImagen || p.imagen_principal }} />
            </div>
          </div>
        </div>

        <div className="fotos-upload-bloque">
          <div className="fotos-upload-header">
            <h4><i className="ti ti-camera" /> Fotos del producto</h4>
          </div>
          <p className="muted">La primera foto será la principal en el catálogo. Formatos: JPG, PNG o WEBP.</p>
          <div className="gestor-fotos-borrador"><GestorFotos productoId={p.id} /></div>
        </div>

        <div className="fotos-upload-bloque">
          <div className="fotos-upload-header">
            <h4><i className="ti ti-scale" /> Unidades de venta adicionales</h4>
            <button type="button" className="btn btn-outline btn-sm btn-toggle-unidades" disabled={publicando} onClick={() => { setVerUnidades(!verUnidades); setUnidadesAbiertas(true); }}>Ver / editar</button>
          </div>
          <p className="muted">Editable en cualquier momento, publicado o no.</p>
          <div className={`panel-expandible ${verUnidades ? '' : 'hidden'}`} data-panel="unidades" data-id={p.id}>{unidadesAbiertas && <GestorUnidades producto={p} />}</div>
        </div>

        <button type="button" className="btn btn-primary btn-block btn-publicar-borrador" data-producto-id={p.id} disabled={!puedePublicar || publicando} onClick={() => { void publicar(); }}>Publicar</button>
      </div>
    </div>
  );
}

export default function BorradoresPage() {
  const misProductos = useMisProductos();
  const registros = useRegistros();
  const chacras = useChacras();
  const borradores = (misProductos.data ?? []).filter((p) => p.estado === 'borrador');
  // Las siembras y las chacras dan el precio y la descripción sugeridos; si fallan, el borrador sale sin ellos.
  const listos = !misProductos.isLoading && !registros.isLoading && !chacras.isLoading;
  return (
    <div className="productor-tab-panel active" data-tab-panel="borradores">
      <div className="section-header inner">
        <h3><i className="ti ti-pencil" /> <Texto clave="seccion.borradores">Borradores pendientes de completar</Texto></h3>
      </div>
      <div id="productor-borradores-lista" className="borradores-lista">
        {!listos && <SkeletonFilas cantidad={1} />}
        {listos && borradores.map((p, i) => <TarjetaBorrador key={p.id} p={p} indice={i} sug={sugerenciasDeBorrador(p, registros.data ?? [], chacras.data ?? [])} />)}
      </div>
      {listos && !misProductos.isError && !borradores.length && (
        <div id="productor-borradores-empty" className="empty-state">
          <span className="empty-state-icon"><i className="ti ti-pencil" /></span>
          <p><strong>No tienes borradores pendientes</strong></p>
          <p className="muted">Cuando completes una cosecha (con su foto, desde tu chacra), el producto nace acá como borrador — completa categoría, precio y fotos para publicarlo.</p>
        </div>
      )}
    </div>
  );
}
