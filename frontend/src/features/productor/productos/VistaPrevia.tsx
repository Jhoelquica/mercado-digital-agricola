// La tarjeta del catálogo tal como la verá el comprador, sin botones (renderTarjetaProductoCatalogo con clickable: false).
import type { UnidadAlternativa } from '../../../api/productos';
import { formatearMoneda } from '../../../lib/formato';
import { iconoCategoria, mejorUnidadPorMayor, nombreCategoria, unidadCorta, unidadPlural } from '../../../lib/productos';
import { MediaProducto } from '../../catalogo/TarjetasProducto';

interface ProductoVistaPrevia {
  nombre: string;
  categoria: string | null;
  precio: number | null;
  stock: number | null;
  unidad_medida: string | null;
  productor_nombre?: string | null;
  imagen_url?: string | null;
  imagen_principal?: string | null;
  calificacion_promedio?: number | null;
  total_resenas?: number;
  unidades_alternativas?: UnidadAlternativa[];
}

export function TarjetaProductoVistaPrevia({ producto: p }: { producto: ProductoVistaPrevia }) {
  const stock = Number(p.stock ?? 0);
  const tieneCalificacion = p.calificacion_promedio != null && Number(p.total_resenas) > 0;
  const porMayor = mejorUnidadPorMayor(p);
  return (
    <div className="product-card" style={{ animationDelay: '0ms', cursor: 'default' }}>
      <div className="product-card-media">
        <MediaProducto key={p.imagen_principal || p.imagen_url || ''} producto={p} clase="product-banner" />
      </div>
      <div className="product-card-body">
        <div className="product-card-top">
          <span className="pill pill-categoria">{iconoCategoria(p.categoria)} {nombreCategoria(p.categoria)}</span>
          {tieneCalificacion && <span className="product-rating"><span className="rating-stars"><i className="ti ti-star estrella-llena" /></span> {Number(p.calificacion_promedio).toFixed(1)} <span className="rating-count-mini">({p.total_resenas})</span></span>}
        </div>
        <h4>{p.nombre}</h4>
        <span className="product-productor"><i className="ti ti-plant-2" /> {p.productor_nombre || 'Productor local'}</span>
        <span className="product-price">{formatearMoneda(p.precio)} <span className="price-unit">/ {unidadCorta(p.unidad_medida)}</span></span>
        {porMayor && (
          <span className="product-por-mayor"><i className="ti ti-packages" aria-hidden="true" /> Por mayor: {formatearMoneda(porMayor.porBase)} / {unidadCorta(p.unidad_medida)}{porMayor.ahorro ? <> <strong>−{porMayor.ahorro}%</strong></> : null}</span>
        )}
        <span className={`product-stock ${stock <= 5 ? 'low' : ''}`}>{stock > 0 ? `${stock} ${unidadPlural(p.unidad_medida, stock)} disponibles` : 'Sin stock'}</span>
      </div>
    </div>
  );
}
