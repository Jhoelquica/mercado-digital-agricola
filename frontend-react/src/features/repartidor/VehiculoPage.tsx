// /repartidor/vehiculo: el repartidor cambia su vehículo cuando quiera (hallazgo de la validación del 2026-10-13).
import { useRef, useState, type FormEvent } from 'react';
import { repartidores, type Repartidor } from '../../api/repartidores';
import { toast } from '../../components/Toast';
import { useMiRepartidor, useRefrescarPanel } from './comun';

const VEHICULOS = [
  { valor: 'moto', texto: 'Moto', icono: 'motorbike' },
  { valor: 'auto', texto: 'Auto', icono: 'car' },
  { valor: 'camioneta', texto: 'Camioneta', icono: 'truck' },
  { valor: 'camion', texto: 'Camión', icono: 'truck' },
];

function TarjetaVehiculo({ perfil }: { perfil: Repartidor }) {
  const refrescar = useRefrescarPanel();
  const vehiculo = VEHICULOS.find((v) => v.valor === perfil.tipo_vehiculo) || { texto: 'Vehículo', icono: 'truck' };
  const capacidad = Number(perfil.capacidad_maxima_kg) || 0;
  const [abierto, setAbierto] = useState(false);
  const [tipo, setTipo] = useState(perfil.tipo_vehiculo || 'moto');
  const [kg, setKg] = useState(capacidad ? String(capacidad) : '');
  const [mensaje, setMensaje] = useState('');
  const [guardando, setGuardando] = useState(false);
  const cambiar = useRef<HTMLButtonElement>(null);
  const campoKg = useRef<HTMLInputElement>(null);

  function alternar(abrir: boolean) {
    setAbierto(abrir);
    setTimeout(() => (abrir ? campoKg.current?.focus() : cambiar.current?.focus()));
  }

  async function guardar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const valor = Number(kg);
    if (!Number.isFinite(valor) || valor < 1 || valor > 40000) {
      setMensaje('Escribe cuántos kilos carga tu vehículo: de 1 a 40 000 kg.');
      campoKg.current?.focus();
      return;
    }
    setGuardando(true);
    try {
      await repartidores.actualizarVehiculo({ tipo_vehiculo: tipo, capacidad_maxima_kg: valor });
      toast('Vehículo actualizado: te llegarán los envíos y recojos que ahora puedes cargar 🚚');
      setAbierto(false);
      await refrescar();
    } catch (err) {
      setMensaje((err as Error).message || 'No se pudo guardar. Intenta de nuevo.');
    }
    setGuardando(false);
  }

  return (
    <div className="card-panel vehiculo-panel">
      <div className="carga-cabecera">
        <span className="carga-cabecera-icono"><i className={`ti ti-${vehiculo.icono}`} aria-hidden="true" /></span>
        <div>
          <h3>Mi vehículo</h3>
          <p>{vehiculo.texto} · carga hasta <b>{capacidad.toLocaleString('es-PE', { maximumFractionDigits: 0 })} kg</b></p>
        </div>
        <button ref={cambiar} type="button" className="btn btn-outline btn-sm btn-editar-vehiculo" aria-expanded={abierto} aria-controls="form-vehiculo" onClick={() => alternar(!abierto)}><i className="ti ti-pencil" aria-hidden="true" /> Cambiar</button>
      </div>
      <p className="vehiculo-ayuda">Solo te llegan envíos y recojos que tu vehículo puede cargar. Si cambias de vehículo, actualízalo aquí.</p>
      <form id="form-vehiculo" className={`form-vehiculo ${abierto ? '' : 'hidden'}`} noValidate onSubmit={(e) => { void guardar(e); }}>
        <div className="form-carga-grid">
          <label className="perfil-campo">Tipo de vehículo
            <select name="tipo_vehiculo" required value={tipo} onChange={(e) => setTipo(e.target.value)}>
              {VEHICULOS.map((v) => <option key={v.valor} value={v.valor}>{v.texto}</option>)}
            </select>
          </label>
          <label className="perfil-campo">Capacidad máxima (kg)
            <input ref={campoKg} type="number" name="capacidad_maxima_kg" min="1" max="40000" step="1" inputMode="numeric" required value={kg} onChange={(e) => { setKg(e.target.value); setMensaje(''); }} />
          </label>
        </div>
        {perfil.verificacion_estado === 'verificado' && (
          <p className="envio-aviso info"><i className="ti ti-info-circle" aria-hidden="true" />
            <span>Tus documentos de carga son de este vehículo: si lo cambias, el admin los vuelve a revisar antes de asignarte viajes largos.</span></p>
        )}
        {mensaje ? <p className="form-entrega-mensaje" role="alert">{mensaje}</p> : <p className="form-entrega-mensaje hidden" role="alert" />}
        <div className="form-carga-acciones">
          <button type="button" className="btn btn-ghost btn-cancelar-vehiculo" onClick={() => alternar(false)}>Cancelar</button>
          <button type="submit" className="btn btn-primary" disabled={guardando}>{guardando ? <><i className="ti ti-loader-2" aria-hidden="true" /> Guardando…</> : <><i className="ti ti-device-floppy" aria-hidden="true" /> Guardar</>}</button>
        </div>
      </form>
    </div>
  );
}

export default function VehiculoPage() {
  const perfil = useMiRepartidor();
  return (
    <div className="repartidor-shell">
      <section className="carga-section">{perfil.data && <TarjetaVehiculo key={`${perfil.data.tipo_vehiculo}-${perfil.data.capacidad_maxima_kg}`} perfil={perfil.data} />}</section>
    </div>
  );
}
