"""Cómo se obtiene cada alimento y en qué se gasta en cada etapa (pedido del usuario 2026-10-07: "cada alimento se cosecha
o se obtiene de diferente manera, así que los gastos y los procesos son diferentes").

Cuatro tipos de producción:
- temporada: se siembra cada campaña (papa, quinua, maíz, haba, hortalizas...). Papa: preparar el terreno → siembra →
  deshierbo → aporque → fumigación → cosecha.
- frutal: el árbol o la planta ya está; cada campaña se poda, se abona, se riega, se fumiga y se cosecha.
- crianza: huevos, leche, miel. No se siembran: cada registro es un PERIODO del lote (decisión del usuario 2026-10-07),
  con sus gastos (el alimento es el más grande: 70–75 % del costo del huevo según Gestión) y lo que se recogió.
- elaboracion: queso, yogurt, mantequilla; la materia prima es la leche (7–10 L por kg de queso fresco).

Cada etapa tiene un `momento`: "inicio" (se pregunta al registrar la siembra o el inicio del periodo), "cuidado" (se anota
desde la tarjeta cuando ocurre) o "final" (se pregunta al cosechar o al registrar lo recogido). Los rubros sugeridos son
solo una ayuda: en cualquier etapa se puede anotar cualquier rubro.

Fuentes de la investigación (2026-10-07): DRA Ayacucho (papa, Andina), Congreso (quinua), INIA (haba), agraria.pe (maíz,
leche, palta), Gestión (huevo). Los valores de referencia para "¿qué me conviene sembrar?" van en otro paso.
"""
import unicodedata

# Las palabras de cada tipo: en las pantallas y en el texto que queda sellado en la cadena de certificación.
TIPOS = {
    "temporada": {"nombre": "Cultivo de temporada", "inicio": "Siembra", "fin": "Cosecha", "verbo_inicio": "sembrar",
                  "verbo_fin": "cosechar", "lugar": "chacra", "obtenidos": "cosechados", "desde": "sembrado el",
                  "hasta": "cosechado el", "al_inicio": "registrar la siembra", "al_fin": "completar la cosecha",
                  "foto": "Foto de la cosecha"},
    "frutal": {"nombre": "Frutal", "inicio": "Inicio de la campaña", "fin": "Cosecha", "verbo_inicio": "empezar la campaña",
               "verbo_fin": "cosechar", "lugar": "huerto", "obtenidos": "cosechados", "desde": "campaña desde el",
               "hasta": "cosechado el", "al_inicio": "registrar el inicio de la campaña", "al_fin": "completar la cosecha",
               "foto": "Foto de la cosecha"},
    "crianza": {"nombre": "Crianza de animales", "inicio": "Inicio del periodo", "fin": "Recolección",
                "verbo_inicio": "empezar el periodo", "verbo_fin": "recoger", "lugar": "granja", "obtenidos": "recogidos",
                "desde": "periodo desde el", "hasta": "hasta el", "al_inicio": "registrar el inicio del periodo",
                "al_fin": "registrar lo recogido", "foto": "Foto de lo recogido"},
    "elaboracion": {"nombre": "Elaboración", "inicio": "Inicio de la elaboración", "fin": "Producto listo",
                    "verbo_inicio": "empezar a elaborar", "verbo_fin": "terminar", "lugar": "taller",
                    "obtenidos": "elaborados", "desde": "elaboración desde el", "hasta": "listo el",
                    "al_inicio": "registrar la elaboración", "al_fin": "registrar lo elaborado", "foto": "Foto de lo elaborado"},
}

# Rubro: (nombre, ícono). La columna de siempre a la que suma (para la ganancia y los gráficos de antes): semilla →
# costo_semillas, mano_obra → costo_mano_obra, todo lo demás → costo_insumos.
RUBROS = {
    "semilla": ("Semilla o plantines", "seedling"),
    "abono": ("Abono y fertilizantes", "plant-2"),
    "fitosanitario": ("Productos para fumigar", "spray"),
    "mano_obra": ("Mano de obra (jornales)", "users"),
    "maquinaria": ("Yunta, tractor o herramientas", "tractor"),
    "riego": ("Riego (agua, mangueras)", "droplet"),
    "alimento": ("Alimento de los animales", "bowl"),
    "sanidad": ("Vacunas y medicinas", "vaccine"),
    "leche": ("Leche (materia prima)", "milk"),
    "insumos_elaboracion": ("Cuajo, cultivos, sal o azúcar", "flask"),
    "envases": ("Sacos, bandejas o envases", "package"),
    "energia": ("Gas, leña o luz", "flame"),
    "transporte": ("Transporte o flete", "truck"),
    "otros": ("Otros gastos", "dots"),
}


def columna_de(rubro: str) -> str:
    return {"semilla": "costo_semillas", "mano_obra": "costo_mano_obra"}.get(rubro, "costo_insumos")


def _etapa(clave, nombre, que_se_hace, rubros, momento="cuidado"):
    return {"clave": clave, "nombre": nombre, "que_se_hace": que_se_hace, "rubros": list(rubros), "momento": momento}


PREPARACION = _etapa("preparacion", "Preparación del terreno",
                     "Arar o barbechar la tierra para dejarla suelta y lista para sembrar (con yunta o tractor).",
                     ("maquinaria", "mano_obra"), "inicio")
DESHIERBO = _etapa("deshierbo", "Deshierbo", "Quitar las malas hierbas para que no le quiten agua ni abono a la planta.",
                   ("mano_obra", "fitosanitario"))
RIEGO = _etapa("riego", "Riego", "Regar cuando no llueve: agua, mangueras y el tiempo de quien riega.", ("riego", "mano_obra"))
ABONAMIENTO = _etapa("abonamiento", "Abonamiento", "Abonar de nuevo mientras la planta crece.", ("abono", "mano_obra"))


def _fumigacion(plagas):
    return _etapa("fumigacion", "Control de plagas y enfermedades", f"Fumigar o curar contra {plagas}.",
                  ("fitosanitario", "mano_obra", "maquinaria"))


FAMILIAS = {
    "tuberculos": {"tipo": "temporada", "etapas": [
        PREPARACION,
        _etapa("siembra", "Siembra", "La semilla, el abono de fondo y la mano de obra para sembrar.",
               ("semilla", "abono", "mano_obra"), "inicio"),
        DESHIERBO,
        _etapa("aporque", "Aporque", "Arrimar tierra al pie de la planta y volver a abonar.", ("abono", "mano_obra")),
        _fumigacion("la rancha, el gorgojo de los Andes u otras plagas"),
        _etapa("cosecha", "Cosecha", "Sacar los tubérculos, seleccionarlos y ensacarlos.",
               ("mano_obra", "envases", "transporte"), "final"),
    ]},
    "granos_andinos": {"tipo": "temporada", "etapas": [
        PREPARACION,
        _etapa("siembra", "Siembra", "La semilla, el abono y la mano de obra para sembrar.", ("semilla", "abono", "mano_obra"), "inicio"),
        _etapa("raleo", "Raleo y deshierbo", "Sacar las plantas de más y las malas hierbas.", ("mano_obra",)),
        _etapa("aporque", "Aporque", "Arrimar tierra al pie de la planta y abonar.", ("abono", "mano_obra")),
        _fumigacion("la polilla, el mildiu u otras plagas"),
        _etapa("siega", "Siega", "Cortar las plantas cuando el grano madura y dejarlas secar en arcos.", ("mano_obra",), "final"),
        _etapa("trilla", "Trilla y venteo", "Separar el grano, ventearlo, limpiarlo y ensacarlo.",
               ("mano_obra", "maquinaria", "envases"), "final"),
    ]},
    "cereales": {"tipo": "temporada", "etapas": [
        PREPARACION,
        _etapa("siembra", "Siembra", "La semilla (al voleo o en surcos), el abono y la mano de obra.",
               ("semilla", "abono", "mano_obra"), "inicio"),
        DESHIERBO,
        _fumigacion("la roya u otras plagas"),
        _etapa("siega", "Siega", "Cortar el cereal maduro y juntarlo en parvas.", ("mano_obra", "maquinaria"), "final"),
        _etapa("trilla", "Trilla", "Separar el grano de la paja, limpiarlo y ensacarlo.", ("maquinaria", "mano_obra", "envases"), "final"),
    ]},
    "maiz": {"tipo": "temporada", "etapas": [
        PREPARACION,
        _etapa("siembra", "Siembra", "La semilla, el abono y la mano de obra para sembrar.", ("semilla", "abono", "mano_obra"), "inicio"),
        _etapa("deshierbo", "Deshierbo (primer cultivo)", "Quitar las malas hierbas y aflojar la tierra.", ("mano_obra", "fitosanitario")),
        _etapa("aporque", "Aporque y abonamiento", "Arrimar tierra al pie de la planta y abonar.", ("abono", "mano_obra")),
        _fumigacion("el cogollero, el mazorquero u otras plagas"),
        _etapa("cosecha", "Cosecha", "Recoger los choclos o las mazorcas secas, despancar y desgranar.",
               ("mano_obra", "envases", "transporte"), "final"),
    ]},
    "legumbres": {"tipo": "temporada", "etapas": [
        PREPARACION,
        _etapa("siembra", "Siembra", "La semilla, el abono y la mano de obra para sembrar.", ("semilla", "abono", "mano_obra"), "inicio"),
        DESHIERBO,
        _etapa("aporque", "Aporque", "Arrimar tierra al pie de la planta.", ("mano_obra", "abono")),
        _fumigacion("la mancha chocolate, los pulgones u otras plagas"),
        _etapa("cosecha", "Cosecha", "Recoger las vainas verdes (en varias pasadas) o el grano seco.",
               ("mano_obra", "envases", "transporte"), "final"),
    ]},
    "hortalizas_trasplante": {"tipo": "temporada", "etapas": [
        _etapa("almacigo", "Almácigo o plantines", "Hacer el almácigo o comprar los plantines.", ("semilla", "abono", "mano_obra"), "inicio"),
        _etapa("preparacion", "Preparación del terreno", "Arar, mullir y abonar la tierra donde irán los plantines.",
               ("maquinaria", "mano_obra", "abono"), "inicio"),
        _etapa("trasplante", "Trasplante", "Pasar los plantines a la chacra.", ("mano_obra",), "inicio"),
        RIEGO, DESHIERBO, ABONAMIENTO, _fumigacion("los pulgones, gusanos u hongos"),
        _etapa("cosecha", "Cosecha", "Cosechar, limpiar y poner en jabas o sacos.", ("mano_obra", "envases", "transporte"), "final"),
    ]},
    "hortalizas_directas": {"tipo": "temporada", "etapas": [
        _etapa("preparacion", "Preparación del terreno", "Arar, mullir y abonar la tierra.", ("maquinaria", "mano_obra", "abono"), "inicio"),
        _etapa("siembra", "Siembra", "La semilla y la mano de obra para sembrar.", ("semilla", "mano_obra"), "inicio"),
        RIEGO,
        _etapa("raleo", "Raleo y deshierbo", "Sacar las plantas de más y las malas hierbas.", ("mano_obra",)),
        ABONAMIENTO, _fumigacion("los pulgones, gusanos u hongos"),
        _etapa("cosecha", "Cosecha", "Cosechar, limpiar y poner en jabas o sacos.", ("mano_obra", "envases", "transporte"), "final"),
    ]},
    "hierbas": {"tipo": "temporada", "etapas": [
        _etapa("preparacion", "Preparación del terreno", "Mullir y abonar la tierra.", ("mano_obra", "abono"), "inicio"),
        _etapa("siembra", "Siembra", "La semilla y la mano de obra para sembrar.", ("semilla", "mano_obra"), "inicio"),
        RIEGO, DESHIERBO,
        _etapa("cosecha", "Corte y atado", "Cortar, atar en manojos y empacar.", ("mano_obra", "envases"), "final"),
    ]},
    "frutales": {"tipo": "frutal", "etapas": [
        _etapa("poda", "Poda", "Cortar ramas secas o de más para que la planta dé mejor fruta.", ("mano_obra", "maquinaria"), "inicio"),
        _etapa("abonamiento", "Abonamiento", "Abonar al empezar la campaña.", ("abono", "mano_obra"), "inicio"),
        RIEGO, _fumigacion("la mosca de la fruta, la queresa u hongos"),
        _etapa("cosecha", "Cosecha", "Recoger la fruta en su punto, seleccionarla y ponerla en jabas.",
               ("mano_obra", "envases", "transporte"), "final"),
    ]},
    "aves_huevos": {"tipo": "crianza", "etapas": [
        _etapa("alimentacion", "Alimentación", "El alimento balanceado o los granos del periodo: es el gasto más grande (cerca del 70 %).",
               ("alimento",), "inicio"),
        _etapa("sanidad", "Sanidad", "Vacunas, vitaminas y desparasitación.", ("sanidad",)),
        _etapa("manejo", "Cuidado y limpieza", "Agua, cama (viruta o paja), limpieza del galpón y luz.", ("mano_obra", "otros", "energia")),
        _etapa("recoleccion", "Recolección", "Recoger los huevos cada día, limpiarlos y ponerlos en bandejas.",
               ("mano_obra", "envases"), "final"),
    ]},
    "vacas_leche": {"tipo": "crianza", "etapas": [
        _etapa("alimentacion", "Alimentación", "Pasto, forraje (alfalfa, avena) y concentrado del periodo.", ("alimento",), "inicio"),
        _etapa("sanidad", "Sanidad", "Vacunas, desparasitación y el veterinario.", ("sanidad",)),
        _etapa("ordeno", "Ordeño", "Ordeñar cada día (a mano o con máquina) y mantener la limpieza.", ("mano_obra", "energia", "otros")),
        _etapa("acopio", "Acopio y enfriado", "Porongos, filtrar la leche y enfriarla o llevarla.", ("envases", "transporte", "energia"), "final"),
    ]},
    "abejas_miel": {"tipo": "crianza", "etapas": [
        _etapa("manejo", "Manejo de colmenas", "Revisar las colmenas, cambiar marcos y cuidar el apiario.", ("mano_obra", "otros"), "inicio"),
        _etapa("alimentacion", "Alimentación", "Jarabe o alimento cuando no hay flores.", ("alimento",)),
        _etapa("sanidad", "Sanidad", "Tratamientos contra la varroa u otras enfermedades.", ("sanidad",)),
        _etapa("cosecha", "Cosecha y envasado", "Sacar los marcos, extraer la miel, filtrarla y envasarla.", ("mano_obra", "envases"), "final"),
    ]},
    "queso": {"tipo": "elaboracion", "etapas": [
        _etapa("leche", "Leche", "Los litros de leche que usas: para 1 kg de queso fresco se necesitan de 7 a 10 litros.", ("leche",), "inicio"),
        _etapa("insumos", "Cuajo y sal", "El cuajo, la sal y lo demás que se agrega a la leche.", ("insumos_elaboracion",), "inicio"),
        _etapa("elaboracion", "Elaboración", "Calentar, cuajar, cortar la cuajada, moldear y prensar.", ("mano_obra", "energia"), "final"),
        _etapa("empaque", "Empaque", "Bolsas, papel o envases para venderlo.", ("envases",), "final"),
    ]},
    "yogurt": {"tipo": "elaboracion", "etapas": [
        _etapa("leche", "Leche", "Los litros de leche que usas.", ("leche",), "inicio"),
        _etapa("insumos", "Cultivo, azúcar y fruta", "El cultivo lácteo, el azúcar y la fruta o el sabor.", ("insumos_elaboracion",), "inicio"),
        _etapa("elaboracion", "Elaboración", "Pasteurizar, enfriar, agregar el cultivo y dejar que fermente.", ("mano_obra", "energia"), "final"),
        _etapa("envasado", "Envasado", "Botellas o envases y sus etiquetas.", ("envases",), "final"),
    ]},
    "mantequilla": {"tipo": "elaboracion", "etapas": [
        _etapa("leche", "Leche o crema", "La leche o la crema que usas.", ("leche",), "inicio"),
        _etapa("elaboracion", "Batido y lavado", "Batir la crema, lavar y amasar la mantequilla.", ("mano_obra", "energia"), "final"),
        _etapa("empaque", "Empaque", "Papel o envases para venderla.", ("envases",), "final"),
    ]},
}

# Genéricas, para un alimento que no conocemos: la de su tipo.
FAMILIA_GENERICA = {"temporada": "temporada_generica", "frutal": "frutales", "crianza": "crianza_generica",
                    "elaboracion": "elaboracion_generica"}
FAMILIAS["temporada_generica"] = {"tipo": "temporada", "etapas": [
    PREPARACION,
    _etapa("siembra", "Siembra", "La semilla, el abono y la mano de obra para sembrar.", ("semilla", "abono", "mano_obra"), "inicio"),
    _etapa("labores", "Labores de cultivo", "Deshierbo, riego, aporque o abonamiento mientras crece.", ("mano_obra", "abono", "riego")),
    _fumigacion("las plagas y enfermedades"),
    _etapa("cosecha", "Cosecha", "Cosechar, seleccionar y ensacar.", ("mano_obra", "envases", "transporte"), "final"),
]}
FAMILIAS["crianza_generica"] = {"tipo": "crianza", "etapas": [
    _etapa("alimentacion", "Alimentación", "El alimento de los animales en el periodo.", ("alimento",), "inicio"),
    _etapa("sanidad", "Sanidad", "Vacunas y medicinas.", ("sanidad",)),
    _etapa("manejo", "Cuidado y limpieza", "Agua, limpieza y la mano de obra del cuidado.", ("mano_obra", "otros")),
    _etapa("recoleccion", "Recolección", "Recoger lo producido y prepararlo para vender.", ("mano_obra", "envases"), "final"),
]}
FAMILIAS["elaboracion_generica"] = {"tipo": "elaboracion", "etapas": [
    _etapa("materia_prima", "Materia prima", "Lo que usas para elaborar el producto.", ("leche", "otros"), "inicio"),
    _etapa("elaboracion", "Elaboración", "La mano de obra y la energía para elaborarlo.", ("mano_obra", "energia"), "final"),
    _etapa("empaque", "Empaque", "Envases y etiquetas.", ("envases",), "final"),
]}

# Alimento: (nombre, familia, alias que lo reconocen en el nombre que escribe el agricultor, unidad típica).
# El orden importa: se reconoce el primero cuyo alias aparezca ("queso" antes que "leche", "maiz" antes que todo).
ALIMENTOS = {
    "queso": ("Queso", "queso", ("queso", "quesillo"), "kg"),
    "yogurt": ("Yogurt", "yogurt", ("yogurt", "yogur", "yoghurt"), "litro"),
    "mantequilla": ("Mantequilla", "mantequilla", ("mantequilla", "manteca"), "kg"),
    "huevo": ("Huevos", "aves_huevos", ("huevo", "huevos"), "unidad"),
    "leche": ("Leche", "vacas_leche", ("leche",), "litro"),
    "miel": ("Miel", "abejas_miel", ("miel",), "kg"),
    "papa": ("Papa", "tuberculos", ("papa", "patata"), "kg"),
    "oca": ("Oca", "tuberculos", ("oca",), "kg"),
    "olluco": ("Olluco", "tuberculos", ("olluco", "ulluco", "lisa"), "kg"),
    "mashua": ("Mashua", "tuberculos", ("mashua", "isano"), "kg"),
    "camote": ("Camote", "tuberculos", ("camote",), "kg"),
    "yuca": ("Yuca", "tuberculos", ("yuca",), "kg"),
    "maiz": ("Maíz", "maiz", ("maiz", "choclo"), "kg"),
    "quinua": ("Quinua", "granos_andinos", ("quinua", "quinoa"), "kg"),
    "kiwicha": ("Kiwicha", "granos_andinos", ("kiwicha", "amaranto"), "kg"),
    "canihua": ("Cañihua", "granos_andinos", ("canihua", "kaniwa"), "kg"),
    "trigo": ("Trigo", "cereales", ("trigo",), "kg"),
    "cebada": ("Cebada", "cereales", ("cebada",), "kg"),
    "avena": ("Avena", "cereales", ("avena",), "kg"),
    "haba": ("Haba", "legumbres", ("haba", "habas"), "kg"),
    "arveja": ("Arveja", "legumbres", ("arveja", "alverja"), "kg"),
    "tarwi": ("Tarwi", "legumbres", ("tarwi", "chocho", "lupino"), "kg"),
    "frijol": ("Frijol", "legumbres", ("frijol", "frejol", "poroto"), "kg"),
    "pallar": ("Pallar", "legumbres", ("pallar",), "kg"),
    "cebolla": ("Cebolla", "hortalizas_trasplante", ("cebolla",), "kg"),
    "lechuga": ("Lechuga", "hortalizas_trasplante", ("lechuga",), "unidad"),
    "col": ("Col o repollo", "hortalizas_trasplante", ("col", "repollo", "coliflor", "brocoli"), "unidad"),
    "tomate": ("Tomate", "hortalizas_trasplante", ("tomate",), "kg"),
    "aji": ("Ají o rocoto", "hortalizas_trasplante", ("aji", "rocoto", "pimiento"), "kg"),
    "fresa": ("Fresa", "hortalizas_trasplante", ("fresa", "frutilla"), "kg"),
    "zanahoria": ("Zanahoria", "hortalizas_directas", ("zanahoria",), "kg"),
    "espinaca": ("Espinaca", "hortalizas_directas", ("espinaca", "acelga"), "kg"),
    "beterraga": ("Beterraga", "hortalizas_directas", ("beterraga", "betarraga", "remolacha"), "kg"),
    "rabanito": ("Rabanito o nabo", "hortalizas_directas", ("rabanito", "rabano", "nabo"), "kg"),
    "ajo": ("Ajo", "hortalizas_directas", ("ajo",), "kg"),
    "zapallo": ("Zapallo", "hortalizas_directas", ("zapallo", "calabaza", "zapallito"), "kg"),
    "sandia": ("Sandía o melón", "hortalizas_directas", ("sandia", "melon"), "kg"),
    "pepino": ("Pepino", "hortalizas_directas", ("pepino",), "kg"),
    "cilantro": ("Hierbas aromáticas", "hierbas", ("cilantro", "culantro", "oregano", "perejil", "hierbabuena", "huacatay",
                                                   "albahaca", "romero", "manzanilla", "muna"), "unidad"),
    "palta": ("Palta", "frutales", ("palta", "aguacate"), "kg"),
    "citricos": ("Cítricos", "frutales", ("naranja", "mandarina", "limon", "lima", "toronja"), "kg"),
    "mango": ("Mango", "frutales", ("mango",), "kg"),
    "chirimoya": ("Chirimoya", "frutales", ("chirimoya", "guanabana"), "kg"),
    "manzana": ("Manzana o pera", "frutales", ("manzana", "pera", "membrillo"), "kg"),
    "durazno": ("Durazno", "frutales", ("durazno", "melocoton", "ciruela", "nispero"), "kg"),
    "platano": ("Plátano", "frutales", ("platano", "banano"), "kg"),
    "papaya": ("Papaya", "frutales", ("papaya",), "kg"),
    "pina": ("Piña", "frutales", ("pina", "ananas"), "kg"),
    "tuna": ("Tuna", "frutales", ("tuna",), "kg"),
    "lucuma": ("Lúcuma", "frutales", ("lucuma",), "kg"),
    "granadilla": ("Granadilla o maracuyá", "frutales", ("granadilla", "maracuya", "tumbo"), "kg"),
    "uva": ("Uva", "frutales", ("uva",), "kg"),
    "sauco": ("Sauco o aguaymanto", "frutales", ("sauco", "aguaymanto"), "kg"),
}


# La categoría del catálogo (Productos.CATEGORIAS_VALIDAS) de cada familia; algunos alimentos la cambian (la fresa y la
# sandía se plantan como hortalizas pero se venden como fruta). None = el agricultor la elige al completar el borrador.
CATEGORIA_POR_FAMILIA = {
    "tuberculos": "tuberculo", "granos_andinos": "grano", "cereales": "grano", "maiz": "grano", "legumbres": "legumbre",
    "hortalizas_trasplante": "verdura", "hortalizas_directas": "verdura", "hierbas": "hierba", "frutales": "fruta",
    "aves_huevos": "huevo", "vacas_leche": "lacteo", "queso": "lacteo", "yogurt": "lacteo", "mantequilla": "lacteo",
}
CATEGORIA_POR_ALIMENTO = {"fresa": "fruta", "sandia": "fruta"}


def categoria_de(clave: str | None) -> str | None:
    if not clave or clave not in ALIMENTOS:
        return None
    return CATEGORIA_POR_ALIMENTO.get(clave) or CATEGORIA_POR_FAMILIA.get(ALIMENTOS[clave][1])


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().replace("ñ", "n").split())


def _palabras(texto: str) -> list[str]:
    return [p.strip(".,;:()") for p in normalizar(texto).split()]


def clave_para(nombre: str) -> str | None:
    """La clave del alimento que se reconoce en el nombre que escribió el agricultor ("Papa amarilla" → "papa",
    "Huevo de codorniz" → "huevo"), o None. Por palabra completa o su plural: "col" no reconoce "coliflor" por error
    (coliflor tiene su propio alias) ni "lima" reconoce "limon"."""
    palabras = _palabras(nombre)
    for clave, (_nombre, _familia, alias, _unidad) in ALIMENTOS.items():
        for a in alias:
            if a in palabras or f"{a}s" in palabras or f"{a}es" in palabras:
                return clave
    return None


def plantilla(clave: str, tipo: str | None = None) -> dict:
    """La plantilla de un alimento conocido; si no se conoce, la genérica del tipo (cultivo de temporada si tampoco)."""
    if clave in ALIMENTOS:
        nombre, familia, _alias, unidad = ALIMENTOS[clave]
    else:
        nombre, familia, unidad = None, FAMILIA_GENERICA.get(tipo or "temporada", "temporada_generica"), None
        clave = f"generica_{FAMILIAS[familia]['tipo']}"
    tipo_real = FAMILIAS[familia]["tipo"]
    return {"clave": clave, "nombre": nombre, "familia": familia, "tipo": tipo_real, "unidad": unidad,
            "etiquetas": TIPOS[tipo_real], "etapas": FAMILIAS[familia]["etapas"]}


def plantilla_para(nombre: str) -> dict:
    return plantilla(clave_para(nombre) or "")


def plantilla_por_clave(clave: str | None) -> dict | None:
    """La plantilla guardada en un registro (None en los registros de antes)."""
    if not clave:
        return None
    if clave.startswith("generica_"):
        return plantilla("", clave.removeprefix("generica_"))
    return plantilla(clave) if clave in ALIMENTOS else None


def catalogo() -> dict:
    """Todo lo que la pantalla necesita para elegir un alimento y anotar gastos (una sola llamada, se guarda en caché)."""
    return {
        "tipos": TIPOS,
        "rubros": {k: {"nombre": n, "icono": i} for k, (n, i) in RUBROS.items()},
        "familias": FAMILIAS,
        # Con sus alias, para que la pantalla reconozca el alimento igual que clave_para (sin esperar al servidor).
        "alimentos": [{"clave": k, "nombre": n, "familia": f, "tipo": FAMILIAS[f]["tipo"], "unidad": u, "alias": list(a)}
                      for k, (n, f, a, u) in ALIMENTOS.items()],
    }
