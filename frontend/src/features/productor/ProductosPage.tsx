// ---- Mis Productos: cómo llega cada cosecha publicada al almacén y los productos publicados (reseñas, fotos,
// certificación y unidades de venta) ----
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { productos, type ProductoMio } from '../../api/productos';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { Texto } from '../../idioma/IdiomaContext';
import { formatearFechaServidor, formatearMoneda } from '../../lib/formato';
import { iconoCategoria, nombreCategoria, unidadCorta, unidadPlural } from '../../lib/productos';
import { MediaProducto, SkeletonProductos } from '../catalogo/TarjetasProducto';
import { abrirModalCertificacion } from '../producto/ModalCertificacion';
import { Estrellas } from '../producto/ResenasDetalle';
import { useChacras, useMisProductos, useRegistros } from './comun';
import { GestorFotos } from './productos/GestorFotos';
import { GestorUnidades } from './productos/GestorUnidades';
import { LlegadasAlmacen } from './productos/LlegadasAlmacen';

function ResenasSoloLectura({ productoId }: { productoId: string }) {
  const consulta = useQuery({ queryKey: ['productor', 'resenas', productoId], queryFn: () => productos.listarResenas(productoId), staleTime: Infinity, retry: false });
  if (consulta.isLoading) return <SkeletonFilas cantidad={2} />;
  if (consulta.isError) return <p className="muted">No se pudieron cargar las reseñas.</p>;
  const resenas = consulta.data ?? [];
  if (!resenas.length) return <p className="muted" style={{ paddingTop: 6 }}>Aún no hay reseñas para este producto.</p>;
  return (
    <div className="resenas-lista" style={{ marginTop: 10 }}>
      {resenas.map((r) => (
        <div className="resena-card" key={r.id}>
          <div className="resena-header">
            <span className="resena-usuario">{r.usuario_nombre}</span>
            <span className="rating-stars"><Estrellas promedio={r.calificacion} /></span>
          </div>
          <div className="resena-fecha">{formatearFechaServidor(r.fecha_creacion)}</div>
          {r.comentario && <p className="resena-comentario">{r.comentario}</p>}
        </div>
      ))}
    </div>
  );
}

type Panel = 'resenas' | 'fotos' | 'unidades';

function TarjetaProductorProducto({ p, indice }: { p: ProductoMio & { calificacion_promedio?: number | null; total_resenas?: number }; indice: number }) {
  const stock = Number(p.stock ?? 0);
  const stockBajo = stock > 0 && stock <= 5;
  const sinStock = stock <= 0;
  const totalResenas = Number(p.total_resenas ?? 0);
  const tieneCalificacion = p.calificacion_promedio != null && totalResenas > 0;
  // Reseñas y Fotos se turnan entre sí; Unidades cierra los demás paneles de la tarjeta. Lo abierto una vez queda cargado.
  const [abiertos, setAbiertos] = useState<Record<Panel, boolean>>({ resenas: false, fotos: false, unidades: false });
  const [cargados, setCargados] = useState<Record<Panel, boolean>>({ resenas: false, fotos: false, unidades: false });
  const [fotosCambiadas, setFotosCambiadas] = useState<number | null>(null);
  function alternar(panel: Panel) {
    setCargados({ ...cargados, [panel]: true });
    if (panel === 'resenas') setAbiertos({ ...abiertos, fotos: false, resenas: !abiertos.resenas });
    else if (panel === 'fotos') setAbiertos({ ...abiertos, resenas: false, fotos: !abiertos.fotos });
    else setAbiertos({ resenas: false, fotos: false, unidades: !abiertos.unidades });
  }
  return (
    <div className="product-card productor-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms`, cursor: 'default' }}>
      <div className="product-card-media">
        <MediaProducto producto={p} clase="product-banner" />
        {sinStock && <span className="badge-flotante badge-stock-bajo">Sin stock</span>}
        {!sinStock && stockBajo && <span className="badge-flotante badge-stock-bajo"><i className="ti ti-alert-triangle" /> Poco stock</span>}
      </div>
      <div className="product-card-body">
        <div className="product-card-top">
          <span className="pill pill-categoria">{iconoCategoria(p.categoria)} {nombreCategoria(p.categoria)}</span>
        </div>
        <h4>{p.nombre}</h4>
        <span className="product-price">{formatearMoneda(p.precio)} <span className="price-unit">/ {unidadCorta(p.unidad_medida)}</span></span>
        <span className={`product-stock ${stockBajo || sinStock ? 'low' : ''}`}>{stock > 0 ? `${p.stock} ${unidadPlural(p.unidad_medida, stock)} disponibles` : 'Sin stock'}</span>
        <div className="stock-barra"><div className={`stock-barra-fill ${stockBajo || sinStock ? 'low' : ''}`} style={{ width: `${Math.min(100, (stock / 50) * 100)}%` }} /></div>

        <div className="productor-card-acciones">
          <button type="button" className="btn btn-outline btn-sm btn-toggle-resenas" onClick={() => alternar('resenas')}>
            <i className="ti ti-star" /> {tieneCalificacion ? `${totalResenas} reseña${totalResenas === 1 ? '' : 's'}` : 'Reseñas'}
          </button>
          <button type="button" className="btn btn-outline btn-sm btn-toggle-fotos" onClick={() => alternar('fotos')}><i className="ti ti-camera" /> {fotosCambiadas == null ? 'Fotos' : `Fotos (${fotosCambiadas}/5)`}</button>
          <button type="button" className="btn btn-outline btn-sm btn-ver-certificacion" onClick={() => abrirModalCertificacion(p.id)}><i className="ti ti-link" /> Certificación</button>
          <button type="button" className="btn btn-outline btn-sm btn-toggle-unidades" onClick={() => alternar('unidades')}><i className="ti ti-scale" /> Unidades</button>
        </div>

        <div className={`panel-expandible ${abiertos.resenas ? '' : 'hidden'}`} data-panel="resenas" data-id={p.id}>{cargados.resenas && <ResenasSoloLectura productoId={p.id} />}</div>
        <div className={`panel-expandible ${abiertos.fotos ? '' : 'hidden'}`} data-panel="fotos" data-id={p.id}>{cargados.fotos && <GestorFotos productoId={p.id} alCambiar={setFotosCambiadas} />}</div>
        <div className={`panel-expandible ${abiertos.unidades ? '' : 'hidden'}`} data-panel="unidades" data-id={p.id}>{cargados.unidades && <GestorUnidades producto={p} />}</div>
      </div>
    </div>
  );
}

export default function ProductosPage() {
  const queryClient = useQueryClient();
  const misProductos = useMisProductos();
  const registros = useRegistros();
  const chacras = useChacras();
  const lista = misProductos.data ?? [];
  // Todo lo que no es borrador: en camino al almacén (en_transito) y a la venta (disponible).
  const publicados = lista.filter((p) => p.estado !== 'borrador');
  const enTransito = lista.filter((p) => p.estado === 'en_transito');
  function refrescar() {
    void queryClient.invalidateQueries({ queryKey: ['productor', 'mis-productos'] });
    void queryClient.invalidateQueries({ queryKey: ['productor', 'recojos'] });
  }
  return (
    <div className="productor-tab-panel active" data-tab-panel="productos">
      {/* Cómo llega cada cosecha publicada al almacén: la lleva el agricultor o la recoge un repartidor. */}
      {!misProductos.isLoading && <LlegadasAlmacen enTransito={enTransito} registros={registros.data ?? []} chacras={chacras.data ?? []} />}

      <div className="section-header inner">
        <h3><Texto clave="seccion.publicados">Mis productos publicados</Texto></h3>
        <button type="button" className="btn btn-outline" id="btn-refrescar-mis-productos" onClick={refrescar}>↻ Refrescar</button>
      </div>
      <div id="mis-productos-grid" className="grid">
        {misProductos.isFetching && !publicados.length ? <SkeletonProductos cantidad={3} />
          : publicados.map((p, i) => <TarjetaProductorProducto key={p.id} p={p} indice={i} />)}
      </div>
      {!misProductos.isLoading && !misProductos.isError && !publicados.length && (
        <div id="mis-productos-empty" className="empty-state">
          <span className="empty-state-icon"><i className="ti ti-seedling" /></span>
          <p><strong>Aún no tienes productos publicados</strong></p>
          <p className="muted">Completa un borrador de arriba y publícalo para que aparezca en el catálogo.</p>
        </div>
      )}
    </div>
  );
}
