import { ApiError } from '../api/errors';
import { rucValido } from './reglas';

export type TipoComprador = 'persona' | 'negocio';

// Los datos de persona/negocio que se mandan al registrarse o al guardar el perfil: { datos } o { error }. Un negocio
// tiene que dar su RUC: Usuarios lo verifica en SUNAT (activo y habido) antes de crear o cambiar la cuenta.
export function datosDeComprador(tipo: TipoComprador, nombreComercial: string, ruc: string) {
  if (tipo !== 'negocio') return { datos: { tipo_comprador: 'persona' as const } };
  const rucLimpio = ruc.replace(/\s+/g, '');
  if (!rucLimpio) return { error: 'Para comprar como negocio pon el RUC de tu negocio: lo verificamos en SUNAT.' };
  if (!rucValido(rucLimpio)) return { error: 'El RUC no es válido: son 11 dígitos y empieza con 10 o 20. Revisa que esté bien escrito.' };
  return { datos: { tipo_comprador: 'negocio' as const, nombre_comercial: nombreComercial.trim() || null, ruc: rucLimpio } };
}

// El RUC que el comprador quiere ya está en otra cuenta (Usuarios responde 409 con el qué hacer): se ofrece reclamarlo.
// Devuelve ese RUC, o null si el error es otro.
export function rucOcupadoDe(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 409 || !/ya está registrado en otra cuenta/.test(err.message)) return null;
  return (err.message.match(/RUC (\d{11})/) || [])[1] || '';
}
