# ChakraShop: frontend en React

Migración de `frontend/` a React + Vite + TypeScript, por fases. Ya están la parte pública (landing, búsqueda, detalle,
/verificar), el login y el registro, la compra completa del comprador (carrito, checkout, pago con Culqi o por
transferencia y Mis Pedidos), Mi perfil de los 4 roles, Notificaciones con los avisos al celular y el acceso con Google.
Los paneles de productor, repartidor y admin todavía son marcadores.

## Probar en local

Con el clúster arriba (ver el README de la raíz), desde esta carpeta:

```bash
npm install
npm run dev
```

Abre <http://localhost:5173>. El proxy de Vite lleva `/api` y `/media` a Traefik en `localhost:8000`. Las variables están
en `.env.example`: para pagar con Culqi en local, copia el archivo a `.env.local` y pon la llave pública de pruebas
(`pk_test_...`). El script de Culqi se descarga recién al abrir el checkout. El botón de Google necesita además
`VITE_GOOGLE_CLIENT_ID` y que el origen (por ejemplo `http://localhost:5173`) esté autorizado en ese Client ID.

Los avisos al celular (push) necesitan el service worker, que el servidor de desarrollo no sirve: para probarlos,
`npm run build` y `npm run preview` (puerto 4173, con el mismo proxy a la API).

## Comprobar antes de subir

```bash
npm run build
```

Revisa los tipos con las tres configuraciones (aplicación, service worker y Vite) y genera `dist/`.

Con el servidor de desarrollo en marcha, `BASE=http://localhost:5173 npm run e2e:mapa` comprueba tres veces seguidas que
el mapa del detalle carga sus teselas.

Las demás pruebas de `e2e/` usan las cuentas de `.env.qa` (no se sube) y comparan textos normalizados (`e2e/comun.mjs`).
Solo leen: `e2e:admin` (las 11 pantallas del admin a 375 y 1280 px) y `e2e:viajes` (API simulada). **Escriben datos
reales en el clúster local:** `e2e:siembra`, `e2e:borrador`, `e2e:entrega`, `e2e:recojo`, `e2e:transferencia-viaje` y
`e2e:reclamo-pagos`; cada una dice al empezar qué crea.

## Tipos de la API

- `src/api/esquemas/` son los cuerpos que acepta cada servicio, generados de su OpenAPI. Con el clúster arriba, se
  regeneran con `node scripts/generar-esquemas.mjs`.
- Las respuestas están escritas a mano en cada dominio de `src/api/`, leídas del código de cada servicio: la mayoría de
  las rutas no declara `response_model`, así que su OpenAPI no las describe. Si cambia lo que devuelve un servicio, hay
  que actualizar su tipo a mano.

## Pendientes de backend

- Añadir `response_model` a los endpoints para generar también los tipos de las respuestas, empezando por los de las
  pantallas que se migren. Cuando un endpoint lo tenga, su tipo escrito a mano se reemplaza por el generado.
