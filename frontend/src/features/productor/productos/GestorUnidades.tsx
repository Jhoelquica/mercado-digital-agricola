// ---- Unidades de venta alternativas: editables en cualquier estado del producto (borrador, en_transito, disponible),
// a diferencia de la categoría y el precio, que solo se editan en el borrador. ----
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { productos, type UnidadAlternativaGuardada } from '../../../api/productos';
import { manejarError, toast } from '../../../components/Toast';
import { formatearMoneda } from '../../../lib/formato';
import { CODIGOS_UNIDADES, formatearFactor, infoUnidad, unidadCorta, unidadPlural } from '../../../lib/productos';
import { unidadesParaAgregar } from '../../../lib/reglas';

const claveUnidades = (productoId: string) => ['productor', 'unidades', productoId];

interface ProductoConUnidades { id: string; unidad_medida: string | null; unidades_permitidas: string[] | null }

function FilaEnEdicion({ u, unidadBase, alGuardar, alCancelar }: { u: UnidadAlternativaGuardada; unidadBase: string | null; alGuardar: (precio: number, factor: number) => Promise<boolean>; alCancelar: () => void }) {
  const [precio, setPrecio] = useState(String(u.precio));
  const [factor, setFactor] = useState(String(formatearFactor(u.factor_a_base)));
  const [guardando, setGuardando] = useState(false);
  async function guardar() {
    setGuardando(true);
    if (!(await alGuardar(parseFloat(precio), parseFloat(factor)))) setGuardando(false);
  }
  return (
    <div className="unidades-alt-fila unidades-alt-fila-edicion" data-unidad-id={u.id}>
      <div className="unidades-alt-info">
        <strong>{infoUnidad(u.unidad).label}</strong>
        <label>Precio (S/)
          <input type="number" className="unidad-alt-editar-precio" min="0.01" step="0.01" value={precio} disabled={guardando} onChange={(e) => setPrecio(e.target.value)} />
        </label>
        <label>Factor (1 {unidadCorta(u.unidad)} = X {unidadPlural(unidadBase, 2)})
          <input type="number" className="unidad-alt-editar-factor" min="0.0001" step="0.0001" value={factor} disabled={guardando} onChange={(e) => setFactor(e.target.value)} />
        </label>
      </div>
      <div className="unidades-alt-acciones">
        <button type="button" className="btn btn-primary btn-sm btn-guardar-unidad-alt" disabled={guardando} onClick={() => { void guardar(); }}>Guardar</button>
        <button type="button" className="btn btn-outline btn-sm btn-cancelar-edicion-unidad-alt" disabled={guardando} onClick={alCancelar}>Cancelar</button>
      </div>
    </div>
  );
}

export function GestorUnidades({ producto }: { producto: ProductoConUnidades }) {
  const queryClient = useQueryClient();
  const consulta = useQuery({ queryKey: claveUnidades(producto.id), queryFn: () => productos.unidades.listar(producto.id).catch(() => [] as UnidadAlternativaGuardada[]), staleTime: Infinity });
  const [editando, setEditando] = useState<string | null>(null);
  const [borrando, setBorrando] = useState<string | null>(null);
  const unidades = consulta.data ?? [];
  const base = producto.unidad_medida;
  const permitidas = producto.unidades_permitidas;
  // Solo las unidades en que se vende el alimento (H1: una papa no va por unidades), sin la base ni las ya agregadas.
  const opciones = unidadesParaAgregar(CODIGOS_UNIDADES, permitidas, base, unidades.map((u) => u.unidad));
  const [nueva, setNueva] = useState({ unidad: '', precio: '', factor: '' });
  const [agregando, setAgregando] = useState(false);
  const unidadElegida = opciones.includes(nueva.unidad) ? nueva.unidad : opciones[0] ?? '';
  const fijar = (lista: UnidadAlternativaGuardada[]) => queryClient.setQueryData(claveUnidades(producto.id), lista);

  if (consulta.isLoading) return <p className="muted">Cargando unidades de venta...</p>;

  async function agregar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setAgregando(true);
    try {
      const creada = await productos.unidades.crear(producto.id, { unidad: unidadElegida, precio: parseFloat(nueva.precio), factor_a_base: parseFloat(nueva.factor) });
      fijar([...unidades, creada]);
      setNueva({ unidad: '', precio: '', factor: '' });
      toast('Unidad de venta agregada');
    } catch (err) {
      manejarError(err, 'agregar la unidad de venta');
    }
    setAgregando(false);
  }

  async function guardar(unidadId: string, precio: number, factor: number): Promise<boolean> {
    try {
      const actualizada = await productos.unidades.editar(unidadId, { precio, factor_a_base: factor });
      fijar(unidades.map((u) => (u.id === unidadId ? actualizada : u)));
      setEditando(null);
      toast('Unidad de venta actualizada');
      return true;
    } catch (err) {
      manejarError(err, 'actualizar la unidad de venta');
      return false;
    }
  }

  async function eliminar(unidadId: string) {
    setBorrando(unidadId);
    try {
      await productos.unidades.eliminar(unidadId);
      fijar(unidades.filter((u) => u.id !== unidadId));
      toast('Unidad de venta eliminada');
    } catch (err) {
      manejarError(err, 'eliminar la unidad de venta');
    }
    setBorrando(null);
  }

  const listaPermitidas = (permitidas || []).map((u) => infoUnidad(u).etiqueta).join(', ');
  return (
    <div className="unidades-alt-gestor" data-producto-id={producto.id} data-unidad-base={base ?? ''}>
      <div className="unidades-alt-lista">
        {unidades.length ? unidades.map((u) => {
          if (editando === u.id) return <FilaEnEdicion key={u.id} u={u} unidadBase={base} alGuardar={(p, f) => guardar(u.id, p, f)} alCancelar={() => setEditando(null)} />;
          const factor = formatearFactor(u.factor_a_base);
          return (
            <div className="unidades-alt-fila" data-unidad-id={u.id} key={u.id}>
              <div className="unidades-alt-info">
                <strong>{infoUnidad(u.unidad).label}</strong>
                {Array.isArray(permitidas) && permitidas.length > 0 && !permitidas.includes(u.unidad)
                  && <span className="unidad-alt-no-permitida"><i className="ti ti-eye-off" aria-hidden="true" /> Este producto no se vende por esta unidad: el comprador no la ve. Puedes borrarla.</span>}
                <span>{formatearMoneda(u.precio)} / {unidadCorta(u.unidad)}</span>
                <span className="muted">1 {unidadCorta(u.unidad)} = {factor} {unidadPlural(base, factor)}</span>
              </div>
              <div className="unidades-alt-acciones">
                <button type="button" className="btn btn-outline btn-sm btn-editar-unidad-alt" aria-label="Editar" disabled={borrando === u.id} onClick={() => setEditando(u.id)}><i className="ti ti-pencil" /></button>
                <button type="button" className="btn btn-danger btn-sm btn-eliminar-unidad-alt" aria-label="Eliminar" disabled={borrando === u.id} onClick={() => { void eliminar(u.id); }}><i className="ti ti-trash" /></button>
              </div>
            </div>
          );
        }) : <p className="muted">Todavía no agregaste unidades de venta adicionales.</p>}
      </div>
      {opciones.length ? (
        <form className="unidad-alt-form form-grid" onSubmit={(e) => { void agregar(e); }}>
          <label>Nueva unidad
            <select className="unidad-alt-nueva-select" value={unidadElegida} onChange={(e) => setNueva({ ...nueva, unidad: e.target.value })}>
              {opciones.map((u) => <option key={u} value={u}>{infoUnidad(u).label}</option>)}
            </select>
          </label>
          <label>Precio (S/)
            <input type="number" className="unidad-alt-nuevo-precio" min="0.01" step="0.01" required placeholder="0.00" value={nueva.precio} onChange={(e) => setNueva({ ...nueva, precio: e.target.value })} />
          </label>
          <label>Factor de conversión
            <input type="number" className="unidad-alt-nuevo-factor" min="0.0001" step="0.0001" required placeholder="Ej. 10" value={nueva.factor} onChange={(e) => setNueva({ ...nueva, factor: e.target.value })} />
          </label>
          {/* "¿Cuántos kg equivalen a 1 saco?": el factor nunca se siente un número abstracto. */}
          <p className="unidad-alt-factor-ayuda muted">¿Cuántos {unidadPlural(base, 2)} equivalen a 1 {unidadCorta(unidadElegida)}?</p>
          <button type="submit" className="btn btn-outline btn-sm" disabled={agregando}>Agregar unidad</button>
        </form>
      ) : (
        <p className="muted">{listaPermitidas ? `Este producto se vende por ${listaPermitidas}: ya agregaste todas.` : 'Ya agregaste todas las unidades de medida disponibles para este producto.'}</p>
      )}
    </div>
  );
}
