import { useEffect, useRef, useState, type FormEvent } from 'react';
import { pagos, type CondicionesDePago } from '../../api/pagos';
import { toast } from '../../components/Toast';
import { formatearMoneda } from '../../lib/formato';
import { FOTO_TAMANO_MAXIMO, FOTO_TIPOS_VALIDOS, formatearTamanoArchivo } from '../../lib/fotos';


async function copiarAlPortapapeles(valor: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(valor);
    toast(`Copiado: ${valor}`);
  } catch {
    toast('No se pudo copiar. Selecciónalo y cópialo a mano.', 'error');
  }
}

function BotonCopiar({ valor, etiqueta }: { valor: string; etiqueta: string }) {
  return (
    <button type="button" className="btn btn-ghost btn-copiar" aria-label={`Copiar ${etiqueta}`} onClick={() => { void copiarAlPortapapeles(valor); }}>
      <i className="ti ti-copy" aria-hidden="true" /> Copiar
    </button>
  );
}

function FilaCopiable({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return <div><dt>{etiqueta}</dt><dd><span>{valor}</span><BotonCopiar valor={valor} etiqueta={etiqueta} /></dd></div>;
}

type EstadoFoto =
  | { estado: 'vacia' }
  | { estado: 'error'; mensaje: string }
  | { estado: 'lista'; archivo: File };

function FotoConstancia({ foto, alCambiar }: { foto: EstadoFoto; alCambiar: (f: EstadoFoto) => void }) {
  const entrada = useRef<HTMLInputElement>(null);
  const cambiar = useRef<HTMLButtonElement>(null);
  const [previa, setPrevia] = useState('');
  const archivo = foto.estado === 'lista' ? foto.archivo : null;

  useEffect(() => {
    if (!archivo) { setPrevia(''); return undefined; }
    const url = URL.createObjectURL(archivo);
    setPrevia(url);
    cambiar.current?.focus({ preventScroll: true }); // el foco no se pierde con la zona oculta
    return () => URL.revokeObjectURL(url);
  }, [archivo]);

  function alElegir() {
    const elegido = entrada.current?.files?.[0];
    if (!elegido) { alCambiar({ estado: 'vacia' }); return; }
    if (!FOTO_TIPOS_VALIDOS.includes(elegido.type)) {
      if (entrada.current) entrada.current.value = '';
      alCambiar({ estado: 'error', mensaje: 'Ese archivo no es una imagen JPG, PNG o WEBP. Elige otra foto.' });
      return;
    }
    if (elegido.size > FOTO_TAMANO_MAXIMO) {
      if (entrada.current) entrada.current.value = '';
      alCambiar({ estado: 'error', mensaje: `La foto pesa ${formatearTamanoArchivo(elegido.size)} y el máximo es 5 MB. Toma otra más liviana.` });
      return;
    }
    alCambiar({ estado: 'lista', archivo: elegido });
  }

  const mensaje = foto.estado === 'error' ? foto.mensaje : foto.estado === 'lista' ? 'Foto lista para enviar.' : '';
  const claseAyuda = foto.estado === 'error' ? ' error' : foto.estado === 'lista' ? ' ok' : '';

  return (
    <div className="foto-entrega" data-estado={foto.estado}>
      <span className="foto-entrega-titulo" id="foto-titulo-constancia">Foto o captura de la constancia</span>
      <input ref={entrada} className="foto-entrega-input" id="foto-constancia" type="file" name="foto" accept="image/jpeg,image/png,image/webp" aria-labelledby="foto-titulo-constancia" aria-describedby="foto-ayuda-constancia" onChange={alElegir} />
      <label className="foto-entrega-zona" htmlFor="foto-constancia">
        <span className="foto-entrega-icono"><i className="ti ti-receipt" aria-hidden="true" /></span>
        <span className="foto-entrega-texto"><strong>Elegir la constancia</strong><small>Captura del app del banco o foto del voucher · máx. 5 MB</small></span>
      </label>
      <div className="foto-entrega-previa">
        {previa ? <img alt="Vista previa de la constancia" src={previa} /> : <img alt="Vista previa de la constancia" />}
        <div className="foto-entrega-datos"><strong className="foto-nombre">{archivo?.name}</strong><small className="foto-peso">{archivo && formatearTamanoArchivo(archivo.size)}</small></div>
        <div className="foto-entrega-botones">
          <button ref={cambiar} type="button" className="btn btn-ghost btn-sm foto-cambiar" onClick={() => entrada.current?.click()}><i className="ti ti-refresh" aria-hidden="true" /> Cambiar</button>
          <button
            type="button"
            className="btn btn-ghost btn-sm foto-quitar"
            onClick={() => {
              if (entrada.current) entrada.current.value = '';
              alCambiar({ estado: 'vacia' });
              entrada.current?.focus({ preventScroll: true });
            }}
          ><i className="ti ti-trash" aria-hidden="true" /> Quitar</button>
        </div>
      </div>
      <p className={`foto-entrega-ayuda${claseAyuda}`} id="foto-ayuda-constancia" role={foto.estado === 'error' ? 'alert' : 'status'} aria-live="polite">{mensaje}</p>
    </div>
  );
}

interface Props {
  pedidoId: string;
  monto: number;
  cuenta: NonNullable<CondicionesDePago['transferencia']>;
  alEnviar: (numeroOperacion: string) => void;
}

// El comprador transfiere el monto exacto a la cuenta de Chakra Shop y sube la constancia con el número de operación.
// El pago queda "en revisión" hasta que un admin la confirma; también puede subirla después desde Mis Pedidos.
export function PanelTransferencia({ pedidoId, monto, cuenta, alEnviar }: Props) {
  const [foto, setFoto] = useState<EstadoFoto>({ estado: 'vacia' });
  const [numero, setNumero] = useState('');
  const [mensaje, setMensaje] = useState('');
  const [enviando, setEnviando] = useState(false);
  const enCurso = useRef(false); // evita un segundo POST antes de que React llegue a deshabilitar el botón
  const entradaNumero = useRef<HTMLInputElement>(null);
  const formulario = useRef<HTMLFormElement>(null);

  async function alEnviarFormulario(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (enCurso.current) return;
    setMensaje('');
    if (foto.estado !== 'lista') {
      setFoto({ estado: 'error', mensaje: 'Falta la foto o captura de la constancia.' });
      formulario.current?.querySelector<HTMLInputElement>('.foto-entrega-input')?.focus();
      return;
    }
    const limpio = numero.trim();
    if (!/^[A-Za-z0-9-]{4,30}$/.test(limpio)) {
      setMensaje('Escribe el número de operación tal como aparece en tu constancia (de 4 a 30 letras o números, sin espacios).');
      entradaNumero.current?.focus();
      return;
    }
    enCurso.current = true;
    setEnviando(true);
    try {
      await pagos.enviarTransferencia(pedidoId, limpio, foto.archivo);
      alEnviar(limpio);
    } catch (err) {
      console.error('enviar la constancia', err);
      setMensaje((err as Error).message || 'No se pudo enviar la constancia. Intenta de nuevo.');
      enCurso.current = false;
      setEnviando(false);
    }
  }

  return (
    <div id="checkout-transferencia" className="checkout-confirmacion transferencia">
      <span className="checkout-exito-icono es-banco"><i className="ti ti-building-bank" aria-hidden="true" /></span>
      <h3>Transfiere y sube tu constancia</h3>
      <p>Tu pedido <strong>#{pedidoId.slice(0, 8)}</strong> ya está reservado. Transfiere el monto exacto a esta cuenta:</p>
      <div className="transferencia-monto">
        <span>Monto exacto a transferir</span>
        <strong>{formatearMoneda(monto)}</strong>
        <BotonCopiar valor={Number(monto).toFixed(2)} etiqueta="el monto" />
      </div>
      <dl className="transferencia-cuenta">
        <div><dt>Banco</dt><dd><span>{cuenta.banco}</span></dd></div>
        <div><dt>Titular</dt><dd><span>{cuenta.titular}</span></dd></div>
        <FilaCopiable etiqueta="Número de cuenta" valor={cuenta.cuenta} />
        <FilaCopiable etiqueta="CCI (desde otro banco)" valor={cuenta.cci} />
      </dl>
      <form ref={formulario} className="form-transferencia" noValidate onSubmit={(e) => { void alEnviarFormulario(e); }}>
        <FotoConstancia foto={foto} alCambiar={setFoto} />
        <label className="transferencia-operacion" htmlFor="numero-operacion">Número de operación
          <input ref={entradaNumero} type="text" id="numero-operacion" name="numero_operacion" maxLength={30} autoComplete="off" inputMode="text" placeholder="Está en tu constancia, p. ej. 12345678" aria-describedby="numero-operacion-ayuda" value={numero} onChange={(e) => setNumero(e.target.value)} />
          <small id="numero-operacion-ayuda">De 4 a 30 letras o números, sin espacios.</small>
        </label>
        {mensaje
          ? <p className="form-entrega-mensaje error" role="alert"><i className="ti ti-alert-circle" /><span>{mensaje}</span></p>
          : <p className="form-entrega-mensaje hidden" role="alert" />}
        <button type="submit" className="btn btn-primary btn-block" disabled={enviando}>
          {enviando ? <><i className="ti ti-loader-2 icono-girando" aria-hidden="true" /> Enviando la constancia…</> : 'Enviar constancia'}
        </button>
        <p className="transferencia-despues"><i className="ti ti-clock" aria-hidden="true" /> ¿La harás después? Tu pedido queda reservado: sube la constancia desde Mis Pedidos.</p>
      </form>
    </div>
  );
}

export function ConstanciaRecibida({ pedidoId, numeroOperacion }: { pedidoId: string; numeroOperacion: string }) {
  return (
    <>
      <span className="checkout-exito-icono"><i className="ti ti-receipt" aria-hidden="true" /></span>
      <h3>¡Recibimos tu constancia!</h3>
      <p>Tu pedido <strong>#{pedidoId.slice(0, 8)}</strong> queda reservado mientras revisamos tu transferencia (operación {numeroOperacion}).</p>
      <ol className="checkout-siguientes">
        <li><i className="ti ti-building-bank" aria-hidden="true" /><div><strong>Revisamos que llegó el dinero</strong><span>Normalmente en unas horas. Te avisamos en Notificaciones.</span></div></li>
        <li><i className="ti ti-truck-delivery" aria-hidden="true" /><div><strong>Un repartidor lleva tu pedido</strong><span>Te llamará al teléfono que indicaste para coordinar.</span></div></li>
        <li><i className="ti ti-key" aria-hidden="true" /><div><strong>Confirma la entrega con tu código</strong><span>Lo verás en Mis Pedidos cuando el pedido salga.</span></div></li>
      </ol>
    </>
  );
}
