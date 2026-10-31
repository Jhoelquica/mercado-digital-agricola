import { leerSesion } from '../auth/sesion';

// Favoritos en este navegador, por cuenta. Misma clave que el frontend actual.
function claveFavoritos(): string {
  return `agro_favoritos_${leerSesion().usuarioId || 'invitado'}`;
}

function obtenerFavoritos(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(claveFavoritos()) || '[]') as string[]);
  } catch {
    return new Set();
  }
}

export function esFavorito(productoId: string): boolean {
  return obtenerFavoritos().has(productoId);
}

export function alternarFavorito(productoId: string): boolean {
  const favoritos = obtenerFavoritos();
  const yaEstaba = favoritos.has(productoId);
  if (yaEstaba) favoritos.delete(productoId);
  else favoritos.add(productoId);
  try { localStorage.setItem(claveFavoritos(), JSON.stringify([...favoritos])); } catch { /* sin almacenamiento: solo esta vez */ }
  return !yaEstaba;
}
