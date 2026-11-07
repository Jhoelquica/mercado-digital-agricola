import { useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { repartidores } from '../../api/repartidores';
import { manejarError, toast } from '../../components/Toast';
import { PanelLayout } from '../../layouts/PanelLayout';
import { SECCIONES_REPARTIDOR } from '../../router/secciones';
import { useMiRepartidor, useMisEnvios, useRefrescarPanel } from './comun';
import { ProveedorUbicacion, useSeguimientoUbicacion } from './seguimiento';

// Antes de recibir envíos: nombre, DNI y el vehículo (tipo y cuántos kg carga).
function AltaRepartidor() {
  const queryClient = useQueryClient();
  const [datos, setDatos] = useState({ nombre: '', dni: '', tipo_vehiculo: 'moto', capacidad_maxima_kg: '' });
  const [enviando, setEnviando] = useState(false);
  async function crear(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setEnviando(true);
    try {
      await repartidores.crear({ nombre: datos.nombre.trim(), dni: datos.dni.trim(), tipo_vehiculo: datos.tipo_vehiculo, capacidad_maxima_kg: Number(datos.capacidad_maxima_kg) });
      toast('¡Perfil de repartidor creado! 🚚');
      setDatos({ nombre: '', dni: '', tipo_vehiculo: 'moto', capacidad_maxima_kg: '' });
      await queryClient.invalidateQueries({ queryKey: ['repartidor'] });
    } catch (err) {
      manejarError(err, 'crear tu perfil de repartidor');
    }
    setEnviando(false);
  }
  const campo = (nombre: keyof typeof datos) => ({ value: datos[nombre], onChange: (e: { target: { value: string } }) => setDatos({ ...datos, [nombre]: e.target.value }) });
  return (
    <div id="repartidor-setup" className="card-panel">
      <h3>Completa tu perfil de repartidor</h3>
      <p className="muted">Necesitamos tu nombre y DNI antes de poder asignarte envíos.</p>
      <form id="form-repartidor" className="form-grid" onSubmit={(e) => { void crear(e); }}>
        <label>Nombre completo
          <input type="text" id="repartidor-nombre" required placeholder="Ej. Juan Pérez" {...campo('nombre')} />
        </label>
        <label>DNI
          <input type="text" id="repartidor-dni" required maxLength={8} inputMode="numeric" placeholder="8 dígitos" {...campo('dni')} />
        </label>
        <label>Tipo de vehículo
          <select id="repartidor-tipo-vehiculo" required {...campo('tipo_vehiculo')}>
            <option value="moto">Moto</option>
            <option value="auto">Auto</option>
            <option value="camioneta">Camioneta</option>
            <option value="camion">Camión</option>
          </select>
        </label>
        <label>Capacidad máxima (kg)
          <input type="number" id="repartidor-capacidad-maxima" required min="1" step="0.1" placeholder="Ej. 30" {...campo('capacidad_maxima_kg')} />
          <small className="muted">¿Cuántos kg puede cargar tu vehículo como máximo?</small>
        </label>
        <button type="submit" className="btn btn-primary" disabled={enviando}>Crear perfil de repartidor</button>
      </form>
    </div>
  );
}

export default function RepartidorLayout() {
  const perfil = useMiRepartidor();
  const refrescar = useRefrescarPanel();
  const { mios } = useMisEnvios(perfil.data?.id);
  // El GPS solo corre con un envío "en camino" (ver seguimiento.tsx).
  const ubicacion = useSeguimientoUbicacion(mios.filter((e) => e.estado === 'en_camino').map((e) => e.id));

  let sinPestanas = null;
  if (perfil.isLoading) sinPestanas = <div className="card-panel" aria-busy="true"><div className="skeleton skeleton-line tall w-60" /><div className="skeleton skeleton-line w-40" /></div>;
  else if (perfil.isError) sinPestanas = <p className="muted">No se pudo cargar tu perfil de repartidor.</p>;
  else if (!perfil.data) sinPestanas = <AltaRepartidor />;

  return (
    <ProveedorUbicacion value={ubicacion}>
      <PanelLayout
        titulo="Gestionar Envíos"
        icono="truck"
        base="/repartidor"
        secciones={SECCIONES_REPARTIDOR}
        estilo="productor"
        acciones={<button type="button" className="btn btn-outline" id="btn-refrescar-envios" onClick={() => { void refrescar(); }}>↻ Refrescar</button>}
        sinPestanas={sinPestanas}
      />
    </ProveedorUbicacion>
  );
}
