// ---- Gestor de fotos de producto (subida + eliminación + orden, hasta 5 por producto) ----
// Lo usan el borrador y el panel "Fotos" de un producto publicado; las dos vistas comparten la misma lista (caché).
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type DragEvent } from 'react';
import { productos, type ImagenProducto } from '../../../api/productos';
import { manejarError, toast } from '../../../components/Toast';
import { FOTO_TIPOS_VALIDOS } from '../../../lib/fotos';

const MAXIMO = 5;
const claveImagenes = (productoId: string) => ['productor', 'imagenes', productoId];
const ordenadas = (lista: ImagenProducto[]) => [...lista].sort((a, b) => (a.orden ?? 0) - (b.orden ?? 0));

// GET /productos/mios trae solo la imagen principal; la lista con id y orden sale del detalle de cada producto.
export function useImagenesProducto(productoId: string) {
  return useQuery({
    queryKey: claveImagenes(productoId),
    queryFn: () => productos.obtener(productoId).then((d) => ordenadas(d.imagenes || [])).catch(() => [] as ImagenProducto[]),
    staleTime: Infinity,
  });
}

export function GestorFotos({ productoId, alCambiar }: { productoId: string; alCambiar?: (cantidad: number) => void }) {
  const queryClient = useQueryClient();
  const { data: imagenes = [] } = useImagenesProducto(productoId);
  const entrada = useRef<HTMLInputElement>(null);
  const [encima, setEncima] = useState(false);
  const lista = ordenadas(imagenes);
  const lleno = lista.length >= MAXIMO;
  const leer = () => queryClient.getQueryData<ImagenProducto[]>(claveImagenes(productoId)) ?? [];
  const fijar = (nueva: ImagenProducto[]) => {
    queryClient.setQueryData(claveImagenes(productoId), nueva);
    alCambiar?.(nueva.length);
  };

  async function subir(archivos: FileList | File[]) {
    const actuales = [...leer()];
    const disponibles = MAXIMO - actuales.length;
    if (disponibles <= 0) {
      toast('Ya tienes el máximo de 5 fotos para este producto.', 'error');
      return;
    }
    for (const archivo of Array.from(archivos).slice(0, disponibles)) {
      if (!FOTO_TIPOS_VALIDOS.includes(archivo.type)) {
        toast(`"${archivo.name}" no es una imagen JPG, PNG o WEBP.`, 'error');
        continue;
      }
      try {
        const nueva = await productos.subirImagen(productoId, archivo, actuales.length);
        actuales.push(nueva);
        fijar([...actuales]);
      } catch (err) {
        manejarError(err, 'subir la foto');
      }
    }
  }

  async function eliminar(imagenId: string) {
    try {
      await productos.eliminarImagen(imagenId);
      fijar(leer().filter((img) => img.id !== imagenId));
      toast('Foto eliminada.');
    } catch (err) {
      manejarError(err, 'eliminar la foto');
    }
  }

  // Intercambia el orden con la vecina; si el servidor falla, vuelve a como estaba.
  async function mover(imagenId: string, direccion: 'izq' | 'der') {
    const antes = leer().map((img) => ({ ...img }));
    const orden = ordenadas(antes);
    const indice = orden.findIndex((img) => img.id === imagenId);
    const destino = direccion === 'izq' ? indice - 1 : indice + 1;
    if (indice === -1 || destino < 0 || destino >= orden.length) return;
    const actual = { ...orden[indice]! };
    const vecino = { ...orden[destino]! };
    [actual.orden, vecino.orden] = [vecino.orden, actual.orden];
    fijar(antes.map((img) => (img.id === actual.id ? actual : img.id === vecino.id ? vecino : img)));
    try {
      await Promise.all([productos.actualizarOrdenImagen(actual.id, actual.orden ?? 0), productos.actualizarOrdenImagen(vecino.id, vecino.orden ?? 0)]);
    } catch (err) {
      fijar(antes);
      manejarError(err, 'reordenar las fotos');
    }
  }

  function soltar(e: DragEvent<HTMLDivElement>) {
    if (lleno) return;
    e.preventDefault();
    setEncima(false);
    if (e.dataTransfer.files.length) void subir(e.dataTransfer.files);
  }

  return (
    <div className="gestor-fotos" data-producto-id={productoId}>
      <div className="fotos-upload-header">
        <span className="fotos-contador">{lista.length}/5 fotos</span>
      </div>
      <div className={`upload-zona ${lleno ? 'deshabilitada' : ''}${encima ? ' dragover' : ''}`}
        onClick={(e) => { if (!lleno && e.target !== entrada.current) entrada.current?.click(); }}
        onDragOver={(e) => { if (!lleno) { e.preventDefault(); setEncima(true); } }}
        onDragLeave={() => setEncima(false)}
        onDrop={soltar}>
        <input ref={entrada} type="file" className="upload-input" accept="image/jpeg,image/png,image/webp" multiple disabled={lleno} hidden
          onChange={(e) => { if (e.target.files?.length) void subir(e.target.files); e.target.value = ''; }} />
        <span className="upload-zona-icono"><i className="ti ti-upload" /></span>
        <span>{lleno ? 'Alcanzaste el máximo de 5 fotos' : <>Arrastra tus fotos aquí o <strong>haz clic para elegir</strong></>}</span>
      </div>
      {lista.length > 0 && (
        <div className="upload-miniaturas">
          {lista.map((img, i) => (
            <div className="upload-miniatura" key={img.id}>
              {i === 0 && <span className="miniatura-badge-principal">Principal</span>}
              <img src={img.url} alt="" loading="lazy" />
              <button type="button" className="upload-miniatura-quitar" aria-label="Quitar foto" onClick={() => { void eliminar(img.id); }}><i className="ti ti-x" /></button>
              <div className="upload-miniatura-mover">
                <button type="button" className="miniatura-mover-btn" aria-label="Mover foto a la izquierda" disabled={i === 0} onClick={() => { void mover(img.id, 'izq'); }}>‹</button>
                <button type="button" className="miniatura-mover-btn" aria-label="Mover foto a la derecha" disabled={i === lista.length - 1} onClick={() => { void mover(img.id, 'der'); }}>›</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
