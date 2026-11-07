import { certificacion } from './certificacion';
import { chacras } from './chacras';
import { ganancias } from './ganancias';
import { notificaciones } from './notificaciones';
import { pagos } from './pagos';
import { pedidos } from './pedidos';
import { productores } from './productores';
import { productos } from './productos';
import { recojos } from './recojos';
import { repartidores } from './repartidores';
import { transporte } from './transporte';
import { usuarios } from './usuarios';
import { ventas } from './ventas';

export { ApiError, MENSAJE_SESION_EXPIRADA } from './errors';

// La misma forma que el objeto Api de frontend/js/api.js.
export const Api = {
  usuarios,
  productores,
  chacras,
  productos,
  pedidos,
  transporte,
  recojos,
  repartidores,
  notificaciones,
  pagos,
  ventas,
  ganancias,
  certificacion,
};
