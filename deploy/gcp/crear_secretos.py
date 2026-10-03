#!/usr/bin/env python
"""Crea el Secret `credenciales-app` del despliegue en Google Cloud con valores NUEVOS y aleatorios.

Las claves salen de k8s/secret.yaml (el de desarrollo, ignorado por git) para que no se desactualice cuando se agrega una:
  - DB_USER / DB_PASSWORD: usuario y contraseña de Postgres y de MinIO.
  - JWT_SECRET y todas las *_SECRETO (las que se mandan los servicios entre sí): 64 caracteres al azar.
  - QA_LIMPIEZA_SECRETO: también al azar y nadie lo conoce, así que los DELETE de limpieza de pruebas quedan cerrados.
  - CULQI_*: las que pases con --culqi-public/--culqi-secret; si no, se copian las de PRUEBA del Secret de desarrollo.
  - ADMIN_BOOTSTRAP_EMAIL / _PASSWORD: el primer admin (lo crea Usuarios al arrancar si todavía no hay ninguno).
  - GEMINI_API_KEY (opcional): la IA que sugiere la fecha de cosecha (plan gratuito de Gemini).
  - SUNAT_RUC_TOKEN (opcional): clave del servicio que consulta el RUC en SUNAT, si un día la pide. Se copia la del Secret de desarrollo si la
    tiene; si no, Productores usa la tabla de ciclos típicos.

Ningún valor se imprime. Lo único que necesitas saber (el correo y la contraseña del admin) se guarda en
deploy/gcp/salida/credenciales-generadas.txt (ignorado por git): guárdalo en tu gestor de contraseñas y bórralo.

Seguridad: se niega a tocar el clúster local (docker-desktop) y a reemplazar un Secret que ya existe, porque cambiar
DB_PASSWORD con las bases ya creadas las dejaría inaccesibles.

Uso:  python deploy/gcp/crear_secretos.py --email-admin tu@correo.com [--culqi-public pk_... --culqi-secret sk_...]
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "salida"
LOCAL = AQUI.parents[1] / "k8s" / "secret.yaml"
NOMBRE = "credenciales-app"
DB_USER = "chakra"


def kubectl(*args, entrada=None):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, input=entrada)


def claves_y_valores_locales():
    if not LOCAL.exists():
        sys.exit(f"No encuentro {LOCAL}: hace falta para saber qué claves lleva el Secret.")
    texto = LOCAL.read_text(encoding="utf-8").replace("\r\n", "\n")
    return {m.group(1): m.group(2).strip().strip('"') for m in re.finditer(r"^  ([A-Z][A-Z0-9_]*):\s*(.*)$", texto, re.M)}


def main():
    ap = argparse.ArgumentParser(description="Crea el Secret credenciales-app del despliegue con valores aleatorios.")
    ap.add_argument("--email-admin", required=True, help="correo del primer admin")
    ap.add_argument("--culqi-public", help="llave pública de Culqi (si no, la de PRUEBA del Secret de desarrollo)")
    ap.add_argument("--culqi-secret", help="llave secreta de Culqi (si no, la de PRUEBA del Secret de desarrollo)")
    ap.add_argument("--dry-run", action="store_true", help="solo muestra qué claves se crearían")
    args = ap.parse_args()

    if "@" not in args.email_admin:
        sys.exit("--email-admin no parece un correo")

    contexto = kubectl("config", "current-context").stdout.strip()
    if not contexto or contexto == "docker-desktop" or "minikube" in contexto:
        sys.exit(f"El contexto de kubectl es {contexto!r}: es el clúster LOCAL. Conéctate al de Google Cloud primero "
                 "(gcloud container clusters get-credentials ...). No lo toco para no pisar tu Secret de desarrollo.")
    if not args.dry_run and kubectl("get", "secret", NOMBRE).returncode == 0:
        sys.exit(f"En {contexto} ya existe el Secret {NOMBRE}. No lo reemplazo: cambiar DB_PASSWORD con las bases ya "
                 f"creadas las dejaría inaccesibles. Si de verdad quieres empezar de cero (esto borra los datos): "
                 f"kubectl delete secret {NOMBRE} y borra también los volúmenes.")

    locales = claves_y_valores_locales()
    valores, password_admin, culqi_de_prueba = {}, secrets.token_urlsafe(14), False
    for clave in locales:
        if clave == "DB_USER":
            valores[clave] = DB_USER
        elif clave == "DB_PASSWORD":
            valores[clave] = secrets.token_urlsafe(24)  # solo A-Za-z0-9-_: sirve tal cual dentro de una URL de conexión
        elif clave == "JWT_SECRET" or clave.endswith("_SECRETO"):
            valores[clave] = secrets.token_hex(32)
        elif clave == "CULQI_PUBLIC_KEY":
            valores[clave] = args.culqi_public or locales[clave]
            culqi_de_prueba = culqi_de_prueba or not args.culqi_public
        elif clave == "CULQI_SECRET_KEY":
            valores[clave] = args.culqi_secret or locales[clave]
            culqi_de_prueba = culqi_de_prueba or not args.culqi_secret
        elif clave == "ADMIN_BOOTSTRAP_EMAIL":
            valores[clave] = args.email_admin
        elif clave == "ADMIN_BOOTSTRAP_PASSWORD":
            valores[clave] = password_admin
        elif clave in ("GEMINI_API_KEY", "SUNAT_RUC_TOKEN"):
            if locales[clave]:
                valores[clave] = locales[clave]
        else:
            sys.exit(f"El Secret de desarrollo tiene una clave que este script no sabe generar: {clave}. "
                     "Agrégala aquí (con su regla) antes de desplegar.")

    print(f"Contexto de kubectl: {contexto}")
    print(f"Claves ({len(valores)}): {', '.join(sorted(valores))}")
    if culqi_de_prueba:
        print("Culqi: se usan las llaves de PRUEBA (pk_test/sk_test): los pagos son simulados.")
    if "GEMINI_API_KEY" not in valores:
        print("Sin GEMINI_API_KEY: la fecha de cosecha se sugiere con la tabla de ciclos típicos, sin IA.")
    if args.dry_run:
        print("--dry-run: no se creó nada.")
        return

    manifiesto = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": NOMBRE}, "type": "Opaque", "stringData": valores}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(manifiesto, f)
        ruta = f.name
    try:
        r = kubectl("apply", "-f", ruta)  # por archivo temporal: los valores no pasan por la línea de comandos
    finally:
        os.remove(ruta)
    if r.returncode != 0:
        sys.exit(f"kubectl apply falló:\n{r.stderr}")
    print(r.stdout.strip())

    SALIDA.mkdir(exist_ok=True)
    destino = SALIDA / "credenciales-generadas.txt"
    destino.write_text(
        "Primer admin de Chakra Shop (lo crea Usuarios al arrancar si no hay ninguno).\n"
        "Guárdalo en tu gestor de contraseñas y borra este archivo.\n\n"
        f"correo:     {args.email_admin}\ncontraseña: {password_admin}\n",
        encoding="utf-8",
    )
    try:
        os.chmod(destino, 0o600)
    except OSError:
        pass
    print(f"Listo. El correo y la contraseña del admin están en {destino}")


if __name__ == "__main__":
    main()
