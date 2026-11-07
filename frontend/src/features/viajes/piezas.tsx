// Piezas de la tarjeta de un viaje compartido en camión, iguales para el transportista (/repartidor/viajes) y el admin
// (/admin/viajes): las etapas, el medidor de sacos, los pedidos que lleva y el botón con su paso de confirmación.
import { useRef, useState } from 'react';
import type { Viaje } from '../../api/transporte';

export const ETAPAS_VIAJE = [
  { estado: 'abierto', texto: 'Juntando carga' },
  { estado: 'listo', texto: 'Listo para salir' },
  { estado: 'en_ruta', texto: 'En ruta' },
  { estado: 'llegado', texto: 'En destino' },
];

export function EtapasViaje({ v }: { v: Viaje }) {
  const actual = Math.max(0, ETAPAS_VIAJE.findIndex((e) => e.estado === v.estado));
  return (
    <ol className="viaje-etapas" aria-label="Etapas del viaje">
      {ETAPAS_VIAJE.map((e, i) => <li key={e.estado} className={i < actual ? 'hecho' : i === actual ? 'actual' : ''} aria-current={i === actual ? 'step' : undefined}><span className="viaje-paso-punto" aria-hidden="true" />{e.texto}</li>)}
    </ol>
  );
}

export function MedidorViaje({ v }: { v: Viaje }) {
  const capacidad = Number(v.capacidad_sacos) || 0;
  const cargados = Number(v.sacos_cargados) || 0;
  const paraSalir = Number(v.sacos_para_salir) || 0;
  const porcentaje = (n: number) => (capacidad ? Math.min(100, (n / capacidad) * 100) : 0);
  return (
    <div className="viaje-medidor">
      <div className="viaje-medidor-texto"><strong>{cargados} sacos cargados</strong><span>de {capacidad} · sale con {paraSalir}</span></div>
      <div className="viaje-medidor-barra" role="progressbar" aria-valuemin={0} aria-valuemax={capacidad} aria-valuenow={Math.min(cargados, capacidad)} aria-label="Sacos en el camión">
        <span className="viaje-medidor-relleno" style={{ width: `${porcentaje(cargados)}%` }} />
        <span className="viaje-medidor-marca" style={{ left: `${porcentaje(paraSalir)}%` }} />
      </div>
      <span className="viaje-medidor-leyenda">Sale al llegar a esta marca ({paraSalir} sacos)</span>
    </div>
  );
}

export function EnviosDelViaje({ v }: { v: Viaje }) {
  if (!v.envios?.length) return null;
  const llegado = v.estado === 'llegado';
  return (
    <ul className="viaje-envios" aria-label="Pedidos en este viaje">
      {v.envios.map((e) => (
        <li key={e.id}>
          <span className="viaje-envio-id">Pedido #{String(e.pedido_id).slice(0, 8)}</span>
          <span>{e.sacos ?? '—'} sacos</span>
          {llegado && (e.estado === 'entregado' ? <span className="viaje-saldo ok">Entregado</span> : e.saldo_pagado ? <span className="viaje-saldo ok">Saldo pagado</span> : <span className="viaje-saldo espera">Falta el saldo</span>)}
        </li>
      ))}
    </ul>
  );
}

// Botón con su paso de confirmación: mover un viaje avisa a todos sus compradores. `alConfirmar` devuelve si se pudo
// (si no, la confirmación queda abierta para reintentar).
export function BotonViaje({ accion, icono, texto, pregunta, confirmar, claseBoton = 'btn-primary', alConfirmar }: {
  accion: string; icono: string; texto: string; pregunta: string; confirmar: string; claseBoton?: string; alConfirmar: () => Promise<boolean>;
}) {
  const [confirmando, setConfirmando] = useState(false);
  const [moviendo, setMoviendo] = useState(false);
  const botonConfirmar = useRef<HTMLButtonElement>(null);
  const botonAccion = useRef<HTMLButtonElement>(null);
  async function mover() {
    setMoviendo(true);
    if (await alConfirmar()) setConfirmando(false);
    setMoviendo(false);
  }
  return (
    <>
      <div className={`viaje-acciones ${confirmando ? 'hidden' : ''}`}>
        <button ref={botonAccion} type="button" className={`btn ${claseBoton} btn-viaje-accion`} disabled={moviendo} onClick={() => { setConfirmando(true); setTimeout(() => botonConfirmar.current?.focus()); }}><i className={`ti ti-${icono}`} aria-hidden="true" /> {texto}</button>
      </div>
      <div className={`viaje-confirmar ${confirmando ? '' : 'hidden'}`} role="group" aria-label={texto}>
        <p>{pregunta}</p>
        <div>
          <button type="button" className="btn btn-ghost btn-sm btn-viaje-cancelar" disabled={moviendo} onClick={() => { setConfirmando(false); setTimeout(() => botonAccion.current?.focus()); }}>Todavía no</button>
          <button ref={botonConfirmar} type="button" className="btn btn-primary btn-sm btn-viaje-confirmar" data-accion={accion} disabled={moviendo} onClick={() => { void mover(); }}>{confirmar}</button>
        </div>
      </div>
    </>
  );
}
