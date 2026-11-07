import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { notificaciones } from '../../api/notificaciones';
import { manejarError, toast } from '../../components/Toast';
import { activarAvisosCelular, apagarAvisosCelular, cargarConfigAvisos, esIphoneSinInstalar, pushSoportado, suscripcionPushActual } from '../../lib/avisos';
import { estadoAvisosCelular, type EstadoAvisosCelular } from '../../lib/reglas';

const TEXTOS: Record<EstadoAvisosCelular, string> = {
  'apagado-servidor': 'Todavía no está disponible en ChakraShop.',
  'instalar-iphone': 'En iPhone: toca Compartir y luego «Agregar a inicio». Abre ChakraShop desde ese ícono y actívalo aquí.',
  'no-soportado': 'Este navegador no puede recibir avisos. Prueba con Chrome.',
  bloqueado: 'Bloqueaste los avisos de ChakraShop: permítelos en los permisos del navegador (el candado junto a la dirección).',
  inactivo: 'Apagado: los avisos solo se ven aquí, en Notificaciones.',
  activo: 'Encendido: te llegan a este celular aunque no tengas ChakraShop abierto.',
};

async function cargarEstado() {
  const config = await cargarConfigAvisos();
  if (!config.push && !config.correo) return { config, estado: null, mios: null };
  const [suscripcion, mios] = await Promise.all([
    suscripcionPushActual().catch(() => null),
    notificaciones.avisos.mios().catch(() => null),
  ]);
  const estado = estadoAvisosCelular({
    configurado: config.push, soportado: pushSoportado(), iphoneSinInstalar: esIphoneSinInstalar(),
    permiso: 'Notification' in window ? Notification.permission : 'default', suscrito: Boolean(suscripcion),
  });
  return { config, estado, mios };
}

// H6: avisos al celular (Web Push) y, de respaldo, por correo. El permiso del navegador se pide solo al tocar "Activar".
export function TarjetaAvisos() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ['avisos-celular'], queryFn: cargarEstado, staleTime: 0, gcTime: 0 });
  const [ocupado, setOcupado] = useState(false);
  const repintar = () => queryClient.invalidateQueries({ queryKey: ['avisos-celular'] });

  if (!data || !data.estado) return <section className="avisos-card hidden" id="avisos-celular" aria-labelledby="avisos-celular-titulo" />;
  const { config, estado, mios } = data;

  async function accion(cual: 'activar' | 'apagar' | 'probar') {
    setOcupado(true);
    try {
      if (cual === 'activar') await activarAvisosCelular();
      if (cual === 'apagar') await apagarAvisosCelular();
      if (cual === 'probar') {
        await notificaciones.avisos.probar();
        toast('Te mandamos un aviso de prueba: mira tu celular.');
      }
    } catch (err) {
      manejarError(err, 'cambiar los avisos al celular');
    }
    await repintar();
    setOcupado(false);
  }

  async function cambiarCorreo(activo: boolean) {
    setOcupado(true);
    try {
      const estadoCorreo = await notificaciones.avisos.correo(activo);
      toast(estadoCorreo.correo ? `Listo: si un aviso no te llega al celular, te escribimos a ${estadoCorreo.correo}.` : 'Listo: ya no te mandaremos avisos por correo.');
    } catch (err) {
      manejarError(err, 'cambiar el aviso por correo');
    }
    await repintar();
    setOcupado(false);
  }

  return (
    <section className="avisos-card" id="avisos-celular" aria-labelledby="avisos-celular-titulo">
      <div className="avisos-cabeza">
        <span className="avisos-icono" aria-hidden="true"><i className="ti ti-device-mobile-message" /></span>
        <div>
          <h3 id="avisos-celular-titulo">Avisos en tu celular</h3>
          <p>Cuando llegue tu reserva, cambie tu pedido o vengan por tu cosecha.</p>
        </div>
      </div>
      <div className={`avisos-fila avisos-fila-${estado}`}>
        <span className="avisos-fila-texto"><strong>En este celular</strong><small>{TEXTOS[estado]}</small></span>
        {estado === 'inactivo' && (
          <span className="avisos-acciones">
            <button type="button" className="btn btn-primary" disabled={ocupado} onClick={() => { void accion('activar'); }}><i className="ti ti-bell-ringing" aria-hidden="true" /> Activar</button>
          </span>
        )}
        {estado === 'activo' && (
          <span className="avisos-acciones">
            <button type="button" className="btn btn-ghost" disabled={ocupado} onClick={() => { void accion('probar'); }}>Probar</button>
            <button type="button" className="btn btn-outline" disabled={ocupado} onClick={() => { void accion('apagar'); }}>Apagar</button>
          </span>
        )}
      </div>
      {config.correo && (
        <label className="avisos-fila avisos-fila-correo" htmlFor="avisos-correo">
          <span className="avisos-fila-texto"><strong>Por correo, si no te llega al celular</strong>
            <small>{mios?.correo ? `A ${mios.correo}` : 'Te escribimos al correo de tu cuenta.'}</small></span>
          <input type="checkbox" role="switch" className="avisos-switch" id="avisos-correo" checked={Boolean(mios?.correo)} disabled={ocupado} onChange={(e) => { void cambiarCorreo(e.target.checked); }} />
        </label>
      )}
    </section>
  );
}
