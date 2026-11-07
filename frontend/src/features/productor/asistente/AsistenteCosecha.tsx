// ---- Completar cosecha (de abrirAsistenteCosecha y pasosAsistenteCosecha de app.js) ----
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { normalizarUrlImagen } from '../../../api/imagenes';
import { productores, type CatalogoPlantillas, type RegistroProduccion } from '../../../api/productores';
import { toast } from '../../../components/Toast';
import { formatearMoneda } from '../../../lib/formato';
import { formatearTamanoArchivo, problemaDeFoto } from '../../../lib/fotos';
import { unidadCorta, unidadPlural } from '../../../lib/productos';
import { gastosYaRegistrados } from '../../../lib/reglas';
import { etapasDePlantilla, fechaIso, fechaLegible, palabrasDe, sumarDias } from '../comun';
import { Asistente, CampoFecha, CampoNumero, type ConfigAsistente, type Paso, type PropsPaso } from './Asistente';
import { CamposGastos, GastosYaRegistrados, gastosParaEnviar, pasoUbicacion, recogerGastos, resumenGastos, validarGastos, type ConUbicacion, type GastoAsistente, type Lugar } from './piezas';
import { Voz } from './voz';

interface EstadoCosecha extends ConUbicacion {
  archivoFoto: File | null;
  errorFoto: string;
  fotoGuardadaUrl: string | null;
  cantidad: string;
  fecha: string;
  manoObra: string;
  gastosFinalValores: Record<string, string>;
  gastosFinal?: GastoAsistente[];
}

const firmaArchivo = (archivo: File) => `${archivo.name}|${archivo.size}|${archivo.lastModified}`;

// La foto es el mismo selector de la foto de entrega del repartidor: aquí solo se guarda el archivo en el estado.
function PasoFoto({ estado, cambiar }: PropsPaso<EstadoCosecha>) {
  const entrada = useRef<HTMLInputElement>(null);
  const botonCambiar = useRef<HTMLButtonElement>(null);
  const [previa, setPrevia] = useState('');
  const [recienElegida, setRecienElegida] = useState(false);
  const guardada = !estado.archivoFoto && estado.fotoGuardadaUrl ? normalizarUrlImagen(estado.fotoGuardadaUrl) : null;

  useEffect(() => {
    if (!estado.archivoFoto) { setPrevia(''); return undefined; }
    const url = URL.createObjectURL(estado.archivoFoto);
    setPrevia(url);
    return () => URL.revokeObjectURL(url);
  }, [estado.archivoFoto]);

  function alElegir() {
    const archivo = entrada.current?.files?.[0];
    if (!archivo) { cambiar({ archivoFoto: null, errorFoto: '' }); return; }
    const problema = problemaDeFoto(archivo);
    if (problema) {
      if (entrada.current) entrada.current.value = '';
      cambiar({ archivoFoto: null, errorFoto: problema });
      return;
    }
    cambiar({ archivoFoto: archivo, errorFoto: '' });
    setRecienElegida(true);
    setTimeout(() => botonCambiar.current?.focus({ preventScroll: true })); // el foco no se pierde con la zona oculta
    if (Voz.leerSolo) Voz.hablar('Foto lista. Toca Siguiente.');
  }

  function quitar() {
    if (entrada.current) entrada.current.value = '';
    cambiar({ archivoFoto: null, errorFoto: '', fotoGuardadaUrl: null }); // quitada la foto guardada, hay que tomar otra
    entrada.current?.focus({ preventScroll: true });
  }

  const vista = estado.errorFoto ? 'error' : estado.archivoFoto || guardada ? 'lista' : 'vacia';
  const mensaje = estado.errorFoto || (estado.archivoFoto ? (recienElegida ? 'Foto lista para enviar.' : 'Foto lista.') : '');
  return (
    <div className="foto-entrega asistente-foto" data-estado={vista}>
      {/* En el celular "capture" abre la cámara directamente. */}
      <input ref={entrada} className="foto-entrega-input" id="asistente-foto-cosecha" type="file" name="foto" accept="image/jpeg,image/png,image/webp"
        capture="environment" aria-labelledby="asistente-pregunta" aria-describedby="asistente-foto-ayuda" onChange={alElegir} />
      <label className="foto-entrega-zona" htmlFor="asistente-foto-cosecha">
        <span className="foto-entrega-icono"><i className="ti ti-camera" aria-hidden="true" /></span>
        <span className="foto-entrega-texto">
          <strong>Tomar foto de la cosecha</strong>
          <small>Hazlo en tu chacra · JPG, PNG o WEBP · máx. 5 MB</small>
        </span>
      </label>
      <div className="foto-entrega-previa">
        <img alt="Vista previa de la foto de la cosecha" src={previa || guardada || undefined} />
        <div className="foto-entrega-datos">
          <strong className="foto-nombre">{estado.archivoFoto ? estado.archivoFoto.name : guardada ? 'Foto ya guardada' : ''}</strong>
          <small className="foto-peso">{estado.archivoFoto ? formatearTamanoArchivo(estado.archivoFoto.size) : ''}</small>
        </div>
        <div className="foto-entrega-botones">
          <button ref={botonCambiar} type="button" className="btn btn-ghost foto-cambiar" onClick={() => entrada.current?.click()}><i className="ti ti-refresh" aria-hidden="true" /> Cambiar</button>
          <button type="button" className="btn btn-ghost foto-quitar" onClick={quitar}><i className="ti ti-trash" aria-hidden="true" /> Quitar</button>
        </div>
      </div>
      <p className={`foto-entrega-ayuda${vista === 'error' ? ' error' : estado.archivoFoto ? ' ok' : ''}`} id="asistente-foto-ayuda" role={vista === 'error' ? 'alert' : 'status'} aria-live="polite">{mensaje}</p>
    </div>
  );
}

// Con etapas: lo que se gastó al final (cosechar, trillar, recoger los huevos, empacar el queso...).
function pasoGastosFinales(registro: RegistroProduccion, catalogo: CatalogoPlantillas | null): Paso<EstadoCosecha> {
  const etapas = etapasDePlantilla(catalogo, registro.plantilla).filter((e) => e.momento === 'final');
  const verbo = catalogo?.tipos?.[registro.tipo_produccion || '']?.verbo_fin || 'cosechar';
  const ya = gastosYaRegistrados(registro.gastos_por_etapa);
  const nota = ya.enElFinal.length
    ? `En ${ya.enElFinal.map((e) => `${e.nombre} ya anotaste ${formatearMoneda(e.total)}`).join(' y ')}: escribe solo lo que falte.`
    : `Eso ya está guardado: escribe solo lo nuevo de ${verbo === 'cosechar' ? 'la cosecha' : 'este final'}.`;
  return {
    id: 'gastos-finales',
    pregunta: `¿Cuánto gastaste al ${verbo}?`,
    ayuda: ya.total > 0
      ? `Solo lo nuevo, en soles: lo de antes ya está guardado (${formatearMoneda(ya.total)}). En la mano de obra escribe cuántos peones trabajaron, cuántos días y cuánto le pagaste a cada uno por día. Si no gastaste más, déjalo vacío.`
      : 'En soles. En la mano de obra escribe cuántos peones trabajaron, cuántos días y cuánto le pagaste a cada uno por día. Si no gastaste en algo, déjalo vacío.',
    Contenido: ({ estado, cambiar }) => (
      <div className="asistente-gastos">
        {ya.total > 0 && <GastosYaRegistrados etapas={ya.etapas} total={ya.total} nota={nota} />}
        {etapas.length
          ? <CamposGastos etapas={etapas} valores={estado.gastosFinalValores} catalogo={catalogo} alCambiar={(gastosFinalValores) => cambiar({ gastosFinalValores })} />
          : <p className="muted">No pudimos traer las etapas: anota tus gastos en tu siembra.</p>}
      </div>
    ),
    recoger: (e) => ({ gastosFinal: recogerGastos(etapas, e.gastosFinalValores) }),
    validar: (e) => validarGastos(e.gastosFinal),
    resumen: (e) => ({ etiqueta: 'Gastos del final', valor: resumenGastos(e.gastosFinal), icono: 'coin' }),
  };
}

function pasosAsistenteCosecha(registro: RegistroProduccion, chacraPorId: (id: string | null) => Lugar | null, catalogo: CatalogoPlantillas | null): Paso<EstadoCosecha>[] {
  const palabras = palabrasDe(registro.tipo_produccion);
  const hoy = new Date();
  const unidad = registro.unidad_medida || 'kg';
  const nombreUnidad = ({ kg: 'kilos', unidad: 'unidades', litro: 'litros' } as Record<string, string>)[unidad] || unidadPlural(unidad, 2);
  const siembra = String(registro.fecha_siembra || '').slice(0, 10);
  return [
    pasoUbicacion<EstadoCosecha>(palabras.accionFin, 'cosecha', chacraPorId),
    {
      id: 'foto',
      pregunta: palabras.foto,
      ayuda: 'Toca el botón grande: se abre la cámara del teléfono. Toma la foto ahí mismo, donde se vea lo que '
        + 'obtuviste. La foto queda en el certificado que ve el comprador.',
      Contenido: PasoFoto,
      validar: (e) => (e.archivoFoto || e.fotoGuardadaUrl ? null : 'Falta la foto: tómala aquí, donde está lo que obtuviste.'),
      resumen: (e) => ({ etiqueta: 'Foto', valor: e.archivoFoto ? 'Foto tomada' : 'Foto ya guardada', icono: 'camera' }),
    },
    {
      id: 'cantidad',
      pregunta: palabras.cuanto,
      ayuda: unidad === 'kg'
        ? 'Escribe o di cuántos kilos fueron. No tiene que ser exacto: en el almacén lo vuelven a pesar.'
        : `Escribe o di cuántos ${nombreUnidad} fueron.`,
      Contenido: ({ estado, cambiar }) => (
        <CampoNumero id="asistente-cantidad" etiqueta={`Cantidad en ${nombreUnidad}`} valor={estado.cantidad} minimo={0} maximo={1000000} paso={10} unidad={unidadCorta(unidad)} alCambiar={(cantidad) => cambiar({ cantidad })} />
      ),
      validar: (e) => {
        const n = Number(e.cantidad);
        return e.cantidad !== '' && n > 0 && n <= 1000000 ? null : `Escribe cuántos ${nombreUnidad} fueron (más de cero).`;
      },
      resumen: (e) => ({ etiqueta: palabras.resumenCuanto, valor: `${String(Number(e.cantidad)).replace('.', ',')} ${nombreUnidad}`, icono: 'basket' }),
    },
    {
      id: 'fecha',
      pregunta: palabras.cuando,
      ayuda: 'Toca "Hoy", "Ayer" u otra opción, o elige el día en el calendario.',
      Contenido: ({ estado, cambiar }) => (
        <CampoFecha id="asistente-fecha-cosecha-real" valor={estado.fecha} minimo={siembra} maximo={fechaIso(hoy)} alCambiar={(fecha) => cambiar({ fecha })} atajos={[
          { etiqueta: 'Hoy', fecha: fechaIso(hoy) },
          { etiqueta: 'Ayer', fecha: fechaIso(sumarDias(hoy, -1)) },
          { etiqueta: 'Hace 2 días', fecha: fechaIso(sumarDias(hoy, -2)) },
          { etiqueta: 'Hace una semana', fecha: fechaIso(sumarDias(hoy, -7)) },
        ].filter((a) => !siembra || a.fecha >= siembra)} />
      ),
      validar: (e) => {
        if (!e.fecha) return 'Elige el día.';
        if (siembra && e.fecha < siembra) return 'No puede ser antes del inicio.';
        if (e.fecha > fechaIso(hoy)) return 'No puede ser en el futuro.';
        return null;
      },
      resumen: (e) => ({ etiqueta: palabras.resumenCuando, valor: fechaLegible(e.fecha), icono: 'calendar' }),
    },
    registro.plantilla ? pasoGastosFinales(registro, catalogo) : {
      id: 'mano-obra',
      pregunta: '¿Cuánto pagaste por la mano de obra?',
      ayuda: 'Solo la mano de obra: lo que pagaste a quienes te ayudaron a sembrar, cuidar y cosechar, en soles. Si trabajaste solo con tu familia, deja cero.',
      Contenido: ({ estado, cambiar }) => {
        const ya = gastosYaRegistrados([
          { clave: 'semillas', nombre: 'Semillas', total: registro.costo_semillas ?? 0, momento: '' },
          { clave: 'insumos', nombre: 'Insumos', total: registro.costo_insumos ?? 0, momento: '' },
        ]);
        return (
          <>
            {ya.total > 0 && <div className="asistente-gastos"><GastosYaRegistrados etapas={ya.etapas} total={ya.total} nota="Eso ya está guardado: aquí va solo la mano de obra." /></div>}
            <CampoNumero id="asistente-mano-obra" etiqueta="Mano de obra" valor={estado.manoObra} minimo={0} maximo={9999999.99} paso={10} prefijo="S/" alCambiar={(manoObra) => cambiar({ manoObra })} />
          </>
        );
      },
      validar: (e) => {
        const n = Number(e.manoObra || 0);
        return Number.isFinite(n) && n >= 0 && n <= 9999999.99 ? null : 'El monto debe ser desde cero, sin signos.';
      },
      resumen: (e) => ({ etiqueta: 'Mano de obra', valor: formatearMoneda(Number(e.manoObra || 0)), icono: 'users' }),
    },
  ];
}

export function AsistenteCosecha({ registro, chacras, catalogo, alCerrar }: { registro: RegistroProduccion; chacras: Lugar[]; catalogo: CatalogoPlantillas | null; alCerrar: () => void }) {
  const queryClient = useQueryClient();
  const fotoSubida = useRef<string | null>(null);
  const chacraPorId = (id: string | null) => chacras.find((c) => String(c.id) === String(id)) || null;
  const [config] = useState<ConfigAsistente<EstadoCosecha>>(() => {
    const palabras = palabrasDe(registro.tipo_produccion);
    return {
      titulo: `${palabras.tituloFin} · ${registro.cultivo}`,
      icono: 'basket-check',
      textoFinal: palabras.confirmar,
      estadoInicial: {
        chacraId: registro.chacra_id,
        archivoFoto: null,
        errorFoto: '',
        fotoGuardadaUrl: registro.foto_cosecha_url || null,
        cantidad: registro.cantidad_cosechada != null ? String(Number(registro.cantidad_cosechada)) : '',
        fecha: registro.fecha_cosecha_real ? String(registro.fecha_cosecha_real).slice(0, 10) : '',
        manoObra: registro.costo_mano_obra != null ? String(Number(registro.costo_mano_obra)) : '0',
        gastosFinalValores: {},
      },
      pasos: pasosAsistenteCosecha(registro, chacraPorId, catalogo),
      alCerrar,
      enviar: async (estado, ui) => {
        try {
          // Un reintento (por ejemplo, si falló la confirmación) no vuelve a subir la misma foto.
          if (estado.archivoFoto && fotoSubida.current !== firmaArchivo(estado.archivoFoto)) {
            ui.progreso('Subiendo la foto…');
            await productores.produccion.subirFotoCosecha(registro.id, estado.archivoFoto);
            fotoSubida.current = firmaArchivo(estado.archivoFoto);
          }
          ui.progreso('Confirmando la cosecha…');
          await productores.produccion.completarCosecha(registro.id, {
            cantidad_cosechada: Number(estado.cantidad),
            fecha_cosecha_real: estado.fecha,
            ...(registro.plantilla ? { gastos: gastosParaEnviar(estado.gastosFinal) } : { costo_mano_obra: Number(estado.manoObra || 0) }),
            ...(estado.ubicacion ? { latitud: estado.ubicacion.lat, longitud: estado.ubicacion.lng } : {}),
          });
        } catch (err) {
          console.error('completar la cosecha', err);
          ui.error((err as Error).message || 'No se pudo completar la cosecha. Intenta de nuevo.');
          return false;
        }
        toast('Listo ✅ Tu producto ya está en "Mis productos" como borrador: ponle precio y publícalo.');
        if (Voz.leerSolo) Voz.hablar('Listo. Quedó registrado. Ahora ponle precio a tu producto en Mis productos.');
        void queryClient.invalidateQueries({ queryKey: ['productor'] });
        return true;
      },
    };
  });
  return <Asistente config={config} />;
}
