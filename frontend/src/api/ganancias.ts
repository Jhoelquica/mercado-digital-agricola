import { request } from './client';
import type { components } from './esquemas/pagos';

type Esquemas = components['schemas'];

export interface CuentaCobro {
  yape_celular: string | null;
  yape_titular: string | null;
  banco: string | null;
  cuenta_titular: string | null;
  cci: string | null;
  fecha_actualizacion: string | null;
}

export interface PagoHecho {
  id: string;
  beneficiario_tipo: string;
  beneficiario_id: string;
  nombre: string | null;
  monto: number;
  metodo: string;
  destino: string;
  numero_operacion: string;
  cantidad: number;
  hasta: string;
  fecha: string;
}

export interface MovimientoGanancia {
  id: string;
  pedido_id: string;
  concepto: string;
  estado: string;
  fecha: string | null;
  cumplido_en: string | null;
  se_paga_el: string | null;
  pagado_el: string | null;
  monto_bruto: number;
  comision: number;
  descuento_recojo: number | null;
  monto_neto: number;
}

export interface MisGanancias {
  beneficiario: { tipo: string; id: string; nombre: string };
  cuenta: CuentaCobro | null;
  resumen: { por_cobrar: number; esperando_entrega: number; cobrado: number; deuda_recojos: number };
  proximos_pagos: { fecha: string; monto: number }[];
  movimientos: MovimientoGanancia[];
  pagos: PagoHecho[];
  yape_maximo_por_operacion: number;
  bancos: string[];
}

export interface BeneficiarioPorPagar {
  beneficiario_tipo: string;
  beneficiario_id: string;
  nombre: string | null;
  cuenta: CuentaCobro | null;
  cantidad_a_pagar: number;
  a_pagar: number;
  esta_semana: number;
  esperando_entrega: number;
}

export interface PorPagar {
  corte: string;
  dia_de_pago: string;
  yape_maximo_por_operacion: number;
  total_a_pagar: number;
  beneficiarios: BeneficiarioPorPagar[];
}

// Agricultor o repartidor: lo que gana, qué lunes se le paga y cómo cobra. El admin registra el pago semanal.
export const ganancias = {
  mias: () => request<MisGanancias>('pagos', '/ganancias/me', { auth: true }),
  guardarCuenta: (datos: Esquemas['CuentaCobroDatos']) => request<CuentaCobro>('pagos', '/ganancias/me/cuenta', { method: 'PUT', body: datos, auth: true }),
  admin: {
    porPagar: () => request<PorPagar>('pagos', '/ganancias/admin/por-pagar', { auth: true }),
    // Es la única ruta de este dominio que está en Transporte: { id del repartidor: nombre }.
    nombresRepartidores: (ids: string[]) => request<Record<string, string>>('transporte', `/repartidores/admin/nombres?ids=${ids.map(encodeURIComponent).join(',')}`, { auth: true }),
    pagar: (tipo: string, id: string, datos: Esquemas['PagoABeneficiario']) => request<PagoHecho>('pagos', `/ganancias/admin/${tipo}/${id}/pagar`, { method: 'POST', body: datos, auth: true }),
    pagosHechos: () => request<PagoHecho[]>('pagos', '/ganancias/admin/pagos-hechos', { auth: true }),
  },
};
