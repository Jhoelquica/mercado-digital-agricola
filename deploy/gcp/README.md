# Desplegar Chakra Shop en Google Cloud (GKE)

Guía para llevar el clúster local (Docker Desktop) a un clúster de Google Kubernetes Engine con dominio y HTTPS.
Los manifiestos de `k8s/` **no se tocan**: los scripts de esta carpeta generan los de Google Cloud a partir de ellos.

## Qué quedó probado y qué no

> **Actualización 2026-09-23: ya se desplegó de verdad en GKE** (proyecto `mecadoagricoladigital`, `us-central1-a`, un nodo `e2-standard-4`,
> `https://35-225-121-28.sslip.io`). Quedaron probados el HTTPS con Let's Encrypt (real), el balanceador con IP fija, la descarga de
> imágenes desde Artifact Registry, el nodo pequeño con los 21 pods, la semilla demo por el gateway (30 productos, 12 productores) y las
> fotos, el mapa y el QR por HTTPS. Sigue sin probarse: la carga real de muchos usuarios y Google Sign-In con el dominio nuevo (exige agregar
> el origen autorizado). Lo que falló en el primer intento está en "Lo aprendido en el primer despliegue real", al final del documento.

| Probado en el clúster local | NO probado (solo se puede en Google Cloud) |
|---|---|
| El frontend servido por nginx detrás del gateway, con las APIs bajo `/api/<servicio>` (mismo origen) | HTTPS con Let's Encrypt (Traefik) y la redirección http → https |
| Las fotos de productos por `/media/...` | El balanceador con IP estática y `externalTrafficPolicy: Local` |
| Que el bucket de imágenes se cree solo | Extraer las imágenes de Artifact Registry |
| Los manifiestos generados: render, esquema válido, solo Traefik con IP pública | Que un nodo pequeño aguante todo (los límites están puestos) |
| El gateway bloquea `/docs`, `/openapi.json`, `/metrics`, las rutas internas entre servicios y los DELETE de limpieza (403) | Cómo se comporta bajo carga real de muchos usuarios |
| Actualizar un servicio bajo carga continua por el gateway: 61 de 61 respuestas correctas (sondas + apagado ordenado) | |
| Reiniciar una base de datos: solo hay error mientras está caída; al volver, todo responde bien (`pool_pre_ping`) | |
| Las bases y MinIO se actualizan con `Recreate` (antes `RollingUpdate`, con riesgo de dos bases sobre el mismo volumen) | |
| La semilla de datos demo por el gateway | Google Sign-In con el dominio nuevo |
| Las 8 imágenes con versiones fijas (`pip freeze`) y usuario no-root (uid 10001): 379 tests dentro de cada imagen, paridad de 384 llamadas sin diferencias, subida de fotos a MinIO y consumidores de RabbitMQ conectados | |

## Lo que necesitas de tu lado

1. **Una cuenta de Google Cloud con facturación** y un proyecto.
2. **`gcloud` instalado** (`winget install Google.CloudSDK`) y `gcloud auth login`.
3. **Un dominio**, o usar `IP-con-guiones.sslip.io` (gratis, ver paso 5). Hace falta HTTPS: Google Sign-In no funciona sin él.
4. En **Google Cloud Console → APIs y servicios → Credenciales → tu ID de cliente OAuth**: agregar `https://TU-DOMINIO`
   en *Orígenes autorizados de JavaScript*. Si la pantalla de consentimiento está en modo *Prueba*, agrega como
   usuarios de prueba a quienes vayan a entrar con Google (o publícala).
5. Las **llaves de Culqi** reales, si quieres cobrar de verdad. Sin ellas se usan las de prueba y los pagos son simulados.
   La llave *secreta* va al Secret (paso 6); la *pública* está escrita en `frontend/js/api.js` (`CULQI_PUBLIC_KEY`): si usas
   llaves reales, cámbiala ahí y vuelve a construir la imagen del frontend (paso 7 con `--solo frontend`).

Costo aproximado con un nodo `e2-standard-4`: unos US$4 al día (nodo + discos + balanceador). **Bórralo al terminar** (paso 12).

## Pasos

Usa las mismas variables en todos los comandos (ejemplo):

```bash
PROYECTO=mi-proyecto-gcp
REGION=us-central1
ZONA=us-central1-a
REGISTRO=$REGION-docker.pkg.dev/$PROYECTO/chakrashop
```

**1. Proyecto y APIs**
```bash
gcloud config set project $PROYECTO
gcloud services enable container.googleapis.com artifactregistry.googleapis.com compute.googleapis.com
```

**2. Repositorio de imágenes**
```bash
gcloud artifacts repositories create chakrashop --repository-format=docker --location=$REGION
gcloud auth configure-docker $REGION-docker.pkg.dev
```
**Permiso para que los nodos descarguen las imágenes** (sin esto los pods quedan en `ErrImagePull` con `403 Forbidden`; en proyectos
dentro de una organización la cuenta de servicio de Compute ya no tiene permisos por defecto). Solo lectura y solo sobre este repositorio:
```bash
NUMERO=$(gcloud projects describe $PROYECTO --format='value(projectNumber)')
gcloud artifacts repositories add-iam-policy-binding chakrashop --location=$REGION \
    --member="serviceAccount:$NUMERO-compute@developer.gserviceaccount.com" --role=roles/artifactregistry.reader
```

**3. Clúster** (un nodo; sube `--num-nodes` o el tipo de máquina si lo necesitas)
```bash
gcloud components install gke-gcloud-auth-plugin
gcloud container clusters create chakrashop --zone $ZONA --machine-type e2-standard-4 --num-nodes 1 --disk-size 50
gcloud container clusters get-credentials chakrashop --zone $ZONA
kubectl config current-context        # debe empezar por gke_ (NO docker-desktop)
```

**4. IP fija para el balanceador**
```bash
gcloud compute addresses create chakrashop-ip --region $REGION
IP=$(gcloud compute addresses describe chakrashop-ip --region $REGION --format='value(address)')
echo $IP
```

**5. Dominio**
- Con dominio propio: crea un registro **A** `tienda.midominio.com → $IP`.
- Sin dominio: usa `${IP//./-}.sslip.io` (ej. `34-72-10-5.sslip.io`), que resuelve a esa IP sin configurar nada.

```bash
DOMINIO=34-72-10-5.sslip.io   # el tuyo
```

**6. Traefik (CRDs) y secretos**
```bash
kubectl apply -f https://raw.githubusercontent.com/traefik/traefik/v3.7/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml
python deploy/gcp/crear_secretos.py --email-admin tu@correo.com          # + --culqi-public/--culqi-secret si tienes las reales
```
Crea el Secret con valores aleatorios y **no imprime ninguno**. El correo y la contraseña del primer admin quedan en
`deploy/gcp/salida/credenciales-generadas.txt`: guárdalos y borra el archivo. El script se niega a correr contra el
clúster local y a reemplazar un Secret que ya existe.

**7. Imágenes**
```bash
python deploy/gcp/construir_imagenes.py --registro $REGISTRO --etiqueta v1
```

**8. Manifiestos y despliegue** (la primera vez con `--acme-staging`: certificado de prueba, sin agotar los límites de Let's Encrypt)
```bash
python deploy/gcp/generar_manifiestos.py --registro $REGISTRO --dominio $DOMINIO --email tu@correo.com \
    --etiqueta v1 --ip-estatica $IP --acme-staging
kubectl apply -f deploy/gcp/salida/manifiestos.yaml
kubectl get pods -w                    # esperar a que todo esté Running/Ready (2-4 min)
kubectl logs deploy/traefik-gateway | grep -i acme     # debe verse el certificado obtenido
```

**9. Certificado real**: cuando `https://$DOMINIO` responda (con aviso de certificado de prueba), quita `--acme-staging`:
```bash
kubectl scale deploy/traefik-gateway --replicas=0
kubectl delete pvc traefik-acme                       # borra el certificado de prueba
python deploy/gcp/generar_manifiestos.py --registro $REGISTRO --dominio $DOMINIO --email tu@correo.com --etiqueta v1 --ip-estatica $IP
kubectl apply -f deploy/gcp/salida/manifiestos.yaml
```
Si el certificado real no llega (en el log de Traefik: `Unable to obtain ACME certificate ... Connection refused`), es que Traefik lo pidió en el
mismo segundo de arrancar, antes de que el balanceador de Google enviara tráfico al pod nuevo. **No reinicies** Traefik (repite la carrera):
provoca un cambio real de su configuración con el pod ya sano, por ejemplo
`kubectl patch middleware rate-limit-global --type=merge -p '{"spec":{"rateLimit":{"burst":201}}}'`, espera medio minuto a que
`openssl s_client -connect $IP:443 -servername $DOMINIO` muestre "Let's Encrypt" y devuelve el valor a 200. (Solo anotar un IngressRoute
no basta: no cambia la configuración.) Let's Encrypt limita a 5 validaciones fallidas por hora.

**10. Datos de demostración** (12 agricultores, 30 productos con fotos y certificados). Necesita el admin del paso 6 y la carpeta
`imagenes-productos` (junto a este repositorio, o `SEED_IMAGENES_DIR`). Define una clave PRIVADA para las cuentas demo:
```bash
export SEED_HOST=https://$DOMINIO SEED_PASSWORD_DEMO='una-clave-larga-y-privada'
export SEED_ADMIN_EMAIL=tu@correo.com SEED_ADMIN_PASSWORD='...'      # del archivo credenciales-generadas.txt
python seed_datos_demo.py
```

**11. Comprobaciones**
- `https://$DOMINIO/` carga y muestra el catálogo con fotos; `https://$DOMINIO/api/productos/salud` → `{"estado":"ok",...}`.
- Registro e inicio de sesión (correo y Google), y el panel de admin con el correo del paso 6.
- Escanear/abrir el QR de un producto (`https://$DOMINIO/verificar.html?producto_id=...`) muestra la cadena y "Certificado por".
- Una compra de prueba con la tarjeta de prueba de Culqi.
- El admin **no** debe poder abrirse sin sesión, y `https://$DOMINIO:9000` o `:8006` **no** deben responder (solo entran 80 y 443).

**12. Apagarlo** (deja de cobrar)
```bash
gcloud container clusters delete chakrashop --zone $ZONA
gcloud compute addresses delete chakrashop-ip --region $REGION
gcloud artifacts repositories delete chakrashop --location=$REGION
```

## Actualizar después de un cambio de código
```bash
python deploy/gcp/construir_imagenes.py --registro $REGISTRO --etiqueta v2 --solo frontend usuarios
python deploy/gcp/generar_manifiestos.py ... --etiqueta v2 ...
kubectl apply -f deploy/gcp/salida/manifiestos.yaml
```

## Riesgos conocidos (aceptados para una demo)
- **Las bases y MinIO viven en volúmenes del clúster**, sin copias de seguridad automáticas: si se borra el clúster o el volumen, se pierden.
  Para algo más serio: Cloud SQL para Postgres y un bucket de Cloud Storage.
- **RabbitMQ usa `admin/admin123`** (5 manifiestos, dentro del clúster: no sale a Internet) y no hay `NetworkPolicy`.
- **Una réplica por servicio**: sin alta disponibilidad. `--replicas 2` en `generar_manifiestos.py` las sube (Traefik se queda en 1: guarda el certificado en un volumen).
- **Sin monitoreo** (Prometheus/Grafana traen `admin/admin123`). Si lo quieres, ponle contraseña y déjalo en ClusterIP.
- Los endpoints internos entre servicios (con `X-Servicio-Secreto`) son alcanzables por el gateway y solo los protege ese secreto de 64 caracteres al azar.
- **Culqi en modo de prueba** salvo que pases las llaves reales.
- **Las dependencias de los servicios quedan fijas en cada `requirements.txt`** (salida de `pip freeze`, con las versiones que se probaron):
  una construcción nueva instala exactamente eso. Para actualizar algo hay que cambiar su versión, reconstruir la imagen y correr los tests
  del servicio. Los servicios corren como usuario no-root (uid 10001), pero **los paquetes de prueba (pytest) siguen dentro de la imagen**.
- Las cuentas de un mismo correo se **bloquean 10 minutos tras 8 fallos** (por correo, existan o no): protege de la fuerza bruta, pero
  alguien podría bloquear a propósito la cuenta de otra persona. Es una decisión deliberada para una demo.

## Lo aprendido en el primer despliegue real (2026-09-23)
- **Facturación:** GKE no se puede crear sin una cuenta de facturación *abierta*. Una cuenta que Google crea "cerrada" (aviso rojo "está cerrada",
  botón de reabrir atenuado) casi siempre es una verificación de la tarjeta sin completar (autenticación 3-D Secure del banco). Se comprueba desde
  la terminal con `gcloud billing accounts list` (columna `OPEN`) y `gcloud billing projects describe $PROYECTO` (`billingEnabled: true`).
- **Windows / PowerShell:** si bloquea `gcloud.ps1` ("la ejecución de scripts está deshabilitada"), usa `gcloud.cmd` en vez de cambiar la política de
  ejecución. Para instalar componentes sin interfaz (`gcloud components install gke-gcloud-auth-plugin --quiet`) hace falta antes
  `gcloud components copy-bundled-python` y apuntar `CLOUDSDK_PYTHON` a lo que devuelve.
- **Bases de datos:** los discos de Google traen `lost+found` y Postgres se negaba a arrancar; `generar_manifiestos.py` ya pone `PGDATA` en una
  subcarpeta del volumen. Ver `db-*` en el manifiesto generado.
- **Descarga de imágenes:** falta el permiso de lectura del repositorio para la cuenta de servicio de los nodos (paso 2).
- **Subida de imágenes:** con una red inestable `docker push` puede dar timeouts; se reintenta solo lo que falló con `--solo servicio1 servicio2`.
- **Contexto de kubectl:** `gcloud container clusters get-credentials` cambia el contexto actual al del clúster de Google Cloud. Vuelve al local con
  `kubectl config use-context docker-desktop` y usa `--context gke_<proyecto>_<zona>_chakrashop` para operar el de Google Cloud.
- **`credenciales-generadas.txt`** (correo y contraseña del admin y clave de las cuentas demo) queda en `deploy/gcp/salida/`, ignorado por git, pero
  si el repositorio vive en una carpeta sincronizada (OneDrive) se sube a la nube: guárdalo en un gestor de contraseñas y bórralo.
