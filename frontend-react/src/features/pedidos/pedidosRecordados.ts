// Los pedidos que este navegador recuerda: solo se usan si no se pudo consultar la cuenta. La clave es la del frontend actual.
const clave = (usuarioId: string) => `agro_pedidos_${usuarioId}`;

export function leerPedidosRecordados(usuarioId: string | null | undefined): string[] {
  if (!usuarioId) return [];
  try {
    return JSON.parse(localStorage.getItem(clave(usuarioId)) as string) || [];
  } catch {
    return [];
  }
}

export function guardarPedidoRecordado(usuarioId: string | null | undefined, pedidoId: string): void {
  if (!usuarioId) return;
  const lista = leerPedidosRecordados(usuarioId);
  if (lista.includes(pedidoId)) return;
  try {
    localStorage.setItem(clave(usuarioId), JSON.stringify([pedidoId, ...lista]));
  } catch { /* sin almacenamiento disponible no se recuerda */ }
}
