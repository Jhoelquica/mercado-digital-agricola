import { useState } from 'react';
import type { ProductoCatalogo } from '../../api/productos';
import { formatearMoneda } from '../../lib/formato';
import { iconoCategoria, insigniaProducto, unidadCorta } from '../../lib/productos';

interface ProductoConMedia {
  nombre: string;
  categoria: string | null;
  imagen_url?: string | null;
  imagen_principal?: string | null;
}

// La foto del producto; si no hay o no carga, el emoji de su categoría.
export function MediaProducto({ producto, clase }: { producto: ProductoConMedia; clase: string }) {
  const emoji = iconoCategoria(producto.categoria);
  const url = (producto.imagen_url || producto.imagen_principal || '').trim();
  const [fallo, setFallo] = useState(false);
  if (!url) return <div className={clase}>{emoji}</div>;
  return (
    <div className={`${clase} product-media`}>
      {fallo ? emoji : <img src={url} alt={producto.nombre || ''} loading="lazy" onError={() => setFallo(true)} />}
    </div>
  );
}

// Tarjeta simplificada de la landing: imagen + nombre + (opcional) precio + (opcional) botón "Comprar" que lleva al detalle.
export function ContenidoTarjetaSimple({ producto, conPrecio = false, conBoton = true }: { producto: ProductoCatalogo; conPrecio?: boolean; conBoton?: boolean }) {
  return (
    <>
      <MediaProducto producto={producto} clase="producto-simple-img" />
      <span className="producto-simple-nombre">{producto.nombre}</span>
      {conPrecio && <span className="product-price">{formatearMoneda(producto.precio)} <span className="price-unit">/ {unidadCorta(producto.unidad_medida)}</span></span>}
      {conBoton && <button type="button" className="btn btn-primary btn-sm producto-simple-comprar">Comprar</button>}
    </>
  );
}

// Tarjeta compacta: la comparten el menú de categorías (donde el CSS oculta la línea del productor), el panel de búsqueda
// y "También te puede gustar".
export function ProductoMega({ producto: p, onClick }: { producto: ProductoCatalogo; onClick: () => void }) {
  const insignia = insigniaProducto(p);
  const tieneCalificacion = p.calificacion_promedio != null && p.total_resenas > 0;
  return (
    <button type="button" className={`nav-mega-producto${insignia?.clase === 'agotado' ? ' es-agotado' : ''}`} onClick={onClick}>
      <span className="nmp-media">
        <MediaProducto producto={p} clase="nav-mega-producto-img" />
        {insignia && <span className={`nmp-insignia nmp-insignia--${insignia.clase}`}>{insignia.texto}</span>}
      </span>
      <span className="nav-mega-producto-nombre">{p.nombre}</span>
      <span className="nmp-productor"><i className="ti ti-plant-2" aria-hidden="true" /><span>{p.productor_nombre || 'Productor local'}</span></span>
      <span className="nmp-fila">
        <span className="nav-mega-producto-precio">{formatearMoneda(p.precio)} <small>/ {unidadCorta(p.unidad_medida)}</small></span>
        {tieneCalificacion && (
          <span className="nmp-rating" aria-label={`Calificación ${Number(p.calificacion_promedio).toFixed(1)} de 5`}>
            <i className="ti ti-star" aria-hidden="true" />{Number(p.calificacion_promedio).toFixed(1)}
          </span>
        )}
      </span>
    </button>
  );
}

export function SkeletonProductos({ cantidad }: { cantidad: number }) {
  return (
    <>
      {Array.from({ length: cantidad }, (_, i) => (
        <div className="product-card skeleton-card" key={i}>
          <div className="skeleton skeleton-media" />
          <div className="skeleton-body">
            <div className="skeleton skeleton-line w-40" />
            <div className="skeleton skeleton-line tall w-70" />
            <div className="skeleton skeleton-line w-60" />
            <div className="skeleton skeleton-line w-40" />
            <div className="skeleton skeleton-btn" />
          </div>
        </div>
      ))}
    </>
  );
}
