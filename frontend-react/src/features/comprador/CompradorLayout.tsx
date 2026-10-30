import { PanelLayout } from '../../layouts/PanelLayout';
import { SECCIONES_COMPRADOR } from '../../router/secciones';

export default function CompradorLayout() {
  return <PanelLayout titulo="Mi cuenta" icono="shopping-bag" base="/comprador" secciones={SECCIONES_COMPRADOR} estilo="productor" />;
}
