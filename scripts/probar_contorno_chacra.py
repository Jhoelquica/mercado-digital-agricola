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

Al final borra la chacra de prueba. No modifica ninguna chacra real.

Uso (desde la raíz del repositorio, con el clúster docker-desktop arriba):
    python scripts/probar_contorno_chacra.py [--base http://localhost:8000/api] [--contexto docker-desktop]
Sale con código 1 si algún caso falla.
"""
import argparse
import json
import math
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
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
        if vieja["codigo"] == "QA-CTN":  # restos de una corrida anterior
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

    try:
        ruta = f"/productores/chacras/{cid}/contorno"
        ajena = chacra_ajena(args.contexto, chacra["productor_id"])

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
        registro = registros[0] if estado == 200 and registros else None
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

        print("Quitar el contorno")
        estado, cuerpo = api.llamar("DELETE", ruta)
        estado2, detalle = api.llamar("GET", f"/productores/chacras/{cid}")
        ok = estado == 200 and cuerpo.get("contorno") is None and estado2 == 200 and detalle.get("contorno") is None and detalle.get("area_m2") is None
        caso("DELETE del contorno → 200 y el GET ya no lo trae", ok, f"DELETE {estado} / GET {estado2}")
    finally:
        estado, _ = api.llamar("DELETE", f"/productores/chacras/{cid}")
        print(f"Chacra de prueba borrada: {estado}")

    fallas = resultados.count(False)
    print(f"\n{len(resultados) - fallas}/{len(resultados)} casos OK" + (f", {fallas} con falla" if fallas else ""))
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
