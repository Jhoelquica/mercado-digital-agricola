import { request, requestBlob, requestFormData } from './client';
import type { components } from './esquemas/usuarios';

type Esquemas = components['schemas'];

export interface Token {
  access_token: string;
  token_type: string;
}

export type TipoComprador = 'persona' | 'negocio';

// Los datos de negocio solo vienen en la cuenta de un comprador (ver _perfil en usuarios/main.py).
export interface Perfil {
  id: string;
  nombre: string;
  email: string;
  rol: string;
  tipo_comprador?: TipoComprador;
  ruc?: string | null;
  nombre_comercial?: string | null;
  razon_social?: string | null;
  ruc_verificado_en?: string | null;
}

export interface ReclamoRuc {
  id: string;
  ruc: string;
  razon_social: string | null;
  estado: string;
  motivo_rechazo: string | null;
  fecha_creacion: string;
  fecha_revision: string | null;
}

export interface MisReclamosRuc {
  mis_reclamos: ReclamoRuc[];
  rucs_que_perdi: { ruc: string; razon_social: string | null; fecha_revision: string | null }[];
}

export interface DatosDeCuenta {
  id: string;
  nombre: string;
  email: string;
  tipo_comprador: string | null;
  ruc: string | null;
  nombre_comercial: string | null;
  fecha_registro: string | null;
  ruc_verificado_en: string | null;
}

export interface ReclamoRucAdmin extends ReclamoRuc {
  nombre_titular: string;
  dni: string;
  mensaje: string | null;
  documentos: { ficha: string; dni: string };
  reclamante: DatosDeCuenta | null;
  titular_actual: DatosDeCuenta | null;
}

export interface DatosReclamoRuc {
  ruc: string;
  nombreTitular: string;
  dni: string;
  mensaje?: string;
  fichaRuc: File;
  fotoDni: File;
}

export const usuarios = {
  registrar: (datos: Esquemas['UsuarioRegistro']) => request<Perfil>('usuarios', '/usuarios/registro', { method: 'POST', body: datos }),
  login: (datos: Esquemas['UsuarioLogin']) => request<Token>('usuarios', '/usuarios/login', { method: 'POST', body: datos }),
  obtener: (id: string) => request<Perfil>('usuarios', `/usuarios/${id}`, { auth: true }),
  // El comprador cambia si compra como persona o como negocio (con RUC y nombre comercial opcionales).
  actualizarComprador: (datos: Esquemas['PerfilCompradorActualizar']) => request<Perfil>('usuarios', '/usuarios/me/comprador', { method: 'PATCH', body: datos, auth: true }),
  // Reclamar un RUC que ya está en otra cuenta: datos del dueño o representante + Ficha RUC + foto del DNI.
  reclamarRuc: ({ ruc, nombreTitular, dni, mensaje, fichaRuc, fotoDni }: DatosReclamoRuc) => {
    const formData = new FormData();
    formData.append('ruc', ruc);
    formData.append('nombre_titular', nombreTitular);
    formData.append('dni', dni);
    formData.append('mensaje', mensaje || '');
    formData.append('ficha_ruc', fichaRuc);
    formData.append('foto_dni', fotoDni);
    return requestFormData<ReclamoRuc>('usuarios', '/usuarios/me/reclamos-ruc', formData);
  },
  misReclamosRuc: () => request<MisReclamosRuc>('usuarios', '/usuarios/me/reclamos-ruc', { auth: true }),
  admin: {
    reclamosRuc: (estado = 'pendiente') => request<ReclamoRucAdmin[]>('usuarios', `/usuarios/admin/reclamos-ruc?estado=${encodeURIComponent(estado)}`, { auth: true }),
    documentoReclamo: (id: string, cual: 'ficha' | 'dni') => requestBlob('usuarios', `/usuarios/admin/reclamos-ruc/${id}/documento/${cual}`),
    aprobarReclamo: (id: string) => request<ReclamoRuc>('usuarios', `/usuarios/admin/reclamos-ruc/${id}/aprobar`, { method: 'POST', auth: true }),
    rechazarReclamo: (id: string, motivo: string) => request<ReclamoRuc>('usuarios', `/usuarios/admin/reclamos-ruc/${id}/rechazar`, { method: 'POST', body: { motivo } satisfies Esquemas['RechazoReclamo'], auth: true }),
  },
  // rol solo aplica si el id_token no coincide con ninguna cuenta existente (registro nuevo); el backend aplica el mismo
  // filtro de roles que en registrar (ver POST /usuarios/oauth/google).
  oauthGoogle: (datos: Esquemas['UsuarioOAuthGoogle']) => request<Token>('usuarios', '/usuarios/oauth/google', { method: 'POST', body: datos }),
};
