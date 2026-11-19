#!/usr/bin/env python
"""Conciliación: lo que quedó inconsistente entre servicios porque un evento no llegó. Solo lee: no corrige nada.

Cruza las bases de Pagos, Pedidos, Transporte y Productos (cada servicio tiene la suya) y lista:
  1. pagos aprobados con el pedido sin confirmar (pago_confirmado no llegó a Pedidos);
  2. envíos entregados con el pedido sin "entregado" (envio_actualizado no llegó a Pedidos);
  3. llegadas al almacén con el recojo sin terminar (llegada_almacen no llegó a Transporte);
  4. pedidos cancelados con devolución que tienen un envío entregado o en curso;
  5. pagos aprobados sin envío (pago_confirmado no llegó a Transporte);
  6. envíos cumplidos (entregado o en agencia) sin la entrega anotada en Pagos (el cobro del agricultor no se desbloqueó).

Lo que todavía está en camino no cuenta: si la bandeja de salida del servicio que avisa tiene ese evento pendiente, o lo
publicó hace menos de EN_CAMINO_MINUTOS, se espera. Si un pendiente se atasca, verificar_despliegue.py avisa aparte
(bandejas(): el pendiente más antiguo de cada una).

Uso (desde la raíz del repositorio):
    python scripts/conciliacion.py --contexto docker-desktop    # el clúster
    python scripts/conciliacion.py --compose                    # docker compose
Sale con 1 si encontró inconsistencias.
"""
import argparse
import os
import subprocess
import sys
import uuid

EN_CAMINO_MINUTOS = 2
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
    return consultar(servicio, "SELECT to_regclass('eventos_salientes') IS NOT NULL")[0][0] == "t"


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
    return sorted(hallazgos)


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
}


def resumen(hallazgos) -> str:
    por_regla = {}
    for regla, _ in hallazgos:
        por_regla[regla] = por_regla.get(regla, 0) + 1
    return "; ".join(f"{REGLAS[regla]}: {cuantos}" for regla, cuantos in sorted(por_regla.items()))


def main():
    for salida in (sys.stdout, sys.stderr):
        if hasattr(salida, "reconfigure"):
            salida.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Lista lo inconsistente entre Pagos, Pedidos, Transporte y Productos.")
    ap.add_argument("--contexto", default=None, help="contexto de kubectl (por defecto el actual)")
    ap.add_argument("--compose", action="store_true", help="las bases de docker compose en vez del clúster")
    args = ap.parse_args()
    consultar = consultar_en_compose() if args.compose else consultar_en_k8s(args.contexto)
    hallazgos = inconsistencias(consultar)
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
    print(f"\n{len(hallazgos)} inconsistencia(s)")
    sys.exit(1 if hallazgos else 0)


if __name__ == "__main__":
    main()
