#!/usr/bin/env python
"""Las claves con que se firman (RS256) y se verifican los tokens de sesión, en el entorno LOCAL: el clúster de Docker
Desktop y docker compose. En Google Cloud las crea deploy/gcp/crear_secretos.py.

Dónde quedan (nada de esto va a git):
  - .claves/jwt/firma/privada.pem: la clave PRIVADA (la que firma; solo la tiene Usuarios).
  - .claves/jwt/publicas/<kid>.pem: las claves PÚBLICAS, una por kid (la de la privada actual y las que sigan vigentes).
    Docker compose monta estas dos carpetas.
  - k8s/secret.yaml: el Secret jwt-firma con la privada, como otro documento del archivo (no toca los demás).
  - k8s/jwt-claves-publicas.yaml: la ConfigMap jwt-claves-publicas con las públicas, para los 8 servicios.
El kid de una clave es su huella (los primeros 16 caracteres del SHA-256 de su forma DER), igual que comun/auth.kid_de.

Uso (desde la raíz del repositorio; necesita `pip install cryptography`):
    python scripts/claves_jwt.py nueva          # un par nuevo: la privada reemplaza a la anterior y la pública se suma
    python scripts/claves_jwt.py retirar KID    # saca una clave pública vieja (nunca la de la privada actual)
    python scripts/claves_jwt.py estado         # qué kids están publicados y cuál firma
    python scripts/claves_jwt.py aplicar        # al clúster docker-desktop, en el orden que no corta sesiones

La primera vez: `nueva` y `aplicar`, antes de `kubectl apply -f k8s/`. Rotar: `nueva` y `aplicar`; cuando venza el último
token firmado con la vieja (60 min), `retirar KID_VIEJO` y `aplicar`. Ver README, "Claves de firma de los tokens".
Nunca imprime una clave ni aplica k8s/secret.yaml entero: solo su documento jwt-firma, por la entrada estándar.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / ".claves" / "jwt"
PRIVADA = CARPETA / "firma" / "privada.pem"
PUBLICAS = CARPETA / "publicas"
SECRET_LOCAL = RAIZ / "k8s" / "secret.yaml"
CONFIGMAP = RAIZ / "k8s" / "jwt-claves-publicas.yaml"
CONTEXTO = "docker-desktop"
SERVICIOS = ("usuarios", "productores", "productos", "pedidos", "transporte", "notificaciones", "pagos", "certificacion")
BITS = 3072
MARCA = "# Lo escribe scripts/claves_jwt.py: no editar a mano."

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    sys.exit("Este script necesita la librería cryptography: pip install cryptography")


def kid_de(clave_publica) -> str:
    der = clave_publica.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def _pem_publica(clave_publica) -> bytes:
    return clave_publica.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def kid_que_firma():
    if not PRIVADA.exists():
        return None
    return kid_de(serialization.load_pem_private_key(PRIVADA.read_bytes(), password=None).public_key())


def kids_publicados() -> list:
    return sorted(p.stem for p in PUBLICAS.glob("*.pem")) if PUBLICAS.exists() else []


def _sangrar(texto: str, espacios: int) -> str:
    return "\n".join(" " * espacios + linea for linea in texto.strip().splitlines())


def _documento_secret() -> str:
    return (f"{MARCA}\n# Secret jwt-firma: la clave PRIVADA que firma los tokens (RS256). Solo la monta Usuarios.\n"
            "apiVersion: v1\nkind: Secret\nmetadata:\n  name: jwt-firma\ntype: Opaque\nstringData:\n  privada.pem: |\n"
            f"{_sangrar(PRIVADA.read_text(encoding='ascii'), 4)}\n")


def _documentos(texto: str) -> list:
    """Los documentos YAML de un archivo, como texto (separados por líneas "---")."""
    documentos, actual = [], []
    for linea in texto.replace("\r\n", "\n").split("\n"):
        if linea.strip() == "---":
            documentos.append("\n".join(actual))
            actual = []
        else:
            actual.append(linea)
    documentos.append("\n".join(actual))
    return [d for d in documentos if d.strip()]


def _es_jwt_firma(documento: str) -> bool:
    lineas = documento.split("\n")
    return "kind: Secret" in lineas and "  name: jwt-firma" in lineas


def escribir_archivos():
    """k8s/secret.yaml (su documento jwt-firma) y k8s/jwt-claves-publicas.yaml, desde .claves/jwt."""
    if SECRET_LOCAL.exists():
        otros = [d for d in _documentos(SECRET_LOCAL.read_text(encoding="utf-8")) if not _es_jwt_firma(d)]
        SECRET_LOCAL.write_text("\n---\n".join(otros + [_documento_secret()]).strip("\n") + "\n", encoding="utf-8")
    else:
        print(f"Aviso: no existe {SECRET_LOCAL.relative_to(RAIZ)} (cópialo de deploy/local/secret.ejemplo.yaml): el "
              "Secret jwt-firma queda solo en .claves/jwt.")
    datos = "\n".join(f"  {kid}.pem: |\n{_sangrar((PUBLICAS / f'{kid}.pem').read_text(encoding='ascii'), 4)}"
                      for kid in kids_publicados())
    CONFIGMAP.write_text(
        f"{MARCA}\n# Las claves PÚBLICAS que verifican los tokens (RS256), una por kid. La montan los 8 servicios en\n"
        "# /etc/chakrashop/jwt/publicas (ver comun/auth.py). Varias a la vez: así se rota sin cortar sesiones.\n"
        f"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: jwt-claves-publicas\ndata:\n{datos}\n", encoding="utf-8")


def nueva():
    clave = rsa.generate_private_key(public_exponent=65537, key_size=BITS)
    kid, anterior = kid_de(clave.public_key()), kid_que_firma()
    PUBLICAS.mkdir(parents=True, exist_ok=True)
    PRIVADA.parent.mkdir(parents=True, exist_ok=True)
    (PUBLICAS / f"{kid}.pem").write_bytes(_pem_publica(clave.public_key()))
    PRIVADA.write_bytes(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    escribir_archivos()
    print(f"Clave nueva: kid {kid} ({BITS} bits). Ahora firma esta" + (f"; la de antes ({anterior}) sigue publicada "
          "para que sus tokens valgan hasta que venzan." if anterior else "."))
    print("Siguiente paso: python scripts/claves_jwt.py aplicar (y, en compose, docker compose up -d).")


def retirar(kid: str):
    if kid == kid_que_firma():
        sys.exit(f"{kid} es la clave que firma ahora: no se retira (primero `nueva`, y retírala cuando venzan sus tokens).")
    if kid not in kids_publicados():
        sys.exit(f"{kid} no está publicada. Publicadas: {', '.join(kids_publicados()) or 'ninguna'}")
    (PUBLICAS / f"{kid}.pem").unlink()
    escribir_archivos()
    print(f"Retirada la clave {kid}. Siguiente paso: python scripts/claves_jwt.py aplicar")


def estado():
    firma = kid_que_firma()
    print(f"Firma: {firma or 'ninguna (corre `nueva`)'}")
    for kid in kids_publicados():
        print(f"Publicada: {kid}" + (" (la que firma)" if kid == firma else ""))
    if firma and firma not in kids_publicados():
        print("PROBLEMA: la clave que firma no está publicada: Usuarios no arrancaría.")


def _kubectl(*args, entrada=None):
    return subprocess.run(["kubectl", "--context", CONTEXTO, *args], capture_output=True, text=True, input=entrada,
                          encoding="utf-8", errors="replace")


def _reiniciar(servicios, volumen):
    """Reinicia los Deployments de `servicios` que montan `volumen` (los que todavía no lo montan, como en el primer
    despliegue, no ganan nada con reiniciarse) y espera a que queden listos. Devuelve los reiniciados."""
    existentes = []
    for servicio in servicios:
        r = _kubectl("get", "deployment", servicio, "-o", "jsonpath={.spec.template.spec.volumes[*].name}")
        if r.returncode == 0 and volumen in r.stdout.split():
            existentes.append(servicio)
    for servicio in existentes:
        _kubectl("rollout", "restart", f"deployment/{servicio}")
    for servicio in existentes:
        if _kubectl("rollout", "status", f"deployment/{servicio}", "--timeout=300s").returncode != 0:
            sys.exit(f"{servicio} no quedó listo después de reiniciarlo: revisa kubectl get pods")
    return existentes


def aplicar():
    """1) La ConfigMap con las públicas y reinicio de los 8 (todos conocen la clave nueva); 2) el Secret jwt-firma y
    reinicio de Usuarios (empieza a firmar con ella). En ese orden no hay ningún momento en que un servicio reciba un
    token con un kid que todavía no conoce."""
    firma = kid_que_firma()
    if not firma or firma not in kids_publicados():
        sys.exit("No hay una clave que firme publicada: corre primero python scripts/claves_jwt.py nueva")
    if not SECRET_LOCAL.exists():
        sys.exit(f"Falta {SECRET_LOCAL.relative_to(RAIZ)}: cópialo de deploy/local/secret.ejemplo.yaml y corre `nueva`.")
    escribir_archivos()
    r = _kubectl("apply", "-f", str(CONFIGMAP))
    if r.returncode != 0:
        sys.exit(f"No se pudo aplicar la ConfigMap: {r.stderr.strip()[:300]}")
    print(r.stdout.strip())
    if "unchanged" not in r.stdout:
        print("Reiniciados (conocen las claves públicas nuevas): "
              + (", ".join(_reiniciar(SERVICIOS, "jwt-claves-publicas")) or "ninguno (todavía no la montan)"))
    documento = next(d for d in _documentos(SECRET_LOCAL.read_text(encoding="utf-8")) if _es_jwt_firma(d))
    r = _kubectl("apply", "-f", "-", entrada=documento)  # solo este documento, por la entrada estándar
    if r.returncode != 0:
        sys.exit(f"No se pudo aplicar el Secret jwt-firma: {r.stderr.strip()[:300]}")
    print(r.stdout.strip())
    if "unchanged" not in r.stdout:
        print("Reiniciado (firma con la clave nueva): "
              + (", ".join(_reiniciar(["usuarios"], "jwt-firma")) or "ninguno (Usuarios todavía no lo monta)"))
    print(f"Listo: firma {firma}; publicadas: {', '.join(kids_publicados())}.")


def main():
    for salida in (sys.stdout, sys.stderr):
        if hasattr(salida, "reconfigure"):
            salida.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Las claves RS256 de los tokens en el entorno local.")
    sub = ap.add_subparsers(dest="accion", required=True)
    sub.add_parser("nueva", help="genera un par nuevo (la privada reemplaza a la anterior; la pública se suma)")
    retirar_ap = sub.add_parser("retirar", help="saca una clave pública vieja")
    retirar_ap.add_argument("kid")
    sub.add_parser("estado", help="qué claves están publicadas y cuál firma")
    sub.add_parser("aplicar", help="aplica la ConfigMap y el Secret jwt-firma al clúster docker-desktop")
    args = ap.parse_args()
    {"nueva": nueva, "estado": estado, "aplicar": aplicar}.get(args.accion, lambda: retirar(args.kid))()


if __name__ == "__main__":
    main()
