import { useCallback, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { productos, type MisReservas, type ProductoDetalle } from '../../api/productos';
import { useAuth } from '../../auth/AuthContext';
import { rutaDeLogin } from '../../auth/rutasPorRol';
import { toast } from '../../components/Toast';
import { activarAvisosCelular, configuracionDeAvisos, hayAvisosCelularActivos, pushSoportado } from '../../lib/avisos';
import { unidadEtiqueta, unidadPlural } from '../../lib/productos';
import { cantidadDeReserva } from '../../lib/reglas';

type Cantidad = { cantidad: number | null; unidad: string | null };

// Producto en camino al almacén: no se compra todavía, se reserva gratis y se avisa al llegar. El panel de compra y la barra
// fija usan el mismo estado: el botón de la barra toma la cantidad escrita en el panel.
export function useReserva(producto: ProductoDetalle, inicial: MisReservas | null) {
  const { autenticado } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const id = String(producto.id);
  const [reservados, setReservados] = useState<Set<string> | null>(inicial ? new Set(inicial.productos.map(String)) : null);
  const [cantidades, setCantidades] = useState<Map<string, Cantidad> | null>(
    inicial?.reservas ? new Map(inicial.reservas.filter((r) => r.cantidad != null).map((r) => [String(r.producto_id), r])) : null,
  );
  const [cantidadEscrita, setCantidadEscrita] = useState('');
  const [unidadElegida, setUnidadElegida] = useState(producto.unidad_medida || '');
  const [ocupado, setOcupado] = useState(false);
  const [, setRepintar] = useState(0);

  const reservado = Boolean(reservados?.has(id));

  const marcarReservado = useCallback(() => setReservados((s) => new Set(s).add(id)), [id]);

  async function alternar(accion: 'reservar' | 'quitar') {
    if (!autenticado) {
      toast('Inicia sesión para reservar y recibir el aviso 🔔');
      void navigate(rutaDeLogin(pathname));
      return;
    }
    setOcupado(true);
    try {
      if (accion === 'quitar') {
        await productos.quitarReserva(id);
        setReservados((s) => { const n = new Set(s); n.delete(id); return n; });
        toast('Listo: ya no te avisaremos de este producto.');
      } else {
        const { datos, error } = cantidadDeReserva(cantidadEscrita, unidadElegida);
        if (error) {
          toast(error, 'error');
          return;
        }
        await productos.reservar(id, datos ?? undefined);
        marcarReservado();
        if (datos) setCantidades((m) => new Map(m).set(id, datos));
        toast('Reservado: te avisaremos cuando llegue al almacén 🔔');
      }
    } catch (err) {
      const mensaje = (err as Error).message || '';
      if (accion !== 'quitar' && /ya reservaste/i.test(mensaje)) marcarReservado();
      else {
        const contexto = accion === 'quitar' ? 'quitar tu reserva' : 'reservar el producto';
        console.error(contexto, err);
        toast(mensaje || `Ocurrió un error en ${contexto}`, 'error');
      }
    } finally {
      setOcupado(false);
    }
  }

  async function activarCelular() {
    setOcupado(true);
    try {
      await activarAvisosCelular();
    } catch (err) {
      console.error('activar los avisos al celular', err);
      toast((err as Error).message || 'Ocurrió un error en activar los avisos al celular', 'error');
    }
    setOcupado(false);
    setRepintar((n) => n + 1);
  }

  return { reservado, cantidad: cantidades?.get(id) ?? null, cantidadEscrita, setCantidadEscrita, unidadElegida, setUnidadElegida, ocupado, alternar, activarCelular };
}

type EstadoReserva = ReturnType<typeof useReserva>;

export function PanelReserva({ producto: p, reserva }: { producto: ProductoDetalle; reserva: EstadoReserva }) {
  const { sesion, autenticado } = useAuth();
  if (autenticado && sesion.rol !== 'comprador') {
    return <p className="detalle-reserva-texto">Viene de la chacra al almacén: se podrá comprar cuando llegue.</p>;
  }
  if (reserva.reservado) {
    const { cantidad: pedido } = reserva;
    const cuanto = pedido ? `Reservaste ${String(pedido.cantidad).replace('.', ',')} ${unidadPlural(pedido.unidad, pedido.cantidad as number)}. ` : '';
    return (
      <>
        <div className="detalle-reserva-hecha" role="status">
          <span className="detalle-reserva-icono" aria-hidden="true"><i className="ti ti-bell-check" /></span>
          <div>
            <strong>Te avisaremos cuando llegue</strong>
            <span>{cuanto}El aviso te llegará en Notificaciones. Reservar no te obliga a comprar.</span>
          </div>
        </div>
        {configuracionDeAvisos()?.push && pushSoportado() && !hayAvisosCelularActivos() && Notification.permission !== 'denied' && (
          <button type="button" className="btn btn-outline detalle-reserva-celular" disabled={reserva.ocupado} onClick={() => void reserva.activarCelular()}>
            <i className="ti ti-device-mobile-message" aria-hidden="true" /> Avisarme también al celular
          </button>
        )}
        <button type="button" className="btn-link detalle-reserva-quitar" disabled={reserva.ocupado} onClick={() => void reserva.alternar('quitar')}>Ya no me avises</button>
      </>
    );
  }
  // H7: puede decir cuánto le interesa, en las unidades en que se vende.
  const unidades = [p.unidad_medida, ...(p.unidades_alternativas || []).map((a) => a.unidad)].filter(Boolean) as string[];
  return (
    <>
      <p className="detalle-reserva-texto">Viene de la chacra al almacén y todavía no se puede comprar. Resérvalo gratis: te avisamos apenas llegue para que lo compres.</p>
      <div className="detalle-reserva-cantidad">
        <label htmlFor="reserva-cantidad">¿Cuánto te interesa? <span className="auth-opcional">(opcional)</span></label>
        <div className="detalle-reserva-campos">
          <input id="reserva-cantidad" type="number" inputMode="decimal" min="0" step="any" placeholder="Ej. 2" value={reserva.cantidadEscrita} onChange={(e) => reserva.setCantidadEscrita(e.target.value)} />
          <select id="reserva-unidad" aria-label="Unidad" value={reserva.unidadElegida} onChange={(e) => reserva.setUnidadElegida(e.target.value)}>
            {unidades.map((u) => <option key={u} value={u}>{unidadEtiqueta(u)}</option>)}
          </select>
        </div>
        <small>Así el agricultor sabe cuánto se pide. No aparta el producto ni te obliga a comprar.</small>
      </div>
      <button type="button" className="btn btn-primary btn-lg btn-block" disabled={reserva.ocupado} onClick={() => void reserva.alternar('reservar')}><i className="ti ti-bell" aria-hidden="true" /> Avisarme cuando llegue</button>
    </>
  );
}

export function BotonReservaSticky({ reserva }: { reserva: EstadoReserva }) {
  const { sesion, autenticado } = useAuth();
  if (autenticado && sesion.rol !== 'comprador') return null;
  if (reserva.reservado) return <span className="detalle-sticky-reservado"><i className="ti ti-bell-check" aria-hidden="true" /> Te avisaremos</span>;
  return <button type="button" className="btn btn-primary" disabled={reserva.ocupado} onClick={() => void reserva.alternar('reservar')}><i className="ti ti-bell" aria-hidden="true" /> Avisarme</button>;
}
