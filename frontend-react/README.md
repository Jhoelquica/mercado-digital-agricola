# ChakraShop: frontend en React

Migración de `frontend/` a React + Vite + TypeScript, por fases. Por ahora hay base y login; las pantallas son marcadores.

## Probar en local

Con el clúster arriba (ver el README de la raíz), desde esta carpeta:

```bash
npm install
npm run dev
```

Abre <http://localhost:5173>. El proxy de Vite lleva `/api` y `/media` a Traefik en `localhost:8000`, así que no hace
falta ningún `.env` para desarrollar. Las variables están en `.env.example`.

## Comprobar antes de subir

```bash
npm run build
```

Revisa los tipos con las tres configuraciones (aplicación, service worker y Vite) y genera `dist/`.

## Tipos de la API

- `src/api/esquemas/` son los cuerpos que acepta cada servicio, generados de su OpenAPI. Con el clúster arriba, se
  regeneran con `node scripts/generar-esquemas.mjs`.
- Las respuestas están escritas a mano en cada dominio de `src/api/`, leídas del código de cada servicio: la mayoría de
  las rutas no declara `response_model`, así que su OpenAPI no las describe. Si cambia lo que devuelve un servicio, hay
  que actualizar su tipo a mano.
