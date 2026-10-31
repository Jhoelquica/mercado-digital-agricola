import { useQuery, useQueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { certificacion, type Bloque } from '../../api/certificacion';
import { pedidos } from '../../api/pedidos';
import { productores, type ProductorPublico } from '../../api/productores';
import { productos, type MisReservas, type ProductoDetalle, type Resena } from '../../api/productos';
import { useAuth } from '../../auth/AuthContext';
import { toast } from '../../components/Toast';
import { alternarFavorito, esFavorito } from '../../lib/favoritos';
import { formatearMoneda } from '../../lib/formato';
import {
  esUnidadPorMayor, hayCoordenadas, iconoCategoria, infoUnidad, insigniaProducto, mejorUnidadPorMayor, nombreCategoria,
  nombreUnidadPorMayor, normalizarCategoria, ordenarPorDestacado, precioEnUnidad, precioPorMayor, stockEnUnidad, unidadCorta, unidadPlural,
} from '../../lib/productos';
import { ProductoMega } from '../catalogo/TarjetasProducto';
import { useAbrirProducto, useCatalogo, type EstadoDetalle } from '../catalogo/useCatalogo';
import { fotosProducto, GaleriaDetalle } from './GaleriaDetalle';
import { LineaTiempoCertificacion, QrCertificacion } from './LineaTiempoCertificacion';
import { BotonReservaSticky, PanelReserva, useReserva } from './Reserva';
import { Estrellas, ResenasDetalle } from './ResenasDetalle';
import { VideosDetalle } from './VideosDetalle';

// Leaflet solo se descarga si el productor tiene coordenadas.
const MapaSoloLectura = lazy(() => import('./Mapa').then((m) => ({ default: m.MapaSoloLectura })));

interface DatosDetalle {
  producto: ProductoDetalle;
  resenas: Resena[];
  productor: ProductorPublico | null;
  recibidos: Set<string> | null;
  reservas: MisReservas | null;
}

function SkeletonDetalle() {
  return (
    <div className="detalle-skeleton" aria-hidden="true">
      <div className="skeleton detalle-skeleton-visor" />
      <div className="detalle-skeleton-info">
        <div className="skeleton skeleton-line w-40" />
        <div className="skeleton skeleton-line tall w-70" />
        <div className="skeleton skeleton-line w-60" />
        <div className="skeleton skeleton-line tall w-40" />
        <div className="skeleton skeleton-btn" />
      </div>
    </div>
  );
}

function BloqueProductor({ producto: p, productor }: { producto: ProductoDetalle; productor: ProductorPublico | null }) {
  const nombre = productor?.nombre || p.productor_nombre || 'Productor local';
  const ubicacion = productor?.comunidad || productor?.ubicacion || null;
  const tieneMapa = hayCoordenadas(productor?.latitud, productor?.longitud);
  return (
    <div className="detalle-productor-card">
      <div className="detalle-productor-header">
        <span className="detalle-productor-icono"><i className="ti ti-plant-2" aria-hidden="true" /></span>
        <div className="detalle-productor-info">
          <span className="detalle-productor-label">Cultivado por</span>
          <h4 className="detalle-productor-nombre">{nombre}</h4>
          {ubicacion && <span className="detalle-productor-ubicacion"><i className="ti ti-map-pin" aria-hidden="true" /> {ubicacion}</span>}
        </div>
      </div>
      {tieneMapa && (
        <>
          <Suspense fallback={<div className="mapa-mini" />}>
            <MapaSoloLectura latitud={productor?.latitud as string} longitud={productor?.longitud as string} popup={productor?.nombre || 'Productor'} etiqueta={`Zona de origen aproximada de ${nombre} en el mapa`} />
          </Suspense>
          <p className="mapa-ayuda">Zona de origen aproximada.</p>
        </>
      )}
    </div>
  );
}

function CertificacionDetalle({ productoId }: { productoId: string }) {
  const [historial, setHistorial] = useState<Bloque[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [falloQr, setFalloQr] = useState(false);

  useEffect(() => {
    let vigente = true;
    certificacion.historial(productoId)
      .then((h) => { if (vigente) setHistorial(h); })
      .catch((err: Error) => { if (vigente) setError(err.message); });
    return () => { vigente = false; };
  }, [productoId]);

  if (error) {
    return (
      <div className="verificar-banner banner-error">
        <span className="banner-icon"><i className="ti ti-alert-triangle" /></span>
        <div>
          <strong>No se pudo cargar la certificación</strong>
          <p>{error}</p>
        </div>
      </div>
    );
  }
  if (!historial) return <div className="verificar-cargando"><div className="spinner" /><p>Cargando certificación...</p></div>;
  return (
    <>
      <div className="detalle-cert-cabecera">
        <div className="certificacion-qr-bloque">
          {falloQr ? <p className="muted">No se pudo cargar el código QR.</p> : <QrCertificacion url={certificacion.qrUrl(productoId)} alFallar={() => setFalloQr(true)} />}
        </div>
        <div className="detalle-cert-texto">
          <strong><i className="ti ti-shield-check" aria-hidden="true" /> Origen verificable</strong>
          <p className="muted">Escanea el código para comprobar en cualquier momento de dónde viene este producto. También puedes compartirlo.</p>
        </div>
      </div>
      <h4 className="timeline-titulo">Historial de la cadena</h4>
      <LineaTiempoCertificacion historial={historial} />
    </>
  );
}

// "También te puede gustar": primero lo de su misma categoría, luego lo del mismo productor y después el resto, con los
// mejor calificados por delante.
function Relacionados({ producto: p }: { producto: ProductoDetalle }) {
  const { data: catalogo } = useCatalogo();
  const abrirProducto = useAbrirProducto();
  const otros = (catalogo || []).filter((x) => x.id !== p.id);
  const categoria = normalizarCategoria(p.categoria);
  const mismaCategoria = otros.filter((x) => normalizarCategoria(x.categoria) === categoria);
  const delProductor = otros.filter((x) => x.productor_nombre === p.productor_nombre && !mismaCategoria.includes(x));
  const resto = otros.filter((x) => !mismaCategoria.includes(x) && !delProductor.includes(x));
  const lista = [...ordenarPorDestacado(mismaCategoria), ...ordenarPorDestacado(delProductor), ...ordenarPorDestacado(resto)].slice(0, 4);
  if (!lista.length) return null;
  return (
    <section className="detalle-relacionados" id="detalle-relacionados">
      <h3 className="detalle-seccion-titulo"><i className="ti ti-basket" aria-hidden="true" /> También te puede gustar</h3>
      <div className="detalle-relacionados-grid">
        {lista.map((x) => <ProductoMega key={x.id} producto={x} onClick={() => abrirProducto(x.id)} />)}
      </div>
    </section>
  );
}

function BotonFavorito({ productoId }: { productoId: string }) {
  const [favorito, setFavorito] = useState(() => esFavorito(productoId));
  const boton = useRef<HTMLButtonElement>(null);
  return (
    <button
      ref={boton}
      type="button"
      className={`btn-favorito btn-favorito-detalle ${favorito ? 'activo' : ''}`}
      aria-label="Favorito"
      aria-pressed={favorito}
      onClick={() => {
        setFavorito(alternarFavorito(productoId));
        boton.current?.classList.remove('favorito-pop');
        void boton.current?.offsetWidth; // reinicia la animación aunque se haga clic varias veces seguidas
        boton.current?.classList.add('favorito-pop');
      }}
    >
      <i className="ti ti-heart" /> {favorito ? 'Guardado' : 'Guardar'}
    </button>
  );
}

// Pestañas: scroll suave a la sección, descontando la barra fija, y la pestaña activa sigue a la sección que se está viendo.
// Productor y Certificación comparten fila (llegan al borde a la vez): si la activa ya es una de las dos, se respeta.
function usePestanasDetalle(raiz: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const barra = raiz.current?.querySelector<HTMLElement>('.detalle-barra');
    const tabsEl = raiz.current?.querySelector<HTMLElement>('.detalle-tabs');
    if (!barra || !tabsEl) return undefined;
    const tabs = [...tabsEl.querySelectorAll<HTMLElement>('.detalle-tab')];
    const secciones = tabs.map((t) => document.getElementById(t.dataset.target as string));

    // El alto de la barra (una o dos filas según el ancho) lo descuentan las secciones para no quedar tapadas.
    const aplicarAltoBarra = () => {
      const alto = barra.offsetHeight;
      secciones.forEach((s) => { if (s) s.style.scrollMarginTop = `${alto + 16}px`; });
    };
    aplicarAltoBarra();

    let tabActiva = tabs[0] as HTMLElement;
    let ignorarHasta = 0; // mientras dura el scroll suave de un clic, el seguimiento no pisa la pestaña elegida
    const marcar = (tab: HTMLElement) => {
      tabActiva = tab;
      tabs.forEach((t) => {
        t.classList.toggle('active', t === tab);
        if (t === tab) t.setAttribute('aria-current', 'true'); else t.removeAttribute('aria-current');
      });
      // Deja visible la pestaña activa en la fila desplazable (móvil) sin mover la página.
      if (tabsEl.scrollWidth > tabsEl.clientWidth) {
        const izquierda = tab.getBoundingClientRect().left - tabsEl.getBoundingClientRect().left + tabsEl.scrollLeft;
        tabsEl.scrollLeft = izquierda - (tabsEl.clientWidth - tab.offsetWidth) / 2;
      }
    };

    const alClics = tabs.map((tab) => {
      const alClic = () => {
        marcar(tab);
        ignorarHasta = Date.now() + 900;
        document.getElementById(tab.dataset.target as string)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      };
      tab.addEventListener('click', alClic);
      return () => tab.removeEventListener('click', alClic);
    });

    let pendiente = false;
    const seguirScroll = () => {
      pendiente = false;
      if (Date.now() < ignorarHasta) return;
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        marcar(tabs[tabs.length - 1] as HTMLElement);
        return;
      }
      const limite = barra.offsetHeight + window.innerHeight * 0.3; // una sección cuenta como "actual" al pasar el 30% de la pantalla
      const tops = secciones.map((s) => (s ? s.getBoundingClientRect().top : Infinity));
      const alcanzadas = tabs.filter((_, i) => (tops[i] as number) <= limite);
      if (!alcanzadas.length) { marcar(tabs[0] as HTMLElement); return; }
      const topUltima = tops[tabs.indexOf(alcanzadas[alcanzadas.length - 1] as HTMLElement)] as number;
      const fila = alcanzadas.filter((t) => Math.abs((tops[tabs.indexOf(t)] as number) - topUltima) < 8);
      if (!fila.includes(tabActiva)) marcar(fila[0] as HTMLElement);
    };
    const alScroll = () => {
      if (pendiente) return;
      pendiente = true;
      requestAnimationFrame(seguirScroll);
    };
    window.addEventListener('scroll', alScroll, { passive: true });
    window.addEventListener('resize', aplicarAltoBarra);
    return () => {
      alClics.forEach((quitar) => quitar());
      window.removeEventListener('scroll', alScroll);
      window.removeEventListener('resize', aplicarAltoBarra);
    };
  }, [raiz]);
}

// La barra fija solo muestra nombre, precio y botón de compra cuando la tarjeta de compra ya salió de pantalla.
function useBarraDetalle(raiz: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const barra = raiz.current?.querySelector<HTMLElement>('.detalle-barra');
    const tarjeta = raiz.current?.querySelector('#detalle-compra');
    if (!barra || !tarjeta) return undefined;
    const observador = new IntersectionObserver(([entrada]) => {
      barra.classList.toggle('con-compra', !entrada?.isIntersecting);
    }, { rootMargin: `-${barra.offsetHeight}px 0px 0px 0px` });
    observador.observe(tarjeta);
    return () => observador.disconnect();
  }, [raiz]);
}

// Revelado (fade-in/slide-up) de las secciones: se dispara apenas asoman y deja de observarlas. Con movimiento reducido,
// legacy.css las muestra sin animación.
function useRevelarSecciones(raiz: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const secciones = raiz.current?.querySelectorAll('.detalle-scroll-seccion');
    if (!secciones?.length) return undefined;
    const observador = new IntersectionObserver((entradas) => {
      entradas.forEach((entrada) => {
        if (entrada.isIntersecting) {
          entrada.target.classList.add('visible');
          observador.unobserve(entrada.target);
        }
      });
    }, { threshold: 0.12 });
    secciones.forEach((s) => observador.observe(s));
    return () => observador.disconnect();
  }, [raiz]);
}

// Tarjetas grandes para tocar, una por unidad: la suelta ("por menor") y cada saco o arroba como "precio por mayor".
function SelectorUnidad({ producto: p, seleccion, alCambiar }: { producto: ProductoDetalle; seleccion: string | null; alCambiar: (u: string | null) => void }) {
  const unidadBase = unidadCorta(p.unidad_medida);
  const opcion = (valor: string, nombre: string, porMayor: boolean, precio: React.ReactNode, detalle: string, ahorro: number, sinStock: boolean) => (
    <label className={`detalle-unidad ${sinStock ? 'sin-stock' : ''}`} key={valor}>
      <input type="radio" name="detalle-unidad" value={valor} checked={(seleccion || '') === valor} disabled={sinStock} onChange={() => alCambiar(valor || null)} />
      <span className="detalle-unidad-caja">
        <span className="detalle-unidad-fila">
          <strong className="detalle-unidad-nombre">{nombre}</strong>
          {porMayor ? <span className="detalle-unidad-insignia">Precio por mayor</span> : <span className="detalle-unidad-insignia por-menor">Por menor</span>}
        </span>
        <span className="detalle-unidad-precio">{precio}</span>
        <small className="detalle-unidad-detalle">{detalle}{ahorro > 0 && <> · <strong className="detalle-unidad-ahorro">Ahorras {ahorro}%</strong></>}</small>
      </span>
    </label>
  );
  return (
    <fieldset className="detalle-unidades" id="detalle-unidad-selector">
      <legend>¿Cómo lo quieres?</legend>
      {opcion('', `Por ${infoUnidad(p.unidad_medida).singular}`, false, <>{formatearMoneda(p.precio)} <span className="price-unit">/ {unidadBase}</span></>, 'Lo que necesites, para tu casa', 0, false)}
      {(p.unidades_alternativas || []).map((alt) => {
        const { porBase, ahorro } = precioPorMayor(p, alt);
        const sinStock = stockEnUnidad(p, alt.unidad) <= 0;
        const porMayor = esUnidadPorMayor(alt);
        return opcion(
          alt.unidad, nombreUnidadPorMayor(p, alt), porMayor,
          <>{formatearMoneda(alt.precio)} <span className="price-unit">/ {unidadCorta(alt.unidad)}</span></>,
          sinStock ? 'Sin stock suficiente para uno completo' : `Sale a ${formatearMoneda(porBase)} el ${unidadBase}`,
          sinStock || !porMayor ? 0 : ahorro, sinStock,
        );
      })}
    </fieldset>
  );
}

// El carrito se migra en su propia fase: el botón está para que la ficha quede igual, pero todavía no agrega nada.
function avisarCarritoPendiente() {
  toast('Agregar al pedido llega con la migración del carrito. Por ahora compra en la versión actual.');
}

function ContenidoDetalle({ datos, alPublicarResena }: { datos: DatosDetalle; alPublicarResena: () => Promise<void> }) {
  const { producto: p, resenas, productor, recibidos, reservas } = datos;
  const { sesion } = useAuth();
  const navigate = useNavigate();
  const { state } = useLocation();
  const raiz = useRef<HTMLDivElement>(null);
  const reserva = useReserva(p, reservas);

  const stock = p.stock ?? 0;
  const sinStock = stock <= 0;
  const tieneAlternativas = (p.unidades_alternativas || []).length > 0;
  const enCamino = p.estado === 'en_transito';
  const fotos = fotosProducto(p);
  const insignia = insigniaProducto(p);

  // Un negocio ve primero el precio por mayor (el saco más conveniente con stock); una persona, la unidad suelta.
  const [unidad, setUnidad] = useState<string | null>(() => {
    if (!tieneAlternativas || sinStock || sesion.tipoComprador !== 'negocio') return null;
    return mejorUnidadPorMayor(p)?.alt.unidad || null;
  });
  const [cantidad, setCantidad] = useState(1);

  const maximo = stockEnUnidad(p, unidad);
  const sinStockEnUnidad = maximo <= 0;
  const unidadParaMostrar = unidad || p.unidad_medida;
  const precioHtml = <>{formatearMoneda(precioEnUnidad(p, unidad))} <span className="price-unit">/ {unidadCorta(unidadParaMostrar)}</span></>;
  const agotado = unidad ? sinStockEnUnidad : sinStock;
  const nombreProductor = productor?.nombre || p.productor_nombre || 'Productor local';
  const ubicacion = productor?.comunidad || productor?.ubicacion || '';

  function cambiarUnidad(nueva: string | null) {
    setUnidad(nueva);
    const tope = stockEnUnidad(p, nueva);
    if (cantidad > tope) setCantidad(Math.max(1, tope)); // recorta la cantidad si ya no entra en el nuevo tope
  }

  usePestanasDetalle(raiz);
  useBarraDetalle(raiz);
  useRevelarSecciones(raiz);

  let textoStock: string;
  let claseStock: string;
  if (enCamino) {
    textoStock = `${stock} ${unidadPlural(p.unidad_medida, stock)} en camino al almacén`;
    claseStock = 'transito';
  } else if (agotado) {
    textoStock = unidad ? 'Sin stock disponible en esta unidad' : 'Sin stock disponible';
    claseStock = 'agotado';
  } else {
    textoStock = `${maximo} ${unidadPlural(unidadParaMostrar, maximo)} disponibles`;
    claseStock = maximo <= 5 ? 'low' : '';
  }

  return (
    <div id="detalle-producto-content" ref={raiz}>
      <header className="detalle-barra" id="detalle-barra">
        <button type="button" className="detalle-volver" aria-label="Volver" onClick={() => void navigate((state as EstadoDetalle | null)?.volverA || '/')}><i className="ti ti-arrow-left" aria-hidden="true" /><span className="detalle-volver-texto">Volver</span></button>
        <nav className="detalle-tabs" id="detalle-tabs" aria-label="Secciones del producto">
          <button type="button" className="detalle-tab active" data-target="detalle-seccion-descripcion" aria-current="true">Producto</button>
          <button type="button" className="detalle-tab" data-target="detalle-seccion-productor">Productor</button>
          <button type="button" className="detalle-tab" data-target="detalle-seccion-certificacion">Certificación</button>
          <button type="button" className="detalle-tab" data-target="detalle-seccion-resenas">Reseñas</button>
        </nav>
        <div className="detalle-barra-compra" id="detalle-barra-compra">
          <span className="detalle-sticky-nombre">{p.nombre}</span>
          <span className="detalle-sticky-precio">{precioHtml}</span>
          {enCamino
            ? <span id="detalle-reserva-sticky"><BotonReservaSticky reserva={reserva} /></span>
            : <button type="button" className="btn btn-primary" disabled={agotado} onClick={avisarCarritoPendiente}>{agotado ? 'Agotado' : '+ Agregar al carrito'}</button>}
        </div>
      </header>

      <section className="detalle-principal" id="detalle-seccion-descripcion">
        <GaleriaDetalle producto={p} fotos={fotos} insignia={insignia} />
        <aside className="detalle-compra" id="detalle-compra" aria-label={`Comprar ${p.nombre}`}>
          <div className="detalle-encabezado">
            <span className="pill pill-categoria">{iconoCategoria(p.categoria)} {nombreCategoria(p.categoria)}</span>
            <BotonFavorito productoId={p.id} />
          </div>

          <div className="detalle-cabecera">
            <h2>{p.nombre}</h2>
            {p.calificacion_promedio && p.total_resenas ? (
              <p className="rating-row">
                <span className="rating-stars"><Estrellas promedio={p.calificacion_promedio} /></span>
                <span className="rating-value">{Number(p.calificacion_promedio).toFixed(1)}</span>
                <span className="rating-count">(basado en {p.total_resenas} reseña{p.total_resenas === 1 ? '' : 's'})</span>
              </p>
            ) : <p className="rating-row"><span className="rating-count">Sin reseñas todavía</span></p>}
          </div>
          {p.descripcion && <p className="detalle-descripcion">{p.descripcion}</p>}

          <div className="detalle-precio-row">
            <span className="product-price">{precioHtml}</span>
            <span className={`product-stock detalle-stock ${claseStock}`}>{textoStock}</span>
          </div>

          {enCamino && <div className="detalle-accion-principal detalle-reserva" id="detalle-reserva"><PanelReserva producto={p} reserva={reserva} /></div>}

          {!enCamino && !sinStock && tieneAlternativas && <SelectorUnidad producto={p} seleccion={unidad} alCambiar={cambiarUnidad} />}

          {!enCamino && (
            <div className="detalle-accion-principal">
              {!sinStock && (
                <div className={`detalle-cantidad-fila ${agotado ? 'hidden' : ''}`}>
                  <div className="carrito-qty detalle-cantidad-selector" role="group" aria-label="Cantidad">
                    <button type="button" aria-label="Disminuir cantidad" onClick={() => { if (cantidad > 1) setCantidad(cantidad - 1); }}>−</button>
                    <span role="status" aria-live="polite">{cantidad}</span>
                    <button
                      type="button"
                      aria-label="Aumentar cantidad"
                      onClick={() => {
                        if (cantidad < maximo) setCantidad(cantidad + 1);
                        else toast('Alcanzaste el stock disponible.', 'error');
                      }}
                    >+</button>
                  </div>
                  <div className="detalle-total" aria-live="polite">
                    <span>Subtotal</span>
                    <strong>{formatearMoneda(precioEnUnidad(p, unidad) * cantidad)}</strong>
                  </div>
                </div>
              )}
              <button className="btn btn-primary btn-lg btn-block" disabled={agotado} onClick={avisarCarritoPendiente}>
                {agotado ? 'Agotado' : '+ Agregar al pedido'}
              </button>
            </div>
          )}

          <ul className="detalle-confianza">
            <li><i className="ti ti-shield-check" aria-hidden="true" /><span>Origen verificado</span></li>
            <li><i className="ti ti-plant-2" aria-hidden="true" /><span>Directo del productor</span></li>
            <li><i className="ti ti-truck-delivery" aria-hidden="true" /><span>Entrega con repartidor</span></li>
          </ul>

          <button
            type="button"
            className="detalle-productor-mini"
            onClick={() => raiz.current?.querySelector<HTMLElement>('.detalle-tab[data-target="detalle-seccion-productor"]')?.click()}
          >
            <span className="detalle-productor-mini-icono"><i className="ti ti-plant-2" aria-hidden="true" /></span>
            <span className="detalle-productor-mini-texto">
              <small>Cultivado por</small>
              <strong>{nombreProductor}</strong>
              {ubicacion && <small><i className="ti ti-map-pin" aria-hidden="true" /> {ubicacion}</small>}
            </span>
            <i className="ti ti-arrow-right" aria-hidden="true" />
          </button>
        </aside>
      </section>

      <VideosDetalle producto={p} fotos={fotos} />

      <div className="detalle-secciones">
        <section className="detalle-bloque detalle-bloque--productor detalle-scroll-seccion" id="detalle-seccion-productor">
          <h3 className="detalle-seccion-titulo"><i className="ti ti-plant-2" aria-hidden="true" /> De la chacra a tu mesa</h3>
          <BloqueProductor producto={p} productor={productor} />
        </section>

        <section className="detalle-bloque detalle-bloque--certificacion detalle-scroll-seccion" id="detalle-seccion-certificacion">
          <h3 className="detalle-seccion-titulo"><i className="ti ti-link" aria-hidden="true" /> Certificación de trazabilidad</h3>
          <div id="detalle-certificacion-contenido"><CertificacionDetalle productoId={p.id} /></div>
        </section>

        <section className="detalle-bloque detalle-bloque--resenas detalle-scroll-seccion" id="detalle-seccion-resenas">
          <h3 className="detalle-seccion-titulo"><i className="ti ti-star" aria-hidden="true" /> Reseñas{resenas.length ? ` (${resenas.length})` : ''}</h3>
          <ResenasDetalle resenas={resenas} producto={p} recibidos={recibidos} alPublicar={alPublicarResena} />
        </section>
      </div>

      <Relacionados producto={p} />
    </div>
  );
}

export default function DetallePage() {
  const { id = '' } = useParams();
  const { sesion } = useAuth();
  const navigate = useNavigate();
  const { state } = useLocation();
  const queryClient = useQueryClient();
  const esComprador = Boolean(sesion.token) && sesion.rol === 'comprador';

  // El usuarioId en la clave: cambiar de cuenta no debe reutilizar lo que recibió o reservó la anterior.
  const clave = ['detalle-producto', id, sesion.usuarioId];
  const { data, error } = useQuery({
    queryKey: clave,
    queryFn: async (): Promise<DatosDetalle> => {
      const producto = await productos.obtener(id);
      const [resenas, productor, recibidos, reservas] = await Promise.all([
        productos.listarResenas(id).catch(() => [] as Resena[]), // sin reseñas disponibles por ahora
        productores.obtener(producto.productor_id).catch(() => null), // seguimos sin mapa
        esComprador ? pedidos.productosRecibidos().catch(() => null) : null,
        esComprador && producto.estado === 'en_transito' ? productos.misReservas().catch(() => null) : null,
      ]);
      return { producto, resenas, productor, recibidos: recibidos ? new Set(recibidos.productos.map(String)) : null, reservas };
    },
  });

  useEffect(() => {
    if (!error) return;
    console.error('cargar el producto', error);
    toast(error.message || 'Ocurrió un error en cargar el producto', 'error');
    void navigate((state as EstadoDetalle | null)?.volverA || '/', { replace: true });
    // Solo reacciona al error: el destino es el del momento en que falló.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error]);

  useEffect(() => {
    if (data) document.title = `${data.producto.nombre} — ChakraShop`;
  }, [data]);

  return (
    <section id="view-detalle-producto" className="view active">
      {data
        ? <ContenidoDetalle key={id} datos={data} alPublicarResena={() => queryClient.invalidateQueries({ queryKey: clave })} />
        : <div id="detalle-producto-content"><SkeletonDetalle /></div>}
    </section>
  );
}
