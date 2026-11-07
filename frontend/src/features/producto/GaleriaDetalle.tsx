import { useRef, useState } from 'react';
import type { ProductoDetalle } from '../../api/productos';
import { iconoCategoria } from '../../lib/productos';

// Fotos del producto en su orden (la primera es la principal). Sin fotos: lista vacía y el visor muestra el emoji de su
// categoría.
export function fotosProducto(p: ProductoDetalle): string[] {
  const urls = (Array.isArray(p.imagenes) ? [...p.imagenes].sort((a, b) => (a.orden ?? 0) - (b.orden ?? 0)).map((img) => img.url) : []).filter(Boolean);
  if (!urls.length && p.imagen_url) urls.push(p.imagen_url);
  return urls;
}

interface Props {
  producto: ProductoDetalle;
  fotos: string[];
  insignia: { clase: string; texto: string } | null;
}

// Foto principal grande + tira de miniaturas: clic, flechas, teclado (← →) y deslizar en pantallas táctiles. Con una sola
// foto no hay flechas ni miniaturas.
export function GaleriaDetalle({ producto: p, fotos, insignia }: Props) {
  const varias = fotos.length > 1;
  const total = fotos.length;
  const [actual, setActual] = useState(0);
  const [fallo, setFallo] = useState(false);
  const imagen = useRef<HTMLImageElement>(null);
  const tira = useRef<HTMLDivElement>(null);
  const inicioX = useRef<number | null>(null);

  function mostrar(indice: number) {
    const nuevo = (indice + total) % total;
    setActual(nuevo);
    const img = imagen.current;
    if (img) {
      img.classList.remove('cambio');
      void img.offsetWidth; // reinicia la animación de entrada
      img.classList.add('cambio');
    }
    const mini = tira.current?.children[nuevo] as HTMLElement | undefined;
    if (tira.current && mini) tira.current.scrollLeft = mini.offsetLeft - (tira.current.clientWidth - mini.offsetWidth) / 2; // solo la tira, no la página
  }

  const atributosVisor = varias ? { tabIndex: 0, role: 'group', 'aria-roledescription': 'carrusel', 'aria-label': `Fotos de ${p.nombre}` } : {};

  return (
    <div className="detalle-galeria" id="detalle-galeria">
      <div
        className="detalle-visor"
        {...atributosVisor}
        onKeyDown={varias ? (e) => {
          if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
          e.preventDefault();
          mostrar(actual + (e.key === 'ArrowRight' ? 1 : -1));
        } : undefined}
        onTouchStart={varias ? (e) => { inicioX.current = e.touches[0]?.clientX ?? null; } : undefined}
        onTouchEnd={varias ? (e) => {
          if (inicioX.current == null) return;
          const dx = (e.changedTouches[0]?.clientX ?? inicioX.current) - inicioX.current;
          inicioX.current = null;
          if (Math.abs(dx) > 40) mostrar(actual + (dx < 0 ? 1 : -1));
        } : undefined}
      >
        {fotos.length && !fallo
          ? <img ref={imagen} className="detalle-visor-img" src={fotos[actual]} alt={p.nombre || ''} fetchPriority="high" onError={() => setFallo(true)} />
          : <span className="detalle-visor-emoji" aria-hidden="true">{iconoCategoria(p.categoria)}</span>}
        {insignia && <span className={`nmp-insignia nmp-insignia--${insignia.clase}`}>{insignia.texto}</span>}
        {varias && (
          <>
            <button type="button" className="detalle-visor-nav detalle-visor-prev" aria-label="Foto anterior" onClick={() => mostrar(actual - 1)}><i className="ti ti-chevron-left" aria-hidden="true" /></button>
            <button type="button" className="detalle-visor-nav detalle-visor-next" aria-label="Foto siguiente" onClick={() => mostrar(actual + 1)}><i className="ti ti-chevron-right" aria-hidden="true" /></button>
            <span className="detalle-contador" role="status" aria-live="polite">{actual + 1} / {total}</span>
          </>
        )}
      </div>
      {varias && (
        <div className="detalle-miniaturas" ref={tira}>
          {fotos.map((url, i) => (
            <button key={url} type="button" className={`detalle-miniatura${i === actual ? ' active' : ''}`} aria-label={`Ver foto ${i + 1} de ${total}`} aria-current={i === actual} onClick={() => mostrar(i)}>
              <img src={url} alt="" loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
