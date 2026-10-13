#!/usr/bin/env bash
# Devuelve el clúster local al punto de un respaldo hecho con scripts/respaldo_completo.sh: las 8 bases y los archivos de
# MinIO quedan EXACTAMENTE como estaban. Todo lo creado después (cuentas, pedidos, fotos…) desaparece.
#
# Uso (desde la raíz del repositorio; en Windows, con Git Bash):
#   bash scripts/restaurar_completo.sh backups/<fecha> --simular   # solo compara con el estado actual, no cambia nada
#   bash scripts/restaurar_completo.sh backups/<fecha>             # restaura (pide escribir "restaurar")
#
# Antes de tocar nada guarda OTRO respaldo del estado actual (en backups/), por si después se necesita lo que había.
# Pasos:
#   1. Respaldo de seguridad del estado actual.
#   2. MinIO: borra los archivos que no estaban en el respaldo y vuelve a subir los que faltan.
#   3. Detiene los 8 servicios (0 réplicas), para que nadie escriba durante la restauración.
#   4. Restaura cada base con pg_restore --clean, todo o nada por base.
#   5. Levanta los servicios con sus réplicas de antes.
#   6. Vacía la caché de Redis (catálogo y detalle de productos).
#   7. Compara las filas de cada tabla con el inventario del respaldo.
set -euo pipefail
cd "$(dirname "$0")/.."

CTX="${KUBE_CONTEXT:-docker-desktop}"
BASES=(usuarios productores productos pedidos transporte notificaciones pagos certificacion)
ORIGEN="${1:-}"
MODO="${2:-}"
k() { kubectl --context "$CTX" "$@"; }

if [ -z "$ORIGEN" ] || [ ! -f "$ORIGEN/minio.tar.gz" ]; then
  echo "Uso: bash scripts/restaurar_completo.sh backups/<fecha> [--simular]   (la carpeta debe tener los .dump y minio.tar.gz)"
  exit 1
fi
for base in "${BASES[@]}"; do
  [ -s "$ORIGEN/$base.dump" ] || { echo "Falta $ORIGEN/$base.dump"; exit 1; }
done

CONTEO_FILAS="select table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I', table_name), false, true, '')))[1]::text
from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by table_name;"

comparar_filas() {  # imprime las tablas cuyo conteo actual difiere del respaldo; devuelve 1 si hay diferencias
  local hay=0
  for base in "${BASES[@]}"; do
    actual=$(k exec -i "deploy/db-$base" -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA' <<< "$CONTEO_FILAS")
    diferencias=$(diff <(sort "$ORIGEN/$base.filas.txt") <(echo "$actual" | sort) | grep -E '^[<>]' || true)
    if [ -n "$diferencias" ]; then
      hay=1
      echo "  $base (respaldo < | > ahora):"
      echo "$diferencias" | sed 's/^/      /'
    fi
  done
  return $hay
}

# MinIO: el pod de Productos lee el .tar.gz por la entrada estándar y lo compara con lo que hay (simular | aplicar).
minio_python='
import io, os, sys, tarfile
from minio import Minio
modo = sys.argv[1]
c = Minio(os.environ.get("MINIO_ENDPOINT", "minio:9000"), access_key=os.environ["MINIO_ACCESS_KEY"],
          secret_key=os.environ["MINIO_SECRET_KEY"], secure=False)
respaldo = {}
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as tar:
    for m in tar:
        if m.isfile():
            respaldo[m.name] = tar.extractfile(m).read()
actuales = set()
for bucket in (b.name for b in c.list_buckets()):
    for obj in c.list_objects(bucket, recursive=True):
        actuales.add(f"{bucket}/{obj.object_name}")
sobran = sorted(actuales - set(respaldo))
faltan = sorted(set(respaldo) - actuales)
print(f"  MinIO: {len(respaldo)} archivos en el respaldo, {len(actuales)} ahora: {len(sobran)} nuevos para borrar, {len(faltan)} para volver a subir")
if modo == "aplicar":
    for nombre in sobran:
        bucket, clave = nombre.split("/", 1)
        c.remove_object(bucket, clave)
    for nombre in faltan:
        bucket, clave = nombre.split("/", 1)
        if not c.bucket_exists(bucket):
            c.make_bucket(bucket)
        datos = respaldo[nombre]
        c.put_object(bucket, clave, io.BytesIO(datos), len(datos))
    print(f"  MinIO restaurado: {len(sobran)} borrados, {len(faltan)} subidos")
'
correr_minio() { k exec -i deploy/productos -- python -c "$minio_python" "$1" < "$ORIGEN/minio.tar.gz"; }

if [ "$MODO" = "--simular" ]; then
  echo "SIMULACIÓN contra $ORIGEN (no se cambia nada)"
  echo "Bases:"
  if comparar_filas; then echo "  las 8 bases tienen las mismas filas que el respaldo"; fi
  correr_minio simular
  exit 0
fi

echo "Esto devuelve las 8 bases y MinIO al punto de $ORIGEN."
echo "Todo lo creado después (cuentas, pedidos, siembras, fotos…) se borra. Antes se guarda un respaldo del estado actual."
read -r -p 'Escribe "restaurar" para continuar: ' confirmacion
[ "$confirmacion" = "restaurar" ] || { echo "Cancelado: no se cambió nada."; exit 0; }

echo "1/7 Respaldo de seguridad del estado actual…"
bash scripts/respaldo_completo.sh | sed 's/^/    /'

echo "2/7 MinIO…"
correr_minio aplicar

echo "3/7 Deteniendo los 8 servicios…"
declare -A REPLICAS
for base in "${BASES[@]}"; do
  REPLICAS[$base]=$(k get deploy "$base" -o jsonpath='{.spec.replicas}')
  k scale "deployment/$base" --replicas=0 >/dev/null
done
for base in "${BASES[@]}"; do
  k wait --for=delete pod -l "app=$base" --timeout=120s >/dev/null 2>&1 || true
done

echo "4/7 Restaurando las bases…"
fallas=()
for base in "${BASES[@]}"; do
  if k exec -i "deploy/db-$base" -- sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction' < "$ORIGEN/$base.dump"; then
    echo "    $base: OK"
  else
    echo "    $base: FALLÓ (quedó como estaba: la restauración es todo o nada)"; fallas+=("$base")
  fi
done

echo "5/7 Levantando los servicios…"
for base in "${BASES[@]}"; do
  k scale "deployment/$base" --replicas="${REPLICAS[$base]}" >/dev/null
done
for base in "${BASES[@]}"; do
  k rollout status "deployment/$base" --timeout=240s >/dev/null
done

echo "6/7 Vaciando la caché de Redis…"
k exec deploy/redis -- redis-cli FLUSHALL >/dev/null

echo "7/7 Comprobando las filas contra el inventario del respaldo…"
if comparar_filas; then echo "    las 8 bases tienen exactamente las filas del respaldo"; fi

if [ ${#fallas[@]} -gt 0 ]; then echo "Con fallas: ${fallas[*]}"; exit 1; fi
echo "Restauración terminada."
