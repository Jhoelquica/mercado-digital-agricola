"""Resúmenes de ventas para los paneles (observación del usuario 2026-10-06: "los dashboards deben tener información para
la toma de decisiones": ventas por mes, qué productos se vendieron más, comparativas).

Vendido = pedido pagado ("confirmado" o "entregado"): uno sin pagar o cancelado (y devuelto) no cuenta. Los meses son
los de Perú (UTC-5). Funciones puras sobre "líneas" ya leídas de la base; main.py hace las consultas y las rutas.
"""
from collections import defaultdict
from datetime import datetime, timedelta

HORAS_PERU = timedelta(hours=5)
ESTADOS_VENDIDOS = ("confirmado", "entregado")


def mes_peru(fecha: datetime) -> str:
    return (fecha - HORAS_PERU).strftime("%Y-%m")


def meses_hasta(ahora: datetime, cantidad: int) -> list[str]:
    """Los últimos `cantidad` meses de Perú, del más antiguo al actual: ["2025-11", ..., "2026-10"]."""
    local = ahora - HORAS_PERU
    anio, mes = local.year, local.month
    salida = []
    for _ in range(cantidad):
        salida.append(f"{anio:04d}-{mes:02d}")
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
    return list(reversed(salida))


def mes_anterior(mes: str, meses_atras: int = 1) -> str:
    anio, numero = int(mes[:4]), int(mes[5:])
    total = anio * 12 + (numero - 1) - meses_atras
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def linea(pedido, item) -> dict:
    """Una línea vendida, con lo que piden los resúmenes."""
    return {
        "mes": mes_peru(pedido.fecha_creacion),
        "pedido_id": str(pedido.id),
        "usuario_id": str(pedido.usuario_id) if pedido.usuario_id else None,
        "producto_id": str(item.producto_id),
        "nombre": item.producto_nombre,
        "productor_id": str(item.productor_id) if item.productor_id else None,
        "soles": float(item.precio_unitario) * item.cantidad,
        "kg": float(item.peso_kg) if item.peso_kg is not None else 0.0,
        "destino": pedido.destino_ciudad or "Ayacucho",
        "por_mayor": bool(pedido.requiere_vehiculo_grande),
    }


def _cifras(lineas) -> dict:
    pedidos = {l["pedido_id"] for l in lineas}
    compradores = {l["usuario_id"] for l in lineas if l["usuario_id"]}
    soles = round(sum(l["soles"] for l in lineas), 2)
    return {"soles": soles, "kg": round(sum(l["kg"] for l in lineas), 2), "pedidos": len(pedidos), "compradores": len(compradores),
            "ticket_promedio": round(soles / len(pedidos), 2) if pedidos else 0.0}


def resumen(lineas: list[dict], meses: list[str], actual: str) -> dict:
    """Ventas de la ventana `meses` (y del mes de comparación de hace un año, que puede quedar fuera de ella)."""
    en_ventana = [l for l in lineas if l["mes"] in meses]
    por_mes_lineas = defaultdict(list)
    for l in lineas:
        por_mes_lineas[l["mes"]].append(l)

    productos = {}
    for l in en_ventana:
        p = productos.setdefault(l["producto_id"], {
            "producto_id": l["producto_id"], "nombre": l["nombre"], "productor_id": l["productor_id"],
            "soles": 0.0, "kg": 0.0, "pedidos": set(), "soles_por_mes": defaultdict(float), "kg_por_mes": defaultdict(float)})
        p["nombre"] = p["nombre"] or l["nombre"]
        p["soles"] += l["soles"]
        p["kg"] += l["kg"]
        p["pedidos"].add(l["pedido_id"])
        p["soles_por_mes"][l["mes"]] += l["soles"]
        p["kg_por_mes"][l["mes"]] += l["kg"]
    lista_productos = sorted(({
        "producto_id": p["producto_id"], "nombre": p["nombre"], "productor_id": p["productor_id"],
        "soles": round(p["soles"], 2), "kg": round(p["kg"], 2), "pedidos": len(p["pedidos"]),
        "precio_promedio_kg": round(p["soles"] / p["kg"], 2) if p["kg"] else None,
        "soles_por_mes": [round(p["soles_por_mes"].get(m, 0.0), 2) for m in meses],
        "kg_por_mes": [round(p["kg_por_mes"].get(m, 0.0), 2) for m in meses],
    } for p in productos.values()), key=lambda p: -p["soles"])

    compras_por_comprador = defaultdict(set)
    for l in en_ventana:
        if l["usuario_id"]:
            compras_por_comprador[l["usuario_id"]].add(l["pedido_id"])

    def agrupar(clave):
        grupos = defaultdict(float)
        for l in en_ventana:
            grupos[clave(l)] += l["soles"]
        return sorted(({"nombre": k, "soles": round(v, 2)} for k, v in grupos.items()), key=lambda g: -g["soles"])

    return {
        "meses": meses,
        "por_mes": [{"mes": m, **_cifras(por_mes_lineas.get(m, []))} for m in meses],
        "productos": lista_productos,
        "totales": {**_cifras(en_ventana), "compradores_recurrentes": sum(1 for p in compras_por_comprador.values() if len(p) > 1)},
        "comparativa": {
            "actual": {"mes": actual, **_cifras(por_mes_lineas.get(actual, []))},
            "anterior": {"mes": mes_anterior(actual), **_cifras(por_mes_lineas.get(mes_anterior(actual), []))},
            "anio_pasado": {"mes": mes_anterior(actual, 12), **_cifras(por_mes_lineas.get(mes_anterior(actual, 12), []))},
        },
        "por_destino": agrupar(lambda l: l["destino"]),
        "por_tipo": agrupar(lambda l: "Por mayor" if l["por_mayor"] else "Por menor"),
    }


def productores_que_mas_venden(lineas: list[dict], meses: list[str], limite: int = 10) -> list[dict]:
    grupos = {}
    for l in lineas:
        if l["mes"] not in meses or not l["productor_id"]:
            continue
        g = grupos.setdefault(l["productor_id"], {"productor_id": l["productor_id"], "soles": 0.0, "kg": 0.0, "pedidos": set()})
        g["soles"] += l["soles"]
        g["kg"] += l["kg"]
        g["pedidos"].add(l["pedido_id"])
    return sorted(({"productor_id": g["productor_id"], "soles": round(g["soles"], 2), "kg": round(g["kg"], 2),
                    "pedidos": len(g["pedidos"])} for g in grupos.values()), key=lambda g: -g["soles"])[:limite]


def compradores_nuevos_por_mes(primera_compra: dict, meses: list[str]) -> list[int]:
    """primera_compra = {usuario_id: mes de su primer pedido pagado}."""
    cuenta = defaultdict(int)
    for mes in primera_compra.values():
        cuenta[mes] += 1
    return [cuenta.get(m, 0) for m in meses]
