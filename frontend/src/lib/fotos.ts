// Las fotos que se suben (constancia de transferencia, foto de la cosecha): mismos tipos y tamaño que acepta el backend.
export const FOTO_TIPOS_VALIDOS = ['image/jpeg', 'image/png', 'image/webp'];
export const FOTO_TAMANO_MAXIMO = 5 * 1024 * 1024;

export function formatearTamanoArchivo(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// El mensaje si la foto no sirve; null si sirve.
export function problemaDeFoto(archivo: File): string | null {
  if (!FOTO_TIPOS_VALIDOS.includes(archivo.type)) return 'Ese archivo no es una imagen JPG, PNG o WEBP. Elige otra foto.';
  if (archivo.size > FOTO_TAMANO_MAXIMO) return `La foto pesa ${formatearTamanoArchivo(archivo.size)} y el máximo es 5 MB. Toma otra más liviana.`;
  return null;
}
