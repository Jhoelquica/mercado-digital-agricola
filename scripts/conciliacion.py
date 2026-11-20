#!/usr/bin/env python
"""Conciliación: lo que quedó inconsistente entre servicios porque un evento no llegó. Solo lee: no corrige nada.

Cruza las bases de Pagos, Pedidos, Transporte y Productos (cada servicio tiene la suya) y lista:
  1. pagos aprobados con el pedido sin confirmar (pago_confirmado no llegó a Pedidos);
  2. envíos entregados con el pedido sin "entregado" (envio_actualizado no llegó a Pedidos);
  3. llegadas al almacén con el recojo sin terminar (llegada_almacen no llegó a Transporte);
  4. pedidos cancelados con devolución que tienen un envío entregado o en curso;
  5. pagos aprobados sin envío (pago_confirmado no llegó a Transporte);
  6. envíos cumplidos (entregado o en agencia) sin la entrega anotada en Pagos (el cobro del agricultor no se desbloqueó);
  7. cobros "procesando" hace más de MINUTOS_EN_PROCESO minutos (la conciliación de Pagos no pudo resolverlos), con su
     referencia para buscarla en el panel de Culqi;
  8. devoluciones a medias ("reembolso_procesando" hace más de MINUTOS_EN_PROCESO minutos), con sus referencias;
  9. la regla inversa: pedidos confirmados o entregados sin el pago aprobado (un entregado exige el pago aprobado; un
     confirmado admite el adelanto pagado o un cobro o una devolución en proceso).

Lo que todavía está en camino no cuenta: si la bandeja de salida del servicio que avisa tiene ese evento pendiente, o lo
publicó hace menos de EN_CAMINO_MINUTOS, se espera. Si un pendiente se atasca, verificar_despliegue.py avisa aparte
(bandejas(): el pendiente más antiguo de cada una).

Excepciones documentadas (scripts/excepciones_conciliacion.json): un hallazgo que se conserva a propósito (por ejemplo,
un pedido con un bloque sellado en la cadena, que no se borra ni se le inventa un pago) se lista aparte y no cuenta.
Cada una dice su regla, el id, el contexto, el motivo y la fecha. Solo valen en docker-desktop: en producción la lista
debe quedar vacía, y una excepción para otro contexto es un problema de la lista (verificar_despliegue.py da FALLA).

Uso (desde la raíz del repositorio):
    python scripts/conciliacion.py --contexto docker-desktop    # el clúster
    python scripts/conciliacion.py --compose                    # docker compose
Sale con 1 si encontró inconsistencias (o la lista de excepciones tiene problemas).
"""
import argparse
import json
import os
import subprocess
import sys
import uuid

EN_CAMINO_MINUTOS = 2
MINUTOS_EN_PROCESO = 10
# Con qué estado del pago está bien cada estado del pedido (regla 9).
PAGO_ESPERADO = {"entregado": ("aprobado",),
                 "confirmado": ("aprobado", "adelanto_pagado", "procesando", "reembolso_procesando")}
PAGADOS = ("aprobado", "adelanto_pagado")
CUMPLIDOS = ("entregado", "en_agencia")
RECOJO_EN_CURSO = ("pendiente_asignacion", "propuesto", "asignado", "recogido", "en_almacen", "en_camino")
CON_BANDEJA = ("pedidos", "pagos", "transporte", "productos")
SEPARADOR = "|"
# Dentro del contenedor de la base: las variables del propio Postgres (la contraseña no hace falta por el socket local).
PSQL = f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q -tA -F "{SEPARADOR}"'
RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _correr(comando, sql):
    r = subprocess.run(comando, input=sql, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60, cwd=RAIZ)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["la consulta falló"])[-1][:200])
    return [linea.split(SEPARADOR) for linea in r.stdout.splitlines() if linea.strip()]


def consultar_en_k8s(contexto=None):
    def consultar(servicio, sql):
        return _correr(["kubectl"] + (["--context", contexto] if contexto else [])
                       + ["exec", "-i", f"deploy/db-{servicio}", "--", "sh", "-c", PSQL], sql)
    return consultar


def consultar_en_compose():
    def consultar(servicio, sql):
        return _correr(["docker", "compose", "exec", "-T", f"db-{servicio}", "sh", "-c", PSQL], sql)
    return consultar


def _en(valores) -> str:
    """Una lista SQL de UUID ('a', 'b'), validados: vienen de las bases, pero no se pega texto sin revisar."""
    return "(" + ", ".join(f"'{uuid.UUID(v)}'" for v in valores) + ")"


def _tiene_bandeja(consultar, servicio) -> bool:
    return _tiene_tabla(consultar, servicio, "eventos_salientes")


def _tiene_tabla(consultar, servicio, tabla) -> bool:
    return consultar(servicio, f"SELECT to_regclass('{tabla}') IS NOT NULL")[0][0] == "t"


def _en_camino(consultar, servicio, tipo, clave) -> set:
    """Los ids (pedido_id o producto_id) de los eventos `tipo` que la bandeja de `servicio` todavía no publicó o publicó
    hace muy poco: su efecto puede no haber llegado aún."""
    if not _tiene_bandeja(consultar, servicio):
        return set()
    return {fila[0] for fila in consultar(servicio, (
        f"SELECT cuerpo::json->>'{clave}' FROM eventos_salientes WHERE tipo = '{tipo}' AND (fecha_envio IS NULL OR "
        f"fecha_envio > timezone('utc', now()) - interval '{EN_CAMINO_MINUTOS} minutes')")) if fila[0]}


def inconsistencias(consultar) -> list:
    """[(número de la regla, frase)] de lo inconsistente ahora mismo; vacío si todo coincide."""
    pedidos = {fila[0]: (fila[1], fila[2] == "t")
               for fila in consultar("pedidos", "SELECT id, estado, motivo_cancelacion IS NOT NULL FROM pedidos")}
    pagados = {fila[0]: fila[1] for fila in consultar(
        "pagos", f"SELECT pedido_id, estado FROM pagos WHERE estado IN {PAGADOS!r}")}
    entregas_en_pagos = {fila[0] for fila in consultar("pagos", "SELECT pedido_id FROM entregas_pedido")}
    envios = {fila[0]: fila[1] for fila in consultar("transporte", "SELECT pedido_id, estado FROM envios")}
    recojos = consultar("transporte",
                        f"SELECT id, producto_id, estado FROM recojos WHERE estado IN {RECOJO_EN_CURSO!r}")
    disponibles = set()
    if recojos:
        disponibles = {fila[0] for fila in consultar("productos", (
            f"SELECT id FROM productos WHERE estado = 'disponible' AND id IN {_en({r[1] for r in recojos})}"))}
    pago_en_camino = _en_camino(consultar, "pagos", "pago_confirmado", "pedido_id")
    envio_en_camino = _en_camino(consultar, "transporte", "envio_actualizado", "pedido_id")
    llegada_en_camino = _en_camino(consultar, "productos", "llegada_almacen", "producto_id")

    hallazgos = []
    for pedido_id, estado_pago in sorted(pagados.items()):
        if pedido_id in pago_en_camino:
            continue
        estado, con_motivo = pedidos.get(pedido_id, (None, False))
        if estado is None:
            hallazgos.append((1, f"pago {estado_pago} del pedido {pedido_id}, que no existe en Pedidos"))
        elif estado in ("pendiente", "cancelado"):
            hallazgos.append((1, f"pago {estado_pago} con el pedido {pedido_id} en '{estado}'"
                                 + (" (cancelado por el admin: ¿falta la devolución?)" if con_motivo else "")))
        if pedido_id not in envios:
            hallazgos.append((5, f"pago {estado_pago} del pedido {pedido_id} sin envío en Transporte"))
    for pedido_id, estado_envio in sorted(envios.items()):
        estado, con_motivo = pedidos.get(pedido_id, (None, False))
        if estado == "cancelado" and con_motivo:
            if estado_envio != "cancelado":
                hallazgos.append((4, f"pedido {pedido_id} cancelado con devolución y su envío en '{estado_envio}'"))
        elif estado_envio == "entregado" and estado != "entregado" and pedido_id not in envio_en_camino:
            hallazgos.append((2, f"envío entregado con el pedido {pedido_id} en '{estado}'"))
        if estado_envio in CUMPLIDOS and pedido_id not in entregas_en_pagos and pedido_id not in envio_en_camino:
            hallazgos.append((6, f"envío '{estado_envio}' del pedido {pedido_id} sin la entrega anotada en Pagos"))
    for recojo_id, producto_id, estado in recojos:
        if producto_id in disponibles and producto_id not in llegada_en_camino:
            hallazgos.append((3, f"recojo {recojo_id} en '{estado}' con su producto {producto_id} ya en el almacén"))
    hallazgos.extend(_en_proceso_hace_mucho(consultar))
    estados_de_pago = {fila[0]: fila[1] for fila in consultar("pagos", "SELECT pedido_id, estado FROM pagos")}
    for pedido_id, (estado, _) in sorted(pedidos.items()):
        if estado in PAGO_ESPERADO and estados_de_pago.get(pedido_id) not in PAGO_ESPERADO[estado]:
            if pedido_id not in pago_en_camino:
                hallazgos.append((9, f"pedido {pedido_id} '{estado}' con el pago "
                                     + (f"'{estados_de_pago[pedido_id]}'" if pedido_id in estados_de_pago else "sin registro")))
    return sorted(hallazgos)


def _en_proceso_hace_mucho(consultar) -> list:
    """Reglas 7 y 8: los cobros y devoluciones en proceso hace más de MINUTOS_EN_PROCESO minutos, con las referencias de
    sus intentos (las que se buscan en el panel de Culqi)."""
    if not _tiene_tabla(consultar, "pagos", "intentos_pago"):
        return []
    filas = consultar("pagos", (
        "SELECT p.pedido_id, p.estado, coalesce(string_agg(i.id::text || CASE WHEN i.revision_manual THEN ' (revisión "
        "manual)' ELSE '' END, ', '), '') FROM pagos p LEFT JOIN intentos_pago i ON i.pago_id = p.id AND "
        "(i.estado = 'en_curso' OR i.revision_manual) WHERE p.estado IN ('procesando', 'reembolso_procesando') AND "
        f"p.procesando_desde < timezone('utc', now()) - interval '{MINUTOS_EN_PROCESO} minutes' "
        "GROUP BY p.pedido_id, p.estado ORDER BY p.pedido_id"))
    hallazgos = []
    for pedido_id, estado, referencias in filas:
        detalle = f"referencia {referencias}" if referencias else "sin intento en curso"
        if estado == "procesando":
            hallazgos.append((7, f"cobro del pedido {pedido_id} 'procesando' hace más de {MINUTOS_EN_PROCESO} minutos "
                                 f"({detalle})"))
        else:
            hallazgos.append((8, f"devolución del pedido {pedido_id} a medias hace más de {MINUTOS_EN_PROCESO} minutos "
                                 f"({detalle})"))
    return hallazgos


def bandejas(consultar) -> list:
    """[(servicio, pendientes, segundos del más antiguo)] de cada bandeja de salida (None si el servicio todavía no la
    tiene)."""
    resultado = []
    for servicio in CON_BANDEJA:
        if not _tiene_bandeja(consultar, servicio):
            resultado.append((servicio, None, None))
            continue
        cuantos, segundos = consultar(servicio, (
            "SELECT count(*), coalesce(EXTRACT(EPOCH FROM timezone('utc', now()) - min(fecha_creacion))::int, 0) "
            "FROM eventos_salientes WHERE fecha_envio IS NULL"))[0]
        resultado.append((servicio, int(cuantos), int(segundos)))
    return resultado


REGLAS = {
    1: "Pago aprobado con el pedido sin confirmar",
    2: "Envío entregado con el pedido sin «entregado»",
    3: "Llegada al almacén con el recojo sin terminar",
    4: "Pedido cancelado con devolución con un envío entregado o en curso",
    5: "Pago aprobado sin envío",
    6: "Envío cumplido sin la entrega anotada en Pagos (el cobro no se desbloqueó)",
    7: f"Cobro en proceso hace más de {MINUTOS_EN_PROCESO} minutos (resolverlo en el panel «Cobros por revisar en Culqi»)",
    8: f"Devolución a medias hace más de {MINUTOS_EN_PROCESO} minutos (resolverla en el mismo panel)",
    9: "Pedido confirmado o entregado sin el pago aprobado",
}


def resumen(hallazgos) -> str:
    por_regla = {}
    for regla, _ in hallazgos:
        por_regla[regla] = por_regla.get(regla, 0) + 1
    return "; ".join(f"{REGLAS[regla]}: {cuantos}" for regla, cuantos in sorted(por_regla.items()))


# ---- Excepciones documentadas ----
EXCEPCIONES = os.environ.get("EXCEPCIONES_CONCILIACION") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                          "excepciones_conciliacion.json")
CONTEXTO_DE_EXCEPCIONES = "docker-desktop"  # el único donde puede haberlas (el clúster local)
CAMPOS_DE_EXCEPCION = ("regla", "id", "contexto", "motivo", "fecha")


def cargar_excepciones(ruta=None) -> list:
    """[{regla, id, contexto, motivo, fecha}] de la lista (vacía si no existe el archivo)."""
    ruta = ruta or EXCEPCIONES
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8") as archivo:
        return json.load(archivo).get("excepciones", [])


def problemas_de_excepciones(excepciones) -> list:
    """Lo que está mal en la lista: una excepción incompleta, de una regla que no existe, o para un contexto que no es
    docker-desktop (en producción la lista debe estar vacía)."""
    problemas = []
    for numero, excepcion in enumerate(excepciones, 1):
        faltan = [campo for campo in CAMPOS_DE_EXCEPCION if not excepcion.get(campo)]
        if faltan:
            problemas.append(f"la excepción {numero} no tiene {', '.join(faltan)}")
        if excepcion.get("regla") not in REGLAS:
            problemas.append(f"la excepción {numero} es de una regla que no existe ({excepcion.get('regla')})")
        if excepcion.get("contexto") != CONTEXTO_DE_EXCEPCIONES:
            problemas.append(f"la excepción {numero} ({excepcion.get('id')}) es para «{excepcion.get('contexto')}»: solo "
                             f"se aceptan en {CONTEXTO_DE_EXCEPCIONES}; en producción la lista debe estar vacía")
    return problemas


def aplicar_excepciones(hallazgos, excepciones, contexto) -> tuple:
    """Separa los hallazgos con una excepción documentada para este contexto (la misma regla y su id en la frase).
    Devuelve (los que quedan, [(excepción, frase)] aplicadas, las excepciones de este contexto que ya no aparecen)."""
    de_aqui = [e for e in excepciones if e.get("contexto") == contexto]
    quedan, aplicadas, usadas = [], [], set()
    for regla, frase in hallazgos:
        excepcion = next((e for e in de_aqui if e.get("regla") == regla and str(e.get("id")) in frase), None)
        if excepcion is None:
            quedan.append((regla, frase))
        else:
            aplicadas.append((excepcion, frase))
            usadas.add(id(excepcion))
    return quedan, aplicadas, [e for e in de_aqui if id(e) not in usadas]


def contexto_actual():
    """El contexto de kubectl en uso, o None si no se puede saber."""
    try:
        r = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def main():
    for salida in (sys.stdout, sys.stderr):
        if hasattr(salida, "reconfigure"):
            salida.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Lista lo inconsistente entre Pagos, Pedidos, Transporte y Productos.")
    ap.add_argument("--contexto", default=None, help="contexto de kubectl (por defecto el actual)")
    ap.add_argument("--compose", action="store_true", help="las bases de docker compose en vez del clúster")
    args = ap.parse_args()
    consultar = consultar_en_compose() if args.compose else consultar_en_k8s(args.contexto)
    contexto = "compose" if args.compose else (args.contexto or contexto_actual())
    excepciones = cargar_excepciones()
    problemas = problemas_de_excepciones(excepciones)
    hallazgos, aplicadas, sin_uso = aplicar_excepciones(inconsistencias(consultar), excepciones, contexto)
    for regla, texto in REGLAS.items():
        de_esta = [frase for numero, frase in hallazgos if numero == regla]
        print(f"{regla}. {texto}: {len(de_esta)}")
        for frase in de_esta[:20]:
            print(f"     - {frase}")
        if len(de_esta) > 20:
            print(f"     ... y {len(de_esta) - 20} más")
    for servicio, pendientes, segundos in bandejas(consultar):
        print(f"Bandeja de salida de {servicio}: "
              + ("todavía no existe" if pendientes is None else f"{pendientes} pendiente(s), el más antiguo de hace {segundos} s"))
    print(f"Excepciones documentadas (no cuentan; scripts/excepciones_conciliacion.json): {len(aplicadas)}")
    for excepcion, frase in aplicadas:
        print(f"     - regla {excepcion['regla']}: {frase}. Motivo: {excepcion['motivo']} (documentada el {excepcion['fecha']})")
    for excepcion in sin_uso:
        print(f"     - sin uso (ya no aparece: se puede quitar): regla {excepcion.get('regla')}, {excepcion.get('id')}")
    for problema in problemas:
        print(f"PROBLEMA en la lista de excepciones: {problema}")
    print(f"\n{len(hallazgos)} inconsistencia(s)")
    sys.exit(1 if hallazgos or problemas else 0)


if __name__ == "__main__":
    main()
