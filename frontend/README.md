# ChakraShop: frontend

La aplicación web en React + Vite + TypeScript: la parte pública (landing, búsqueda, detalle, la página del QR en
/verificar), el acceso (correo o Google), la compra (carrito, checkout, Culqi con tarjeta o Yape, transferencia, Mis
Pedidos), Mi perfil, Notificaciones con los avisos al celular, y los paneles del productor, el repartidor y el admin.
Reemplazó al frontend anterior en HTML/JS (fases 0 a 8a de la rama `feat/frontend-react`; el anterior queda en el
historial de git).

En el clúster la sirve nginx (`Dockerfile`, `nginx.conf`, `security-headers.conf` con la CSP); el gateway Traefik le
manda todo lo que no es `/api/...` ni `/media/...`.

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
npm test
```

`npm run build` revisa los tipos con las tres configuraciones (aplicación, service worker y Vite) y genera `dist/`.
`npm test` corre con Vitest las pruebas de las reglas puras y del diccionario de quechua (`src/**/*.test.ts`); también las
corre `scripts/correr_pruebas.sh` en la raíz.

Con el servidor de desarrollo en marcha, `BASE=http://localhost:5173 npm run e2e:mapa` comprueba tres veces seguidas que
el mapa del detalle carga sus teselas.

Las demás pruebas de `e2e/` usan las cuentas de `.env.qa` (no se sube) y comparan textos normalizados (`e2e/comun.mjs`).
Corren contra Vite (`BASE=http://localhost:5173`, por defecto) o contra el clúster (`BASE=http://localhost:8000`).
Solo leen: `e2e:admin` (las 11 pantallas del admin a 375 y 1280 px), `e2e:notificaciones` y `e2e:viajes` (API
simulada). **Escriben datos reales en el clúster local:** `e2e:siembra`, `e2e:borrador`, `e2e:recojo`, `e2e:entrega`
(Yape, o `MEDIO=tarjeta`), `e2e:transferencia-viaje`, `e2e:camion-saldo` y `e2e:reclamo-pagos`; cada una dice al
empezar qué crea. La tarjeta de prueba de Culqi se lee de `QA_CULQI_TARJETA` en `.env.qa`.

`e2e:contorno` dibuja el contorno de la chacra CH-01 del agricultor QA en el editor satelital, lo guarda, lo comprueba en
la tarjeta y en Mi perfil y al final lo quita (no deja datos). Con el contorno guardado abre, como visitante sin sesión,
el detalle de un producto de esa chacra: debe decir su nombre, sus hectáreas y "Contorno registrado", con el mapa en la
zona aproximada y sin que ninguna respuesta de la red traiga el polígono ni coordenadas. `ANCHO=390` lo corre con el
ancho de un celular. Necesita internet (teselas de Esri) y deja capturas en `e2e/capturas/`, que no se versionan.

`e2e:geocerca` recorre la geocerca por contorno de punta a punta. Por la API le pone un contorno a la chacra CH-02 del
agricultor QA, registra una siembra fuera y una cosecha dentro, y publica el producto. Después comprueba la frase sellada
en la cadena, la página del QR ("Ubicación confirmada en la chacra" solo en la cosecha), Mis siembras y la etiqueta de la
llegada al almacén del admin. **Escribe datos reales y los borra al final** con los DELETE de limpieza QA, llamados
dentro de cada pod con `kubectl exec`. Queda un bloque en la cadena de certificación, que no se borra.

## Tipos de la API

- `src/api/esquemas/` son los cuerpos que acepta cada servicio, generados de su OpenAPI. Con el clúster arriba, se
  regeneran con `node scripts/generar-esquemas.mjs`.
- Las respuestas están escritas a mano en cada dominio de `src/api/`, leídas del código de cada servicio: la mayoría de
  las rutas no declara `response_model`, así que su OpenAPI no las describe. Si cambia lo que devuelve un servicio, hay
  que actualizar su tipo a mano.

## Pendientes de backend

- Añadir `response_model` a los endpoints para generar también los tipos de las respuestas, empezando por los de las
  pantallas que se migren. Cuando un endpoint lo tenga, su tipo escrito a mano se reemplaza por el generado.
