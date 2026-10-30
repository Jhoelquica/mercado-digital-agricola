import { SelectorIdioma, Texto, useIdioma } from '../../idioma/IdiomaContext';
import { PanelLayout } from '../../layouts/PanelLayout';
import { SECCIONES_PRODUCTOR } from '../../router/secciones';

export default function ProductorLayout() {
  const { actual } = useIdioma();
  return (
    <PanelLayout
      titulo={<Texto clave="panel.titulo">Panel del Productor</Texto>}
      icono="tractor"
      base="/productor"
      secciones={SECCIONES_PRODUCTOR}
      estilo="productor"
      acciones={<SelectorIdioma />}
    >
      {actual === 'qu' && (
        <p className="idioma-nota">
          <i className="ti ti-info-circle" aria-hidden="true" /> <Texto clave="idioma.nota">Estamos empezando en quechua: algunos textos siguen en castellano.</Texto>
        </p>
      )}
    </PanelLayout>
  );
}
