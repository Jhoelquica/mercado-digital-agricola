#!/usr/bin/env python
"""
Seed de demostracion de Chakra Shop: 12 agricultores de Ayacucho con sus productos e imagenes
reales, cargados POR EL PROCESO REAL de la plataforma (no insertando filas a mano), de modo que
cada producto quede con su certificado de trazabilidad (lo que se ve al escanear el QR):

  1. registro del usuario productor + perfil de productor + 2 chacras
  2. siembra (registro de produccion "planificado")
  3. cosecha completada -> "pendiente_verificacion"
  4. un Verificador aprueba la cosecha  -> nace el producto en "borrador" y se registran en la
     cadena de Certificacion cosecha_registrada + certificado_productor
  5. el productor completa el borrador: categoria, precio e imagenes (de la carpeta local de
     imagenes-productos) y lo publica -> "en_transito"
  6. el Admin confirma la llegada al almacen -> "disponible" (ya comprable)

Los pasos 4 y 6 necesitan credenciales de un ADMIN (el Verificador demo se crea con una invitacion
del admin). Se piden por consola (o SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD) y nunca se guardan.
Sin ellas, el script hace igual los pasos 1-3 y avisa como continuar: es idempotente, se puede
volver a correr y retoma donde quedo.

Si la consola no deja escribir la contrasena (getpass falla en algunas terminales embebidas de
Windows), en PowerShell se le pasa por variable de entorno, sin que quede en el historial:
    $env:SEED_ADMIN_EMAIL = "admin@chakrashop.pe"
    $env:SEED_ADMIN_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Contrasena del admin' -AsSecureString)).Password
    python seed_datos_demo.py
    Remove-Item Env:SEED_ADMIN_PASSWORD

Uso (desde la carpeta del proyecto, con los servicios corriendo):
    python seed_datos_demo.py                     # completo
    python seed_datos_demo.py --dry-run           # solo muestra el plan y revisa las imagenes
    python seed_datos_demo.py --fases agricultores  # solo pasos 1-3 (no necesita admin)
    python seed_datos_demo.py --limite 2          # solo los 2 primeros agricultores (prueba)

Solo toca cuentas @chakrashop-demo.pe y solo aprueba cosechas de esos agricultores.
Requiere: pip install httpx pillow   (pillow es opcional: reduce las fotos antes de subirlas)
"""
import argparse
import getpass
import io
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx

try:
    from PIL import Image
except ImportError:  # las fotos originales pesan ~2-4 MB; sin Pillow se suben tal cual
    Image = None

HOST = os.getenv("SEED_HOST", "http://localhost")
PUERTOS = {"usuarios": 8006, "productores": 8001, "productos": 8002, "certificacion": 8008}
PASSWORD_DEMO = os.getenv("SEED_PASSWORD_DEMO", "Demo12345!")
DOMINIO = "chakrashop-demo.pe"
EMAIL_VERIFICADOR = f"verificador.demo@{DOMINIO}"
IMAGENES_DIR = Path(os.getenv("SEED_IMAGENES_DIR", Path(__file__).resolve().parent.parent / "imagenes-productos"))
MAX_IMAGENES = 3   # por producto (el limite del backend es 5)
LADO_MAX = 1400    # px, lado mayor tras reducir

# clave: (nombre, categoria, carpeta de imagenes, unidad de cosecha, equivalencia_kg, precio S/, rango de cosecha)
PRODUCTOS = {
    "papa_blanca": ("Papa blanca", "tuberculo", "tuberculo/papa_blanca", "kg", None, 2.6, (500, 1100)),
    "habas": ("Habas", "legumbre", "legumbre/habas", "kg", None, 4.8, (180, 420)),
    "arveja": ("Arveja", "legumbre", "legumbre/averja", "kg", None, 6.0, (150, 380)),
    "quinua_roja": ("Quinua roja", "grano", "grano/quinua_roja", "kg", None, 12.0, (200, 520)),
    "kiwicha": ("Kiwicha", "grano", "grano/kiwicha", "kg", None, 9.0, (150, 360)),
    "trigo": ("Trigo", "grano", "grano/trigo", "kg", None, 5.0, (300, 700)),
    "palta": ("Palta", "fruta", "fruta/palta", "kg", None, 9.0, (250, 600)),
    "chirimoya": ("Chirimoya", "fruta", "fruta/chirimoya", "kg", None, 6.5, (150, 340)),
    "naranja": ("Naranja", "fruta", "fruta/naranja", "kg", None, 3.5, (400, 900)),
    "lechuga": ("Lechuga", "verdura", "verdura/lechuga", "kg", None, 2.4, (120, 300)),
    "espinaca": ("Espinaca", "verdura", "verdura/espinaca", "kg", None, 3.2, (90, 220)),
    "zanahoria": ("Zanahoria", "verdura", "verdura/zanahoria", "kg", None, 2.5, (250, 600)),
    "cebolla": ("Cebolla", "verdura", "verdura/cebolla", "kg", None, 2.8, (300, 700)),
    "maiz_choclo": ("Maiz choclo", "grano", "grano/Maiz_Choclo", "kg", None, 4.0, (300, 650)),
    "maiz_morado": ("Maiz morado", "grano", "grano/maizMorado", "kg", None, 6.5, (200, 480)),
    "oregano": ("Oregano", "hierba", "hierba/oregano", "kg", None, 18.0, (40, 110)),
    "manzana": ("Manzana", "fruta", "fruta/manzana", "kg", None, 4.5, (300, 700)),
    "limon": ("Limon", "fruta", "fruta/limon", "kg", None, 4.0, (250, 600)),
    "fresa": ("Fresa", "fruta", "fruta/freza", "kg", None, 8.0, (100, 240)),
    "cilantro": ("Cilantro", "hierba", "hierba/cilantro", "kg", None, 3.5, (60, 150)),
    "huevo_campo": ("Huevo de campo", "huevo", "huevo/huevo_huevoCampo", "unidad", 0.06, 0.9, (500, 900)),
    "huevo_codorniz": ("Huevo de codorniz", "huevo", "huevo/huevo_huevocodorniz", "unidad", 0.012, 0.35, (900, 1600)),
    "leche": ("Leche fresca", "lacteo", "lacteo/lechefresca", "litro", 1.03, 3.8, (300, 700)),
    "queso": ("Queso fresco", "lacteo", "lacteo/queso", "kg", None, 24.0, (60, 160)),
    "yogurt": ("Yogurt natural", "lacteo", "lacteo/yogurtnatural", "litro", 1.05, 7.0, (120, 300)),
    "mango": ("Mango", "fruta", "fruta/mango", "kg", None, 5.0, (300, 700)),
    "platano": ("Platano", "fruta", "fruta/platano", "kg", None, 2.8, (400, 900)),
    "papaya": ("Papaya", "fruta", "fruta/papaya", "kg", None, 4.0, (200, 480)),
    "pina": ("Pina", "fruta", "fruta/piña", "kg", None, 4.5, (250, 560)),
    "sandia": ("Sandia", "fruta", "fruta/sandia", "kg", None, 3.0, (300, 700)),
}

# Coordenadas aproximadas de cada comunidad (demo): suficiente para el mapa, no son parcelas reales.
AGRICULTORES = [
    dict(usuario="Rosa Huaman Quispe", razon="Asociacion Agraria Sol Naciente", comunidad="Vinchos, Huamanga",
         contacto="951203401", lat=-13.1889, lng=-74.3086, productos=["papa_blanca", "habas", "arveja"]),
    dict(usuario="Ernesto Ccasani Pariona", razon="Cooperativa Quinua Real", comunidad="Quinua, Huamanga",
         contacto="951203402", lat=-13.0517, lng=-74.1367, productos=["quinua_roja", "kiwicha", "trigo"]),
    dict(usuario="Marleni Tenorio Vargas", razon="Fundo Frutal San Jose", comunidad="Huanta",
         contacto="951203403", lat=-12.9394, lng=-74.2472, productos=["palta", "chirimoya", "naranja"]),
    dict(usuario="Wilber Gutierrez Llamocca", razon="Huerta Organica Wari", comunidad="Carmen Alto, Huamanga",
         contacto="951203404", lat=-13.1640, lng=-74.2180, productos=["lechuga", "espinaca", "zanahoria"]),
    dict(usuario="Nelida Palomino Soto", razon="Chacra Los Andes de Acocro", comunidad="Acocro, Huamanga",
         contacto="951203405", lat=-13.3711, lng=-74.1508, productos=["cebolla", "maiz_choclo"]),
    dict(usuario="Genaro Quispe Ayala", razon="Asociacion Mama Pacha", comunidad="Tambillo, Huamanga",
         contacto="951203406", lat=-13.2103, lng=-74.1831, productos=["maiz_morado", "oregano"]),
    dict(usuario="Yolanda Cardenas Roca", razon="Agropecuaria Rio Cachi", comunidad="Pacaycasa, Huamanga",
         contacto="951203407", lat=-13.1039, lng=-74.2308, productos=["manzana", "limon"]),
    dict(usuario="Julio Sulca Berrocal", razon="Productores Unidos de Iribamba", comunidad="Iribamba, Huanta",
         contacto="951203408", lat=-12.9850, lng=-74.2360, productos=["fresa", "cilantro"]),
    dict(usuario="Paulina Ore Mendoza", razon="Granja Avicola Santa Rosa", comunidad="San Juan Bautista, Huamanga",
         contacto="951203409", lat=-13.1800, lng=-74.2200, productos=["huevo_campo", "huevo_codorniz"]),
    dict(usuario="Cirilo Rojas Baldeon", razon="Lacteos Puka Punta", comunidad="Chiara, Huamanga",
         contacto="951203410", lat=-13.2200, lng=-74.3000, productos=["leche", "queso", "yogurt"]),
    dict(usuario="Beatriz Fernandez Lapa", razon="Frutas del Valle de Ayna", comunidad="Ayna, La Mar",
         contacto="951203411", lat=-12.7333, lng=-73.8333, productos=["mango", "platano", "papaya"]),
    dict(usuario="Hugo Salazar Tello", razon="Huertos del VRAEM", comunidad="Santa Rosa, La Mar",
         contacto="951203412", lat=-12.5000, lng=-73.7167, productos=["pina", "sandia"]),
]

HOY = date.today()


def log(mensaje=""):
    print(mensaje, flush=True)


class Api:
    def __init__(self):
        self.cliente = httpx.Client(timeout=60)

    def url(self, servicio, ruta):
        return f"{HOST}:{PUERTOS[servicio]}{ruta}"

    def pedir(self, metodo, servicio, ruta, token=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.cliente.request(metodo, self.url(servicio, ruta), headers=headers, **kwargs)


def detalle(r):
    try:
        return r.json().get("detail", r.text)
    except Exception:
        return r.text


def exigir(r, contexto, aceptar=(200,)):
    if r.status_code not in aceptar:
        raise RuntimeError(f"{contexto}: HTTP {r.status_code} - {detalle(r)}")
    return r


def iso(d):
    return datetime.combine(d, time(0, 0)).isoformat()


def asegurar_sesion(api, nombre, email, rol, codigo_invitacion=None):
    """Registra al usuario si no existe y devuelve su token (login)."""
    cuerpo = {"nombre": nombre, "email": email, "password": PASSWORD_DEMO, "rol": rol}
    if codigo_invitacion:
        cuerpo["codigo_invitacion"] = codigo_invitacion
    r = api.pedir("POST", "usuarios", "/usuarios/registro", json=cuerpo)
    if r.status_code not in (200, 409):  # 409 = ya existia
        raise RuntimeError(f"registro de {email}: HTTP {r.status_code} - {detalle(r)}")
    return login(api, email, PASSWORD_DEMO)


def login(api, email, password):
    r = api.pedir("POST", "usuarios", "/usuarios/login", json={"email": email, "password": password})
    exigir(r, f"login de {email}")
    return r.json()["access_token"]


def preparar_imagenes(carpeta):
    """Hasta MAX_IMAGENES fotos de la carpeta, reducidas a JPEG de LADO_MAX px si hay Pillow."""
    ruta = IMAGENES_DIR / carpeta
    archivos = sorted(p for p in ruta.glob("*") if p.suffix.lower() in (".jpg", ".jpeg"))[:MAX_IMAGENES]
    resultado = []
    for archivo in archivos:
        if Image is not None:
            with Image.open(archivo) as im:
                im = im.convert("RGB")
                im.thumbnail((LADO_MAX, LADO_MAX))
                buffer = io.BytesIO()
                im.save(buffer, "JPEG", quality=82, optimize=True)
                datos = buffer.getvalue()
        else:
            datos = archivo.read_bytes()
        resultado.append((archivo.name, datos))
    return resultado


# ---------------------------------------------------------------- fase 1: agricultores
def fase_agricultores(api, indice, ag):
    """Usuario, perfil, chacras, siembras y cosechas. Devuelve (token, productor_id)."""
    email = f"agricultor{indice:02d}@{DOMINIO}"
    token = asegurar_sesion(api, ag["usuario"], email, "productor")

    r = api.pedir("GET", "productores", "/productores/me", token)
    if r.status_code == 404:
        r = api.pedir("POST", "productores", "/productores", token, json={
            "nombre": ag["razon"], "comunidad": ag["comunidad"], "contacto": ag["contacto"],
            "ubicacion": ag["comunidad"], "latitud": str(ag["lat"]), "longitud": str(ag["lng"]),
        })
        exigir(r, f"perfil de productor de {ag['razon']}")
    else:
        exigir(r, f"perfil de {email}")
    productor_id = r.json()["id"]

    rng = random.Random(indice)
    r = api.pedir("GET", "productores", "/chacras/me", token)
    exigir(r, "listar chacras")
    chacras = {c["codigo"]: c["id"] for c in r.json()}
    for codigo, nombre, delta in (("CH-01", "Parcela alta", 0.004), ("CH-02", "Parcela baja", -0.004)):
        if codigo in chacras:
            continue
        r = api.pedir("POST", "productores", "/chacras", token, json={
            "codigo": codigo, "nombre": nombre,
            "ubicacion_latitud": round(ag["lat"] + delta, 6),
            "ubicacion_longitud": round(ag["lng"] + delta * rng.uniform(0.5, 1.5), 6),
        })
        chacras[codigo] = exigir(r, f"chacra {codigo}").json()["id"]

    r = api.pedir("GET", "productores", "/productores/produccion/me", token)
    exigir(r, "listar siembras")
    existentes = {reg["cultivo"].lower(): reg for reg in r.json()}

    for n, clave in enumerate(ag["productos"]):
        nombre, _cat, _carpeta, unidad, equiv, _precio, (cmin, cmax) = PRODUCTOS[clave]
        if nombre.lower() in existentes:
            log(f"    - {nombre}: ya tiene siembra ({existentes[nombre.lower()]['estado']})")
            continue
        siembra = HOY - timedelta(days=rng.randint(130, 210))
        estimada = siembra + timedelta(days=rng.randint(90, 125))
        real = min(HOY - timedelta(days=rng.randint(2, 15)), estimada + timedelta(days=rng.randint(0, 6)))
        codigo_chacra = "CH-01" if n % 2 == 0 else "CH-02"
        r = api.pedir("POST", "productores", "/productores/produccion", token, json={
            "chacra_id": chacras[codigo_chacra], "cultivo": nombre, "numero_parcelas": rng.randint(1, 3),
            "ubicacion_cosecha": f"{ag['comunidad']} - {codigo_chacra}",
            "costo_semillas": round(rng.uniform(80, 260), 2), "costo_insumos": round(rng.uniform(60, 220), 2),
            "unidad_medida": unidad, "equivalencia_kg": equiv,
            "fecha_siembra": iso(siembra), "fecha_cosecha_estimada": iso(estimada),
        })
        registro_id = exigir(r, f"siembra de {nombre}").json()["id"]
        r = api.pedir("PATCH", "productores", f"/productores/produccion/{registro_id}/completar-cosecha", token, json={
            "cantidad_cosechada": float(rng.randint(cmin, cmax)), "fecha_cosecha_real": iso(real),
            "costo_mano_obra": round(rng.uniform(120, 380), 2), "costo_envio": round(rng.uniform(30, 120), 2),
        })
        exigir(r, f"cosecha de {nombre}")
        log(f"    + {nombre}: siembra + cosecha enviada a verificacion")
    return token, productor_id


# ---------------------------------------------------------------- fase 2: verificador
def asegurar_verificador(api, token_admin):
    """Devuelve el token del Verificador demo (lo crea con una invitacion del admin si hace falta)."""
    r = api.pedir("POST", "usuarios", "/usuarios/login", json={"email": EMAIL_VERIFICADOR, "password": PASSWORD_DEMO})
    if r.status_code == 200:
        token = r.json()["access_token"]
    else:
        r = api.pedir("POST", "usuarios", "/admin/invitaciones", token_admin, json={"rol_destino": "verificador"})
        codigo = exigir(r, "crear invitacion de verificador").json()["codigo"]
        token = asegurar_sesion(api, "Ana Verificadora (demo)", EMAIL_VERIFICADOR, "verificador", codigo)
    r = api.pedir("GET", "certificacion", "/verificadores/me", token)
    if r.status_code == 404:
        r = api.pedir("POST", "certificacion", "/verificadores", token, json={
            "nombre": "Ana Verificadora (demo)", "credencial": "COL-DEMO-001"})
        exigir(r, "perfil de verificador")
    return token


def certificacion_actualizada(api):
    """Certificacion desplegada con el registro automatico del certificado? Ese endpoint interno
    responde 403 sin el secreto de servicio; en una version anterior ni existe (404)."""
    r = api.pedir("POST", "certificacion", "/certificacion/00000000-0000-0000-0000-000000000000/cosecha-aprobada",
                  json={"datos_cosecha": "x", "datos_certificado": "y", "verificador_usuario_id": "z"})
    return r.status_code != 404


def aprobar_cosechas(api, token_verificador, ids_productores):
    r = api.pedir("GET", "productores", "/productores/produccion/pendientes-verificacion", token_verificador)
    pendientes = [p for p in exigir(r, "listar cosechas pendientes").json() if p["productor_id"] in ids_productores]
    for p in pendientes:
        r = api.pedir("POST", "productores", f"/productores/produccion/{p['id']}/aprobar", token_verificador)
        res = exigir(r, f"aprobar {p['cultivo']}").json()
        log(f"    + aprobada: {p['cultivo']} ({p['productor_nombre']})")
        # producto_id viene vacio solo en el reintento idempotente (409): ahi no se puede afirmar nada.
        if res.get("producto_id") and res.get("certificacion_registrada") is not True:
            raise RuntimeError(
                f"'{p['cultivo']}' quedo aprobada pero SIN certificado: el servicio productores desplegado no lo "
                "registra (falta redesplegarlo y agregar PRODUCTORES_A_CERTIFICACION_SECRETO al Secret). "
                "Se detiene aqui para no dejar mas productos sin certificado.")
    if not pendientes:
        log("    (no habia cosechas pendientes de estos agricultores)")


# ---------------------------------------------------------------- fase 3: borradores -> publicados
def fase_publicar(api, token, ag):
    r = api.pedir("GET", "productos", "/productos/mios", token)
    mios = {p["nombre"].lower(): p for p in exigir(r, "listar mis productos").json()}
    for clave in ag["productos"]:
        nombre, categoria, carpeta, _u, _e, precio, _rango = PRODUCTOS[clave]
        p = mios.get(nombre.lower())
        if p is None:
            log(f"    - {nombre}: todavia no tiene producto (falta aprobar la cosecha)")
            continue
        if p["estado"] != "borrador":
            log(f"    - {nombre}: ya publicado ({p['estado']})")
            continue
        r = api.pedir("PATCH", "productos", f"/productos/{p['id']}", token, json={"categoria": categoria, "precio": precio})
        exigir(r, f"categoria/precio de {nombre}")
        detalle_producto = api.pedir("GET", "productos", f"/productos/{p['id']}").json()
        if not detalle_producto.get("imagenes"):
            for orden, (archivo, datos) in enumerate(preparar_imagenes(carpeta), start=1):
                r = api.pedir("POST", "productos", f"/productos/{p['id']}/imagenes/subir", token,
                              params={"orden": orden}, files={"archivo": (archivo, datos, "image/jpeg")})
                exigir(r, f"imagen {archivo}")
        r = api.pedir("PATCH", "productos", f"/productos/{p['id']}/publicar", token)
        exigir(r, f"publicar {nombre}")
        log(f"    + {nombre}: S/ {precio:.2f}, imagenes subidas y publicado")


# ---------------------------------------------------------------- fase 4: llegada al almacen
def confirmar_llegadas(api, token_admin, ids_productores):
    catalogo = api.pedir("GET", "productos", "/productos").json()
    en_transito = [p for p in catalogo if p.get("estado") == "en_transito" and p.get("productor_id") in ids_productores]
    for p in en_transito:
        r = api.pedir("PATCH", "productos", f"/productos/{p['id']}/confirmar-llegada-almacen", token_admin)
        exigir(r, f"confirmar llegada de {p['nombre']}")
        log(f"    + {p['nombre']}: llego al almacen -> disponible")
    if not en_transito:
        log("    (no habia productos en transito de estos agricultores)")


# ---------------------------------------------------------------- verificacion final
def verificar_certificados(api, ids_productores):
    catalogo = api.pedir("GET", "productos", "/productos").json()
    propios = [p for p in catalogo if p.get("productor_id") in ids_productores]
    log(f"\n== Verificacion: {len(propios)} producto(s) de los agricultores demo en el catalogo")
    sin_cadena = 0
    for p in propios:
        v = api.pedir("GET", "certificacion", f"/certificacion/{p['id']}/verificar").json()
        estado = "OK" if v.get("valido") and v.get("total_bloques", 0) > 0 else "SIN CERTIFICADO"
        if estado != "OK":
            sin_cadena += 1
        log(f"    [{estado}] {p['nombre']} ({p.get('estado')}) - {v.get('total_bloques', 0)} bloque(s): {v.get('mensaje')}")
    if sin_cadena:
        log(f"\n! {sin_cadena} producto(s) sin cadena de certificacion. Los servicios productores y")
        log("  certificacion desplegados deben ser la version con registro automatico del certificado.")
    return propios


# ---------------------------------------------------------------- dry run
def plan(limite):
    log("== PLAN (dry-run, no se envia nada)")
    log(f"   Servicios: {HOST} puertos {PUERTOS}")
    log(f"   Imagenes:  {IMAGENES_DIR}  (Pillow: {'si' if Image else 'NO - se subiran las fotos originales'})")
    total = 0
    problemas = 0
    for i, ag in enumerate(AGRICULTORES[:limite], start=1):
        log(f"\n{i:02d}. {ag['razon']} - {ag['comunidad']}  <agricultor{i:02d}@{DOMINIO}>")
        for clave in ag["productos"]:
            nombre, cat, carpeta, unidad, _e, precio, _r = PRODUCTOS[clave]
            imgs = preparar_imagenes(carpeta)
            kb = sum(len(d) for _n, d in imgs) // 1024
            marca = "ok" if imgs else "SIN IMAGENES"
            if not imgs:
                problemas += 1
            log(f"      {nombre:<18} {cat:<9} S/ {precio:>5.2f}/{unidad:<6} {len(imgs)} imagen(es), {kb} KB  [{marca}]")
            total += 1
    log(f"\n   Total: {min(limite, len(AGRICULTORES))} agricultores, {total} productos.")
    if problemas:
        log(f"   ! {problemas} producto(s) sin imagenes: revisa SEED_IMAGENES_DIR.")
    return problemas == 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Seed de demostracion: agricultores con productos certificados.")
    ap.add_argument("--dry-run", action="store_true", help="solo muestra el plan y revisa las imagenes")
    ap.add_argument("--fases", choices=["todo", "agricultores"], default="todo")
    ap.add_argument("--limite", type=int, default=len(AGRICULTORES), help="cantidad de agricultores a procesar")
    args = ap.parse_args()

    if args.dry_run:
        sys.exit(0 if plan(args.limite) else 1)
    if not IMAGENES_DIR.exists():
        sys.exit(f"No existe la carpeta de imagenes: {IMAGENES_DIR} (define SEED_IMAGENES_DIR)")

    api = Api()
    try:
        api.pedir("GET", "usuarios", "/salud").raise_for_status()
    except Exception as e:
        sys.exit(f"No se pudo conectar con los servicios en {HOST}: {e}")

    agricultores = list(enumerate(AGRICULTORES, start=1))[: args.limite]
    sesiones = {}
    fallos = 0
    log("== Fase 1: agricultores, chacras, siembras y cosechas")
    for i, ag in agricultores:
        log(f"\n{i:02d}. {ag['razon']} ({ag['comunidad']})")
        try:
            sesiones[i] = fase_agricultores(api, i, ag)
        except Exception as e:
            fallos += 1
            log(f"    ! FALLO: {e}")
    ids_productores = {pid for _t, pid in sesiones.values()}

    if args.fases == "agricultores" or not sesiones:
        log("\nListo (fase 1). Para continuar con verificacion y publicacion: python seed_datos_demo.py")
        sys.exit(1 if fallos else 0)

    email_admin = os.getenv("SEED_ADMIN_EMAIL") or (input("\nEmail de un ADMIN (Enter para omitir verificacion/publicacion): ").strip() if sys.stdin.isatty() else "")
    if not email_admin:
        log("\nSin credenciales de admin: se omiten verificacion, publicacion y llegada al almacen.")
        log("Vuelve a correr el script con SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD (o interactivo) para completarlo.")
        sys.exit(1 if fallos else 0)
    token_admin = None
    for intento in range(1, 4):
        password_admin = os.getenv("SEED_ADMIN_PASSWORD") or getpass.getpass("Contrasena del admin (no se guarda): ")
        # getpass no muestra nada al escribir: el largo permite notar un pegado que no llego (0 caracteres).
        log(f"   ({len(password_admin)} caracteres recibidos)")
        try:
            token_admin = login(api, email_admin, password_admin)
            break
        except Exception as e:
            log(f"   ! {e}")
            if os.getenv("SEED_ADMIN_PASSWORD") or not sys.stdin.isatty() or intento == 3:
                break
            log("   Reintenta (en la consola de Windows, Ctrl+V puede no pegar: usa clic derecho).")
    if token_admin is None:
        sys.exit("No se pudo iniciar sesion como admin.")
    if not certificacion_actualizada(api):
        sys.exit("\nSe detiene ANTES de aprobar: el servicio de certificacion desplegado es anterior al registro\n"
                 "automatico del certificado, y los productos quedarian sin certificado (sin 'ver certificado').\n"
                 "Redespliega productores, certificacion (y pedidos/transporte), agrega\n"
                 "PRODUCTORES_A_CERTIFICACION_SECRETO al Secret y vuelve a correr el script (retoma donde quedo).")
    try:
        token_verificador = asegurar_verificador(api, token_admin)
    except Exception as e:
        sys.exit(f"No se pudo preparar el verificador: {e}")

    log("\n== Fase 2: el Verificador aprueba las cosechas (nace el producto y su certificado)")
    try:
        aprobar_cosechas(api, token_verificador, ids_productores)
    except Exception as e:
        sys.exit(f"\n! {e}")

    log("\n== Fase 3: cada agricultor completa el borrador (categoria, precio, imagenes) y publica")
    for i, ag in agricultores:
        if i not in sesiones:
            continue
        log(f"\n{i:02d}. {ag['razon']}")
        try:
            token = login(api, f"agricultor{i:02d}@{DOMINIO}", PASSWORD_DEMO)  # token fresco
            fase_publicar(api, token, ag)
        except Exception as e:
            fallos += 1
            log(f"    ! FALLO: {e}")

    log("\n== Fase 4: el Admin confirma la llegada al almacen")
    confirmar_llegadas(api, token_admin, ids_productores)

    verificar_certificados(api, ids_productores)
    log("\nCuentas demo (todas con la misma contrasena): " + PASSWORD_DEMO)
    for i, ag in agricultores:
        log(f"   agricultor{i:02d}@{DOMINIO}  - {ag['razon']}")
    log(f"   {EMAIL_VERIFICADOR}  - Verificador demo")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
