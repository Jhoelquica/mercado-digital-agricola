#!/usr/bin/env bash
# Respaldo COMPLETO del clúster local, para volver exactamente a este punto después de algo que llena la plataforma de
# datos (por ejemplo, la validación con usuarios reales). Guarda en backups/<fecha>/ (carpeta ignorada por git):
#   - <base>.dump        las 8 bases PostgreSQL con pg_dump en formato personalizado (binario: no lo altera la consola
#                        de Windows y permite una restauración limpia con pg_restore --clean).
#   - <base>.filas.txt   cuántas filas tiene cada tabla, para comprobar la restauración.
#   - minio.tar.gz       todos los archivos de MinIO (fotos de cosecha y de productos, entregas, constancias, documentos).
#   - minio.objetos.txt  la lista de esos archivos.
# No lee ni imprime contraseñas: cada comando usa las variables que ya tiene su propio pod.
#
# Uso (desde la raíz del repositorio; en Windows, con Git Bash):
#   bash scripts/respaldo_completo.sh
# Para volver a este punto: bash scripts/restaurar_completo.sh backups/<fecha>
set -euo pipefail
cd "$(dirname "$0")/.."

CTX="${KUBE_CONTEXT:-docker-desktop}"
BASES=(usuarios productores productos pedidos transporte notificaciones pagos certificacion)
DESTINO="backups/$(date +%Y-%m-%d_%H%M)"
mkdir -p "$DESTINO"
k() { kubectl --context "$CTX" "$@"; }

CONTEO_FILAS="select table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I', table_name), false, true, '')))[1]::text
from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by table_name;"

echo "Respaldo en $DESTINO"
for base in "${BASES[@]}"; do
  k exec "deploy/db-$base" -- sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner' > "$DESTINO/$base.dump"
  k exec -i "deploy/db-$base" -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA' <<< "$CONTEO_FILAS" > "$DESTINO/$base.filas.txt"
  filas=$(awk -F'|' '{s += $2} END {print s + 0}' "$DESTINO/$base.filas.txt")
  echo "  base $base: $(du -h "$DESTINO/$base.dump" | cut -f1), $(wc -l < "$DESTINO/$base.filas.txt") tablas, $filas filas"
done

# MinIO no trae tar: el pod de Productos (cliente de MinIO + credenciales en sus variables) arma el .tar.gz y lo
# manda por la salida estándar, sin guardar nada en el pod.
k exec -i deploy/productos -- python - > "$DESTINO/minio.tar.gz" <<'PY'
import io, os, sys, tarfile
from minio import Minio
c = Minio(os.environ.get("MINIO_ENDPOINT", "minio:9000"), access_key=os.environ["MINIO_ACCESS_KEY"],
          secret_key=os.environ["MINIO_SECRET_KEY"], secure=False)
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as tar:
    for bucket in sorted(b.name for b in c.list_buckets()):
        for obj in c.list_objects(bucket, recursive=True):
            resp = c.get_object(bucket, obj.object_name)
            try:
                datos = resp.read()
            finally:
                resp.close()
                resp.release_conn()
            info = tarfile.TarInfo(f"{bucket}/{obj.object_name}")
            info.size = len(datos)
            tar.addfile(info, io.BytesIO(datos))
PY
python - "$DESTINO" <<'PY'
import sys, tarfile
carpeta = sys.argv[1]
with tarfile.open(f"{carpeta}/minio.tar.gz") as tar:
    nombres = sorted(m.name for m in tar.getmembers())
open(f"{carpeta}/minio.objetos.txt", "w", encoding="utf-8").write("\n".join(nombres) + "\n")
print(f"  MinIO: {len(nombres)} archivos")
PY
echo "  minio.tar.gz: $(du -h "$DESTINO/minio.tar.gz" | cut -f1)"
echo "Listo. Para volver a este punto: bash scripts/restaurar_completo.sh $DESTINO"
