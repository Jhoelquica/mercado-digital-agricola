"""Plantillas de producción (plantillas_produccion.py): funciones puras, sin base de datos."""
import plantillas_produccion as p


def test_reconoce_el_alimento_por_el_nombre_que_escribe_el_agricultor():
    casos = {"Papa amarilla": "papa", "Maiz choclo": "maiz", "quinua roja": "quinua", "Habas": "haba", "Huevo de codorniz": "huevo",
             "Leche fresca": "leche", "Queso fresco": "queso", "Yogurt natural": "yogurt", "Limón": "citricos",
             "Papaya": "papaya", "Orégano": "cilantro", "Piña": "pina", "Coliflor": "col"}
    assert {nombre: p.clave_para(nombre) for nombre in casos} == casos
    assert p.clave_para("Chuño negro") is None
    generica = p.plantilla_para("Chuño negro")
    assert (generica["clave"], generica["tipo"]) == ("generica_temporada", "temporada")
    assert p.plantilla_por_clave("generica_temporada")["etapas"] == generica["etapas"]


def test_la_papa_tiene_sus_etapas_en_orden_y_los_huevos_no_se_siembran():
    papa = p.plantilla_para("Papa blanca")
    assert [e["clave"] for e in papa["etapas"]] == ["preparacion", "siembra", "deshierbo", "aporque", "fumigacion", "cosecha"]
    huevos = p.plantilla_para("Huevo de campo")
    assert huevos["tipo"] == "crianza" and "siembra" not in [e["clave"] for e in huevos["etapas"]]
    assert huevos["etiquetas"]["fin"] == "Recolección"
    queso = p.plantilla_para("Queso fresco")
    assert queso["tipo"] == "elaboracion" and queso["etapas"][0]["rubros"] == ["leche"]


def test_cada_familia_tiene_inicio_y_final_y_solo_rubros_conocidos():
    for clave, familia in p.FAMILIAS.items():
        momentos = [e["momento"] for e in familia["etapas"]]
        assert "inicio" in momentos and "final" in momentos, clave
        claves = [e["clave"] for e in familia["etapas"]]
        assert len(claves) == len(set(claves)), clave
        for etapa in familia["etapas"]:
            assert etapa["rubros"] and set(etapa["rubros"]) <= set(p.RUBROS), (clave, etapa["clave"])
    for clave, (_n, familia, alias, unidad) in p.ALIMENTOS.items():
        assert familia in p.FAMILIAS and alias and unidad in ("kg", "unidad", "litro"), clave
    assert p.columna_de("semilla") == "costo_semillas" and p.columna_de("mano_obra") == "costo_mano_obra"
    assert p.columna_de("alimento") == "costo_insumos"
