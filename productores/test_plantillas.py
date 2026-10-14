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



def test_cada_alimento_trae_su_categoria_del_catalogo():
    casos = {"papa": "tuberculo", "quinua": "grano", "maiz": "grano", "haba": "legumbre", "cebolla": "verdura",
             "fresa": "fruta", "sandia": "fruta", "palta": "fruta", "cilantro": "hierba", "huevo": "huevo",
             "leche": "lacteo", "queso": "lacteo", "miel": None}
    assert {clave: p.categoria_de(clave) for clave in casos} == casos
    assert p.categoria_de(None) is None and p.categoria_de("generica_temporada") is None
    validas = {"fruta", "verdura", "tuberculo", "grano", "legumbre", "lacteo", "huevo", "hierba"}
    assert set(p.CATEGORIA_POR_FAMILIA.values()) <= validas


def test_cada_alimento_se_vende_y_se_mide_en_sus_unidades():
    """H1 de la validación con usuarios (2026-10-13): la papa no se vende por unidades."""
    assert p.unidades_venta("papa") == ["kg", "saco", "arroba"]
    assert p.unidades_cosecha("papa") == ["kg"]
    assert p.unidades_cosecha("huevo") == ["unidad"]
    assert p.unidades_cosecha("leche") == ["litro"]
    assert p.unidades_cosecha("maiz") == ["kg", "unidad"]  # el choclo también por unidad
    assert p.unidades_venta(None) is None and p.unidades_venta("generica_temporada") is None
    papa = p.plantilla_para("Papa amarilla")
    assert papa["unidades_venta"] == ["kg", "saco", "arroba"] and papa["unidades_cosecha"] == ["kg"]
    catalogo = {a["clave"]: a for a in p.catalogo()["alimentos"]}
    assert catalogo["huevo"]["unidades_venta"] == ["unidad"]
    # Toda familia con alimentos tiene sus unidades: un alimento nuevo no queda sin restricción por olvido.
    assert all(p.unidades_venta(clave) for clave in p.ALIMENTOS)
