import { request, requestBlob, requestFormData } from './client';
import type { components } from './esquemas/pagos';

type Esquemas = components['schemas'];

export interface CondicionesDePago {
  comision_plataforma_porcentaje: number;
  yape_maximo_por_operacion: number;
  // null si la plataforma no tiene una cuenta configurada para transferir.
  transferencia: { banco: string; titular: string; cuenta: string; cci: string } | null;
}

// La fila de la base más lo que el comprador tiene que pagar ahora (null si ya está todo pagado).
export interface Pago {
  id: string;
  pedido_id: string;
  monto: number;
  estado: string | null;
  metodo: string | null;
  culqi_charge_id: string | null;
  fecha_creacion: string | null;
  items: string | null;
  numero_operacion: string | null;
  constancia_objeto: string | null;
  constancia_tipo: string | null;
  fecha_constancia: string | null;
  motivo_rechazo: string | null;
  revisado_por: string | null;
  fecha_revision: string | null;
  monto_adelanto: number | null;
  fecha_adelanto: string | null;
  metodo_saldo: string | null;
  culqi_charge_id_saldo: string | null;
  fecha_saldo: string | null;
  parte_en_revision: string | null;
  vence_en: string | null;
  vencido_en: string | null;
  monto_reembolsado: number | null;
  reembolso_manual: number | null;
  reembolso_ids: string | null;
  motivo_reembolso: string | null;
  fecha_reembolso: string | null;
  parte_a_pagar: string | null;
  monto_a_pagar: number | null;
}

export interface ResultadoCobro {
  estado: string | null;
  parte: string;
  detalle: unknown;
}

export interface TransferenciaEnviada {
  estado: string;
  pedido_id: string;
  numero_operacion: string;
  parte: string;
}

export interface TransferenciaEnRevision {
  pedido_id: string;
  monto: number;
  parte: string;
  monto_total: number;
  estado: string | null;
  numero_operacion: string | null;
  fecha_constancia: string | null;
  motivo_rechazo: string | null;
}

export const pagos = {
  procesar: (datos: Esquemas['ProcesarPago']) => request<ResultadoCobro>('pagos', '/pagos/procesar', { method: 'POST', body: datos, auth: true }),
  obtener: (pedidoId: string) => request<Pago>('pagos', `/pagos/${pedidoId}`, { auth: true }),
  // Público: la comisión de la plataforma, el límite de Yape y la cuenta para transferir (null si no hay).
  condiciones: () => request<CondicionesDePago>('pagos', '/pagos/condiciones'),
  // El comprador sube la constancia de su transferencia: el pago queda "en revisión" hasta que un admin la revisa.
  enviarTransferencia: (pedidoId: string, numeroOperacion: string, archivo: File) => {
    const formData = new FormData();
    formData.append('pedido_id', pedidoId);
    formData.append('numero_operacion', numeroOperacion);
    formData.append('archivo', archivo);
    return requestFormData<TransferenciaEnviada>('pagos', '/pagos/transferencia', formData);
  },
  admin: {
    transferencias: () => request<TransferenciaEnRevision[]>('pagos', '/pagos/admin/transferencias', { auth: true }),
    constancia: (pedidoId: string) => requestBlob('pagos', `/pagos/admin/transferencias/${pedidoId}/constancia`),
    aprobar: (pedidoId: string) => request<TransferenciaEnRevision>('pagos', `/pagos/admin/transferencias/${pedidoId}/aprobar`, { method: 'POST', auth: true }),
    rechazar: (pedidoId: string, motivo: string) => request<TransferenciaEnRevision>('pagos', `/pagos/admin/transferencias/${pedidoId}/rechazar`, { method: 'POST', body: { motivo } satisfies Esquemas['RechazarTransferencia'], auth: true }),
  },
};
