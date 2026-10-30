import { PanelLayout } from '../../layouts/PanelLayout';
import { SECCIONES_ADMIN } from '../../router/secciones';

export default function AdminLayout() {
  return <PanelLayout titulo="Panel Admin" icono="shield-check" base="/admin" secciones={SECCIONES_ADMIN} estilo="admin" />;
}
