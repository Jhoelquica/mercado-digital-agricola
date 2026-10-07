"""Resúmenes de ventas (ventas.py): funciones puras, sin base de datos."""
from datetime import datetime

import ventas


def _linea(mes, soles, producto="p1", nombre="Papa", pedido="a", comprador="c1", kg=10.0, destino="Ayacucho", por_mayor=False):
    return {"mes": mes, "pedido_id": pedido, "usuario_id": comprador, "producto_id": producto, "nombre": nombre,
            "productor_id": "x", "soles": soles, "kg": kg, "destino": destino, "por_mayor": por_mayor}


def test_los_meses_son_los_de_peru_y_cuentan_hacia_atras():
    # 1 de octubre a las 03:00 UTC todavía es 30 de setiembre en Perú
    assert ventas.mes_peru(datetime(2026, 10, 1, 3)) == "2026-09"
    assert ventas.meses_hasta(datetime(2026, 10, 7, 12), 3) == ["2026-08", "2026-09", "2026-10"]
    assert ventas.meses_hasta(datetime(2026, 2, 7, 12), 3) == ["2025-12", "2026-01", "2026-02"]
    assert ventas.mes_anterior("2026-01") == "2025-12" and ventas.mes_anterior("2026-10", 12) == "2025-10"


def test_el_resumen_ordena_productos_compara_meses_y_cuenta_compradores_que_repiten():
    lineas = [
        _linea("2026-10", 30, pedido="a", comprador="c1"),
        _linea("2026-10", 60, producto="p2", nombre="Quinua", pedido="b", comprador="c1", kg=5, destino="Lima", por_mayor=True),
        _linea("2026-09", 20, pedido="c", comprador="c2"),
        _linea("2025-10", 12, pedido="d", comprador="c3"),  # hace un año: fuera de la ventana, pero se compara
    ]
    datos = ventas.resumen(lineas, ["2026-08", "2026-09", "2026-10"], "2026-10")
    assert [p["nombre"] for p in datos["productos"]] == ["Quinua", "Papa"]
    papa = datos["productos"][1]
    assert (papa["soles"], papa["kg"], papa["pedidos"], papa["precio_promedio_kg"]) == (50.0, 20.0, 2, 2.5)
    assert papa["soles_por_mes"] == [0.0, 20.0, 30.0]
    assert [m["soles"] for m in datos["por_mes"]] == [0.0, 20.0, 90.0]
    assert datos["totales"]["compradores"] == 2 and datos["totales"]["compradores_recurrentes"] == 1
    assert datos["totales"]["ticket_promedio"] == round(110 / 3, 2)
    c = datos["comparativa"]
    assert (c["actual"]["soles"], c["anterior"]["soles"], c["anio_pasado"]["soles"]) == (90.0, 20.0, 12.0)
    assert datos["por_destino"][0] == {"nombre": "Lima", "soles": 60.0}
    assert {g["nombre"]: g["soles"] for g in datos["por_tipo"]} == {"Por mayor": 60.0, "Por menor": 50.0}


def test_productores_que_mas_venden_y_compradores_nuevos():
    lineas = [_linea("2026-10", 30), {**_linea("2026-10", 70, pedido="z"), "productor_id": "y"}]
    assert [p["productor_id"] for p in ventas.productores_que_mas_venden(lineas, ["2026-10"])] == ["y", "x"]
    assert ventas.compradores_nuevos_por_mes({"c1": "2026-09", "c2": "2026-10", "c3": "2026-10"}, ["2026-09", "2026-10"]) == [1, 2]
