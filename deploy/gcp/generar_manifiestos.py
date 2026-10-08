#!/usr/bin/env python
"""Genera los manifiestos para desplegar Chakra Shop en Google Cloud (GKE) a partir de los de k8s/, que son los de
desarrollo local. NO modifica k8s/: arma un kustomize en deploy/gcp/salida/ y lo renderiza a manifiestos.yaml, que
se revisa y se aplica con kubectl (ver README.md).

Qué cambia respecto al clúster local:
  - Las imágenes salen de tu Artifact Registry (no de localhost:5000).
  - Solo Traefik (el gateway) tiene IP pública: los microservicios y MinIO pasan a ClusterIP (sin esto cada puerto
    quedaría abierto a Internet saltándose el JWT, el rate-limit y el CORS del gateway).
  - HTTPS con Let's Encrypt (Traefik, 1 réplica y un volumen para el certificado) y redirección de http a https.
  - Una réplica por servicio y límites de memoria/CPU (para caber en un nodo pequeño).
  - FRONTEND_URL (a dónde apunta el QR) y MINIO_PUBLIC_URL (las fotos) con tu dominio.
  - CORS del gateway restringido a tu dominio (la app y la API comparten origen, así que basta).
  - Sin Prometheus ni Grafana (traen admin/admin123 y no hacen falta para desplegar).

Uso:
    python deploy/gcp/generar_manifiestos.py --registro us-central1-docker.pkg.dev/MI-PROYECTO/chakrashop \\
        --dominio tienda.midominio.com --email yo@correo.com --etiqueta v1 [--ip-estatica 34.1.2.3] [--acme-staging]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
K8S = RAIZ / "k8s"
SALIDA = AQUI / "salida"

SERVICIOS = ["usuarios", "productores", "productos", "pedidos", "transporte", "notificaciones", "pagos", "certificacion"]
# Lo de k8s/ que NO se despliega: el Secret de desarrollo (los reales los crea crear_secretos.py) y el monitoreo.
EXCLUIDOS = {"secret.yaml", "grafana.yaml", "grafana-provisioning.yaml", "prometheus.yaml", "prometheus-config.yaml", "kustomization.yaml"}
ACME_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"


def sangrar(texto, n):
    return "\n".join((" " * n + linea) if linea else linea for linea in texto.split("\n"))


def parche(target, operaciones):
    """Un parche JSON6902 de kustomize. `operaciones` ya viene como YAML."""
    return f"  - target: {target}\n    patch: |-\n{sangrar(operaciones.strip(), 6)}\n"


def recursos(limite_cpu, limite_mem, pedido_cpu, pedido_mem):
    return (
        "- op: add\n  path: /spec/template/spec/containers/0/resources\n  value:\n"
        f"    requests: {{cpu: {pedido_cpu}, memory: {pedido_mem}}}\n"
        f"    limits: {{cpu: {limite_cpu}, memory: {limite_mem}}}"
    )


def construir(args):
    # Los pvc-*.yaml repiten el volumen que ya define cada db-*.yaml (con `storageClassName: standard`, propio de Docker
    # Desktop): en GKE basta con el de db-*.yaml, que usa la clase de almacenamiento por defecto del clúster.
    archivos = sorted(p.name for p in K8S.glob("*.yaml") if p.name not in EXCLUIDOS and not p.name.startswith("pvc-"))
    rel = "../../../k8s"
    lineas_recursos = "\n".join(f"  - {rel}/{nombre}" for nombre in archivos)
    imagenes = "\n".join(
        f"  - name: localhost:5000/{s}\n    newName: {args.registro}/{s}\n    newTag: {args.etiqueta}"
        for s in SERVICIOS + ["frontend"]
    )
    replicas = "\n".join(f"  - name: {s}\n    count: {args.replicas}" for s in SERVICIOS) + "\n  - name: traefik-gateway\n    count: 1"

    servicios_re = "|".join(SERVICIOS)
    partes = []

    # --- solo Traefik sale a Internet
    partes.append(parche(
        f'{{kind: Service, name: "^({servicios_re}|minio)$"}}',
        "- op: replace\n  path: /spec/type\n  value: ClusterIP",
    ))
    puertos_lb = (
        "- op: replace\n  path: /spec/ports\n  value:\n"
        "    - {name: web, port: 80, targetPort: 8000}\n"
        "    - {name: websecure, port: 443, targetPort: 8443}\n"
        "- op: add\n  path: /spec/externalTrafficPolicy\n  value: Local  # conserva la IP real del cliente (rate-limit)"
    )
    if args.ip_estatica:
        puertos_lb += f"\n- op: add\n  path: /spec/loadBalancerIP\n  value: {args.ip_estatica}"
    partes.append(parche('{kind: Service, name: "^traefik-gateway$"}', puertos_lb))

    # --- HTTPS (Let's Encrypt por HTTP-01 en el puerto 80) y un volumen para guardar el certificado
    argumentos_tls = [
        # ":443" y no "websecure": Traefik redirige al puerto REAL del entrypoint (8443 dentro del contenedor), que el balanceador no
        # expone (solo 80 y 443), así que http://dominio llevaba a https://dominio:8443/, inaccesible.
        "--entrypoints.web.http.redirections.entrypoint.to=:443",
        "--entrypoints.web.http.redirections.entrypoint.scheme=https",
        "--entrypoints.websecure.address=:8443",
        f"--certificatesresolvers.le.acme.email={args.email}",
        "--certificatesresolvers.le.acme.storage=/acme/acme.json",
        "--certificatesresolvers.le.acme.httpchallenge.entrypoint=web",
    ]
    if args.acme_staging:
        argumentos_tls.append(f"--certificatesresolvers.le.acme.caserver={ACME_STAGING}")
    operaciones_traefik = "\n".join(
        f"- op: add\n  path: /spec/template/spec/containers/0/args/-\n  value: '{a}'" for a in argumentos_tls
    ) + (
        "\n- op: add\n  path: /spec/template/spec/containers/0/ports/-\n  value: {name: websecure, containerPort: 8443}"
        "\n- op: add\n  path: /spec/template/spec/containers/0/volumeMounts\n  value: [{name: acme, mountPath: /acme}]"
        "\n- op: add\n  path: /spec/template/spec/volumes\n  value: [{name: acme, persistentVolumeClaim: {claimName: traefik-acme}}]"
        "\n- op: add\n  path: /spec/strategy\n  value: {type: Recreate}  # el volumen es de un solo nodo: no puede haber 2 pods a la vez"
    )
    partes.append(parche('{kind: Deployment, name: "^traefik-gateway$"}', operaciones_traefik))
    partes.append(parche(
        "{kind: IngressRoute}",
        "- op: replace\n  path: /spec/entryPoints\n  value: [websecure]\n"
        f"- op: add\n  path: /spec/tls\n  value: {{certResolver: le, domains: [{{main: {args.dominio}}}]}}",
    ))
    partes.append(parche(
        '{kind: Middleware, name: "^cors-global$"}',
        f'- op: replace\n  path: /spec/headers/accessControlAllowOriginList\n  value: ["https://{args.dominio}"]',
    ))

    # --- límites de recursos
    partes.append(parche(f'{{kind: Deployment, name: "^({servicios_re})$"}}', recursos("500m", "512Mi", "50m", "128Mi")))
    partes.append(parche('{kind: Deployment, name: "^db-.*"}', recursos("500m", "512Mi", "50m", "192Mi")))
    # Los discos de Google (ext4) traen una carpeta lost+found en la raíz del volumen y Postgres se niega a usar como datos
    # un directorio que no está vacío ("initdb: directory ... exists but is not empty"): en Docker Desktop no pasa. Se le
    # dice que guarde los datos en una subcarpeta del volumen.
    partes.append(parche(
        '{kind: Deployment, name: "^db-.*"}',
        "- op: add\n  path: /spec/template/spec/containers/0/env/-\n  value: {name: PGDATA, value: /var/lib/postgresql/data/pgdata}",
    ))
    partes.append(parche('{kind: Deployment, name: "^rabbitmq$"}', recursos("500m", "768Mi", "100m", "256Mi")))
    partes.append(parche('{kind: Deployment, name: "^minio$"}', recursos("500m", "512Mi", "50m", "128Mi")))
    partes.append(parche('{kind: Deployment, name: "^redis$"}', recursos("200m", "128Mi", "20m", "32Mi")))

    # --- URLs públicas
    partes.append(
        "  - target: {kind: Deployment, name: \"^certificacion$\"}\n    patch: |-\n"
        + sangrar(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: certificacion\nspec:\n  template:\n    spec:\n"
            "      containers:\n        - name: certificacion\n          env:\n            - name: FRONTEND_URL\n"
            f"              value: https://{args.dominio}",
            6,
        ) + "\n"
    )
    for servicio in ("productos", "transporte", "productores"):
        partes.append(
            f"  - target: {{kind: Deployment, name: \"^{servicio}$\"}}\n    patch: |-\n"
            + sangrar(
                f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {servicio}\nspec:\n  template:\n    spec:\n"
                f"      containers:\n        - name: {servicio}\n          env:\n            - name: MINIO_PUBLIC_URL\n"
                f"              value: https://{args.dominio}/media",
                6,
            ) + "\n"
        )

    return (
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n\n"
        "# GENERADO por deploy/gcp/generar_manifiestos.py: no lo edites, vuelve a generarlo.\n\n"
        f"resources:\n{lineas_recursos}\n  - traefik-acme-pvc.yaml\n\n"
        f"images:\n{imagenes}\n\nreplicas:\n{replicas}\n\npatches:\n" + "\n".join(partes)
    )


PVC_ACME = """apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: traefik-acme
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
"""


def validar(args):
    if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", args.dominio):
        sys.exit(f"--dominio debe ser solo el nombre (tienda.midominio.com o 34-1-2-3.sslip.io), sin https:// ni barras: {args.dominio!r}")
    if "@" not in args.email:
        sys.exit("--email no parece un correo (Let's Encrypt lo usa para avisarte si el certificado no se renueva)")
    if args.registro.endswith("/") or "://" in args.registro:
        sys.exit("--registro va sin https:// y sin barra final, ej. us-central1-docker.pkg.dev/MI-PROYECTO/chakrashop")
    if args.ip_estatica and not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", args.ip_estatica):
        sys.exit(f"--ip-estatica no es una IPv4: {args.ip_estatica!r}")


def main():
    ap = argparse.ArgumentParser(description="Genera los manifiestos de Google Cloud (GKE) desde los de k8s/.")
    ap.add_argument("--registro", required=True, help="Artifact Registry sin https://, ej. us-central1-docker.pkg.dev/PROYECTO/chakrashop")
    ap.add_argument("--dominio", required=True, help="dominio público (o IP-con-guiones.sslip.io)")
    ap.add_argument("--email", required=True, help="correo para Let's Encrypt")
    ap.add_argument("--etiqueta", default="v1", help="etiqueta de las imágenes (la misma de construir_imagenes.py)")
    ap.add_argument("--ip-estatica", help="IP reservada del balanceador (gcloud compute addresses create ...)")
    ap.add_argument("--replicas", type=int, default=1, help="réplicas por microservicio (1 para una demo)")
    ap.add_argument("--acme-staging", action="store_true", help="certificado de PRUEBA de Let's Encrypt (no agota límites)")
    args = ap.parse_args()
    validar(args)

    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "kustomization.yaml").write_text(construir(args), encoding="utf-8", newline="\n")
    (SALIDA / "traefik-acme-pvc.yaml").write_text(PVC_ACME, encoding="utf-8", newline="\n")
    resultado = subprocess.run(
        ["kubectl", "kustomize", "--load-restrictor", "LoadRestrictionsNone", str(SALIDA)],
        capture_output=True, text=True,
    )
    if resultado.returncode != 0:
        sys.exit(f"kubectl kustomize falló:\n{resultado.stderr}")
    (SALIDA / "manifiestos.yaml").write_text(resultado.stdout, encoding="utf-8", newline="\n")
    print(f"OK: {SALIDA / 'manifiestos.yaml'}  ({resultado.stdout.count(chr(10)) + 1} líneas)")
    print("Revísalo y aplícalo con:  kubectl apply -f deploy/gcp/salida/manifiestos.yaml")


if __name__ == "__main__":
    main()
