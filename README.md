# ChakraShop — Mercado Digital Agrícola

Plataforma de venta directa del productor al comprador para Ayacucho (Perú). La componen **8 microservicios FastAPI**,
cada uno con su propia base PostgreSQL. Se comunican por HTTP (con una clave por cada par de servicios) y por eventos en
RabbitMQ.

- **Frontend:** React + Vite + TypeScript (`frontend/`), servido por nginx.
- **Gateway:** Traefik.
- **Trazabilidad:** una cadena de hashes por producto, verificable con un QR.
- **Despliegue:** Kubernetes (Docker Desktop en local y Google Kubernetes Engine en la nube).

| Servicio | Puerto | Qué hace | Base |
|---|---|---|---|
| `productores` | 8001 | Perfil del agricultor, chacras, siembras y cosechas con gastos por etapa, "¿qué sembrar?" | `db-productores` |
| `productos` | 8002 | Catálogo, borradores, fotos (MinIO), unidades de venta, reservas, reseñas, caché en Redis | `db-productos` |
| `pedidos` | 8003 | Pedidos, descuento de stock, costo de envío, compradores lejanos, ventas | `db-pedidos` |
| `transporte` | 8004 | Propuesta y asignación de repartidor, GPS, ruta, entrega con PIN y foto, recojos | `db-transporte` |
| `notificaciones` | 8005 | Avisos de cada usuario a partir de los eventos | `db-notificaciones` |
| `usuarios` | 8006 | Registro, login (JWT) y Google Sign-In; tipo de comprador y RUC | `db-usuarios` |
| `pagos` | 8007 | Culqi (tarjeta y Yape), transferencias, devoluciones, liquidaciones y pagos semanales | `db-pagos` |
| `certificacion` | 8008 | Cadena de certificación por producto, QR y certificado Merkle por pedido | `db-certificacion` |

Además corren: `frontend` (nginx), `traefik-gateway` (8000), RabbitMQ, Redis, MinIO (9000 y consola 9001), Prometheus
(9090) y Grafana (3000). En el host solo se publican el gateway (8000) y la API de MinIO (9000); los puertos de la tabla
son los de dentro del clúster.

## Requisitos

- **Docker Desktop** con **Kubernetes activado** (contexto `docker-desktop`) y `kubectl`.
- **Python 3.10+** para las semillas y los scripts (la semilla necesita `pip install httpx pillow`; los demás scripts, solo la biblioteca estándar).
- **Bash:** en Windows, Git Bash, para `scripts/correr_pruebas.sh`.

## Arranque local con Kubernetes

Desde la raíz del repositorio:

```bash
# 1. Registro local de imágenes (los manifiestos usan localhost:5000/<servicio>:latest)
docker run -d -p 5000:5000 --restart=always --name registry registry:2

# 2. CRDs de Traefik (IngressRoute, Middleware…), una sola vez por clúster
kubectl apply -f https://raw.githubusercontent.com/traefik/traefik/v3.7/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml

# 3. Credenciales: copiar la plantilla, poner valores propios y aplicarla
cp deploy/local/secret.ejemplo.yaml k8s/secret.yaml      # editar k8s/secret.yaml antes de seguir
kubectl apply -f k8s/secret.yaml

# 4. Construir y subir las 9 imágenes
for s in usuarios productores productos pedidos transporte notificaciones pagos certificacion frontend; do
  docker build -t localhost:5000/$s:latest $s && docker push localhost:5000/$s:latest
done

# 5. Todo lo demás y esperar a que esté listo
kubectl apply -f k8s/
kubectl wait --for=condition=Ready pod --all --timeout=300s

# 6. Comprobar (frontend, 8 servicios, rutas públicas, seguridad del gateway, pods y consumidores de RabbitMQ)
python scripts/verificar_despliegue.py --base http://localhost:8000 --contexto docker-desktop
```

**Dónde entrar:**
- **Sitio:** <http://localhost:8000>
- **APIs:** `http://localhost:8000/api/<servicio>/...`. El gateway es la única entrada: valida el JWT y limita las
  peticiones. Los servicios no se publican en el host (son ClusterIP), así que no se le puede dar la vuelta.
- **Grafana, Prometheus y la consola de MinIO:** tampoco se publican. Para desarrollo se abren solo en `127.0.0.1` con
  `kubectl port-forward`:

  ```powershell
  .\scripts\abrir-puertos.ps1                    # Grafana http://127.0.0.1:3000, Prometheus :9090 y consola de MinIO :9001
  .\scripts\abrir-puertos.ps1 -Solo servicios    # los 8 servicios en 127.0.0.1:8001..8008 (para depurar)
  .\scripts\abrir-puertos.ps1 -Estado            # qué está abierto
  .\scripts\abrir-puertos.ps1 -Cerrar            # cerrarlos
  ```

  Otros grupos: `base-productos` (127.0.0.1:15432, para `seed_imagenes.py`), `traefik` (panel en :18080) y `todo`. Grafana
  abre directo el tablero "ChakraShop — Microservicios" (tráfico, latencia p95 y errores 5xx de los 8 servicios).
  `seed_datos_demo.py` va por el gateway, y `frontend/scripts/generar-esquemas.mjs` lee dentro de cada pod: ninguno
  necesita abrir puertos.

> Tras encender Docker Desktop, el clúster tarda 1–2 minutos. Si el sitio muestra "404 page not found" (texto plano de
> Traefik), el frontend todavía está arrancando: espera al `kubectl wait` del paso 5.

### Datos de demostración

```bash
python seed_datos_demo.py --dry-run     # muestra el plan y revisa las fotos
python seed_datos_demo.py               # 12 agricultores con sus productos, por el flujo real
```

- La semilla crea `agricultor01@chakrashop-demo.pe` … `agricultor12@chakrashop-demo.pe` (rol productor). Sigue el flujo
  real: siembra, cosecha con foto, borrador, publicación y llegada al almacén, así que cada producto queda con su
  certificado.
- Para confirmar las llegadas al almacén pide la cuenta admin (`ADMIN_BOOTSTRAP_EMAIL` del Secret).
- La contraseña de las cuentas demo se define con `SEED_PASSWORD_DEMO`. Si no la defines, usa una clave por defecto que
  está en el código: **no la uses en un despliegue público**.
- Las fotos de la semilla (unos 374 MB) **no están en el repositorio**: se esperan en `../imagenes-productos` o en la
  carpeta que indique `SEED_IMAGENES_DIR`.

### Actualizar un servicio

```bash
docker build -t localhost:5000/pedidos:latest pedidos && docker push localhost:5000/pedidos:latest
kubectl rollout restart deployment/pedidos && kubectl rollout status deployment/pedidos
```

El frontend se actualiza igual (`docker build -t localhost:5000/frontend:latest frontend` y el rollout): sus archivos llevan
un hash en el nombre, así que el navegador nunca usa uno viejo.

### Código común (`comun/`)

`comun/` tiene el código que comparten los 8 servicios. Cada imagen se construye solo con la carpeta de su servicio, así
que cada uno lleva una copia en `<servicio>/comun/`, con un encabezado que lo dice. Se edita solo `comun/`:

```bash
python scripts/sincronizar_comun.py               # copia comun/ a los servicios que lo usan
python scripts/sincronizar_comun.py --verificar   # solo comprueba (scripts/correr_pruebas.sh lo hace antes de probar)
```

Un cambio en `comun/` cambia todos los servicios que lo llevan: se reconstruyen y despliegan todos.

### Respaldo y restauración

Antes de algo que llene la plataforma de datos (por ejemplo, una validación con usuarios), se guarda el estado completo y
después se vuelve a él:

```bash
bash scripts/respaldo_completo.sh                                     # crea backups/<fecha>/
bash scripts/restaurar_completo.sh backups/<fecha> --simular          # compara con el estado actual, sin cambiar nada
bash scripts/restaurar_completo.sh backups/<fecha>                    # restaura (pide escribir "restaurar")
```

**Qué guarda el respaldo:** las 8 bases (`pg_dump` en formato personalizado), un inventario de filas por tabla y todos
los archivos de MinIO. La carpeta `backups/` no se sube a git.

**Dónde están los respaldos anteriores:** en `C:\dev\respaldos-mercado_agricola`, fuera del repositorio y de cualquier
carpeta sincronizada (ya no en OneDrive), con acceso solo para el usuario de esta máquina porque tienen datos reales de
usuarios. Ahí están `backups/`, `salida/` (la copia de `deploy/gcp/salida/`) y `commits-huerfanos.bundle`; para recuperar
sus commits: `git fetch <bundle> "refs/huerfanos/*:refs/huerfanos/*"`.

**Qué hace la restauración, en orden:**
1. Guarda primero otro respaldo del estado de ese momento.
2. Deja MinIO igual que en el respaldo.
3. Detiene los 8 servicios mientras restaura cada base (`pg_restore --clean`, todo o nada).
4. Levanta los servicios.
5. Vacía la caché de Redis.
6. Comprueba las filas contra el inventario.

## Arranque sin Kubernetes (Docker Compose)

`docker compose up -d --build` levanta los 8 servicios, sus bases, Redis, RabbitMQ, MinIO y el frontend
(<http://localhost:8090>). **No incluye el gateway Traefik, Prometheus ni Grafana.**

```bash
cp .env.example .env      # y cambiar TODOS los valores: los de la plantilla son de ejemplo
docker compose up -d --build
python scripts/verificar_despliegue.py --directo --compose
```

- **Credenciales:** todas salen de `.env` (ignorado por git); `docker-compose.yml` no lleva ninguna escrita. Si falta una
  obligatoria, `docker compose` se niega a arrancar y dice cuál. La cabecera de `docker-compose.yml` explica qué cambia
  respecto de Kubernetes.
- **Puertos:** todos los publicados escuchan solo en `127.0.0.1`; no se ven desde otras máquinas de la red.
- **Ojo con un `.env` de antes del 2026-11-16:** su `DB_PASSWORD` es la contraseña de desarrollo vieja, que está en el
  historial público de git, y los volúmenes de las bases de compose la tienen guardada. Antes de volver a levantar compose,
  rótala: cámbiala en `.env` y en cada base (`ALTER ROLE <usuario> PASSWORD '<nueva>'`, con las bases arriba), o borra los
  volúmenes de las bases para que se creen de nuevo con la nueva (se pierden los datos de compose).

## Pruebas automáticas

```bash
bash scripts/correr_pruebas.sh                  # los 8 servicios y las reglas del frontend (Vitest)
bash scripts/correr_pruebas.sh pedidos pagos    # solo algunos
bash scripts/correr_pruebas.sh --cobertura      # con cobertura (pytest-cov)
python scripts/probar_contorno_chacra.py        # contorno, geocerca y cadenas contra el clúster local (crea y borra QA-CTN)
```

- **Cómo corren:** cada suite usa `pytest` dentro de la imagen de su servicio, contra un PostgreSQL desechable
  (`chakrashop-pg-pruebas`). Nunca toca las bases del clúster. Antes de empezar comprueba que las copias de `comun/`
  coincidan con la fuente: si no, no prueba nada.
- **Qué son:** la mayoría son pruebas de integración por servicio: la API real con una base real, y los demás servicios,
  RabbitMQ, Culqi y MinIO simulados.
- **Dónde están:** en `<servicio>/test_*.py`, y las del código común en `comun/test_*.py` (corren en cada servicio, con
  su copia).

## Despliegue en Google Cloud

Ver [`deploy/gcp/README.md`](deploy/gcp/README.md). Los scripts generan los manifiestos de GKE a partir de `k8s/`, con
dominio y HTTPS, y crean secretos aleatorios.

## Estructura

```
<servicio>/            8 microservicios: main.py, models.py, módulos de lógica, Dockerfile, requirements.txt y test_*.py
comun/                 código común a los 8 servicios; cada uno lleva una copia generada en <servicio>/comun/
frontend/              aplicación de React (src/), pruebas (Vitest en src/**/*.test.ts, E2E en e2e/), nginx.conf y Dockerfile
k8s/                   manifiestos del clúster local (servicios, bases con volumen, RabbitMQ, Redis, MinIO, Traefik,
                       Prometheus, Grafana con su tablero)
deploy/gcp/            guía y scripts para Google Kubernetes Engine
deploy/local/          plantilla del Secret del clúster local
scripts/               pruebas, verificación del despliegue, tablero de Grafana, respaldo y restauración completos,
                       y abrir-puertos.ps1 (port-forward en 127.0.0.1 para desarrollo)
seed_datos_demo.py     semilla de demostración (y seed_imagenes.py)
docker-compose.yml     entorno sin Kubernetes
```

No se suben (`.gitignore`): `k8s/secret.yaml`, `.env`, `.mcp.json`, `.claude/settings.local.json`, `backups/`, `docs/` y `deploy/gcp/salida/`.
Sí se suben las plantillas, con valores de ejemplo que no sirven: `deploy/local/secret.ejemplo.yaml` y `.env.example`.

## Limitaciones conocidas

**Seguridad y configuración**
- Las credenciales salen del Secret `credenciales-app` (Kubernetes) o de `.env` (Docker Compose); ningún archivo versionado
  lleva una. Cada base, RabbitMQ, MinIO y Grafana tienen su propia contraseña, y el código no trae valores por defecto: si
  falta una variable, el servicio no arranca. El historial de git es público y conserva las contraseñas de desarrollo de
  antes del 2026-11-16: ya no sirven (se cambiaron todas).
- Cada servicio llega a su base con `DB_HOST`, `DB_PORT` y `DB_NAME` (obligatoria). En Kubernetes salen de la ConfigMap
  `base-<servicio>` de `k8s/<servicio>.yaml`, la misma que leen el initContainer y la propia base; en compose, el nombre
  se escribe una vez en `POSTGRES_DB` y el servicio lo toma de ahí. El pool se ajusta con `DB_POOL_SIZE` y
  `DB_MAX_OVERFLOW` (por defecto 5 y 10 conexiones por proceso).
- Los logs salen con hora, nivel y nombre del logger, al nivel de `LOG_LEVEL` (INFO por defecto). Las librerías ruidosas
  (httpx, pika, Gemini...) quedan en WARNING, y el log de acceso de uvicorn solo escribe las respuestas con error.
- CORS abierto (`*`).
- El JWT es simétrico (HS256) y todos los servicios comparten la misma clave.

**Certificación**
- La cadena se guarda solo en la base de Certificación, sin anclaje externo. Detecta un dato alterado, pero no que
  alguien con acceso de escritura a la base la recalcule entera o borre el último bloque.

**Consistencia**
- Si Productos no responde al devolver el stock de un pago rechazado, la compensación queda solo en el log: no se
  reintenta.

**Pagos y datos externos**
- Culqi en modo prueba.
- El pago semanal a agricultores y repartidores se registra a mano (Yape o transferencia).
- La consulta de DNI a RENIEC no se probó con un DNI real.

**Producto**
- La ubicación de la siembra y la cosecha se compara con la chacra (1,5 km), pero solo genera un aviso: no bloquea.
- El mapa del repartidor se actualiza cada 15 s (consulta periódica, no WebSocket).

**Pruebas y despliegue**
- Las E2E del frontend (`frontend/e2e/`) corren a mano contra el clúster local, no en CI. No hay pruebas de carga.
- No hay NetworkPolicy ni respaldo automático de las bases en la nube.

**Pendiente antes de producción**
- Fase 8b: desplegar en Google Cloud la versión con el frontend en React (etiqueta `v2.0-react`). Sin planificar todavía.
- `seed_imagenes.py:9` tiene fija la ruta a las imágenes en OneDrive (`...\OneDrive\Documentos\Proyectos\imagenes-productos`).
- Fase de despliegue: Google OAuth con dos proyectos, cada uno con su `VITE_GOOGLE_CLIENT_ID` (frontend) y
  `GOOGLE_CLIENT_ID` en Usuarios. El de desarrollo está **cerrado**: con `localhost` en modo Prueba, el inicio de sesión
  con Google en `http://localhost:5173/login`, la sesión al recargar y `?next=` funcionan (2026-11-07). Falta el de
  producción: solo HTTPS y publicado.
- Cosechas más grandes que la capacidad del vehículo más grande (decisión de negocio, no de la migración): decidir si se
  sugiere transporte contratado automáticamente o se divide el recojo.
