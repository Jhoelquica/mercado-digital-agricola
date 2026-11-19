#!/usr/bin/env python
"""Copia el código común (comun/) a los servicios que lo usan (<servicio>/comun/) y comprueba que las copias coincidan.

Cada imagen se construye solo con la carpeta de su servicio (README, docker-compose.yml, scripts/correr_pruebas.sh y
deploy/gcp/construir_imagenes.py), así que ningún servicio puede importar comun/ desde la raíz: cada uno lleva una copia,
generada desde comun/ con un encabezado que lo dice. Edita siempre comun/ y corre este script.

También mantiene la copia de deploy/rabbitmq/definiciones.json (lo que RabbitMQ importa al arrancar) dentro de la
ConfigMap rabbitmq-definiciones de k8s/rabbitmq.yaml: compose monta el archivo y Kubernetes la ConfigMap.

Y comprueba, sin arreglarlo (se corrige a mano):
- que cada dependencia que está en el requirements.txt de dos o más servicios tenga en todos la misma versión: el código
  común corre igual en todos solo si sus librerías son las mismas;
- que ningún servicio publique directo en RabbitMQ: todo evento pasa por la bandeja de salida (comun/bandeja_salida.py)
  y solo su relevo publica;
- que definiciones.json declare las colas de todos los consumidores (y sus .fallidos), durables y con sus enlaces: así
  existen desde que RabbitMQ arranca, aunque su consumidor todavía no se haya conectado.

Uso (desde la raíz del repositorio):
    python scripts/sincronizar_comun.py               # copia comun/ a los servicios
    python scripts/sincronizar_comun.py --verificar   # solo comprueba: sale con error si algo no coincide
"""
import argparse
import ast
import json
import re
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
# Los que reciben fotos (comun/imagenes.py) y los que reciben documentos en foto o PDF (comun/documentos.py).
CON_IMAGENES = ("productores", "productos", "transporte", "pagos")
CON_DOCUMENTOS = ("usuarios", "transporte")
# Los que publican eventos: siempre por su bandeja de salida (comun/bandeja_salida.py).
CON_BANDEJA_DE_SALIDA = ("productos", "pedidos", "transporte", "pagos")

# Qué servicios llevan cada archivo de comun/. Un archivo nuevo en comun/ tiene que agregarse aquí.
DESTINOS = {
    "__init__.py": TODOS,
    "auth.py": TODOS,
    "bandeja_salida.py": CON_BANDEJA_DE_SALIDA,
    "conftest.py": TODOS,
    "database.py": TODOS,
    "documentos.py": CON_DOCUMENTOS,
    "imagenes.py": CON_IMAGENES,
    "inbox.py": CON_INBOX,
    "logging.py": TODOS,
    "migraciones.py": CON_MIGRACION_DE_COLUMNAS,
    "rabbitmq.py": CON_RABBITMQ,
    "test_bandeja_salida.py": CON_BANDEJA_DE_SALIDA,
    "test_database.py": TODOS,
    "test_documentos.py": CON_DOCUMENTOS,
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

# Ningún servicio publica directo: solo el relevo de la bandeja de salida, con el Publicador de comun/rabbitmq.py, que
# solo usa comun/bandeja_salida.py. Lo que un consumidor devuelve a su propia cola para mandarlo a fallidos (_apartar) no
# es publicar un evento.
PUBLICAR_SOLO_EN = {("comun/rabbitmq.py", "Publicador.publicar"), ("comun/rabbitmq.py", "_apartar")}
USAR_EL_PUBLICADOR_SOLO_EN = {"comun/rabbitmq.py", "comun/bandeja_salida.py"}
# TEMPORAL: los servicios que todavía publican directo (con comun.rabbitmq.publicar_evento) mientras pasan, uno por
# commit, a la bandeja de salida. Con el último se borran esto y publicar_evento.
PUBLICAN_DIRECTO_TODAVIA = {"transporte", "productos"}
CARPETAS_QUE_NO_SON_CODIGO = {"venv", ".venv", "__pycache__", "node_modules"}

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


def versiones_de_las_dependencias() -> dict:
    """{paquete: {servicio: versión}} según los requirements.txt (paquete==versión), con el nombre normalizado como pip."""
    versiones = {}
    for servicio in TODOS:
        for linea in _texto(RAIZ / servicio / "requirements.txt").splitlines():
            m = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*==\s*([^\s;#]+)\s*", linea)
            if m:
                versiones.setdefault(re.sub(r"[-_.]+", "-", m.group(1)).lower(), {})[servicio] = m.group(2)
    return versiones


def dependencias_con_versiones_distintas() -> list:
    """Las compartidas (en dos o más servicios) que no tienen la misma versión en todos, en frases."""
    distintas = []
    for paquete, por_servicio in sorted(versiones_de_las_dependencias().items()):
        if len(por_servicio) > 1 and len(set(por_servicio.values())) > 1:
            grupos = {}
            for servicio, version in por_servicio.items():
                grupos.setdefault(version, []).append(servicio)
            detalle = "; ".join(f"{version} en {', '.join(servicios)}" for version, servicios in sorted(grupos.items()))
            distintas.append(f"requirements.txt: {paquete} tiene versiones distintas ({detalle})")
    return distintas


def _archivos_de_codigo():
    """(ruta relativa, ruta) de cada .py de comun/ y de los servicios que corre en producción: sin las pruebas, sus
    ayudas ni las copias de comun/ (que son iguales a comun/)."""
    for carpeta in (ORIGEN, *(RAIZ / servicio for servicio in TODOS)):
        for ruta in sorted(carpeta.rglob("*.py")):
            partes = ruta.relative_to(RAIZ).parts
            if partes[0] != "comun" and "comun" in partes[1:-1]:
                continue
            if ruta.name.startswith("test_") or ruta.name == "conftest.py" or CARPETAS_QUE_NO_SON_CODIGO & set(partes):
                continue
            yield "/".join(partes), ruta


class _Publicaciones(ast.NodeVisitor):
    """Las llamadas que publican (basic_publish, publicar_evento) y los usos del Publicador, con la función donde están."""

    def __init__(self):
        self.pila, self.encontradas = [], []

    def _dentro(self, nodo):
        self.pila.append(nodo.name)
        self.generic_visit(nodo)
        self.pila.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _dentro

    def visit_Call(self, nodo):
        nombre = nodo.func.attr if isinstance(nodo.func, ast.Attribute) else getattr(nodo.func, "id", None)
        if nombre in ("basic_publish", "publicar_evento"):
            self.encontradas.append((nodo.lineno, nombre, ".".join(self.pila)))
        self.generic_visit(nodo)

    def visit_Name(self, nodo):
        if nodo.id == "Publicador":
            self.encontradas.append((nodo.lineno, "Publicador", ".".join(self.pila)))

    def visit_Attribute(self, nodo):
        if nodo.attr == "Publicador":
            self.encontradas.append((nodo.lineno, "Publicador", ".".join(self.pila)))
        self.generic_visit(nodo)


def publicaciones_directas() -> list:
    """Lo que publica en RabbitMQ fuera del relevo de la bandeja de salida, en frases."""
    permitidas = set(PUBLICAR_SOLO_EN)
    if PUBLICAN_DIRECTO_TODAVIA:
        permitidas.add(("comun/rabbitmq.py", "publicar_evento"))
    encontradas = []
    for relativa, ruta in _archivos_de_codigo():
        visitante = _Publicaciones()
        visitante.visit(ast.parse(_texto(ruta), filename=relativa))
        for linea, nombre, donde in visitante.encontradas:
            if nombre == "Publicador":
                bien = relativa in USAR_EL_PUBLICADOR_SOLO_EN
            elif nombre == "publicar_evento":
                bien = relativa.split("/")[0] in PUBLICAN_DIRECTO_TODAVIA
            else:
                bien = (relativa, donde) in permitidas
            if not bien:
                encontradas.append(f"{relativa}:{linea}: {nombre} fuera del relevo de la bandeja de salida (en "
                                   f"{donde or 'el módulo'}): los eventos se dejan con registrar_evento")
    return encontradas


def suscripciones_de_los_consumidores() -> set:
    """{(exchange, cola)} de las SUSCRIPCIONES de cada rabbitmq_consumer.py: lo que declara cada consumidor al conectar."""
    suscripciones = set()
    for servicio in TODOS:
        ruta = RAIZ / servicio / "rabbitmq_consumer.py"
        if not ruta.exists():
            continue
        arbol = ast.parse(_texto(ruta), filename=str(ruta))
        constantes = {nodo.targets[0].id: nodo.value.value for nodo in arbol.body
                      if isinstance(nodo, ast.Assign) and isinstance(nodo.targets[0], ast.Name)
                      and isinstance(nodo.value, ast.Constant) and isinstance(nodo.value.value, str)}
        for nodo in arbol.body:
            if isinstance(nodo, ast.Assign) and getattr(nodo.targets[0], "id", None) == "SUSCRIPCIONES":
                for tupla in nodo.value.elts:
                    exchange, cola = (e.value if isinstance(e, ast.Constant) else constantes[e.id]
                                      for e in tupla.elts[:2])
                    suscripciones.add((exchange, cola))
    return suscripciones


def topologia_sin_declarar() -> list:
    """Lo que los consumidores declaran al conectarse y definiciones.json no (o declara distinto), en frases."""
    definiciones = json.loads(_texto(DEFINICIONES_RABBITMQ))
    colas = {cola["name"]: cola for cola in definiciones.get("queues", [])}
    exchanges = {exchange["name"]: exchange for exchange in definiciones.get("exchanges", [])}
    enlaces = {(e["source"], e["destination"], e["routing_key"]) for e in definiciones.get("bindings", [])}
    encontrados = []

    def exigir_cola(nombre):
        if nombre not in colas:
            encontrados.append(f"definiciones.json no declara la cola {nombre}")
        elif not colas[nombre].get("durable") or colas[nombre].get("auto_delete") or colas[nombre].get("arguments"):
            encontrados.append(f"definiciones.json: la cola {nombre} debe ser durable, sin auto_delete ni argumentos "
                               "(como la declaran los consumidores)")

    def exigir_exchange(nombre, tipo):
        if exchanges.get(nombre, {}).get("type") != tipo or not exchanges.get(nombre, {}).get("durable"):
            encontrados.append(f"definiciones.json no declara el exchange {nombre} ({tipo}, durable)")

    def exigir_enlace(origen, destino, clave):
        if (origen, destino, clave) not in enlaces:
            encontrados.append(f"definiciones.json no liga {destino} a {origen} (routing key {clave!r})")

    suscripciones = suscripciones_de_los_consumidores()
    con_consumidor = {cola for _, cola in suscripciones}
    exigir_exchange("fallidos", "direct")
    for exchange, cola in sorted(suscripciones):
        exigir_exchange(exchange, "fanout")
        exigir_cola(cola)
        exigir_cola(f"{cola}.fallidos")
        exigir_enlace(exchange, cola, "")
        exigir_enlace("fallidos", f"{cola}.fallidos", cola)
    for nombre in sorted(colas):
        if nombre != "fallidos.sin_ruta" and nombre.removesuffix(".fallidos") not in con_consumidor:
            encontrados.append(f"definiciones.json declara la cola {nombre}, que no tiene consumidor: se llenaría")
    return encontrados


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
    encontrados.extend(dependencias_con_versiones_distintas())
    encontrados.extend(publicaciones_directas())
    encontrados.extend(topologia_sin_declarar())
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
        print("Lo común no coincide:", file=sys.stderr)
        for problema in encontrados:
            print(f"  - {problema}", file=sys.stderr)
        print("Las copias se arreglan editando la fuente (comun/ o deploy/rabbitmq/definiciones.json) y corriendo "
              "python scripts/sincronizar_comun.py; lo demás, a mano.", file=sys.stderr)
        sys.exit(1)
    copias = sum(len(_destinos_de(s)) for s in TODOS)
    compartidas = sum(1 for v in versiones_de_las_dependencias().values() if len(v) > 1)
    print(f"comun/: las {copias} copias coinciden ({len(DESTINOS)} archivos en {len(TODOS)} servicios), las "
          f"definiciones de RabbitMQ en k8s/rabbitmq.yaml (con las {len(suscripciones_de_los_consumidores())} colas de "
          f"los consumidores), las {compartidas} dependencias compartidas tienen la misma versión en todos, y solo el "
          "relevo de la bandeja de salida publica"
          + (f" (salvo {', '.join(sorted(PUBLICAN_DIRECTO_TODAVIA))}, que todavía no pasaron a ella)."
             if PUBLICAN_DIRECTO_TODAVIA else "."))


if __name__ == "__main__":
    main()
