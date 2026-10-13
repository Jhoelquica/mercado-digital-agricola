# ChakraShop — Mercado Digital Agrícola

Plataforma de venta directa del productor al comprador para Ayacucho (Perú). La componen **8 microservicios FastAPI**,
cada uno con su propia base PostgreSQL. Se comunican por HTTP (con una clave por cada par de servicios) y por eventos en
RabbitMQ.

- **Frontend:** una SPA en HTML, CSS y JavaScript.
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
(9090) y Grafana (3000).

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
- **APIs:** `http://localhost:8000/api/<servicio>/...`
- **Grafana:** <http://localhost:3000>. Abre directo el tablero "ChakraShop — Microservicios" (tráfico, latencia p95 y errores 5xx de los 8 servicios); mirarlo no pide sesión.
- **Prometheus:** <http://localhost:9090>
- **Consola de MinIO:** <http://localhost:9001>

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

Para el frontend, corre antes `python frontend/bump_cache_version.py`, para que el navegador no use archivos viejos.

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

**Qué hace la restauración, en orden:**
1. Guarda primero otro respaldo del estado de ese momento.
2. Deja MinIO igual que en el respaldo.
3. Detiene los 8 servicios mientras restaura cada base (`pg_restore --clean`, todo o nada).
4. Levanta los servicios.
5. Vacía la caché de Redis.
6. Comprueba las filas contra el inventario.

## Arranque sin Kubernetes (Docker Compose)

`docker compose up -d --build` levanta los 8 servicios, sus bases, Redis, RabbitMQ, MinIO y el frontend
(<http://localhost:8090>). Necesita un archivo `.env`: la cabecera de `docker-compose.yml` dice qué variables lleva y qué
cambia respecto de Kubernetes. **No incluye el gateway Traefik, Prometheus ni Grafana.** Comprobación:
`python scripts/verificar_despliegue.py --directo --compose`.

## Pruebas automáticas

```bash
bash scripts/correr_pruebas.sh                  # los 8 servicios
bash scripts/correr_pruebas.sh pedidos pagos    # solo algunos
bash scripts/correr_pruebas.sh --cobertura      # con cobertura (pytest-cov)
```

- **Cómo corren:** cada suite usa `pytest` dentro de la imagen de su servicio, contra un PostgreSQL desechable
  (`chakrashop-pg-pruebas`). Nunca toca las bases del clúster.
- **Qué son:** la mayoría son pruebas de integración por servicio: la API real con una base real, y los demás servicios,
  RabbitMQ, Culqi y MinIO simulados.
- **Dónde están:** en `<servicio>/test_*.py`.

## Despliegue en Google Cloud

Ver [`deploy/gcp/README.md`](deploy/gcp/README.md). Los scripts generan los manifiestos de GKE a partir de `k8s/`, con
dominio y HTTPS, y crean secretos aleatorios.

## Estructura

```
<servicio>/            8 microservicios: main.py, models.py, auth.py, módulos de lógica, Dockerfile, requirements.txt y test_*.py
frontend/              SPA (index.html, verificar.html, js/, css/), nginx.conf y Dockerfile
k8s/                   manifiestos del clúster local (servicios, bases con volumen, RabbitMQ, Redis, MinIO, Traefik,
                       Prometheus, Grafana con su tablero)
deploy/gcp/            guía y scripts para Google Kubernetes Engine
deploy/local/          plantilla del Secret del clúster local
scripts/               pruebas, verificación del despliegue, tablero de Grafana, respaldo y restauración completos
seed_datos_demo.py     semilla de demostración (y crear_productos_seed.py, seed_imagenes.py)
docker-compose.yml     entorno sin Kubernetes
```

No se suben (`.gitignore`): `k8s/secret.yaml`, `.env`, `backups/` y `deploy/gcp/salida/`.

## Limitaciones conocidas

**Seguridad y configuración**
- RabbitMQ y Grafana usan credenciales fijas de desarrollo (`admin/admin123`) en `k8s/`.
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
- No hay pruebas automáticas del frontend ni pruebas de carga.
- No hay NetworkPolicy ni respaldo automático de las bases en la nube.
