import { useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router';
import type { ProductoCatalogo } from '../../api/productos';
import { useAuth } from '../../auth/AuthContext';
import { toast } from '../../components/Toast';
import { buscarProductoPorNombre, ordenarPorDestacado, sinProductosDePrueba } from '../../lib/productos';
import { abrirMenu, abrirPanelBusqueda } from './menus';
import { ContenidoTarjetaSimple, SkeletonProductos } from './TarjetasProducto';
import { useAbrirProducto, useCatalogo } from './useCatalogo';

// Control del video de fondo del hero: un video que arranca solo tiene que poder detenerse (WCAG 2.2.2). Además se pausa
// cuando el hero sale de pantalla (no decodifica video que nadie ve) y no arranca si el usuario pidió menos movimiento:
// en ese caso queda el poster.
function useControlVideoHero() {
  const video = useRef<HTMLVideoElement>(null);
  const boton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const v = video.current;
    const b = boton.current;
    if (!v || !b) return undefined;

    let pausadoPorUsuario = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const pintar = () => {
      const detenido = v.paused;
      b.setAttribute('aria-pressed', String(detenido));
      b.setAttribute('aria-label', detenido ? 'Reproducir video de fondo' : 'Pausar video de fondo');
      b.innerHTML = `<i class="ti ti-player-${detenido ? 'play' : 'pause'}"></i>`;
    };
    if (pausadoPorUsuario) v.pause();

    const alClic = () => {
      pausadoPorUsuario = !v.paused; // si se estaba reproduciendo, lo detiene el usuario
      if (v.paused) v.play().catch(() => {}); else v.pause();
    };
    b.addEventListener('click', alClic);
    v.addEventListener('play', pintar);
    v.addEventListener('pause', pintar);

    const observador = new IntersectionObserver(([entrada]) => {
      if (!entrada?.isIntersecting) v.pause();
      else if (!pausadoPorUsuario) v.play().catch(() => {});
    }, { threshold: 0.15 });
    observador.observe(v);
    pintar();

    return () => {
      observador.disconnect();
      b.removeEventListener('click', alClic);
      v.removeEventListener('play', pintar);
      v.removeEventListener('pause', pintar);
    };
  }, []);

  return { video, boton };
}

// Texto del banner escalonado (lo da el CSS) y repetible: .visible se agrega Y se quita según entra o sale de pantalla,
// a propósito, para que el efecto se repita siempre que se pasa por el banner.
function useBannerCinematico(raiz: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const contenidos = raiz.current?.querySelectorAll('.landing-banner-cine .landing-banner-content');
    if (!contenidos?.length) return undefined;
    const observador = new IntersectionObserver((entradas) => {
      entradas.forEach((entrada) => entrada.target.classList.toggle('visible', entrada.isIntersecting));
    }, { threshold: 0.3 });
    contenidos.forEach((c) => observador.observe(c));
    return () => observador.disconnect();
  }, [raiz]);
}

interface PropsBanner {
  titulo: string;
  texto: string;
  categoria: string;
  nombresFondo: string;
  tarjetas: string[];
  candidatos: ProductoCatalogo[];
}

function BannerCategoria({ titulo, texto, categoria, nombresFondo, tarjetas, candidatos }: PropsBanner) {
  const abrirProducto = useAbrirProducto();
  const fondo = buscarProductoPorNombre(candidatos, nombresFondo);
  // Sin coincidencia (o sin foto): se queda el fondo verde sólido de legacy.css.
  const estiloFondo = fondo?.imagen_principal ? { backgroundImage: `url("${fondo.imagen_principal}")` } : undefined;

  // Estos botones abren el mismo menú de categorías del navbar, con productos reales.
  function verCategoria() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    setTimeout(() => abrirMenu({ tipo: 'categoria', categoria }), 350);
  }

  return (
    <div className="landing-banner-grupo landing-banner-grupo--cine">
      <section className="landing-banner landing-banner-cine">
        <div className="landing-banner-bg" style={estiloFondo} />
        <div className="landing-banner-content">
          <h2>{titulo}</h2>
          <p>{texto}</p>
          <button type="button" className="btn btn-primary" onClick={verCategoria}>Ver categoría →</button>
        </div>
      </section>
      <div className="landing-banner-tarjetas landing-banner-tarjetas--cine">
        {tarjetas.map((nombres) => {
          const producto = buscarProductoPorNombre(candidatos, nombres);
          // Sin coincidencia en el catálogo actual: se omite en silencio.
          return producto && (
            <div className="landing-banner-tarjeta" key={nombres} onClick={() => abrirProducto(producto.id)}>
              <ContenidoTarjetaSimple producto={producto} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Carrusel({ productos, cargando }: { productos: ProductoCatalogo[]; cargando: boolean }) {
  const abrirProducto = useAbrirProducto();
  const cont = useRef<HTMLDivElement>(null);
  const barra = useRef<HTMLDivElement>(null);

  function actualizarProgreso() {
    if (!cont.current || !barra.current) return;
    const maximo = cont.current.scrollWidth - cont.current.clientWidth;
    const porcentaje = maximo > 0 ? (cont.current.scrollLeft / maximo) * 100 : 0;
    barra.current.style.width = `${Math.min(100, Math.max(0, porcentaje))}%`;
  }

  function desplazar(direccion: number) {
    const item = cont.current?.querySelector('.landing-carousel-tarjeta');
    const anchoItem = item ? item.getBoundingClientRect().width + 16 : 166; // 16px = gap del flex
    cont.current?.scrollBy({ left: direccion * anchoItem * 2, behavior: 'smooth' }); // avanza ~2 tarjetas por clic
  }

  useEffect(actualizarProgreso, [productos]);

  return (
    <>
      <div className="landing-carousel-wrap">
        <div className="landing-carousel" id="landing-productos-carousel" ref={cont} onScroll={actualizarProgreso}>
          {cargando && <SkeletonProductos cantidad={6} />}
          {!cargando && (productos.length
            ? productos.map((p) => (
              <div className="landing-carousel-tarjeta" key={p.id} onClick={() => abrirProducto(p.id)}>
                <ContenidoTarjetaSimple producto={p} conPrecio conBoton={false} />
              </div>
            ))
            : <p className="empty-state"><i className="ti ti-basket-off" /> Todavía no hay productos publicados.</p>)}
        </div>
      </div>
      <div className="landing-carousel-footer">
        <div className="landing-carousel-progreso">
          <div className="landing-carousel-progreso-barra" ref={barra} />
        </div>
        <div className="landing-carousel-arrows">
          <button type="button" className="landing-carousel-arrow landing-carousel-arrow-prev" aria-label="Ver productos anteriores" onClick={() => desplazar(-1)}>‹</button>
          <button type="button" className="landing-carousel-arrow landing-carousel-arrow-next" aria-label="Ver más productos" onClick={() => desplazar(1)}>›</button>
        </div>
      </div>
    </>
  );
}

const ROLES_LANDING = [
  {
    clase: 'comprador', icono: 'shopping-cart', tag: 'Para tu mesa', titulo: 'Comprador', boton: 'Registrarme como comprador',
    texto: 'Explora el catálogo, arma tu pedido y sigue el envío en vivo hasta tu puerta.',
    lista: ['Origen verificado con blockchain', 'Pago con tarjeta o Yape', 'Seguimiento en vivo de tu envío'],
  },
  {
    clase: 'productor', icono: 'tractor', tag: 'Vende sin intermediarios', titulo: 'Productor', boton: 'Registrarme como productor',
    texto: 'Publica tus productos con fotos reales y certificación de trazabilidad, sin intermediarios.',
    lista: ['Publica con fotos reales de tu chakra', 'Certificado de trazabilidad por cosecha', 'Vende directo, sin intermediarios'],
  },
  {
    clase: 'repartidor', icono: 'truck', tag: 'Reparte cerca de ti', titulo: 'Repartidor', boton: 'Registrarme como repartidor',
    texto: 'Acepta envíos cercanos y comparte tu ubicación en vivo con el comprador.',
    lista: ['Propuestas según tu capacidad de carga', 'Entrega confirmada con código, foto y GPS', 'Liquidación por cada envío entregado'],
  },
];

const PASOS = [
  { icono: 'search', titulo: 'Elige', texto: 'Explora productos frescos de productores locales, con foto real y precio directo.' },
  { icono: 'credit-card', titulo: 'Paga seguro', texto: 'Con tarjeta o Yape. Tu pago se procesa de forma segura con Culqi.' },
  { icono: 'truck-delivery', titulo: 'Sigue tu envío', texto: 'Mira el recorrido en el mapa y recibe tu pedido con un código de entrega.' },
  { icono: 'qrcode', titulo: 'Verifica el origen', texto: 'Escanea el QR y revisa la cadena de trazabilidad de cada producto.' },
];

export default function LandingPage() {
  const { autenticado } = useAuth();
  const { data: productos, isPending, error } = useCatalogo();
  const { video, boton } = useControlVideoHero();
  const raiz = useRef<HTMLElement>(null);
  const location = useLocation();
  useBannerCinematico(raiz);

  useEffect(() => {
    if (error) toast(error.message || 'Ocurrió un error en cargar los productos recomendados', 'error');
  }, [error]);

  // "Cómo funciona la trazabilidad" del menú de categorías baja hasta su tarjeta.
  const anclar = (location.state as { anclar?: string } | null)?.anclar;
  useEffect(() => {
    if (anclar) document.getElementById(anclar)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [anclar, location.key]);

  const lista = productos || [];
  const candidatos = sinProductosDePrueba(lista);
  const recomendados = ordenarPorDestacado(lista).slice(0, 10);

  return (
    <section id="view-landing" className="view active" ref={raiz}>
      <div className="landing-hero">
        <div className="detalle-video-wrap">
          <video
            ref={video}
            className="detalle-video"
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
            poster="/static/hero-valle-andino-1600.jpg"
            src="/static/videos/hero-valle-bg.mp4"
            aria-label="Valle andino con terrazas de cultivo agrícola, video ambiental"
          />
          <div className="detalle-video-degradado" />
          <button ref={boton} type="button" className="detalle-video-replay landing-video-control" aria-label="Pausar video de fondo" aria-pressed="false" />
          <div className="landing-hero-content detalle-video-texto">
            <span className="landing-kicker"><i className="ti ti-map-pin" /> Hecho en Ayacucho, Perú</span>
            <h1>Del productor a tu mesa, sin intermediarios</h1>
            <p>Frutas, verduras y más, directo del productor a tu mesa. Con trazabilidad verificada.</p>
            <div className="landing-hero-actions">
              {!autenticado && <Link className="btn btn-primary btn-lg" to="/registro">Crear cuenta gratis</Link>}
              <button type="button" className="landing-buscador" aria-label="Buscar productos" onClick={() => abrirPanelBusqueda()}>
                <i className="ti ti-search" aria-hidden="true" />
                <span className="landing-buscador-texto">Busca papa, quinua, queso…</span>
                <span className="landing-buscador-accion">Buscar</span>
              </button>
            </div>
            <div className="landing-hero-stats">
              <div><strong>{productos ? productos.length : '—'}</strong><span>productos frescos</span></div>
              <div><strong>{productos ? new Set(productos.map((p) => p.productor_id)).size : '—'}</strong><span>productores locales</span></div>
              <div><strong><i className="ti ti-link" /></strong><span>Trazabilidad blockchain</span></div>
            </div>
          </div>
        </div>
      </div>

      <BannerCategoria candidatos={candidatos} titulo="Frutas de temporada" texto="Plátano de temporada, cosecha reciente de Ayacucho" categoria="fruta" nombresFondo="platano,chirimoya" tarjetas={['mango', 'fresa', 'naranja', 'manzana']} />
      <BannerCategoria candidatos={candidatos} titulo="Sabor de la sierra" texto="Granos y tubérculos andinos, tradición en cada cosecha" categoria="grano" nombresFondo="maiz morado,kiwicha" tarjetas={['maiz choclo', 'trigo', 'papa blanca', 'kiwicha']} />
      <BannerCategoria candidatos={candidatos} titulo="Lácteos frescos" texto="Lácteos frescos, directo de la comunidad de Acco" categoria="lacteo" nombresFondo="queso,leche fresca" tarjetas={['yogurt natural', 'huevo de campo', 'huevo de codorniz', 'queso']} />

      <div className="landing-ancho-completo">
        <div className="section-header">
          <h2><i className="ti ti-sparkles" /> Recomendado para ti</h2>
          <button className="btn btn-outline btn-sm" onClick={() => abrirPanelBusqueda()}>Ver todos los productos →</button>
        </div>
        <Carrusel productos={recomendados} cargando={isPending} />

        <section className="landing-pasos" aria-labelledby="landing-pasos-titulo">
          <span className="landing-eyebrow">Cómo funciona</span>
          <h2 id="landing-pasos-titulo">Del chakra a tu mesa en 4 pasos</h2>
          <ol className="landing-pasos-lista">
            {PASOS.map((paso, i) => (
              <li className="landing-paso" key={paso.titulo}>
                <span className="landing-paso-nodo" aria-hidden="true"><i className={`ti ti-${paso.icono}`} /><b>{i + 1}</b></span>
                <div className="landing-paso-texto">
                  <h3>{paso.titulo}</h3>
                  <p>{paso.texto}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {!autenticado && (
          <div className="landing-roles">
            <span className="landing-eyebrow">Únete</span>
            <h2>Una plataforma, tres roles</h2>
            <div className="landing-roles-grid">
              {ROLES_LANDING.map((r) => (
                <article className={`landing-role-card landing-role-card--${r.clase}`} key={r.clase}>
                  <header className="landing-role-cabecera">
                    <span className="landing-role-icon"><i className={`ti ti-${r.icono}`} /></span>
                    <span className="landing-role-tag">{r.tag}</span>
                  </header>
                  <div className="landing-role-cuerpo">
                    <h3>{r.titulo}</h3>
                    <p>{r.texto}</p>
                    <ul className="landing-role-lista">
                      {r.lista.map((item) => <li key={item}><i className="ti ti-check" aria-hidden="true" /> {item}</li>)}
                    </ul>
                    <Link className="btn btn-primary landing-role-cta" to={`/registro?rol=${r.clase}`}>{r.boton}</Link>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}

        <div className="landing-confianza">
          <div className="landing-confianza-item">
            <span className="landing-confianza-icon"><i className="ti ti-map-pin" /></span>
            <div><strong>Hecho en Ayacucho, Perú</strong><p>Conectamos comunidades agrícolas locales con compradores urbanos, sin cadenas de intermediarios.</p></div>
          </div>
          <div className="landing-confianza-item" id="landing-confianza-blockchain">
            <span className="landing-confianza-icon"><i className="ti ti-shield-check" /></span>
            <div><strong>Trazabilidad garantizada</strong><p>Cada producto certificado tiene su historial verificado con blockchain, desde la chakra hasta tu mesa.</p></div>
          </div>
          <div className="landing-confianza-item">
            <span className="landing-confianza-icon"><i className="ti ti-star" /></span>
            <div><strong>Reseñas reales</strong><p>Calificaciones y comentarios de compradores verificados en cada producto.</p></div>
          </div>
        </div>
      </div>
    </section>
  );
}
