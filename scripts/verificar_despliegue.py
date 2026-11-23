#!/usr/bin/env python
"""Comprueba que un despliegue de Chakra Shop está arriba y sano. SOLO LEE: no crea ni cambia datos, y no imprime tokens
ni contraseñas. Sin dependencias (solo la biblioteca estándar de Python 3.9+).

Casos de uso:
  python scripts/verificar_despliegue.py --contexto docker-desktop       # clúster local, por el gateway (http://localhost:8000)
  python scripts/verificar_despliegue.py --base https://35-225-121-28.sslip.io \\
      --contexto gke_mecadoagricoladigital_us-central1-a_chakrashop      # Google Cloud (HTTPS, certificado, puertos cerrados)
  python scripts/verificar_despliegue.py --directo --compose             # docker-compose (puertos directos, sin gateway)

Qué comprueba:
  - El frontend responde (/ y /healthz) y los 8 servicios contestan GET /salud.
  - Datos y rutas públicas: catálogo, condiciones de compra, una foto de producto y el QR (cadena íntegra).
  - Seguridad (gateway): una ruta protegida sin sesión da 401; una ruta pública con un token inválido da 401 (el
    ForwardAuth corre también en las rutas generales); /docs, /openapi.json y /metrics dan 403. Con --contexto, toda
    ruta de /api de los IngressRoute pasa por el ForwardAuth, auth-jwt o auth-sesion (FALLA si alguna no).
  - Tokens RS256 (con --contexto): la clave privada (Secret jwt-firma) solo la monta Usuarios y su archivo solo está en
    sus pods, ningún Deployment ni el Secret credenciales-app usan ya JWT_SECRET, la ConfigMap jwt-claves-publicas
    tiene al menos una clave, y el gateway rechaza un token HS256 forjado con esa clave pública, en PEM y en DER.
  - HTTPS (si --base es https): certificado válido (avisa si es el de PRUEBA o vence pronto), http redirige a https, y los
    puertos internos (9000, 8006, 8080, 5432) NO responden desde fuera.
  - Kubernetes (con --contexto): todos los pods listos y ninguno en CrashLoopBackOff (FALLA), ningún contenedor que se
    haya reiniciado después de estar listo (AVISO), réplicas deseadas = listas y RabbitMQ (abajo).
    Los reinicios acumulados desde que se creó cada pod se muestran solo como dato ([INFO]).
  - Compose (con --compose): todos los contenedores en marcha y sanos, y RabbitMQ.
  - RabbitMQ: todas las colas durables, cada una con consumidores y con su policy de fallidos, y fallidos.sin_ruta como
    alternate exchange de fallidos (FALLA); <cola>.fallidos y fallidos.sin_ruta vacías (AVISO si no).
  - Conciliación (scripts/conciliacion.py, en el clúster o en compose): pagos, pedidos, envíos y recojos coinciden entre
    servicios, y ningún evento espera más de 5 minutos en una bandeja de salida (AVISO si no).
  - Garantías en la base (en el clúster o en compose): existen los índices únicos de un pedido pendiente por comprador,
    un pago por pedido y un cargo de Culqi por pago (FALLA si falta alguno).
  - Transferencias: ninguna lleva más de HORAS_MAX_REVISION (12) horas en revisión esperando al admin (AVISO si no).
  - Opcional: si defines VERIFICAR_EMAIL y VERIFICAR_PASSWORD (variables de entorno, nunca por línea de comandos) prueba el login.

Código de salida: 0 = todo bien (los AVISO no cuentan), 1 = hay al menos un FALLA.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

import conciliacion  # scripts/conciliacion.py: lo inconsistente entre servicios

SERVICIOS = {"usuarios": 8006, "productores": 8001, "productos": 8002, "pedidos": 8003,
             "transporte": 8004, "notificaciones": 8005, "pagos": 8007, "certificacion": 8008}
PUERTO_FRONTEND_COMPOSE = 8090
PUERTO_MINIO = 9000
# pedidos_{pagos,envios}, transporte_{pagos,productos}, notificaciones_{pedidos,envios,productos,pagos} y pagos_{pedidos,envios}
COLAS_ESPERADAS = 10

resultados = []  # (nivel, nombre, detalle)


def registrar(nivel, nombre, detalle=""):
    resultados.append((nivel, nombre, detalle))
    etiqueta = {"OK": "[ OK ]", "FALLA": "[FALLA]", "AVISO": "[AVISO]", "INFO": "[INFO]"}[nivel]
    print(f"{etiqueta} {nombre}" + (f"  -  {detalle}" if detalle else ""), flush=True)


def comprobar(nombre, condicion, detalle_ok="", detalle_mal="", nivel_mal="FALLA"):
    registrar("OK" if condicion else nivel_mal, nombre, detalle_ok if condicion else detalle_mal)
    return bool(condicion)


class SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def contexto_tls(verificar):
    contexto = ssl.create_default_context()
    if not verificar:
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
    return contexto


def pedir(url, timeout, verificar_tls=True, seguir_redirecciones=False, cabeceras=None):
    """(estado, cabeceras, cuerpo) — el estado es None si no se pudo conectar (con el motivo en cuerpo)."""
    manejadores = [urllib.request.HTTPSHandler(context=contexto_tls(verificar_tls))]
    if not seguir_redirecciones:
        manejadores.append(SinRedireccion())
    abrir = urllib.request.build_opener(*manejadores).open
    peticion = urllib.request.Request(url, headers={"User-Agent": "verificar-despliegue/1.0", **(cabeceras or {})})
    try:
        with abrir(peticion, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except Exception as e:  # sin conexión, TLS inválido, tiempo agotado…
        motivo = getattr(e, "reason", e)
        return None, {}, str(motivo).encode()


def como_json(cuerpo):
    try:
        return json.loads(cuerpo)
    except (ValueError, TypeError):
        return None


def kubectl(argumentos, contexto, timeout=60):
    if shutil.which("kubectl") is None:
        return None, "kubectl no está en el PATH"
    comando = ["kubectl"] + (["--context", contexto] if contexto else []) + argumentos
    try:
        r = subprocess.run(comando, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return None, "kubectl tardó más de %d s" % timeout
    if r.returncode != 0:
        return None, (r.stderr.strip().splitlines() or ["error de kubectl"])[0][:200]
    return r.stdout, ""


MARGEN_LISTO_S = 30  # holgura por el ritmo de las sondas (startupProbe cada 5 s, readinessProbe cada 10 s)
TERMINACIONES_DEL_NODO = ("Unknown", "ContainerStatusUnknown")  # el nodo (Docker Desktop) se apagó con el contenedor en marcha


def _fecha(texto):
    """'2026-11-16T17:11:25Z' → datetime con zona; None si no hay."""
    return datetime.fromisoformat(texto.replace("Z", "+00:00")) if texto else None


def _duracion(segundos):
    if segundos < 120:
        return f"{round(segundos)} s"
    if segundos < 7200:
        return f"{round(segundos / 60)} min"
    return f"{round(segundos / 3600)} h" if segundos < 172800 else f"{round(segundos / 86400)} d"


def revisar_pods(pods):
    """La sección 6 sobre `kubectl get pods -o json`:
    - no_listos: no están Running o algún contenedor no está listo (FALLA);
    - en_crashloop: algún contenedor esperando en CrashLoopBackOff (FALLA);
    - tras_listo: contenedores cuya instancia anterior se cayó DESPUÉS de haber estado lista (AVISO);
    - total: reinicios acumulados desde que se creó cada pod. Es solo un dato: cada reinicio de Docker Desktop suma uno
      por contenedor, y los arranques de antes también cuentan.

    Si la instancia que se cayó había estado lista no se puede leer directamente: Kubernetes guarda solo la ÚLTIMA
    terminación de cada contenedor (lastState.terminated) y solo el ÚLTIMO cambio de la condición Ready. Comparar la caída
    con la condición Ready no sirve: si el contenedor se cayó y volvió, Ready se puso de nuevo después de la caída. Se
    compara entonces cuánto vivió la instancia que se cayó con lo que tardó la actual en quedar lista: si vivió más (con
    MARGEN_LISTO_S de holgura por el ritmo de las sondas), llegó a estar lista. Una caída al arrancar (p. ej. sin la base
    todavía) vive unos segundos y no cuenta. Tampoco cuentan las terminaciones por un reinicio del nodo."""
    no_listos, en_crashloop, tras_listo, total = [], [], [], 0
    for p in pods:
        nombre = p["metadata"]["name"]
        estado_pod = p.get("status", {})
        estados = estado_pod.get("containerStatuses", [])
        total += sum(c.get("restartCount", 0) for c in estados)
        if estado_pod.get("phase") != "Running" or not estados or not all(c.get("ready") for c in estados):
            no_listos.append(nombre)
        listo_desde = next((_fecha(c.get("lastTransitionTime")) for c in estado_pod.get("conditions", [])
                            if c.get("type") == "ContainersReady" and c.get("status") == "True"), None)
        for c in estados:
            if ((c.get("state") or {}).get("waiting") or {}).get("reason") == "CrashLoopBackOff":
                en_crashloop.append(f"{nombre}/{c.get('name')}")
            anterior = (c.get("lastState") or {}).get("terminated")
            arranco = _fecha(((c.get("state") or {}).get("running") or {}).get("startedAt"))
            if not anterior or anterior.get("reason") in TERMINACIONES_DEL_NODO or not (listo_desde and arranco and c.get("ready")):
                continue
            inicio, fin = _fecha(anterior.get("startedAt")), _fecha(anterior.get("finishedAt"))
            if not (inicio and fin):
                continue
            vivio = (fin - inicio).total_seconds()
            if vivio > max(0.0, (listo_desde - arranco).total_seconds()) + MARGEN_LISTO_S:
                tras_listo.append(f"{nombre}: {anterior.get('reason') or 'terminó'} (código {anterior.get('exitCode')}) el "
                                  f"{fin:%d/%m %H:%M} UTC, después de {_duracion(vivio)} en marcha")
    return {"no_listos": no_listos, "en_crashloop": en_crashloop, "tras_listo": tras_listo, "total": total}


LISTAR_COLAS = ["rabbitmqctl", "list_queues", "-q", "--formatter", "json", "name", "consumers", "messages", "policy",
                "durable"]
LISTAR_LIGADURAS = ["rabbitmqctl", "list_bindings", "-q", "--formatter", "json", "source_name", "destination_name",
                    "routing_key"]
LISTAR_EXCHANGES = ["rabbitmqctl", "list_exchanges", "-q", "--formatter", "json", "name", "policy"]
# Lo rechazado que no tiene ruta en el exchange "fallidos" (la policy fallidos-sin-ruta le pone este alternate exchange).
COLA_SIN_RUTA = "fallidos.sin_ruta"


def es_de_fallidos(cola):
    return cola.endswith(".fallidos") or cola == COLA_SIN_RUTA


def leer_json(salida):
    """La salida de `rabbitmqctl ... --formatter json` como lista, o [] si no se pudo leer."""
    try:
        datos = json.loads(salida or "[]")
    except ValueError:
        return []
    return datos if isinstance(datos, list) else []


MIDDLEWARES_DEL_FORWARDAUTH = {"auth-jwt", "auth-sesion"}
ARCHIVO_DE_LA_CLAVE_PRIVADA = "/etc/chakrashop/jwt/firma/privada.pem"


def _b64url(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def token_hs256_forjado(secreto: bytes, kid: str) -> str:
    """Lo que intentaría un atacante con la clave pública (que es pública): un token "HS256" con ella como secreto
    HMAC y con todos los claims de uno bueno. Solo sirve para ver que el gateway lo rechaza."""
    ahora = int(time.time())
    inicio = (_b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode()) + "." + _b64url(json.dumps(
        {"sub": "00000000-0000-0000-0000-000000000000", "rol": "admin", "ver": 1, "iss": "chakrashop-usuarios",
         "aud": "chakrashop-api", "iat": ahora, "exp": ahora + 300}).encode()))
    return f"{inicio}.{_b64url(hmac.new(secreto, inicio.encode(), hashlib.sha256).digest())}"


def der_de_pem(pem: str) -> bytes:
    return base64.b64decode("".join(l for l in pem.strip().splitlines() if not l.startswith("-----")))


def comprobar_claves_de_firma(contexto_k):
    """RS256: la clave privada (Secret jwt-firma) solo la monta Usuarios y está solo en sus pods; nadie usa ya
    JWT_SECRET (ni el Secret credenciales-app lo tiene)."""
    salida, error = kubectl(["get", "deploy", "-o", "json"], contexto_k)
    if salida is None:
        registrar("FALLA", "Claves de firma: leer los Deployment", error)
        return
    con_la_privada, con_jwt_secret = [], []
    for deploy in json.loads(salida)["items"]:
        nombre, plantilla = deploy["metadata"]["name"], deploy["spec"]["template"]["spec"]
        usa = {v.get("secret", {}).get("secretName") for v in plantilla.get("volumes") or []}
        for contenedor in plantilla.get("containers", []) + plantilla.get("initContainers", []):
            for variable in contenedor.get("env") or []:
                referencia = (variable.get("valueFrom") or {}).get("secretKeyRef") or {}
                usa.add(referencia.get("name"))
                if variable.get("name") == "JWT_SECRET" or referencia.get("key") == "JWT_SECRET":
                    con_jwt_secret.append(nombre)
            usa.update((origen.get("secretRef") or {}).get("name") for origen in contenedor.get("envFrom") or [])
        if "jwt-firma" in usa:
            con_la_privada.append(nombre)
    comprobar("Clave privada de firma (Secret jwt-firma): solo la monta Usuarios", con_la_privada == ["usuarios"],
              "solo usuarios", f"la montan: {', '.join(con_la_privada) or 'ninguno'}")
    comprobar("Ningún Deployment usa JWT_SECRET (los tokens van con RS256)", not con_jwt_secret, "",
              ", ".join(sorted(set(con_jwt_secret))))
    salida, error = kubectl(["get", "secret", "credenciales-app", "-o", "jsonpath={.data}"], contexto_k)
    if salida is not None:
        comprobar("El Secret credenciales-app ya no tiene JWT_SECRET", "JWT_SECRET" not in json.loads(salida or "{}"),
                  "", "todavía la tiene: kubectl patch secret credenciales-app --type=json "
                      "-p='[{\"op\":\"remove\",\"path\":\"/data/JWT_SECRET\"}]'")
    mirar = ("import os; print(int(os.path.exists(%r)), int(any('PRIVATE KEY' in v for v in os.environ.values())))"
             % ARCHIVO_DE_LA_CLAVE_PRIVADA)
    en_pods = {}
    for servicio in SERVICIOS:
        salida, error = kubectl(["exec", f"deploy/{servicio}", "-c", servicio, "--", "python", "-c", mirar], contexto_k)
        en_pods[servicio] = (salida or "").split()[:2] if salida is not None else ["?", "?"]
    con_el_archivo = [s for s, (archivo, _) in en_pods.items() if archivo != "0"]
    en_el_entorno = [s for s, (_, entorno) in en_pods.items() if entorno != "0"]
    comprobar("Clave privada de firma: el archivo solo existe en los pods de Usuarios, y en ningún entorno",
              con_el_archivo == ["usuarios"] and not en_el_entorno, "solo en usuarios",
              f"archivo en: {', '.join(con_el_archivo) or 'ninguno'}; en variables de entorno: {', '.join(en_el_entorno) or 'ninguno'}")


def comprobar_tokens_forjados(base, contexto_k, timeout, tls):
    """El gateway rechaza (401) un token HS256 firmado con la clave pública publicada, en PEM y en DER, en una ruta
    general y en una que exige sesión."""
    salida, error = kubectl(["get", "configmap", "jwt-claves-publicas", "-o", "json"], contexto_k)
    if salida is None:
        registrar("FALLA", "Claves públicas del JWT (ConfigMap jwt-claves-publicas)", error)
        return
    datos = json.loads(salida).get("data") or {}
    comprobar("Claves públicas del JWT publicadas (ConfigMap jwt-claves-publicas)", bool(datos),
              f"{len(datos)} clave(s): {', '.join(k.removesuffix('.pem') for k in sorted(datos))}", "vacía")
    if not datos:
        return
    nombre, pem = sorted(datos.items())[0]
    aceptados = []
    for forma, secreto in (("PEM", pem.encode()), ("DER", der_de_pem(pem))):
        token = token_hs256_forjado(secreto, nombre.removesuffix(".pem"))
        for ruta in ("/api/productos/productos", "/api/usuarios/usuarios/00000000-0000-0000-0000-000000000000"):
            estado, _, _ = pedir(base + ruta, timeout, tls, cabeceras={"Authorization": f"Bearer {token}"})
            if estado != 401:
                aceptados.append(f"{forma} en {ruta}: HTTP {estado}")
    comprobar("Gateway: un token HS256 forjado con la clave pública (PEM y DER) se rechaza (401)", not aceptados,
              "4 de 4 rechazados", "; ".join(aceptados))


def rutas_de_api_sin_forwardauth(ingressroutes) -> list:
    """Las rutas del gateway que llegan a /api sin pasar por el ForwardAuth (k8s/traefik-middlewares.yaml): sin él, un
    token revocado seguiría valiendo ahí. No cuentan las que lo bloquean todo (denegar-todo) ni la del frontend, que es
    justamente la que no va a /api."""
    sin = []
    for ruta_ir in ingressroutes:
        for ruta in ruta_ir.get("spec", {}).get("routes", []):
            regla = " ".join(str(ruta.get("match", "")).split())
            if "/api" not in regla or regla.startswith("!PathPrefix"):
                continue
            nombres = {m.get("name") for m in ruta.get("middlewares") or []}
            if "denegar-todo" in nombres or nombres & MIDDLEWARES_DEL_FORWARDAUTH:
                continue
            sin.append(f"{ruta_ir['metadata']['name']}: {regla[:80]}")
    return sin


def comprobar_colas(colas, ligaduras, exchanges):
    """colas: list_queues (name, consumers, messages, policy, durable); ligaduras: list_bindings (source, destination,
    key); exchanges: list_exchanges (name, policy)."""
    colas = {c["name"]: c for c in colas if isinstance(c, dict) and "name" in c}
    if not colas:
        return registrar("FALLA", "RabbitMQ: colas con consumidores", "no se pudieron leer las colas")
    # Una cola no durable se pierde (con sus mensajes) al reiniciar RabbitMQ: todas lo son, también las de fallidos.
    no_durables = sorted(n for n, c in colas.items() if c.get("durable") is not True)
    comprobar("RabbitMQ: todas las colas son durables (también las de fallidos)", not no_durables, f"{len(colas)} colas",
              f"no durables: {no_durables}")
    # <cola>.fallidos guarda lo que falló 3 veces (comun/rabbitmq.py) y fallidos.sin_ruta lo que no tuvo ruta: nadie las
    # consume, se revisan a mano.
    de_trabajo = {n: c for n, c in colas.items() if not es_de_fallidos(n)}
    sin = sorted(n for n, c in de_trabajo.items() if not c.get("consumers"))
    comprobar("RabbitMQ: cada cola tiene consumidores conectados", len(de_trabajo) >= COLAS_ESPERADAS and not sin,
              f"{len(de_trabajo)} colas, consumidores por cola: {sorted({c.get('consumers') for c in de_trabajo.values()})}",
              f"colas sin consumidores: {sin}" if sin else f"solo {len(de_trabajo)} colas (se esperaban {COLAS_ESPERADAS})")
    # La policy "fallidos" (deploy/rabbitmq/definiciones.json) manda lo rechazado al exchange "fallidos", y este lo
    # reparte por el nombre de la cola: cada <cola>.fallidos tiene que estar ligada con ese nombre.
    ligadas = {(l.get("source_name"), l.get("destination_name"), l.get("routing_key")) for l in ligaduras
               if isinstance(l, dict)}
    sin_ruta = sorted(n for n, c in de_trabajo.items()
                      if c.get("policy") != "fallidos" or ("fallidos", f"{n}.fallidos", n) not in ligadas)
    comprobar("RabbitMQ: cada cola manda lo que falla a <cola>.fallidos (policy fallidos)", not sin_ruta,
              f"{len(de_trabajo)} colas con la policy y su cola de fallidos ligada",
              f"sin la policy o sin su cola de fallidos ligada: {sin_ruta}")
    politica_del_exchange = {e.get("name"): e.get("policy") for e in exchanges if isinstance(e, dict)}.get("fallidos")
    comprobar("RabbitMQ: fallidos.sin_ruta existe y es el alternate exchange de fallidos (policy fallidos-sin-ruta)",
              COLA_SIN_RUTA in colas and politica_del_exchange == "fallidos-sin-ruta", "",
              f"cola {COLA_SIN_RUTA}: {'sí' if COLA_SIN_RUTA in colas else 'no existe'}; policy del exchange fallidos: "
              f"{politica_del_exchange or 'ninguna'}")
    con_mensajes = sorted(f"{n}: {c.get('messages')}" for n, c in colas.items() if es_de_fallidos(n) and c.get("messages"))
    comprobar("RabbitMQ: las colas de fallidos y fallidos.sin_ruta están vacías", not con_mensajes, "",
              f"mensajes por revisar en el panel de RabbitMQ: {', '.join(con_mensajes)}", "AVISO")


# Un evento que espera más que esto en una bandeja de salida está atascado (el relevo publica cada 1,5 s).
LIMITE_PENDIENTE_EN_BANDEJA_S = 300


def comprobar_conciliacion(consultar, donde, contexto):
    """Avisos: lo inconsistente entre Pagos, Pedidos, Transporte y Productos (scripts/conciliacion.py) y los eventos
    que esperan en una bandeja de salida más de LIMITE_PENDIENTE_EN_BANDEJA_S. Las excepciones documentadas de este
    contexto (scripts/excepciones_conciliacion.json) se muestran como dato y no cuentan; una lista con problemas (una
    excepción fuera de docker-desktop: en producción debe estar vacía) da FALLA."""
    try:
        hallazgos = conciliacion.inconsistencias(consultar)
        cajas = conciliacion.bandejas(consultar)
    except Exception as error:  # noqa: BLE001 - un aviso, no un fallo del chequeo entero
        return registrar("AVISO", f"{donde}: conciliación entre servicios", f"no se pudo revisar: {error}")
    try:
        excepciones = conciliacion.cargar_excepciones()
    except (OSError, ValueError) as error:
        return registrar("FALLA", f"{donde}: lista de excepciones de la conciliación", f"no se pudo leer: {error}")
    problemas = conciliacion.problemas_de_excepciones(excepciones)
    comprobar(f"{donde}: excepciones de la conciliación solo en {conciliacion.CONTEXTO_DE_EXCEPCIONES} (en producción, "
              "ninguna)", not problemas, f"{len(excepciones)} en la lista", "; ".join(problemas))
    hallazgos, aplicadas, sin_uso = conciliacion.aplicar_excepciones(hallazgos, excepciones, contexto)
    if aplicadas or sin_uso:
        registrar("INFO", f"{donde}: excepciones documentadas de la conciliación (no cuentan)", "; ".join(
            [f"regla {e['regla']}, {e['id']}: {e['motivo'][:90]}… ({e['fecha']})" for e, _ in aplicadas]
            + [f"sin uso, se puede quitar: regla {e.get('regla')}, {e.get('id')}" for e in sin_uso]))
    comprobar(f"{donde}: pagos, pedidos, envíos y recojos coinciden (conciliación)", not hallazgos,
              "sin inconsistencias", f"{conciliacion.resumen(hallazgos)} (detalle: python scripts/conciliacion.py)",
              "AVISO")
    try:
        esperando = conciliacion.transferencias_en_revision_hace_mucho(consultar)
    except Exception as error:  # noqa: BLE001 - un aviso, no un fallo del chequeo entero
        esperando = [("?", f"no se pudo revisar: {error}")]
    comprobar(f"{donde}: ninguna transferencia espera más de {conciliacion.HORAS_MAX_REVISION} h en revisión (el admin la "
              "aprueba o la rechaza)", not esperando, "",
              "; ".join(f"pedido {pedido} hace {horas} h" for pedido, horas in esperando[:6]), "AVISO")
    atascadas = [f"{servicio}: {n} pendiente(s), el más antiguo de hace {segundos} s" for servicio, n, segundos in cajas
                 if n and segundos > LIMITE_PENDIENTE_EN_BANDEJA_S]
    sin_bandeja = [servicio for servicio, n, _ in cajas if n is None]
    pendientes = sum(n for _, n, _ in cajas if n)
    comprobar(f"{donde}: ningún evento espera más de {LIMITE_PENDIENTE_EN_BANDEJA_S // 60} minutos en una bandeja de "
              "salida", not atascadas,
              f"{pendientes} pendiente(s) ahora" + (f"; sin bandeja todavía: {', '.join(sin_bandeja)}" if sin_bandeja else ""),
              "; ".join(atascadas), "AVISO")


# Garantías en la base: los índices únicos que las migraciones de cada servicio crean al arrancar (pedidos/migraciones.py y
# pagos/migraciones.py). Si una migración no pudo (datos que ya la violan), el servicio arranca igual y la base deja de
# impedir lo que garantiza el índice: un segundo pedido sin pagar del mismo comprador, dos pagos de un pedido o un cargo de
# Culqi asignado a dos pagos. Por eso es FALLA.
INDICES_DE_GARANTIA = {
    "pedidos": ("uq_pedidos_pendiente_por_comprador",),
    "pagos": ("uq_pagos_pedido_id", "uq_pagos_culqi_charge_id", "uq_pagos_culqi_charge_id_saldo",
              "uq_intentos_pago_cargo_cobro"),
}


def comprobar_indices(consultar, donde):
    """FALLA si falta alguno de INDICES_DE_GARANTIA (o no se pueden leer)."""
    faltan = []
    try:
        for servicio, indices in INDICES_DE_GARANTIA.items():
            presentes = {fila[0] for fila in consultar(servicio, "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
            faltan += [f"{servicio}.{indice}" for indice in indices if indice not in presentes]
    except Exception as error:  # noqa: BLE001 - se informa como FALLA, no rompe el resto del chequeo
        return registrar("FALLA", f"{donde}: índices únicos de las garantías", f"no se pudieron leer: {error}")
    total = sum(len(indices) for indices in INDICES_DE_GARANTIA.values())
    comprobar(f"{donde}: índices únicos de las garantías (un pendiente por comprador, un pago por pedido, un cargo por pago)",
              not faltan, f"{total} de {total}", "faltan: " + ", ".join(faltan))


def main():
    ap = argparse.ArgumentParser(description="Verifica un despliegue de Chakra Shop (solo lectura).")
    ap.add_argument("--base", default="http://localhost:8000", help="URL del sitio/gateway (por defecto el clúster local)")
    ap.add_argument("--directo", action="store_true", help="modo puertos directos (docker-compose): sin gateway, cada servicio en su puerto")
    ap.add_argument("--desplazamiento", type=int, default=0, help="suma N a los puertos en modo directo (compose con puertos remapeados)")
    ap.add_argument("--contexto", nargs="?", const="", default=None,
                    help="revisa también los pods con kubectl en ese contexto (sin valor: el contexto actual)")
    ap.add_argument("--compose", action="store_true", help="revisa también los contenedores de docker compose")
    ap.add_argument("--sin-verificar-tls", action="store_true", help="no valida el certificado (p. ej. el de PRUEBA de Let's Encrypt)")
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    tls = not args.sin_verificar_tls
    base = args.base.rstrip("/")
    desp = args.desplazamiento
    if args.directo:
        def url_servicio(n): return f"http://localhost:{SERVICIOS[n] + desp}"
        url_sitio = f"http://localhost:{PUERTO_FRONTEND_COMPOSE + desp}"
        modo = f"puertos directos (compose), desplazamiento {desp}" if desp else "puertos directos (compose)"
    else:
        def url_servicio(n): return f"{base}/api/{n}"
        url_sitio = base
        modo = f"gateway {base}"
    print(f"Verificando: {modo}\n")

    # ---- 1. frontend
    estado, _, cuerpo = pedir(url_sitio + "/", args.timeout, tls)
    comprobar("Frontend: la página principal carga", estado == 200 and b"Chakra" in cuerpo,
              f"HTTP {estado}", f"HTTP {estado}: {cuerpo[:80]!r}")
    estado, _, cuerpo = pedir(url_sitio + "/healthz", args.timeout, tls)
    comprobar("Frontend: /healthz", estado == 200, "HTTP 200", f"HTTP {estado}")

    # ---- 2. salud de los servicios
    caidos = []
    for nombre in SERVICIOS:
        estado, _, cuerpo = pedir(url_servicio(nombre) + "/salud", args.timeout, tls)
        datos = como_json(cuerpo) or {}
        if not (estado == 200 and datos.get("estado") == "ok"):
            caidos.append(f"{nombre}(HTTP {estado})")
    comprobar("Los 8 servicios responden GET /salud", not caidos, "usuarios, productores, productos, pedidos, transporte, "
              "notificaciones, pagos, certificacion", "no responden: " + ", ".join(caidos))

    # ---- 3. datos y rutas públicas
    estado, _, cuerpo = pedir(url_servicio("productos") + "/productos", args.timeout, tls)
    catalogo = como_json(cuerpo)
    if comprobar("Catálogo: GET /productos", estado == 200 and isinstance(catalogo, list), "", f"HTTP {estado}"):
        registrar("OK" if catalogo else "AVISO", "Catálogo: productos disponibles",
                  f"{len(catalogo)} productos" if catalogo else "vacío (base sin datos: falta sembrar la demostración)")
    estado, _, cuerpo = pedir(url_servicio("pedidos") + "/pedidos/condiciones", args.timeout, tls)
    condiciones = como_json(cuerpo) or {}
    comprobar("Pedidos: condiciones de compra (monto mínimo)", estado == 200 and "monto_minimo" in condiciones,
              f"monto mínimo S/ {condiciones.get('monto_minimo')}", f"HTTP {estado}")

    con_foto = next((p for p in (catalogo or []) if p.get("imagen_principal") or p.get("imagen_url")), None)
    if con_foto:
        url = con_foto.get("imagen_principal") or con_foto.get("imagen_url")
        if args.directo and desp:
            url = url.replace(f"localhost:{PUERTO_MINIO}", f"localhost:{PUERTO_MINIO + desp}")
        estado, cab, cuerpo = pedir(url, args.timeout, tls)
        comprobar("Foto de un producto se descarga", estado == 200 and str(cab.get("content-type", "")).startswith("image/"),
                  f"HTTP 200, {cab.get('content-type')}, {len(cuerpo)} bytes", f"HTTP {estado} en {urlparse(url).netloc}")
    else:
        registrar("AVISO", "Foto de un producto se descarga", "ningún producto con foto (nada que comprobar)")
    if catalogo:
        verificada = None
        for producto in catalogo[:10]:
            estado, _, cuerpo = pedir(f"{url_servicio('certificacion')}/certificacion/{producto['id']}/verificar", args.timeout, tls)
            if estado == 200:
                verificada = como_json(cuerpo) or {}
                if verificada.get("total_bloques"):
                    break
        comprobar("QR: la cadena de certificación de un producto es íntegra",
                  bool(verificada) and verificada.get("valido") is True and verificada.get("total_bloques", 0) > 0,
                  f"{(verificada or {}).get('total_bloques')} bloques, válida", "no se pudo verificar la cadena de ningún producto", "AVISO")

    # ---- 4. seguridad
    estado, _, _ = pedir(url_servicio("productores") + "/productores/me", args.timeout, tls)
    if args.directo:
        comprobar("Ruta protegida sin sesión se rechaza", estado in (401, 403), f"HTTP {estado}", f"HTTP {estado} (se esperaba 401/403)")
    else:
        comprobar("Ruta protegida sin sesión se rechaza (401)", estado == 401, "HTTP 401", f"HTTP {estado} (se esperaba 401)")
        # El ForwardAuth corre también en las rutas generales (auth-sesion): el catálogo es público, pero con un token que
        # no vale el gateway responde 401 antes de llegar a Productos (sin el middleware, Productos lo ignoraría: 200).
        estado, _, _ = pedir(url_servicio("productos") + "/productos", args.timeout, tls,
                             cabeceras={"Authorization": "Bearer token.que-no.vale"})
        comprobar("Ruta pública con un token inválido se rechaza en el gateway (401)", estado == 401, "HTTP 401",
                  f"HTTP {estado} (se esperaba 401: ¿la ruta general sin auth-sesion?)")
        for ruta in ("/api/usuarios/docs", "/api/productos/openapi.json", "/api/productos/metrics"):
            estado, _, _ = pedir(base + ruta, args.timeout, tls)
            comprobar(f"Gateway bloquea {ruta}", estado == 403, "HTTP 403", f"HTTP {estado} (se esperaba 403)")

    email, clave = os.getenv("VERIFICAR_EMAIL"), os.getenv("VERIFICAR_PASSWORD")
    if email and clave:
        peticion = urllib.request.Request(url_servicio("usuarios") + "/usuarios/login", method="POST",
                                          data=json.dumps({"email": email, "password": clave}).encode(),
                                          headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(peticion, timeout=args.timeout, context=contexto_tls(tls)) as r:
                comprobar("Login con VERIFICAR_EMAIL", r.status == 200 and "access_token" in (como_json(r.read()) or {}), "HTTP 200")
        except urllib.error.HTTPError as e:
            registrar("FALLA", "Login con VERIFICAR_EMAIL", f"HTTP {e.code}")
        except Exception as e:
            registrar("FALLA", "Login con VERIFICAR_EMAIL", str(e)[:80])

    # ---- 5. HTTPS
    partes = urlparse(base)
    if partes.scheme == "https" and not args.directo:
        try:
            with socket.create_connection((partes.hostname, partes.port or 443), timeout=args.timeout) as s:
                with contexto_tls(tls).wrap_socket(s, server_hostname=partes.hostname) as t:
                    cert = t.getpeercert()
            if cert:
                emisor = dict(x[0] for x in cert["issuer"])
                vence = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
                dias = (vence - datetime.now(timezone.utc)).days
                nombre_emisor = emisor.get("organizationName", "?") + " / " + emisor.get("commonName", "?")
                if "STAGING" in nombre_emisor.upper():
                    registrar("AVISO", "HTTPS: certificado", f"es el de PRUEBA de Let's Encrypt ({nombre_emisor}): los navegadores lo rechazan")
                else:
                    comprobar("HTTPS: certificado válido y vigente", dias > 14, f"{nombre_emisor}, vence en {dias} días",
                              f"vence en {dias} días", "AVISO")
            else:
                registrar("AVISO", "HTTPS: certificado", "no se pudo leer (usa --sin-verificar-tls para ver el de PRUEBA)")
        except Exception as e:
            registrar("FALLA", "HTTPS: certificado válido", str(e)[:120] + " (con el certificado de PRUEBA añade --sin-verificar-tls)")
        estado, cab, _ = pedir(f"http://{partes.hostname}/", args.timeout, tls)
        destino = cab.get("Location", "") if cab else ""
        comprobar("HTTP redirige a HTTPS", estado in (301, 302, 307, 308) and destino.startswith("https://"),
                  f"HTTP {estado} → {destino[:40]}", f"HTTP {estado} sin redirección a https")
        # Solo tiene sentido cuando el dominio es la IP real del servidor (Google Cloud): ahí, si otro puerto conecta, es
        # ese mismo servidor respondiendo. Con un túnel de terceros (VS Code Dev Tunnels, ngrok, Cloudflare Tunnel…) el
        # nombre resuelve a la infraestructura del proveedor, que puede responder en cualquier puerto sin que eso exponga
        # nada tuyo — se vio con un túnel real: el "puerto 8080 abierto" era Microsoft devolviendo un 404, no Traefik.
        DOMINIOS_TUNEL = (".devtunnels.ms", ".ngrok-free.app", ".ngrok.io", ".ngrok.app", ".trycloudflare.com", ".loca.lt")
        if partes.hostname not in ("localhost", "127.0.0.1") and not partes.hostname.endswith(DOMINIOS_TUNEL):
            abiertos = []
            for puerto in (9000, 8006, 8080, 5432):
                try:
                    socket.create_connection((partes.hostname, puerto), timeout=4).close()
                    abiertos.append(puerto)
                except OSError:
                    pass
            comprobar("Puertos internos cerrados desde fuera (9000, 8006, 8080, 5432)", not abiertos, "ninguno responde",
                      f"RESPONDEN: {abiertos}")
        elif partes.hostname.endswith(DOMINIOS_TUNEL):
            registrar("OK", "Puertos internos", "omitido: dominio de túnel de terceros, no aplica (ver comentario en el código)")

    # ---- 6. Kubernetes
    if args.contexto is not None:
        contexto_k = args.contexto or None
        salida, error = kubectl(["get", "pods", "-o", "json"], contexto_k)
        if salida is None:
            registrar("FALLA", "Kubernetes: leer los pods", error)
        else:
            pods = json.loads(salida)["items"]
            r = revisar_pods(pods)
            comprobar("Kubernetes: todos los pods están listos", pods and not r["no_listos"], f"{len(pods)} pods Running y listos",
                      f"no listos ({len(r['no_listos'])}): {', '.join(r['no_listos'][:6])}")
            comprobar("Kubernetes: ningún contenedor en CrashLoopBackOff", not r["en_crashloop"], "",
                      f"en CrashLoopBackOff ({len(r['en_crashloop'])}): {', '.join(r['en_crashloop'][:6])}")
            comprobar("Kubernetes: ningún contenedor se reinició después de estar listo", not r["tras_listo"],
                      "sin contar los arranques ni los reinicios del nodo", "; ".join(r["tras_listo"][:6]), "AVISO")
            registrar("INFO", "Kubernetes: reinicios acumulados desde que se creó cada pod",
                      f"{r['total']} (solo un dato: cada reinicio de Docker Desktop suma uno por contenedor)")
        salida, error = kubectl(["get", "deploy", "-o", "json"], contexto_k)
        if salida is not None:
            malos = [f"{d['metadata']['name']} {d['status'].get('readyReplicas', 0)}/{d['spec']['replicas']}"
                     for d in json.loads(salida)["items"] if d["status"].get("readyReplicas", 0) != d["spec"]["replicas"]]
            comprobar("Kubernetes: réplicas listas = réplicas deseadas en todos los Deployment", not malos, "", ", ".join(malos))
        salida, error = kubectl(["get", "ingressroutes.traefik.io", "-o", "json"], contexto_k)
        if salida is None:
            registrar("FALLA", "Gateway: leer las rutas (IngressRoute)", error)
        else:
            sin_forwardauth = rutas_de_api_sin_forwardauth(json.loads(salida)["items"])
            comprobar("Gateway: toda ruta de /api pasa por el ForwardAuth (auth-jwt o auth-sesion)", not sin_forwardauth,
                      "", "sin ForwardAuth: " + "; ".join(sin_forwardauth[:4]))
        comprobar_claves_de_firma(contexto_k)
        if not args.directo:
            comprobar_tokens_forjados(base, contexto_k, args.timeout, tls)
        salida, error = kubectl(["exec", "deploy/rabbitmq", "--"] + LISTAR_COLAS, contexto_k)
        ligaduras, _ = kubectl(["exec", "deploy/rabbitmq", "--"] + LISTAR_LIGADURAS, contexto_k)
        exchanges, _ = kubectl(["exec", "deploy/rabbitmq", "--"] + LISTAR_EXCHANGES, contexto_k)
        if salida is None:
            registrar("FALLA", "RabbitMQ: leer las colas", error)
        else:
            comprobar_colas(leer_json(salida), leer_json(ligaduras), leer_json(exchanges))
        comprobar_conciliacion(conciliacion.consultar_en_k8s(contexto_k), "Kubernetes",
                               contexto_k or conciliacion.contexto_actual())
        comprobar_indices(conciliacion.consultar_en_k8s(contexto_k), "Kubernetes")

    # ---- 7. Compose
    if args.compose:
        if shutil.which("docker") is None:
            registrar("FALLA", "Compose: leer los contenedores", "docker no está en el PATH")
        else:
            r = subprocess.run(["docker", "compose", "ps", "-a", "--format", "json"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60,
                               cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
            filas = [json.loads(l) for l in r.stdout.splitlines() if l.strip().startswith("{")]
            if r.returncode != 0 or not filas:
                registrar("FALLA", "Compose: leer los contenedores", (r.stderr.strip() or "no hay contenedores; ¿docker compose up -d?")[:120])
            else:
                malos = [f"{f['Service']}({f['State']}/{f.get('Health') or 'sin healthcheck'})" for f in filas
                         if f["State"] != "running" or f.get("Health") in ("unhealthy", "starting")]
                comprobar("Compose: todos los contenedores en marcha y sanos", not malos, f"{len(filas)} contenedores", ", ".join(malos))
                salidas = []
                for listar in (LISTAR_COLAS, LISTAR_LIGADURAS, LISTAR_EXCHANGES):
                    r = subprocess.run(["docker", "compose", "exec", "-T", "rabbitmq"] + listar, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=60,
                                       cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
                    salidas.append(leer_json(r.stdout) if r.returncode == 0 else [])
                comprobar_colas(*salidas)
                comprobar_conciliacion(conciliacion.consultar_en_compose(), "Compose", "compose")
                comprobar_indices(conciliacion.consultar_en_compose(), "Compose")

    # ---- resumen
    fallas = sum(1 for n, *_ in resultados if n == "FALLA")
    avisos = sum(1 for n, *_ in resultados if n == "AVISO")
    bien = sum(1 for n, *_ in resultados if n == "OK")  # los [INFO] son datos, no comprobaciones
    print(f"\n{bien} bien, {avisos} avisos, {fallas} fallos")
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
