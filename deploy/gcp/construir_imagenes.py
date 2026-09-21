#!/usr/bin/env python
"""Construye y sube a tu Artifact Registry las imágenes de los 8 microservicios y del frontend.

Antes (una sola vez): gcloud auth configure-docker us-central1-docker.pkg.dev
Uso:  python deploy/gcp/construir_imagenes.py --registro us-central1-docker.pkg.dev/MI-PROYECTO/chakrashop --etiqueta v1
      python deploy/gcp/construir_imagenes.py ... --solo frontend usuarios     # solo algunas
"""
import argparse
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
IMAGENES = ["usuarios", "productores", "productos", "pedidos", "transporte", "notificaciones", "pagos", "certificacion", "frontend"]


def correr(*comando):
    print("  $", " ".join(comando), flush=True)
    return subprocess.run(comando).returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Construye y sube las imágenes a Artifact Registry.")
    ap.add_argument("--registro", required=True, help="sin https:// ni barra final, ej. us-central1-docker.pkg.dev/PROYECTO/chakrashop")
    ap.add_argument("--etiqueta", default="v1", help="etiqueta de las imágenes (la misma de generar_manifiestos.py)")
    ap.add_argument("--solo", nargs="+", choices=IMAGENES, help="construir solo algunas")
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
