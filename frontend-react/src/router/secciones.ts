// Las pestañas de cada panel. Es la fuente tanto de las rutas (router/index.tsx) como de la navegación (PanelLayout).
export interface SeccionDePanel {
  ruta: string;
  etiqueta: string;
  icono: string;
  claveIdioma?: string;
}

export const SECCIONES_COMPRADOR: SeccionDePanel[] = [
  { ruta: 'pedidos', etiqueta: 'Mis Pedidos', icono: 'package' },
  { ruta: 'checkout', etiqueta: 'Tu pedido', icono: 'shopping-cart' },
];

export const SECCIONES_PRODUCTOR: SeccionDePanel[] = [
  { ruta: '', etiqueta: 'Inicio', icono: 'home', claveIdioma: 'tab.inicio' },
  { ruta: 'que-sembrar', etiqueta: '¿Qué sembrar?', icono: 'bulb', claveIdioma: 'tab.que_sembrar' },
  { ruta: 'siembras', etiqueta: 'Mis siembras', icono: 'plant-2', claveIdioma: 'tab.siembras' },
  { ruta: 'chacras', etiqueta: 'Mis Chacras', icono: 'map-pin', claveIdioma: 'tab.chacras' },
  { ruta: 'borradores', etiqueta: 'Borradores', icono: 'pencil', claveIdioma: 'tab.borradores' },
  { ruta: 'productos', etiqueta: 'Mis Productos', icono: 'shopping-bag', claveIdioma: 'tab.productos' },
  { ruta: 'ganancias', etiqueta: 'Mis ganancias', icono: 'wallet', claveIdioma: 'tab.ganancias' },
  { ruta: 'economia', etiqueta: 'Gestión Económica', icono: 'chart-bar', claveIdioma: 'tab.economia' },
];

export const SECCIONES_REPARTIDOR: SeccionDePanel[] = [
  { ruta: '', etiqueta: 'Mis envíos', icono: 'clipboard-list' },
  { ruta: 'recojos', etiqueta: 'Recojos en chacras', icono: 'plant-2' },
  { ruta: 'viajes', etiqueta: 'Mis viajes en camión', icono: 'truck' },
  { ruta: 'ganancias', etiqueta: 'Mis ganancias', icono: 'wallet' },
  { ruta: 'vehiculo', etiqueta: 'Mi vehículo', icono: 'moped' },
];

export const SECCIONES_ADMIN: SeccionDePanel[] = [
  { ruta: '', etiqueta: 'Inicio', icono: 'home' },
  { ruta: 'transferencias', etiqueta: 'Transferencias', icono: 'building-bank' },
  { ruta: 'pagos', etiqueta: 'Pagos por hacer', icono: 'cash' },
  { ruta: 'sin-entregar', etiqueta: 'Pedidos sin entregar', icono: 'package' },
  { ruta: 'reclamos-ruc', etiqueta: 'Reclamos de RUC', icono: 'file-certificate' },
  { ruta: 'transportistas', etiqueta: 'Transportistas', icono: 'id' },
  { ruta: 'viajes', etiqueta: 'Viajes en camión', icono: 'route' },
  { ruta: 'destinos', etiqueta: 'Destinos lejanos', icono: 'map-2' },
  { ruta: 'productores', etiqueta: 'Productores', icono: 'tractor' },
  { ruta: 'capacidad', etiqueta: 'Pendientes por Capacidad', icono: 'truck' },
  { ruta: 'transito', etiqueta: 'Productos en Tránsito', icono: 'building-warehouse' },
];
