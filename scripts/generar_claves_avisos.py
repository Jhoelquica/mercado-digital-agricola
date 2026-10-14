#!/usr/bin/env python
"""Enciende los avisos fuera de la aplicación (H6): guarda en el Secret `credenciales-app` del clúster ACTUAL de kubectl
las claves VAPID de Web Push y, si se pide, los datos SMTP del correo de respaldo. Después reinicia Notificaciones para
que las lea (ver notificaciones/avisos_externos.py).

Ningún secreto se imprime ni pasa por la línea de comandos: la clave privada se genera aquí y la contraseña SMTP se pide
sin mostrarla. La clave pública sí se muestra (es pública: la usa el navegador).

No reemplaza claves VAPID que ya existan (cambiarlas deja sin avisos a todos los celulares que ya los activaron, hasta
que los vuelvan a activar) salvo con --reemplazar.

Uso (desde la raíz del repositorio):
  python scripts/generar_claves_avisos.py --contacto mailto:tu@correo.com
  python scripts/generar_claves_avisos.py --contacto mailto:tu@correo.com --smtp-host smtp.gmail.com \\
      --smtp-usuario tu@gmail.com --smtp-remitente "ChakraShop <tu@gmail.com>"   # pide la contraseña de aplicación
"""
import argparse
import getpass
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "notificaciones"))
import claves_vapid  # noqa: E402

SECRET = "credenciales-app"


def kubectl(*args):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser(description="Guarda las claves de los avisos al celular (y el SMTP) en el Secret.")
    ap.add_argument("--contacto", required=True, help='"mailto:<correo>" de quien opera ChakraShop')
    ap.add_argument("--reemplazar", action="store_true", help="reemplaza claves VAPID que ya existan")
    ap.add_argument("--smtp-host")
    ap.add_argument("--smtp-puerto", default="587")
    ap.add_argument("--smtp-usuario")
    ap.add_argument("--smtp-remitente")
    ap.add_argument("--url-app", help="dirección pública de ChakraShop (para el enlace del correo)")
    ap.add_argument("--si", action="store_true", help="no pide confirmación")
    args = ap.parse_args()
    if not args.contacto.startswith(("mailto:", "https://")):
        sys.exit('--contacto debe empezar con "mailto:" o "https://"')

    contexto = kubectl("config", "current-context").stdout.strip()
    if kubectl("get", "secret", SECRET).returncode != 0:
        sys.exit(f"En el contexto {contexto!r} no existe el Secret {SECRET}.")
    ya_hay = kubectl("get", "secret", SECRET, "-o", "jsonpath={.data.VAPID_CLAVE_PRIVADA}").stdout.strip()

    datos = {"VAPID_CONTACTO": args.contacto}
    publica = None
    if ya_hay and not args.reemplazar:
        print("Las claves VAPID ya existen: se dejan como están (usa --reemplazar para cambiarlas).")
    else:
        publica, privada = claves_vapid.generar()
        datos.update(VAPID_CLAVE_PUBLICA=publica, VAPID_CLAVE_PRIVADA=privada)
    if args.smtp_host:
        if not args.smtp_remitente:
            sys.exit("Con --smtp-host hace falta --smtp-remitente.")
        datos.update(SMTP_HOST=args.smtp_host, SMTP_PUERTO=str(args.smtp_puerto), SMTP_REMITENTE=args.smtp_remitente)
        if args.smtp_usuario:
            datos["SMTP_USUARIO"] = args.smtp_usuario
            datos["SMTP_CLAVE"] = getpass.getpass("Contraseña SMTP (no se muestra): ")
    if args.url_app:
        datos["URL_APP"] = args.url_app

    print(f"Contexto de kubectl: {contexto}")
    print("Claves que se guardan en el Secret:", ", ".join(sorted(datos)))
    if not args.si and input("¿Seguir? (si/no) ").strip().lower() not in ("si", "sí", "s"):
        sys.exit("No se cambió nada.")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"stringData": datos}, f)
        ruta = f.name
    try:
        r = kubectl("patch", "secret", SECRET, "--type", "merge", "--patch-file", ruta)
    finally:
        os.remove(ruta)
    if r.returncode != 0:
        sys.exit(f"kubectl patch falló:\n{r.stderr}")
    r = kubectl("rollout", "restart", "deployment/notificaciones")
    print("Listo." if r.returncode == 0 else f"Guardado, pero no se pudo reiniciar Notificaciones:\n{r.stderr}")
    if publica:
        print(f"Clave pública (no es secreta): {publica}")


if __name__ == "__main__":
    main()
