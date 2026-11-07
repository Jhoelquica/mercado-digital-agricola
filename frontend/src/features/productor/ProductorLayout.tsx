import { SelectorIdioma, Texto, useIdioma } from '../../idioma/IdiomaContext';
import { PanelLayout } from '../../layouts/PanelLayout';
import { SECCIONES_PRODUCTOR } from '../../router/secciones';
import { AltaPerfil } from './AltaPerfil';
import { AsistentesDelAgricultor } from './asistente/AsistentesDelAgricultor';
import { ESTADOS_POR_COMPLETAR, useMisProductos, usePerfilProductor, useRegistros } from './comun';
import { recojoVigenteDe, useRecojos } from './productos/LlegadasAlmacen';

export default function ProductorLayout() {
  const { actual } = useIdioma();
  const perfil = usePerfilProductor();
  const conPerfil = Boolean(perfil.data);
  const { data: registros = [] } = useRegistros(conPerfil);
  const enCurso = registros.filter((r) => ESTADOS_POR_COMPLETAR.includes(r.estado)).length;
  // Cosechas publicadas (en camino al almacén) que todavía no tienen cómo llegar: ni recojo pedido ni fecha de entrega.
  const { data: misProductos = [] } = useMisProductos(conPerfil);
  const enTransito = misProductos.filter((p) => p.estado === 'en_transito');
  const { data: recojos = [] } = useRecojos(enTransito.length > 0);
  const sinCoordinar = enTransito.filter((p) => !recojoVigenteDe(recojos, p.id)).length;

  let sinPestanas = null;
  if (perfil.isLoading) sinPestanas = <div className="card-panel" aria-busy="true"><div className="skeleton skeleton-line tall w-60" /><div className="skeleton skeleton-line w-40" /></div>;
  else if (perfil.isError) sinPestanas = <p className="muted">No se pudo cargar tu perfil de productor.</p>;
  else if (!conPerfil) sinPestanas = <AltaPerfil />;

  return (
    <>
    <PanelLayout
      titulo={<Texto clave="panel.titulo">Panel del Productor</Texto>}
      icono="tractor"
      base="/productor"
      secciones={SECCIONES_PRODUCTOR}
      estilo="productor"
      acciones={<SelectorIdioma />}
      insignias={{ siembras: { valor: enCurso, titulo: 'Siembras y producciones en curso' }, productos: { valor: sinCoordinar, titulo: 'Cosechas por coordinar' } }}
      sinPestanas={sinPestanas}
    >
      {actual === 'qu' && (
        <p className="idioma-nota">
          <i className="ti ti-info-circle" aria-hidden="true" /> <Texto clave="idioma.nota">Estamos empezando en quechua: algunos textos siguen en castellano.</Texto>
        </p>
      )}
    </PanelLayout>
    {conPerfil && <AsistentesDelAgricultor />}
    </>
  );
}
