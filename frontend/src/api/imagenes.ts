// Las fotos de producto (y las de las cosechas) se guardan en la base con la URL completa que tenía MinIO al subirlas
// (por defecto http://localhost:9000/productos-imagenes/...): sirve en tu propia máquina, pero no en la de nadie más ni
// detrás de un túnel o dominio distinto. Por el gateway existe /media/productos-imagenes/... (ver k8s/traefik-routes.yaml)
// que sirve las mismas fotos por el mismo origen de la página: se reescribe cualquier URL de esos buckets para que apunte ahí.
export function normalizarUrlImagen<T extends string | null | undefined>(url: T): T | string {
  if (!url) return url;
  try {
    const u = new URL(url, location.origin);
    if (u.pathname.startsWith('/productos-imagenes/') || u.pathname.startsWith('/cosechas-evidencias/')) {
      return `${location.origin}/media${u.pathname}`;
    }
  } catch { /* url rara o relativa: se deja tal cual */ }
  return url;
}

interface ConImagenes {
  imagen_url?: string | null;
  imagen_principal?: string | null;
  imagenes?: { url?: string | null }[];
}

export function normalizarImagenesDeProducto<T extends ConImagenes>(p: T): T {
  if (!p) return p;
  if (p.imagen_url) p.imagen_url = normalizarUrlImagen(p.imagen_url);
  if (p.imagen_principal) p.imagen_principal = normalizarUrlImagen(p.imagen_principal);
  if (Array.isArray(p.imagenes)) p.imagenes.forEach((img) => { if (img.url) img.url = normalizarUrlImagen(img.url); });
  return p;
}
