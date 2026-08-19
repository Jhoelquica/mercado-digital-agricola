import os
import re
import psycopg2
from minio import Minio
from unidecode import unidecode
import httpx
import uuid

CARPETA_RAIZ = r"C:\Users\AMD Ryzen\OneDrive\Documentos\Proyectos\imagenes-productos"  # <-- CAMBIA ESTO a tu carpeta real

MINIO_CLIENT = Minio("localhost:9000", access_key="admin", secret_key="admin123", secure=False)
BUCKET = "productos-imagenes"

conn = psycopg2.connect(host="localhost", port=15432, dbname="productos", user="admin", password="admin123")
cur = conn.cursor()

ALIAS = {
    "freza": "fresa",
    "hierva": "hierba",
    "averja": "arveja",
    "huevocordorniz": "huevodecodorniz",
    "huevocampo": "huevodecampo",
}

def normalizar(texto):
    texto = unidecode(texto).lower()
    texto = re.sub(r"[-_\s]", "", texto)
    return ALIAS.get(texto, texto)

# 1. Trae el catálogo real
resp = httpx.get("http://localhost:8002/productos")
productos = resp.json()
indice = {normalizar(p["nombre"]): p for p in productos}

# 2. Recorre las carpetas buscando imágenes
patron = re.compile(r"^(?P<categoria>[a-z]+)_(?P<producto>[a-z0-9\-]+)_(?P<numero>\d+)\.(jpg|jpeg|png|webp)$", re.IGNORECASE)

no_emparejados = []
subidos = 0

for carpeta, _, archivos in os.walk(CARPETA_RAIZ):
    for archivo in archivos:
        m = patron.match(archivo)
        if not m:
            continue

        nombre_producto_archivo = normalizar(m.group("producto"))
        producto = indice.get(nombre_producto_archivo)

        if not producto:
            no_emparejados.append(archivo)
            continue

        # Verifica cuántas imágenes ya tiene (límite 5)
        cur.execute("SELECT COUNT(*) FROM producto_imagenes WHERE producto_id = %s", (producto["id"],))
        total_actual = cur.fetchone()[0]
        if total_actual >= 5:
            print(f"  {archivo}: {producto['nombre']} ya tiene 5 imágenes, se omite")
            continue

        ruta_completa = os.path.join(carpeta, archivo)
        nombre_objeto = f"{uuid.uuid4()}.jpg"
        MINIO_CLIENT.fput_object(BUCKET, nombre_objeto, ruta_completa)
        url = f"http://localhost:9000/{BUCKET}/{nombre_objeto}"

        cur.execute(
            "INSERT INTO producto_imagenes (id, producto_id, url, orden) VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), producto["id"], url, int(m.group("numero")))
        )
        conn.commit()
        subidos += 1
        print(f"  ✔ {archivo} -> {producto['nombre']}")

print(f"\n{subidos} imágenes subidas correctamente")
if no_emparejados:
    print(f"\n{len(no_emparejados)} archivos SIN emparejar (revísalos manualmente):")
    for f in no_emparejados:
        print(f"  - {f}")

cur.close()
conn.close()