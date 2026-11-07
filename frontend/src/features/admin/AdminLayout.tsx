import { PanelLayout } from '../../layouts/PanelLayout';
import { SECCIONES_ADMIN } from '../../router/secciones';
import { textoEnvioSinEntregar, usePagosPorHacer, usePedidosSinEntregar, useReclamosRuc, useTransferencias, useTransportistas, useViajesAdmin } from './comun';

// Como en el frontend anterior, al abrir el panel se cargan las listas que tienen contador en su pestaña (lo que espera una
// acción del admin); cada pantalla lee la misma consulta.
export default function AdminLayout() {
  const transferencias = useTransferencias();
  const pagos = usePagosPorHacer();
  const sinEntregar = usePedidosSinEntregar();
  const reclamos = useReclamosRuc();
  const transportistas = useTransportistas('pendiente');
  const viajes = useViajesAdmin();
  // Viajes que ya pueden salir o que no tienen quién los maneje; pedidos que esperan sin repartidor.
  const viajesPorAtender = (viajes.data?.viajes ?? []).filter((v) => v.estado === 'listo' || (v.estado === 'abierto' && !v.transportista)).length;
  const sinRepartidor = (sinEntregar.data ?? []).filter((d) => textoEnvioSinEntregar(d.envio).alerta).length;
  const insignias = {
    transferencias: { valor: transferencias.data?.length ?? 0, titulo: '' },
    pagos: { valor: (pagos.data?.porPagar.beneficiarios ?? []).filter((b) => b.a_pagar > 0).length, titulo: 'Personas a las que hay que pagar este lunes' },
    'sin-entregar': { valor: sinRepartidor, titulo: 'Pedidos pagados que no tienen quién los lleve' },
    'reclamos-ruc': { valor: reclamos.data?.length ?? 0, titulo: '' },
    transportistas: { valor: transportistas.data?.length ?? 0, titulo: '' },
    viajes: { valor: viajesPorAtender, titulo: '' },
  };
  return <PanelLayout id="view-panel-admin" titulo="Panel Admin" icono="shield-lock" base="/admin" secciones={SECCIONES_ADMIN} estilo="admin" insignias={insignias} />;
}
