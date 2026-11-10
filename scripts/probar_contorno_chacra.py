"""Prueba contra el clúster local el contorno poligonal de la chacra (PUT/DELETE /chacras/{id}/contorno).

Entra con la cuenta QA del productor (QA_PRODUCTOR_* de frontend/.env.qa), crea una chacra de prueba "QA-CTN" junto a la
primera chacra real de esa cuenta (o en Ayacucho si no tiene ninguna) y recorre los casos:
  contorno válido (200 y área ≈ 1 ha), lista con el contorno, autointersectado (422), fuera del Perú (422),
  punto de la chacra fuera del contorno (422), chacra ajena (403; su id sale de la base con kubectl), DELETE del
  contorno (200 y GET sin él). Al final borra la chacra de prueba. No toca ninguna chacra real.

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
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # la consola de Windows (cp1252) no imprime las flechas

RAIZ = Path(__file__).resolve().parent.parent
METROS_POR_GRADO_LAT = 111_320.0
MENSAJE_PUNTO_FUERA = "El punto registrado de la chacra queda fuera del contorno dibujado"


def leer_env_qa():
    valores = {}
    for linea in (RAIZ / "frontend" / ".env.qa").read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def llamar(self, metodo, ruta, cuerpo=None):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        peticion = urllib.request.Request(self.base + ruta, data=datos, method=metodo)
        peticion.add_header("Content-Type", "application/json")
        if self.token:
            peticion.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(peticion, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            texto = e.read()
            try:
                return e.code, json.loads(texto)
            except ValueError:
                return e.code, texto.decode("utf-8", "replace")

    def entrar(self, email, password):
        estado, cuerpo = self.llamar("POST", "/usuarios/usuarios/login", {"email": email, "password": password})
        if estado != 200:
            sys.exit(f"No se pudo iniciar sesión con la cuenta QA del productor: {estado} {cuerpo}")
        self.token = cuerpo["access_token"]


def cuadrado(lat, lng, lado_m=100.0):
    dlat = lado_m / 2 / METROS_POR_GRADO_LAT
    dlng = lado_m / 2 / (METROS_POR_GRADO_LAT * math.cos(math.radians(lat)))
    esquinas = [[lng - dlng, lat - dlat], [lng + dlng, lat - dlat], [lng + dlng, lat + dlat], [lng - dlng, lat + dlat]]
    return {"type": "Polygon", "coordinates": [esquinas + [esquinas[0]]]}


def desplazar(lat, lng, metros_este):
    return lat, lng + metros_este / (METROS_POR_GRADO_LAT * math.cos(math.radians(lat)))


def chacra_ajena(contexto, productor_id):
    """El id de una chacra de OTRO productor, leído de la base del clúster (solo lectura)."""
    consulta = f"SELECT id FROM chacras WHERE productor_id <> '{productor_id}' LIMIT 1"
    orden = ["kubectl", "--context", contexto, "exec", "deploy/db-productores", "--", "sh", "-c",
             f'psql -U "$POSTGRES_USER" -d productores -tAc "{consulta}"']
    try:
        salida = subprocess.run(orden, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  (no se pudo consultar la base con kubectl: {e})")
        return None
    return salida.stdout.strip() or None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000/api")
    parser.add_argument("--contexto", default="docker-desktop")
    args = parser.parse_args()

    env = leer_env_qa()
    api = Api(args.base)
    api.entrar(env["QA_PRODUCTOR_EMAIL"], env["QA_PRODUCTOR_PASSWORD"])
    resultados = []

    def caso(nombre, ok, detalle=""):
        resultados.append(ok)
        print(f"  [{'OK' if ok else 'FALLA'}] {nombre}" + (f"  → {detalle}" if detalle else ""))

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

        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": cuadrado(lat, lng), "area_m2": 1})
        ok = estado == 200 and isinstance(cuerpo, dict) and 9_500 <= (cuerpo.get("area_m2") or 0) <= 10_500 \
            and cuerpo.get("contorno", {}).get("type") == "Polygon"
        caso("contorno válido → 200 con el área calculada en el servidor", ok, f"{estado} area_m2={cuerpo.get('area_m2') if isinstance(cuerpo, dict) else cuerpo}")

        estado, lista = api.llamar("GET", "/productores/chacras/me")
        mia = next((c for c in lista if c["id"] == cid), None) if estado == 200 else None
        caso("GET /chacras/me trae el contorno y el área", bool(mia and mia.get("contorno") and mia.get("area_m2")), str(estado))

        d = 0.0005
        lazo = {"type": "Polygon", "coordinates": [[[lng - d, lat - d], [lng + d, lat + d], [lng + d, lat - d], [lng - d, lat + d], [lng - d, lat - d]]]}
        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": lazo})
        caso("autointersectado → 422", estado == 422 and str(cuerpo.get("detail", "")).startswith("El contorno no es válido"), f"{estado} {cuerpo.get('detail') if isinstance(cuerpo, dict) else cuerpo}")

        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": cuadrado(4.71, -74.07)})
        caso("fuera del Perú → 422", estado == 422 and cuerpo.get("detail") == "El contorno debe estar dentro del Perú", f"{estado} {cuerpo.get('detail') if isinstance(cuerpo, dict) else cuerpo}")

        lejos_lat, lejos_lng = desplazar(lat, lng, 550)
        estado, cuerpo = api.llamar("PUT", ruta, {"contorno": cuadrado(lejos_lat, lejos_lng)})
        caso("punto de la chacra fuera del contorno → 422", estado == 422 and cuerpo.get("detail") == MENSAJE_PUNTO_FUERA, f"{estado} {cuerpo.get('detail') if isinstance(cuerpo, dict) else cuerpo}")

        ajena = chacra_ajena(args.contexto, chacra["productor_id"])
        if ajena:
            estado, cuerpo = api.llamar("PUT", f"/productores/chacras/{ajena}/contorno", {"contorno": cuadrado(lat, lng)})
            estado2, _ = api.llamar("DELETE", f"/productores/chacras/{ajena}/contorno")
            caso("chacra ajena → 403 (PUT y DELETE)", estado == 403 and estado2 == 403, f"PUT {estado} / DELETE {estado2}")
        else:
            caso("chacra ajena → 403", False, "no hay otra chacra en la base para probarlo")

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
