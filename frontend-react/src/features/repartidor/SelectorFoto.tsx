// Zona táctil para tomar o elegir una foto (entrega con código, guía de la agencia): vista previa, nombre y peso, y
// validación al momento (mismo tope y tipos que el servidor, que igual vuelve a comprobarlo).
import { useEffect, useRef, useState, type Ref } from 'react';
import { formatearTamanoArchivo, problemaDeFoto } from '../../lib/fotos';

interface Props {
  id: string;
  titulo: string;
  detalle: string;
  textoAlternativo: string;
  disabled?: boolean;
  alCambiar: (archivo: File | null) => void;
  inputRef?: Ref<HTMLInputElement>;
}

export function SelectorFoto({ id, titulo, detalle, textoAlternativo, disabled = false, alCambiar, inputRef }: Props) {
  const entrada = useRef<HTMLInputElement | null>(null);
  const cambiar = useRef<HTMLButtonElement>(null);
  const [estado, setEstado] = useState<{ tipo: 'vacia' } | { tipo: 'error'; mensaje: string } | { tipo: 'lista'; archivo: File }>({ tipo: 'vacia' });
  const [previa, setPrevia] = useState('');
  const archivo = estado.tipo === 'lista' ? estado.archivo : null;

  useEffect(() => {
    if (!archivo) { setPrevia(''); return undefined; }
    const url = URL.createObjectURL(archivo);
    setPrevia(url);
    cambiar.current?.focus({ preventScroll: true }); // el foco no se pierde con la zona oculta
    return () => URL.revokeObjectURL(url);
  }, [archivo]);

  function alElegir() {
    const elegido = entrada.current?.files?.[0];
    if (!elegido) { setEstado({ tipo: 'vacia' }); alCambiar(null); return; }
    const problema = problemaDeFoto(elegido);
    if (problema) {
      if (entrada.current) entrada.current.value = '';
      setEstado({ tipo: 'error', mensaje: problema });
      alCambiar(null);
      return;
    }
    setEstado({ tipo: 'lista', archivo: elegido });
    alCambiar(elegido);
  }

  function quitar() {
    if (entrada.current) { entrada.current.value = ''; entrada.current.focus({ preventScroll: true }); }
    setEstado({ tipo: 'vacia' });
    alCambiar(null);
  }

  const asignarRef = (el: HTMLInputElement | null) => {
    entrada.current = el;
    if (typeof inputRef === 'function') inputRef(el);
    else if (inputRef) (inputRef as { current: HTMLInputElement | null }).current = el;
  };
  const mensaje = estado.tipo === 'error' ? estado.mensaje : estado.tipo === 'lista' ? 'Foto lista para enviar.' : '';
  return (
    <div className="foto-entrega" data-estado={estado.tipo}>
      <span className="foto-entrega-titulo" id={`foto-titulo-${id}`}>{titulo}</span>
      {/* El input nativo queda oculto pero operable con teclado; en el celular "capture" abre la cámara directamente. Sin
          "required": la falta de foto se avisa en el propio formulario. */}
      <input ref={asignarRef} className="foto-entrega-input" id={`foto-${id}`} type="file" name="foto" accept="image/jpeg,image/png,image/webp" capture="environment"
        aria-labelledby={`foto-titulo-${id}`} aria-describedby={`foto-ayuda-${id}`} disabled={disabled} onChange={alElegir} />
      <label className="foto-entrega-zona" htmlFor={`foto-${id}`}>
        <span className="foto-entrega-icono"><i className="ti ti-camera" aria-hidden="true" /></span>
        <span className="foto-entrega-texto">
          <strong>Tomar o elegir foto</strong>
          <small>{detalle}</small>
        </span>
      </label>
      <div className="foto-entrega-previa">
        <img alt={textoAlternativo} src={previa || undefined} />
        <div className="foto-entrega-datos">
          <strong className="foto-nombre">{archivo?.name}</strong>
          <small className="foto-peso">{archivo && formatearTamanoArchivo(archivo.size)}</small>
        </div>
        <div className="foto-entrega-botones">
          <button ref={cambiar} type="button" className="btn btn-ghost btn-sm foto-cambiar" disabled={disabled} onClick={() => entrada.current?.click()}><i className="ti ti-refresh" aria-hidden="true" /> Cambiar</button>
          <button type="button" className="btn btn-ghost btn-sm foto-quitar" disabled={disabled} onClick={quitar}><i className="ti ti-trash" aria-hidden="true" /> Quitar</button>
        </div>
      </div>
      <p className={`foto-entrega-ayuda${estado.tipo === 'error' ? ' error' : estado.tipo === 'lista' ? ' ok' : ''}`} id={`foto-ayuda-${id}`} role={estado.tipo === 'error' ? 'alert' : 'status'} aria-live="polite">{mensaje}</p>
    </div>
  );
}
