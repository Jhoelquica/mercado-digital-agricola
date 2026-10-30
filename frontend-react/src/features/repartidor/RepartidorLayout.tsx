import { PanelLayout } from '../../layouts/PanelLayout';
import { SECCIONES_REPARTIDOR } from '../../router/secciones';

export default function RepartidorLayout() {
  return <PanelLayout titulo="Gestionar Envíos" icono="truck" base="/repartidor" secciones={SECCIONES_REPARTIDOR} estilo="productor" />;
}
