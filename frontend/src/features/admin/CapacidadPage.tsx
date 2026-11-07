// /admin/capacidad: pedidos que esperan un vehículo más grande (ningún repartidor disponible alcanzaba). Caso poco frecuente:
// motivo_pendiente "sin_capacidad_suficiente" (GET /envios/pendientes-por-capacidad en Transporte).
import type { Envio } from '../../api/transporte';
import { formatearFechaServidor } from '../../lib/formato';
import { EncabezadoSeccion, usePendientesPorCapacidad, Vacio } from './comun';

// Sin peso del pedido (el envío no lo trae, y pedirlo de a uno por tarjeta sería una llamada más por envío): la instrucción
// queda en términos generales.
function TarjetaPendienteCapacidad({ envio, indice }: { envio: Envio; indice: number }) {
  return (
    <div className="pedido-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-envio-id={envio.id}>
      <div className="pedido-card-header">
        <h4>Pedido #{String(envio.pedido_id).slice(0, 8)}</h4>
      </div>
      <div className="perfil-datos-grid">
        <div className="perfil-dato"><span className="perfil-dato-label">Envío creado</span><span className="perfil-dato-valor">{formatearFechaServidor(envio.fecha_creacion)}</span></div>
      </div>
      <p className="muted">Este pedido necesita un vehículo con más capacidad — ningún repartidor disponible alcanzaba. Contrata o coordina uno, y pide a quien lo va a manejar que se registre como Repartidor desde su propia cuenta (pantalla de inicio → "Soy repartidor" → completar perfil con capacidad y tipo de vehículo).</p>
    </div>
  );
}

export default function CapacidadPage() {
  const consulta = usePendientesPorCapacidad();
  const lista = consulta.data ?? [];
  return (
    <div className="admin-tab-panel active" data-admin-panel="capacidad">
      <EncabezadoSeccion icono="truck" titulo="Pedidos esperando vehículo con más capacidad" />
      <div id="pendientes-capacidad-lista" className="grid-admin">{lista.map((e, i) => <TarjetaPendienteCapacidad key={e.id} envio={e} indice={i} />)}</div>
      {consulta.data && !lista.length && <Vacio icono="truck" titulo="No hay pedidos esperando vehículo" detalle="Cuando ningún repartidor disponible tenga capacidad suficiente para un pedido, aparece acá." />}
    </div>
  );
}
