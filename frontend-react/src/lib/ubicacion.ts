// La ubicación del navegador, con el motivo en palabras si no se pudo. La usan el checkout, el perfil del agricultor y
// sus chacras ("Usar mi ubicación").
export function obtenerUbicacionActual(): Promise<{ lat: number; lng: number }> {
  return new Promise((resolver, rechazar) => {
    if (!navigator.geolocation) {
      rechazar(new Error('Tu navegador no soporta geolocalización.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolver({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      (err) => {
        const mensajes: Record<number, string> = {
          [err.PERMISSION_DENIED]: 'Denegaste el permiso de ubicación. Puedes marcar el punto manualmente en el mapa.',
          [err.POSITION_UNAVAILABLE]: 'No se pudo determinar tu ubicación actual.',
          [err.TIMEOUT]: 'Se agotó el tiempo esperando tu ubicación.',
        };
        rechazar(new Error(mensajes[err.code] || 'No se pudo obtener tu ubicación.'));
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  });
}
