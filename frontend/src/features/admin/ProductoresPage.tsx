// /admin/productores: los productores registrados (solo lectura), con su contacto, cuántos productos publicados tienen y
// un mapa de dónde están. Se buscan por nombre, comunidad o contacto.
import { lazy, Suspense, useState } from 'react';
import { SkeletonFilas } from '../../components/SkeletonFilas';
import { formatearFechaCortaServidor } from '../../lib/formato';
import { hayCoordenadas } from '../../lib/productos';
import type { Productor } from '../../api/productores';
import { EncabezadoSeccion, useProductoresAdmin, useRefrescarAdmin, Vacio } from './comun';

const MapaSoloLectura = lazy(() => import('../../components/Mapa').then((m) => ({ default: m.MapaSoloLectura })));

function TarjetaProductor({ p, total, indice }: { p: Productor; total: number; indice: number }) {
  const tel = String(p.contacto || '').replace(/\D/g, '');
  return (
    <div className="pedido-card productor-admin-card" style={{ animationDelay: `${Math.min(indice, 12) * 35}ms` }} data-productor-id={p.id}>
      <div className="pedido-card-header">
        <h4>{p.nombre}</h4>
        <span className={`pill pill-estado ${total ? 'pill-vigente' : 'pill-default'}`}><i className="ti ti-shopping-bag" /> {total} producto{total === 1 ? '' : 's'}</span>
      </div>
      <div className="perfil-datos-grid">
        <div className="perfil-dato"><span className="perfil-dato-label">Comunidad / Región</span><span className="perfil-dato-valor">{p.comunidad || '—'}</span></div>
        <div className="perfil-dato"><span className="perfil-dato-label">Contacto</span><span className="perfil-dato-valor">{tel ? <a className="productor-admin-tel" href={`tel:${tel}`}>{p.contacto}</a> : '—'}</span></div>
        <div className="perfil-dato"><span className="perfil-dato-label">Registrado</span><span className="perfil-dato-valor">{formatearFechaCortaServidor(p.fecha_registro)}</span></div>
      </div>
      {hayCoordenadas(p.latitud, p.longitud)
        ? <Suspense fallback={<div className="chacra-card-mapa-mini" />}><MapaSoloLectura latitud={p.latitud as string} longitud={p.longitud as string} popup={p.nombre} etiqueta={`Ubicación de ${p.nombre}`} icono="tractor" clase="chacra-card-mapa-mini" /></Suspense>
        : <div className="mapa-bloque-neutro"><span className="icon"><i className="ti ti-map-pin" /></span> Sin ubicación registrada.</div>}
    </div>
  );
}

export default function ProductoresPage() {
  const consulta = useProductoresAdmin();
  const refrescar = useRefrescarAdmin();
  const [termino, setTermino] = useState('');
  const lista = consulta.data ?? [];
  const buscado = termino.trim().toLowerCase();
  const filtrados = lista.filter((item) => !buscado || [item.p.nombre, item.p.comunidad, item.p.contacto].some((v) => String(v || '').toLowerCase().includes(buscado)));
  return (
    <div className="admin-tab-panel active" data-admin-panel="productores">
      <EncabezadoSeccion icono="tractor" titulo={<>Productores registrados {lista.length > 0 && <span id="admin-productores-total" className="badge-contador">{lista.length}</span>}</>} alRefrescar={() => { void refrescar('productores'); }} />
      <div className="admin-buscador">
        <i className="ti ti-search" />
        <input type="search" id="admin-productores-buscar" placeholder="Buscar por nombre, comunidad o contacto…" aria-label="Buscar productores" autoComplete="off" value={termino} onChange={(e) => setTermino(e.target.value)} />
      </div>
      <div id="admin-productores-lista" className="grid-admin">
        {consulta.isFetching && !consulta.data ? <SkeletonFilas cantidad={2} /> : filtrados.map((item, i) => <TarjetaProductor key={item.p.id} p={item.p} total={item.total} indice={i} />)}
      </div>
      {consulta.data && !filtrados.length && <Vacio icono="tractor" titulo="No hay productores para mostrar" detalle="Cuando alguien complete su perfil de productor, aparece acá." />}
    </div>
  );
}
