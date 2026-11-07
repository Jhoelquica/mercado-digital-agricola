#!/usr/bin/env python
"""Construye y sube a tu Artifact Registry las imágenes de los 8 microservicios y del frontend (la aplicación de React).

Antes (una sola vez): gcloud auth configure-docker us-central1-docker.pkg.dev
Uso:  python deploy/gcp/construir_imagenes.py --registro us-central1-docker.pkg.dev/MI-PROYECTO/chakrashop --etiqueta v1 \
          --culqi-publica pk_live_... --google-client-id 1234-abc.apps.googleusercontent.com
      python deploy/gcp/construir_imagenes.py ... --solo frontend usuarios     # solo algunas

La llave pública de Culqi y el Client ID de Google quedan dentro del código del navegador (no son secretos) y se fijan al
construir el frontend. Sin ellos se usan los de desarrollo: Culqi de pruebas y el Client ID que solo acepta localhost.
"""
import argparse
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
IMAGENES = ["usuarios", "productores", "productos", "pedidos", "transporte", "notificaciones", "pagos", "certificacion", "frontend"]
# La carpeta de cada imagen (la del frontend es la aplicación de React).
CARPETAS = {"frontend": "frontend-react"}


def correr(*comando):
    print("  $", " ".join(comando), flush=True)
    return subprocess.run(comando).returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Construye y sube las imágenes a Artifact Registry.")
    ap.add_argument("--registro", required=True, help="sin https:// ni barra final, ej. us-central1-docker.pkg.dev/PROYECTO/chakrashop")
    ap.add_argument("--etiqueta", default="v1", help="etiqueta de las imágenes (la misma de generar_manifiestos.py)")
    ap.add_argument("--solo", nargs="+", choices=IMAGENES, help="construir solo algunas")
    ap.add_argument("--culqi-publica", help="llave pública de Culqi del frontend (pk_live_... para cobrar de verdad)")
    ap.add_argument("--google-client-id", help="Client ID de Google del frontend, con el dominio en sus orígenes autorizados")
    args = ap.parse_args()
    if args.registro.endswith("/") or "://" in args.registro:
        sys.exit("--registro va sin https:// y sin barra final")

    fallidas = []
    for nombre in args.solo or IMAGENES:
        etiqueta = f"{args.registro}/{nombre}:{args.etiqueta}"
        print(f"\n== {nombre}")
        if not (correr("docker", "build", "-t", etiqueta, str(RAIZ / nombre)) and correr("docker", "push", etiqueta)):
            fallidas.append(nombre)
    if fallidas:
        sys.exit(f"\nFallaron: {', '.join(fallidas)}")
    print(f"\nListo: {len(args.solo or IMAGENES)} imagen(es) en {args.registro} con la etiqueta {args.etiqueta}.")


if __name__ == "__main__":
    main()
