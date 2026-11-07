// Dibuja el asistente que esté abierto (lo abren Inicio y "Mis siembras" con abrirAsistenteSiembra/abrirAsistenteCosecha).
import { useEffect, useSyncExternalStore } from 'react';
import { useNavigate } from 'react-router';
import { toast } from '../../../components/Toast';
import { hayCoordenadas } from '../../../lib/productos';
import { useChacras, usePerfilProductor, usePlantillas, useRegistros } from '../comun';
import { cerrarAsistente, leerAsistente, suscribirAsistente } from './abrir';
import { AsistenteCosecha } from './AsistenteCosecha';
import { AsistenteSiembra, ID_CHACRA_DEL_PERFIL } from './AsistenteSiembra';
import type { Lugar } from './piezas';

export function AsistentesDelAgricultor() {
  const abierto = useSyncExternalStore(suscribirAsistente, leerAsistente);
  const navegar = useNavigate();
  const perfil = usePerfilProductor();
  const chacras = useChacras(Boolean(abierto));
  const registros = useRegistros(Boolean(abierto));
  const catalogo = usePlantillas();
  const cargando = Boolean(abierto) && (perfil.isLoading || chacras.isLoading || registros.isLoading || catalogo.isLoading);
  const lista: Lugar[] = chacras.data ?? [];

  // La parcela del perfil, ofrecida como chacra si todavía no registró ninguna.
  const p = perfil.data;
  const chacraDelPerfil: Lugar | null = !lista.length && p && hayCoordenadas(p.latitud, p.longitud)
    ? { id: ID_CHACRA_DEL_PERFIL, codigo: 'Mi chacra registrada', nombre: 'la que marcaste en tu perfil', ubicacion_latitud: Number(p.latitud), ubicacion_longitud: Number(p.longitud) }
    : null;
  const registro = abierto?.tipo === 'cosecha' ? (registros.data ?? []).find((r) => String(r.id) === String(abierto.registroId)) : undefined;
  const sinLugar = abierto?.tipo === 'siembra' && !cargando && !lista.length && !chacraDelPerfil;
  const sinRegistro = abierto?.tipo === 'cosecha' && !cargando && !registro;

  useEffect(() => {
    if (sinLugar) {
      toast('Primero registra tu chacra en "Mis chacras".', 'error');
      cerrarAsistente();
      void navegar('/productor/chacras');
    } else if (sinRegistro) {
      toast('No se encontró esa siembra. Recarga la página.', 'error');
      cerrarAsistente();
    }
  }, [sinLugar, sinRegistro, navegar]);

  if (!abierto || cargando || sinLugar || sinRegistro) return null;
  if (abierto.tipo === 'siembra') return <AsistenteSiembra chacras={lista} chacraDelPerfil={chacraDelPerfil} catalogo={catalogo.data ?? null} alCerrar={cerrarAsistente} />;
  return registro ? <AsistenteCosecha key={registro.id} registro={registro} chacras={lista} catalogo={catalogo.data ?? null} alCerrar={cerrarAsistente} /> : null;
}
