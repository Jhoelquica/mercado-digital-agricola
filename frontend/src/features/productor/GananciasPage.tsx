import { Texto } from '../../idioma/IdiomaContext';
import { MisGanancias } from '../ganancias/MisGanancias';

export default function GananciasPage() {
  return (
    <div className="productor-tab-panel active" data-tab-panel="ganancias">
      <div className="section-header inner">
        <h3><i className="ti ti-wallet" /> <Texto clave="seccion.ganancias">Mis ganancias</Texto></h3>
      </div>
      <MisGanancias id="productor-ganancias" />
    </div>
  );
}
