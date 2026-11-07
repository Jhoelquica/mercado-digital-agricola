import { useEffect, useRef, useState } from 'react';
import type { ProductoDetalle } from '../../api/productos';
import { slugProducto } from '../../lib/productos';

// Productos con video en su galería de detalle. Un producto nuevo se activa subiendo
// public/static/videos/<slug>-video-1.mp4 (y -2/-3 si aplica) y agregando su slug aquí.
const PRODUCTOS_CON_VIDEO = ['chirimoya'];

// Etiqueta/título/párrafo/fondo/tema por posición (hero=0, primera foto extra=1, segunda=2). "tema" decide el color del
// degradado y del texto (blanco sobre oscuro, o el inverso sobre claro).
function textosVideoGaleria(p: ProductoDetalle) {
  const nombre = (p.nombre || '').toLowerCase();
  return [
    {
      etiqueta: 'Nutrición', titulo: 'Nutritiva y rica en vitaminas',
      parrafo: `La ${nombre} es fuente natural de vitamina C, fibra y antioxidantes — un snack saludable directo de la chacra en Ayacucho.`,
      fondo: '#0f140f', tema: 'oscuro',
    },
    {
      etiqueta: 'Trazabilidad', titulo: 'Cada fruto, verificado',
      parrafo: `Cada ${nombre} que vendemos queda registrada en una cadena de bloques verificable — sabes exactamente de dónde viene.`,
      fondo: '#1a1410', tema: 'oscuro',
    },
    {
      etiqueta: 'Origen', titulo: 'Cultivada por manos que conoces',
      parrafo: `Detrás de cada ${nombre} hay un productor real de tu comunidad, no una gran distribuidora.`,
      fondo: '#f5f0e8', tema: 'claro',
    },
  ];
}

interface PropsSeccion {
  poster: string;
  videoUrl: string;
  indice: number;
  texto: ReturnType<typeof textosVideoGaleria>[number];
}

function SeccionVideo({ poster, videoUrl, indice, texto }: PropsSeccion) {
  const seccion = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [mostrarRepetir, setMostrarRepetir] = useState(false);
  const [fallo, setFallo] = useState(false);

  // Umbral alto: recién dispara el play cuando la sección ya domina la pantalla, y nunca se desconecta: sigue mirando para
  // pausar y reiniciar en cuanto sale de vista. Con secciones de ~88vh y un umbral de 0.65, dos secciones vecinas no pueden
  // estar ambas por encima del umbral: nunca hay dos videos activos a la vez.
  useEffect(() => {
    const v = video.current;
    const s = seccion.current;
    if (!v || !s) return undefined;
    const observador = new IntersectionObserver((entradas) => {
      entradas.forEach((entrada) => {
        if (entrada.isIntersecting) {
          // El src se carga la primera vez: estaba diferido para no descargar los 3 videos de golpe al abrir la página.
          if (!v.src) v.src = v.dataset.src as string;
          setMostrarRepetir(false);
          v.play().catch(() => setMostrarRepetir(true)); // ahorro de datos u otro bloqueo: queda el botón de repetir
        } else if (v.src) {
          v.pause();
          v.currentTime = 0;
          setMostrarRepetir(false);
        }
      });
    }, { threshold: 0.65 });
    observador.observe(s);
    return () => observador.disconnect();
  }, [fallo]);

  function repetir() {
    if (!mostrarRepetir) return; // un doble clic no debe disparar un segundo play() superpuesto al primero
    setMostrarRepetir(false);
    // Sin "currentTime = 0" antes del play(): un video terminado ya reinicia solo, y forzar el seek rompe la promesa de
    // play() en algunos navegadores (queda en pausa sin avisar).
    video.current?.play().catch(() => setMostrarRepetir(true));
  }

  return (
    <div
      ref={seccion}
      className={`${indice === 0 ? 'detalle-hero' : 'detalle-galeria-item'} detalle-scroll-seccion detalle-video-seccion detalle-video-seccion--${texto.tema}`}
      style={{ background: texto.fondo }}
    >
      {fallo ? <img src={poster} alt="" loading="lazy" /> : (
        <div className="detalle-video-wrap">
          <video
            ref={video}
            className="detalle-video"
            muted
            playsInline
            preload="metadata"
            poster={poster}
            data-src={videoUrl}
            onError={() => setFallo(true)}
            onEnded={() => setMostrarRepetir(true)}
          />
          <button type="button" className={`detalle-video-replay ${mostrarRepetir ? '' : 'hidden'}`} aria-label="Reproducir de nuevo" onClick={repetir}><i className="ti ti-player-play" /></button>
          <div className="detalle-video-degradado" />
          <div className="detalle-video-texto">
            <span className="detalle-video-etiqueta">{texto.etiqueta}</span>
            <h3>{texto.titulo}</h3>
            <p>{texto.parrafo}</p>
          </div>
        </div>
      )}
    </div>
  );
}

// Secciones con video (a sangre completa, debajo de la ficha de compra). Un video solo reemplaza una posición que YA tiene
// una foto real (esa foto pasa a ser el poster): si el producto tiene menos fotos que videos, esa posición no existe.
export function VideosDetalle({ producto, fotos }: { producto: ProductoDetalle; fotos: string[] }) {
  const slug = slugProducto(producto.nombre);
  if (!PRODUCTOS_CON_VIDEO.includes(slug)) return null;
  const textos = textosVideoGaleria(producto);
  return (
    <>
      {fotos.map((url, i) => {
        const texto = textos[i];
        if (!url || !texto) return null;
        return <SeccionVideo key={url} poster={url} indice={i} texto={texto} videoUrl={`/static/videos/${slug}-video-${i + 1}.mp4`} />;
      })}
    </>
  );
}
