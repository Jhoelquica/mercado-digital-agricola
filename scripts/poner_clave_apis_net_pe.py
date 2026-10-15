#!/usr/bin/env python
"""Guarda la clave de apis.net.pe en el Secret `credenciales-app` del clúster ACTUAL de kubectl, para las dos consultas
que dependen de ese servicio: el DNI en RENIEC (Pedidos: quien recibe un pedido en otra ciudad) y el RUC en SUNAT
(Usuarios: comprador "Para mi negocio"). Hasta el 2026-10-03 respondía sin clave; desde el 2026-10-14 responde
401 "Apikey Required / Falta de token".

La clave se pide sin mostrarla y nunca se imprime ni pasa por la línea de comandos. Antes de guardarla se prueba con un
DNI que no es de nadie (00000000): si apis.net.pe responde 401, no se guarda. Después reinicia Usuarios y Pedidos para
que la lean.

Uso (desde la raíz del repositorio, en una terminal):
  python scripts/poner_clave_apis_net_pe.py
"""
import getpass
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

SECRET = "credenciales-app"
DNI_DE_NADIE = "00000000"


def kubectl(*args):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def probar(clave):
    """El código HTTP de apis.net.pe con esa clave (401 = la clave no sirve)."""
    pedido = urllib.request.Request(f"https://api.apis.net.pe/v2/reniec/dni?numero={DNI_DE_NADIE}",
                                    headers={"Authorization": f"Bearer {clave}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as error:
        return error.code
    except urllib.error.URLError as error:
        sys.exit(f"No se pudo llegar a apis.net.pe ({error.reason}). Revisa la conexión a internet.")


def main():
    contexto = kubectl("config", "current-context").stdout.strip()
    if kubectl("get", "secret", SECRET).returncode != 0:
        sys.exit(f"En el contexto {contexto!r} no existe el Secret {SECRET}.")
    print(f"Contexto de kubectl: {contexto}")
    clave = getpass.getpass("Clave (token) de apis.net.pe (no se muestra): ").strip()
    if not clave:
        sys.exit("No escribiste ninguna clave: no se cambió nada.")
    codigo = probar(clave)
    if codigo == 401:
        sys.exit("apis.net.pe rechazó esa clave (401). Revisa que la copiaste completa: no se cambió nada.")
    print(f"La clave funciona (apis.net.pe respondió {codigo} para un DNI que no es de nadie).")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"stringData": {"RENIEC_TOKEN": clave, "SUNAT_RUC_TOKEN": clave}}, f)
        ruta = f.name
    try:
        r = kubectl("patch", "secret", SECRET, "--type", "merge", "--patch-file", ruta)
    finally:
        os.remove(ruta)
    if r.returncode != 0:
        sys.exit(f"kubectl patch falló:\n{r.stderr}")
    for servicio in ("pedidos", "usuarios"):
        kubectl("rollout", "restart", f"deployment/{servicio}")
    for servicio in ("pedidos", "usuarios"):
        r = kubectl("rollout", "status", f"deployment/{servicio}", "--timeout=240s")
        print(f"{servicio}: {'listo' if r.returncode == 0 else 'no terminó de reiniciar: ' + r.stderr.strip()}")
    print("Listo: RENIEC (DNI) y SUNAT (RUC) ya usan la clave.")


if __name__ == "__main__":
    main()
