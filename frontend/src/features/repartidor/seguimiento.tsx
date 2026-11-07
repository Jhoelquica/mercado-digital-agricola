// ---- Seguimiento de la ubicación del repartidor (lo ve el comprador en su mapa) ----
// Corre SOLO mientras haya un envío "en camino" y la pestaña se vea; se detiene al ocultarla, al salir del panel, al
// entregar el último envío en camino y al cerrar sesión (el panel se desmonta). Manda la última posición cada 15 s.
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { transporte } from '../../api/transporte';

export const INTERVALO_UBICACION_MS = 15000;

export type EstadoUbicacion = { tipo: 'ok' | 'error' | 'espera'; mensaje: string } | null;
const ContextoUbicacion = createContext<EstadoUbicacion>(null);
export const ProveedorUbicacion = ContextoUbicacion.Provider;
export const useEstadoUbicacion = () => useContext(ContextoUbicacion);

function mensajeErrorGeolocalizacion(err: GeolocationPositionError | null): string {
  if (!err) return 'No se pudo obtener tu ubicación.';
  if (err.code === err.PERMISSION_DENIED) return 'Denegaste el permiso de ubicación. Puedes marcar el punto manualmente en el mapa.';
  if (err.code === err.POSITION_UNAVAILABLE) return 'No se pudo determinar tu ubicación actual.';
  if (err.code === err.TIMEOUT) return 'Se agotó el tiempo esperando tu ubicación.';
  return 'No se pudo obtener tu ubicación.';
}

function usePestanaVisible(): boolean {
  const [visible, setVisible] = useState(() => !document.hidden);
  useEffect(() => {
    const alCambiar = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', alCambiar);
    return () => document.removeEventListener('visibilitychange', alCambiar);
  }, []);
  return visible;
}

// envioIds: los envíos "en camino" del repartidor. Devuelve el estado para el aviso de la pantalla.
export function useSeguimientoUbicacion(envioIds: string[]): EstadoUbicacion {
  const visible = usePestanaVisible();
  const [estado, setEstado] = useState<EstadoUbicacion>(null);
  const ids = useRef(envioIds);
  ids.current = envioIds;
  const activo = envioIds.length > 0 && visible;

  useEffect(() => {
    if (!activo) { setEstado(null); return undefined; }
    if (!navigator.geolocation) {
      setEstado({ tipo: 'error', mensaje: 'Tu navegador no soporta geolocalización.' });
      return undefined;
    }
    setEstado({ tipo: 'espera', mensaje: 'Buscando tu ubicación...' });
    let ultima: { lat: number; lng: number } | null = null;
    const vigilancia = navigator.geolocation.watchPosition(
      (pos) => {
        ultima = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        setEstado({ tipo: 'ok', mensaje: 'Compartiendo tu ubicación en tiempo real.' });
      },
      (err) => setEstado({ tipo: 'error', mensaje: mensajeErrorGeolocalizacion(err) }),
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 },
    );
    const intervalo = setInterval(async () => {
      if (!ultima) return;
      for (const id of ids.current) {
        try {
          await transporte.actualizarUbicacion(id, ultima.lat, ultima.lng);
        } catch { /* se reintenta en el siguiente ciclo de 15 s */ }
      }
    }, INTERVALO_UBICACION_MS);
    return () => {
      navigator.geolocation.clearWatch(vigilancia);
      clearInterval(intervalo);
    };
  }, [activo]);

  return estado;
}

export function AvisoUbicacion({ estado }: { estado: EstadoUbicacion }) {
  const iconos = { ok: 'map-pin', error: 'alert-triangle', espera: 'refresh' };
  return (
    <div id="ubicacion-estado" className="ubicacion-estado">
      {estado && <span className={`ubicacion-chip ubicacion-${estado.tipo}`}><i className={`ti ti-${iconos[estado.tipo]}`} /> {estado.mensaje}</span>}
    </div>
  );
}
