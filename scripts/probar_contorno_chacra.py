"""Prueba contra el clúster local el contorno poligonal de la chacra y lo que de él se ve en público.

Entra con la cuenta QA del productor (QA_PRODUCTOR_* de frontend/.env.qa), crea una chacra de prueba "QA-CTN" junto a la
primera chacra real de esa cuenta (o en Ayacucho si no tiene ninguna) y recorre los casos:

  PUT/DELETE /chacras/{id}/contorno
    contorno válido (200 y área ≈ 1 ha), lista con el contorno, autointersectado (422), fuera del Perú (422),
    punto de la chacra fuera del contorno (422), chacra ajena (403; su id sale de la base con kubectl), DELETE del
    contorno (200 y GET sin él).
  GET /chacras/{id}/contorno (el contorno exacto, por rol)
    dueño 200, admin 200 (QA_ADMIN_*), comprador 403 (QA_EMAIL), productor ajeno 403, sin token 401.
  GET /productores/produccion/{registro_id}/origen (público)
    sin token 200 con solo chacra_codigo, chacra_nombre, area_m2 y tiene_contorno; ni el polígono ni coordenadas, con
    ningún nombre de clave ni como texto; un registro que no existe, 404. Usa una siembra real de la cuenta QA, solo
    para leer.

  Geocerca de la siembra (POST /productores/produccion)
    sin contorno: metodo=radio (resultado radio, o sin_gps sin ubicación). Con contorno: dentro, cerca (30 m fuera del
    borde), fuera (200 m), precision_baja (GPS de 150 m) y sin_gps; todas se registran igual, con la huella del
    contorno vigente y sin devolver coordenadas.
  Versiones del contorno (GET /chacras/{id}/contorno/versiones)
    un contorno distinto es la versión 2; el registro anterior conserva la huella de la 1, que se recalcula aquí con
    la receta canónica (claves ordenadas, sin espacios, 7 decimales, SHA-256).
  GET /productores/produccion/{id}/geocerca (lo que ve el admin al recibir la cosecha)
    admin 200 sin coordenadas; productor 403, comprador 403, sin token 401.
  Cadenas de certificación existentes
    para cada producto del catálogo público: /verificar dice que es válida y el hash de cada bloque, recalculado aquí
    con la fórmula de siempre, coincide con el guardado. Ningún bloque trae coordenadas.

Al final borra las siembras de prueba (con el DELETE de limpieza QA, llamado dentro del pod de Productores: el secreto
no sale del clúster) y la chacra de prueba. No modifica ninguna chacra real.

Uso (desde la raíz del repositorio, con el clúster docker-desktop arriba):
    python scripts/probar_contorno_chacra.py [--base http://localhost:8000/api] [--contexto docker-desktop]
Sale con código 1 si algún caso falla.
"""
import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # la consola de Windows (cp1252) no imprime las flechas

RAIZ = Path(__file__).resolve().parent.parent
METROS_POR_GRADO_LAT = 111_320.0
MENSAJE_PUNTO_FUERA = "El punto registrado de la chacra queda fuera del contorno dibujado"
CONTEXTO_LOCAL = "docker-desktop"
CAMPOS_DEL_ORIGEN = {"chacra_codigo", "chacra_nombre", "area_m2", "tiene_contorno"}
# Lo público nunca expone la finca exacta: ni el polígono ni un punto sin redondear.
CLAVES_QUE_LO_PUBLICO_NO_TRAE = {"contorno", "coordinates", "ubicacion_latitud", "ubicacion_longitud", "latitud", "longitud"}


def leer_env_qa():
    valores = {}
    for linea in (RAIZ / "frontend" / ".env.qa").read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


class Api:
    """Un cliente del gateway con (o sin) el token de una cuenta. `texto` guarda el cuerpo crudo de la última respuesta."""

    def __init__(self, base, token=None):
        self.base = base.rstrip("/")
        self.token = token
        self.texto = ""

    def llamar(self, metodo, ruta, cuerpo=None):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        peticion = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        peticion.add_header("Content-Type", "application/json")
        if self.token:
            peticion.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(peticion, timeout=20) as resp:
                estado, crudo = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            estado, crudo = e.code, e.read()
        self.texto = crudo.decode("utf-8", "replace")
        try:
            return estado, json.loads(self.texto or "null")
        except ValueError:
            return estado, self.texto

    def entrar(self, email, password, cuenta):
        estado, cuerpo = self.llamar("POST", "/usuarios/usuarios/login", {"email": email, "password": password})
        if estado != 200:
            sys.exit(f"No se pudo iniciar sesión con la cuenta QA de {cuenta}: {estado} {cuerpo}")
        self.token = cuerpo["access_token"]
        return self


def cuadrado(lat, lng, lado_m=100.0):
    dlat = lado_m / 2 / METROS_POR_GRADO_LAT
    dlng = lado_m / 2 / (METROS_POR_GRADO_LAT * math.cos(math.radians(lat)))
    esquinas = [[lng - dlng, lat - dlat], [lng + dlng, lat - dlat], [lng + dlng, lat + dlat], [lng - dlng, lat + dlat]]
    return {"type": "Polygon", "coordinates": [esquinas + [esquinas[0]]]}


def desplazar(lat, lng, metros_este):
    return lat, lng + metros_este / (METROS_POR_GRADO_LAT * math.cos(math.radians(lat)))


def claves(dato):
    """Todas las claves de un JSON, a cualquier profundidad."""
    if isinstance(dato, dict):
        return set(dato) | {clave for valor in dato.values() for clave in claves(valor)}
    if isinstance(dato, list):
        return {clave for valor in dato for clave in claves(valor)}
    return set()


def kubectl(contexto, *argumentos):
    try:
        salida = subprocess.run(["kubectl", "--context", contexto, *argumentos], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  (kubectl no respondió: {e})")
        return ""
    return salida.stdout.strip()


def chacra_ajena(contexto, productor_id):
    """El id de una chacra de OTRO productor, leído de la base del clúster (solo lectura)."""
    consulta = f"SELECT id FROM chacras WHERE productor_id <> '{productor_id}' LIMIT 1"
    return kubectl(contexto, "exec", "deploy/db-productores", "--", "sh", "-c",
                   f'psql -U "$POSTGRES_USER" -d productores -tAc "{consulta}"') or None


def huella_del_contorno(contorno):
    """La receta de productores/contornos.py (hash_del_contorno), escrita aparte: el GeoJSON con las claves ordenadas,
    sin espacios y las coordenadas a 7 decimales, y su SHA-256."""
    anillos = [[[round(float(p[0]), 7), round(float(p[1]), 7)] for p in anillo] for anillo in contorno["coordinates"]]
    canonico = json.dumps({"type": "Polygon", "coordinates": anillos}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def hash_del_bloque(bloque):
    """La fórmula de certificacion/logica_cadena.py (calcular_hash), con lo que devuelve el historial público."""
    fecha = str(bloque["fecha"]).replace("T", " ")
    contenido = f"{bloque['indice']}{bloque['producto_id']}{bloque['evento']}{bloque['datos']}{fecha}{bloque['hash_anterior']}"
    return hashlib.sha256(contenido.encode()).hexdigest()


# Una coordenada escrita con decimales (-13.1849, -74.30606). Las distancias de la cadena van con coma: "0,03 km".
PATRON_COORDENADA = re.compile(r"-\d{1,2}\.\d{3,}")
COORDENADAS = {"siembra_latitud", "siembra_longitud", "cosecha_latitud", "cosecha_longitud", "latitud", "longitud",
               "ubicacion_latitud", "ubicacion_longitud", "contorno", "coordinates"}


def borrar_siembras_de_prueba(contexto, ids):
    """Borra las siembras que creó esta prueba con el DELETE de limpieza QA de Productores. Se llama DENTRO del pod
    (kubectl exec): el gateway bloquea esa ruta desde fuera y el secreto de limpieza no sale del clúster. Devuelve
    cuántas borró."""
    if not ids:
        return 0
    programa = (
        "import os, sys, urllib.request\n"
        "borradas = 0\n"
        "for registro in sys.argv[1:]:\n"
        "    peticion = urllib.request.Request('http://127.0.0.1:8001/productores/produccion/' + registro, method='DELETE',\n"
        "                                      headers={'X-Servicio-Secreto': os.environ.get('QA_LIMPIEZA_SECRETO', '')})\n"
        "    try:\n"
        "        urllib.request.urlopen(peticion, timeout=10)\n"
        "        borradas += 1\n"
        "    except Exception:\n"
        "        pass\n"
        "print(borradas)\n"
    )
    salida = kubectl(contexto, "exec", "deploy/productores", "--", "python", "-c", programa, *ids)
    return int(salida) if salida.isdigit() else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000/api")
    parser.add_argument("--contexto", default=CONTEXTO_LOCAL)
    args = parser.parse_args()

    env = leer_env_qa()
    api = Api(args.base).entrar(env["QA_PRODUCTOR_EMAIL"], env["QA_PRODUCTOR_PASSWORD"], "productor")
    visitante = Api(args.base)  # sin token
    resultados = []

    def caso(nombre, ok, detalle=""):
        resultados.append(ok)
        print(f"  [{'OK' if ok else 'FALLA'}] {nombre}" + (f"  → {detalle}" if detalle else ""))

    def detalle_de(estado, cuerpo):
        return f"{estado} {cuerpo.get('detail') if isinstance(cuerpo, dict) else cuerpo}"

    estado, mias = api.llamar("GET", "/productores/chacras/me")
    if estado != 200:
        sys.exit(f"GET /chacras/me devolvió {estado}: {mias}")
    for vieja in mias:
        if vieja["codigo"] == "QA-CTN":  # restos de una corrida anterior: primero sus siembras de prueba, luego la chacra
            _, anteriores = api.llamar("GET", "/productores/productores/produccion/me")
            borrar_siembras_de_prueba(args.contexto, [r["id"] for r in anteriores or [] if r.get("chacra_id") == vieja["id"]])
            api.llamar("DELETE", f"/productores/chacras/{vieja['id']}")
    mias = [c for c in mias if c["codigo"] != "QA-CTN"]
    if mias:
        lat, lng = float(mias[0]["ubicacion_latitud"]), float(mias[0]["ubicacion_longitud"])
    else:
        lat, lng = -13.16, -74.22
    estado, chacra = api.llamar("POST", "/productores/chacras",
                                {"codigo": "QA-CTN", "nombre": "QA contorno", "ubicacion_latitud": lat, "ubicacion_longitud": lng})
    if estado != 200:
        sys.exit(f"No se pudo crear la chacra de prueba: {estado} {chacra}")
    cid = chacra["id"]
    print(f"Chacra de prueba QA-CTN {cid} en ({lat}, {lng})")

    siembras = []   # las que crea esta prueba: se borran al final

    def sembrar(**ubicacion):
        hoy = datetime.now(timezone.utc).replace(tzinfo=None)
        estado, cuerpo = api.llamar("POST", "/productores/productores/produccion", {
            "chacra_id": cid, "cultivo": "Prueba QA geocerca", "numero_parcelas": 1, "unidad_medida": "kg",
            "fecha_siembra": hoy.isoformat(), "fecha_cosecha_estimada": (hoy + timedelta(days=120)).isoformat(), **ubicacion,
        })
        if estado == 200:
            siembras.append(cuerpo["id"])
        return estado, cuerpo if isinstance(cuerpo, dict) else {}

    def gps(metros_este=0.0, precision=None):
        lat_gps, lng_gps = desplazar(lat, lng, metros_este)
        return {"latitud": lat_gps, "longitud": lng_gps, **({} if precision is None else {"precision_m": precision})}

    def geocerca_de(r):
        return f"{r.get('siembra_metodo_geocerca')}/{r.get('siembra_resultado_geocerca')} borde={r.get('siembra_distancia_borde_m')} precision={r.get('siembra_precision_m')}"

    try:
        ruta = f"/productores/chacras/{cid}/contorno"
        ajena = chacra_ajena(args.contexto, chacra["productor_id"])

        print("Geocerca sin contorno (el radio de siempre)")
        estado, r = sembrar(**gps(300, 150))
        ok = estado == 200 and (r.get("siembra_metodo_geocerca"), r.get("siembra_resultado_geocerca")) == ("radio", "radio") \
            and r.get("siembra_distancia_borde_m") is None and r.get("siembra_contorno_hash") is None \
            and r.get("siembra_precision_m") == 150 and 0.25 <= (r.get("siembra_distancia_km") or 0) <= 0.35
        caso("sin contorno, con GPS → método radio y la distancia al punto de siempre", ok, f"{estado} {geocerca_de(r)}")
        estado, r = sembrar()
        ok = estado == 200 and (r.get("siembra_metodo_geocerca"), r.get("siembra_resultado_geocerca")) == ("radio", "sin_gps")
        caso("sin contorno, sin GPS → sin_gps", ok, f"{estado} {geocerca_de(r)}")

        print("PUT / DELETE del contorno")
        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": cuadrado(lat, lng), "area_m2": 1})
        ok = estado == 200 and isinstance(cuerpo, dict) and 9_500 <= (cuerpo.get("area_m2") or 0) <= 10_500 \
            and cuerpo.get("contorno", {}).get("type") == "Polygon"
        caso("contorno válido → 200 con el área calculada en el servidor", ok, f"{estado} area_m2={cuerpo.get('area_m2') if isinstance(cuerpo, dict) else cuerpo}")
        guardado = cuerpo.get("contorno") if isinstance(cuerpo, dict) else None

        estado, lista = api.llamar("GET", "/productores/chacras/me")
        mia = next((c for c in lista if c["id"] == cid), None) if estado == 200 else None
        caso("GET /chacras/me trae el contorno y el área", bool(mia and mia.get("contorno") and mia.get("area_m2")), str(estado))

        d = 0.0005
        lazo = {"type": "Polygon", "coordinates": [[[lng - d, lat - d], [lng + d, lat + d], [lng + d, lat - d], [lng - d, lat + d], [lng - d, lat - d]]]}
        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": lazo})
        caso("autointersectado → 422", estado == 422 and str(cuerpo.get("detail", "")).startswith("El contorno no es válido"), detalle_de(estado, cuerpo))

        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": cuadrado(4.71, -74.07)})
        caso("fuera del Perú → 422", estado == 422 and cuerpo.get("detail") == "El contorno debe estar dentro del Perú", detalle_de(estado, cuerpo))

        lejos_lat, lejos_lng = desplazar(lat, lng, 550)
        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": cuadrado(lejos_lat, lejos_lng)})
        caso("punto de la chacra fuera del contorno → 422", estado == 422 and cuerpo.get("detail") == MENSAJE_PUNTO_FUERA, detalle_de(estado, cuerpo))

        if ajena:
            estado, cuerpo = api.llamar("PUT", f"/productores/chacras/{ajena}/contorno", {"contorno": cuadrado(lat, lng)})
            estado2, _ = api.llamar("DELETE", f"/productores/chacras/{ajena}/contorno")
            caso("chacra ajena → 403 (PUT y DELETE)", estado == 403 and estado2 == 403, f"PUT {estado} / DELETE {estado2}")
        else:
            caso("chacra ajena → 403", False, "no hay otra chacra en la base para probarlo")

        print("GET del contorno exacto, por rol")
        estado, cuerpo = api.llamar("GET", ruta)
        caso("dueño → 200 con su polígono", estado == 200 and cuerpo.get("contorno") == guardado and cuerpo.get("chacra_id") == cid, detalle_de(estado, cuerpo) if estado != 200 else "200")

        admin = Api(args.base).entrar(env["QA_ADMIN_EMAIL"], env["QA_ADMIN_PASSWORD"], "admin")
        estado, cuerpo = admin.llamar("GET", ruta)
        caso("admin → 200", estado == 200 and cuerpo.get("contorno") == guardado, detalle_de(estado, cuerpo) if estado != 200 else "200")

        comprador = Api(args.base).entrar(env["QA_EMAIL"], env["QA_PASSWORD"], "comprador")
        estado, cuerpo = comprador.llamar("GET", ruta)
        caso("comprador → 403, sin el polígono", estado == 403 and "coordinates" not in comprador.texto, detalle_de(estado, cuerpo))

        if ajena:
            estado, cuerpo = api.llamar("GET", f"/productores/chacras/{ajena}/contorno")
            caso("productor ajeno → 403, sin el polígono", estado == 403 and "coordinates" not in api.texto, detalle_de(estado, cuerpo))
        else:
            caso("productor ajeno → 403", False, "no hay otra chacra en la base para probarlo")

        estado, cuerpo = visitante.llamar("GET", ruta)
        caso("sin token → 401, sin el polígono", estado == 401 and "coordinates" not in visitante.texto, detalle_de(estado, cuerpo))

        print("Origen público del producto")
        estado, registros = api.llamar("GET", "/productores/productores/produccion/me")
        # Una siembra de una chacra real de la cuenta (no las de prueba que acaba de crear este script).
        reales = {c["id"] for c in mias}
        registro = next((x for x in registros if x.get("chacra_id") in reales), None) if estado == 200 else None
        if registro:
            de_la_chacra = next((c for c in mias if c["id"] == registro["chacra_id"]), {})
            estado, cuerpo = visitante.llamar("GET", f"/productores/productores/produccion/{registro['id']}/origen")
            ok = estado == 200 and isinstance(cuerpo, dict) and set(cuerpo) == CAMPOS_DEL_ORIGEN \
                and cuerpo["chacra_codigo"] == de_la_chacra.get("codigo") \
                and cuerpo["tiene_contorno"] == bool(de_la_chacra.get("contorno"))
            caso("/origen sin token → 200 con solo sus cuatro campos", ok, f"{estado} {visitante.texto[:160]}")
            sin_la_finca = estado == 200 and claves(cuerpo).isdisjoint(CLAVES_QUE_LO_PUBLICO_NO_TRAE) \
                and "coordinates" not in visitante.texto and "Polygon" not in visitante.texto \
                and str(de_la_chacra.get("ubicacion_latitud")) not in visitante.texto \
                and str(de_la_chacra.get("ubicacion_longitud")) not in visitante.texto
            caso("/origen no trae contorno, coordinates ni ubicacion_latitud/longitud (ni como clave ni como texto)", sin_la_finca,
                 ", ".join(sorted(claves(cuerpo))) if isinstance(cuerpo, dict) else str(estado))
        else:
            caso("/origen sin token → 200", False, "la cuenta QA del productor no tiene siembras para probarlo")
        estado, cuerpo = visitante.llamar("GET", f"/productores/productores/produccion/{uuid.uuid4()}/origen")
        caso("/origen de un registro que no existe → 404", estado == 404, detalle_de(estado, cuerpo))

        print("Geocerca con contorno (cuadrado de 100 m: el borde queda a 50 m del punto)")
        estado, exacto = api.llamar("GET", ruta)
        huella = exacto.get("hash_sha256") if estado == 200 else None
        ok = estado == 200 and exacto.get("version") == 1 and huella is not None and huella == huella_del_contorno(guardado)
        caso("el contorno vigente es la versión 1 y su huella se recalcula con la receta canónica", ok, f"{estado} versión {exacto.get('version') if estado == 200 else '—'}")
        dentro_id = None
        for resultado, ubicacion, borde in [("dentro", gps(0, 12), 0), ("cerca", gps(80, 12), 30), ("fuera", gps(250, 12), 200),
                                            ("precision_baja", gps(0, 150), 0), ("sin_gps", {}, None)]:
            estado, r = sembrar(**ubicacion)
            distancia = r.get("siembra_distancia_borde_m")
            ok = estado == 200 and (r.get("siembra_metodo_geocerca"), r.get("siembra_resultado_geocerca")) == ("poligono", resultado) \
                and r.get("siembra_contorno_hash") == huella and r.get("estado") == "planificado" \
                and (distancia is None if borde is None else distancia is not None and abs(distancia - borde) <= 3) \
                and claves(r).isdisjoint(COORDENADAS) and not PATRON_COORDENADA.search(api.texto)
            caso(f"{resultado} → se registra igual, con su resultado, la huella del contorno y sin coordenadas", ok, f"{estado} {geocerca_de(r)}")
            if resultado == "dentro":
                dentro_id = r.get("id")

        print("Geocerca del registro para el admin (llegada al almacén)")
        ruta_geocerca = f"/productores/productores/produccion/{dentro_id}/geocerca"
        estado, g = admin.llamar("GET", ruta_geocerca)
        ok = estado == 200 and g["siembra"]["resultado"] == "dentro" and g["siembra"]["contorno_sha256"] == huella \
            and g["cosecha"]["resultado"] is None and claves(g).isdisjoint(COORDENADAS) \
            and "coordinates" not in admin.texto and not PATRON_COORDENADA.search(admin.texto)
        caso("admin → 200 con el resultado de la geocerca y sin coordenadas", ok, f"{estado} {', '.join(sorted(claves(g))) if isinstance(g, dict) else g}")
        estados = (api.llamar("GET", ruta_geocerca)[0], comprador.llamar("GET", ruta_geocerca)[0], visitante.llamar("GET", ruta_geocerca)[0])
        caso("productor → 403, comprador → 403, sin token → 401", estados == (403, 403, 401), str(estados))

        print("Versiones del contorno")
        estado, _ = api.llamar("PUT", ruta, {"contorno": cuadrado(lat, lng, 140)})
        estado2, versiones = api.llamar("GET", ruta + "/versiones")
        versiones = versiones if estado2 == 200 else []
        ok = estado == 200 and [(v["version"], v["vigente"]) for v in versiones] == [(1, False), (2, True)] \
            and all(huella_del_contorno(v["contorno"]) == v["hash_sha256"] for v in versiones) and versiones[0]["hash_sha256"] == huella
        caso("un contorno distinto es la versión 2; la 1 se conserva y las dos huellas se recalculan desde su polígono", ok,
             f"PUT {estado} / {[(v['version'], v['vigente']) for v in versiones]}")
        _, registros_ahora = api.llamar("GET", "/productores/productores/produccion/me")
        anterior = next((x for x in registros_ahora or [] if x["id"] == dentro_id), {})
        estado, nuevo = sembrar(**gps(60, 12))   # a 60 m: "cerca" con el contorno de 100 m, "dentro" con el de 140
        ok = anterior.get("siembra_contorno_hash") == huella and anterior.get("siembra_resultado_geocerca") == "dentro" \
            and estado == 200 and len(versiones) == 2 and nuevo.get("siembra_contorno_hash") == versiones[1]["hash_sha256"] \
            and nuevo.get("siembra_resultado_geocerca") == "dentro"
        caso("el registro anterior conserva la huella de la versión 1; uno nuevo se mide contra la 2", ok, geocerca_de(nuevo))
        estados = (comprador.llamar("GET", ruta + "/versiones")[0], visitante.llamar("GET", ruta + "/versiones")[0],
                   admin.llamar("GET", ruta + "/versiones")[0])
        caso("las versiones (con su polígono): comprador → 403, sin token → 401, admin → 200", estados == (403, 401, 200), str(estados))

        print("Cadenas de certificación existentes")
        estado, catalogo = visitante.llamar("GET", "/productos/productos")
        cadenas = bloques = 0
        rotas, con_coordenadas = [], []
        for producto in catalogo if estado == 200 else []:
            e_historial, historial = visitante.llamar("GET", f"/certificacion/certificacion/{producto['id']}/historial")
            if e_historial != 200 or not historial:
                continue
            cadenas += 1
            previo = None
            for bloque in historial:
                bloques += 1
                if hash_del_bloque(bloque) != bloque["hash_actual"] or (previo is not None and bloque["hash_anterior"] != previo):
                    rotas.append(f"{producto['id']}#{bloque['indice']}")
                previo = bloque["hash_actual"]
                if PATRON_COORDENADA.search(str(bloque.get("datos") or "")) or "coordinates" in str(bloque.get("datos") or ""):
                    con_coordenadas.append(f"{producto['id']}#{bloque['indice']}")
            e_verificar, verificacion = visitante.llamar("GET", f"/certificacion/certificacion/{producto['id']}/verificar")
            if e_verificar != 200 or not verificacion.get("valido"):
                rotas.append(f"{producto['id']} (verificar)")
        caso(f"las {cadenas} cadenas del catálogo ({bloques} bloques) verifican y el hash recalculado de cada bloque coincide",
             cadenas > 0 and not rotas, ", ".join(rotas[:4]))
        caso("ningún bloque público trae coordenadas ni el polígono", cadenas > 0 and not con_coordenadas, ", ".join(con_coordenadas[:4]))

        print("Quitar el contorno")
        estado, cuerpo = api.llamar("DELETE", ruta)
        estado2, detalle = api.llamar("GET", f"/productores/chacras/{cid}")
        ok = estado == 200 and cuerpo.get("contorno") is None and estado2 == 200 and detalle.get("contorno") is None and detalle.get("area_m2") is None
        caso("DELETE del contorno → 200 y el GET ya no lo trae", ok, f"DELETE {estado} / GET {estado2}")
    finally:
        borradas = borrar_siembras_de_prueba(args.contexto, siembras)
        estado, _ = api.llamar("DELETE", f"/productores/chacras/{cid}")
        print(f"Siembras de prueba borradas: {borradas}/{len(siembras)}. Chacra de prueba borrada: {estado}")
        if estado != 200:
            print("  OJO: quedó la chacra QA-CTN con sus siembras de prueba; la próxima corrida intenta limpiarla.")

    fallas = resultados.count(False)
    print(f"\n{len(resultados) - fallas}/{len(resultados)} casos OK" + (f", {fallas} con falla" if fallas else ""))
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
