#!/usr/bin/env python
"""Copia el código común (comun/) a los servicios que lo usan (<servicio>/comun/) y comprueba que las copias coincidan.

Cada imagen se construye solo con la carpeta de su servicio (README, docker-compose.yml, scripts/correr_pruebas.sh y
deploy/gcp/construir_imagenes.py), así que ningún servicio puede importar comun/ desde la raíz: cada uno lleva una copia,
generada desde comun/ con un encabezado que lo dice. Edita siempre comun/ y corre este script.

También mantiene la copia de deploy/rabbitmq/definiciones.json (lo que RabbitMQ importa al arrancar) dentro de la
ConfigMap rabbitmq-definiciones de k8s/rabbitmq.yaml: compose monta el archivo y Kubernetes la ConfigMap.

Uso (desde la raíz del repositorio):
    python scripts/sincronizar_comun.py               # copia comun/ a los servicios
    python scripts/sincronizar_comun.py --verificar   # solo comprueba: sale con error si algo no coincide
"""
import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ORIGEN = RAIZ / "comun"
TODOS = ("usuarios", "productores", "productos", "pedidos", "transporte", "notificaciones", "pagos", "certificacion")
# Los que usan RabbitMQ (y tienen pika): Productos publica; Pedidos, Transporte y Pagos publican y consumen;
# Notificaciones consume.
CON_RABBITMQ = ("productos", "pedidos", "transporte", "notificaciones", "pagos")
# Los consumidores que se protegen de los mensajes repetidos con la tabla eventos_procesados (comun/inbox.py).
CON_INBOX = ("pedidos", "notificaciones")
# Los que agregan solas las columnas nuevas de sus tablas (comun/migraciones.py): todos menos Certificación.
CON_MIGRACION_DE_COLUMNAS = ("usuarios", "productores", "productos", "pedidos", "transporte", "notificaciones", "pagos")

# Qué servicios llevan cada archivo de comun/. Un archivo nuevo en comun/ tiene que agregarse aquí.
DESTINOS = {
    "__init__.py": TODOS,
    "auth.py": TODOS,
    "conftest.py": TODOS,
    "database.py": TODOS,
    "inbox.py": CON_INBOX,
    "logging.py": TODOS,
    "migraciones.py": CON_MIGRACION_DE_COLUMNAS,
    "rabbitmq.py": CON_RABBITMQ,
    "test_database.py": TODOS,
    "test_inbox.py": CON_INBOX,
    "test_logging.py": TODOS,
    "test_migraciones.py": CON_MIGRACION_DE_COLUMNAS,
    "test_rabbitmq.py": CON_RABBITMQ,
}
# Las pruebas y sus ayudas viajan con el código, pero un servicio no debe tener su propio módulo con el mismo nombre que
# uno común: el de comun/ dejaría de ser el único (y un logging.py suelto taparía al de Python).
NO_SON_MODULOS = {"__init__.py", "conftest.py"}
# Salvo migraciones.py: comun/migraciones.py tiene la migración común y el migraciones.py del servicio, las suyas.
PUEDEN_TENER_EL_SUYO = {"migraciones.py"}

ENCABEZADO = "# GENERADO desde comun/ — no editar aquí; edita comun/ y corre scripts/sincronizar_comun.py\n"

DEFINICIONES_RABBITMQ = RAIZ / "deploy" / "rabbitmq" / "definiciones.json"
MANIFIESTO_RABBITMQ = RAIZ / "k8s" / "rabbitmq.yaml"
CLAVE_DE_LAS_DEFINICIONES = "  definiciones.json: |\n"  # la clave de la ConfigMap; el JSON va debajo, con 4 espacios
SANGRIA = " " * 4


def _texto(ruta: Path) -> str:
    # Sin distinguir fin de línea: git en Windows puede dejar CRLF en la copia de trabajo (core.autocrlf).
    return ruta.read_text(encoding="utf-8").replace("\r\n", "\n")


def _bloque_de_definiciones(manifiesto: str):
    """Dónde empieza y termina el JSON de definiciones.json dentro de k8s/rabbitmq.yaml, o None si no está la clave."""
    if CLAVE_DE_LAS_DEFINICIONES not in manifiesto:
        return None
    inicio = fin = manifiesto.index(CLAVE_DE_LAS_DEFINICIONES) + len(CLAVE_DE_LAS_DEFINICIONES)
    for linea in manifiesto[inicio:].splitlines(keepends=True):
        if linea.strip() and not linea.startswith(SANGRIA):
            break
        fin += len(linea)
    return inicio, fin


def _definiciones_esperadas() -> str:
    return "".join(SANGRIA + linea if linea.strip() else linea
                   for linea in _texto(DEFINICIONES_RABBITMQ).splitlines(keepends=True))


def _esperado(archivo: str) -> str:
    return ENCABEZADO + _texto(ORIGEN / archivo)


def _destinos_de(servicio: str) -> set:
    return {archivo for archivo, servicios in DESTINOS.items() if servicio in servicios}


def problemas() -> list:
    """Todo lo que no coincide, en frases. Vacío si las copias están al día."""
    encontrados = []
    en_origen = {p.name for p in ORIGEN.glob("*.py")}
    for archivo in sorted(en_origen - DESTINOS.keys()):
        encontrados.append(f"comun/{archivo} no está en DESTINOS de scripts/sincronizar_comun.py")
    for archivo in sorted(DESTINOS.keys() - en_origen):
        encontrados.append(f"DESTINOS nombra comun/{archivo}, que no existe")
    for archivo in sorted(en_origen):
        if _texto(ORIGEN / archivo).startswith(ENCABEZADO):
            encontrados.append(f"comun/{archivo} lleva el encabezado de copia: es la fuente, no una copia")
    for servicio in TODOS:
        carpeta = RAIZ / servicio / "comun"
        deberian = _destinos_de(servicio) & en_origen
        for archivo in sorted(deberian):
            copia = carpeta / archivo
            if not copia.exists():
                encontrados.append(f"{servicio}/comun/{archivo} no existe")
            elif _texto(copia) != _esperado(archivo):
                encontrados.append(f"{servicio}/comun/{archivo} no coincide con comun/{archivo}")
        sobrantes = {p.name for p in carpeta.glob("*.py")} - deberian if carpeta.exists() else set()
        for archivo in sorted(sobrantes):
            encontrados.append(f"{servicio}/comun/{archivo} sobra: no sale de comun/ para este servicio")
        for archivo in sorted(deberian - NO_SON_MODULOS - PUEDEN_TENER_EL_SUYO):
            if (RAIZ / servicio / archivo).exists():
                encontrados.append(f"{servicio}/{archivo} existe además de {servicio}/comun/{archivo}: bórralo")
    manifiesto = _texto(MANIFIESTO_RABBITMQ)
    bloque = _bloque_de_definiciones(manifiesto)
    if bloque is None:
        encontrados.append("k8s/rabbitmq.yaml no tiene la ConfigMap rabbitmq-definiciones (definiciones.json: |)")
    elif manifiesto[bloque[0]:bloque[1]] != _definiciones_esperadas():
        encontrados.append("k8s/rabbitmq.yaml: su definiciones.json no coincide con deploy/rabbitmq/definiciones.json")
    return encontrados


def sincronizar() -> int:
    """Escribe las copias que falten o no coincidan y borra las que sobran (solo si son copias generadas)."""
    cambios = 0
    for servicio in TODOS:
        carpeta = RAIZ / servicio / "comun"
        deberian = _destinos_de(servicio)
        if deberian:
            carpeta.mkdir(exist_ok=True)
        for archivo in sorted(deberian):
            copia = carpeta / archivo
            if not copia.exists() or _texto(copia) != _esperado(archivo):
                copia.write_text(_esperado(archivo), encoding="utf-8", newline="\n")
                print(f"  escrito  {servicio}/comun/{archivo}")
                cambios += 1
        for sobrante in sorted(carpeta.glob("*.py")) if carpeta.exists() else []:
            if sobrante.name not in deberian and _texto(sobrante).startswith(ENCABEZADO):
                sobrante.unlink()
                print(f"  borrado  {servicio}/comun/{sobrante.name}")
                cambios += 1
    manifiesto = _texto(MANIFIESTO_RABBITMQ)
    bloque = _bloque_de_definiciones(manifiesto)
    if bloque and manifiesto[bloque[0]:bloque[1]] != _definiciones_esperadas():
        MANIFIESTO_RABBITMQ.write_text(manifiesto[:bloque[0]] + _definiciones_esperadas() + manifiesto[bloque[1]:],
                                       encoding="utf-8", newline="\n")
        print("  escrito  k8s/rabbitmq.yaml (definiciones.json)")
        cambios += 1
    return cambios


def main():
    for salida in (sys.stdout, sys.stderr):  # la consola de Windows no es UTF-8: sin esto, los acentos salen rotos
        if hasattr(salida, "reconfigure"):
            salida.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Copia comun/ a los servicios o comprueba que las copias coincidan.")
    parser.add_argument("--verificar", action="store_true", help="solo comprobar (para scripts/correr_pruebas.sh)")
    args = parser.parse_args()
    if not args.verificar:
        cambios = sincronizar()
        print(f"{cambios} archivo(s) actualizado(s)." if cambios else "Las copias ya estaban al día.")
    encontrados = problemas()
    if encontrados:
        print("El código común no coincide con sus copias:", file=sys.stderr)
        for problema in encontrados:
            print(f"  - {problema}", file=sys.stderr)
        if args.verificar:
            print("Edita la fuente (comun/ o deploy/rabbitmq/definiciones.json), no las copias, y corre: "
                  "python scripts/sincronizar_comun.py", file=sys.stderr)
        sys.exit(1)
    copias = sum(len(_destinos_de(s)) for s in TODOS)
    print(f"comun/: las {copias} copias coinciden ({len(DESTINOS)} archivos en {len(TODOS)} servicios), y las "
          "definiciones de RabbitMQ en k8s/rabbitmq.yaml.")


if __name__ == "__main__":
    main()
