#!/usr/bin/env bash
# Corre las pruebas automáticas (pytest) de los microservicios, cada suite DENTRO de la imagen Docker de su servicio y
# contra un PostgreSQL desechable (contenedor chakrashop-pg-pruebas). Nunca toca las bases del clúster ni las del Compose:
# algunas pruebas crean y borran datos.
#
# Uso (desde la raíz del repositorio; en Windows, con Git Bash):
#   bash scripts/correr_pruebas.sh                       # los 8 servicios
#   bash scripts/correr_pruebas.sh pedidos pagos         # solo esos
#   bash scripts/correr_pruebas.sh --cobertura           # además mide la cobertura (pytest-cov, solo en el contenedor)
#   bash scripts/correr_pruebas.sh --limpiar             # borra el Postgres y la red de pruebas
#
# Requiere Docker. Los secretos son valores de PRUEBA, no los del despliegue.
set -u
cd "$(dirname "$0")/.."

SERVICIOS_TODOS=(usuarios productores productos pedidos transporte notificaciones pagos certificacion)
RED=chakrashop-pruebas
PG=chakrashop-pg-pruebas
COBERTURA=0
SERVICIOS=()
for arg in "$@"; do
  case "$arg" in
    --cobertura) COBERTURA=1 ;;
    --limpiar) docker rm -f "$PG" >/dev/null 2>&1; docker network rm "$RED" >/dev/null 2>&1; echo "Postgres y red de pruebas borrados."; exit 0 ;;
    *) SERVICIOS+=("$arg") ;;
  esac
done
[ ${#SERVICIOS[@]} -eq 0 ] && SERVICIOS=("${SERVICIOS_TODOS[@]}")

ENVS=(-e DB_USER=admin -e DB_PASSWORD=pruebas -e DB_HOST="$PG" -e JWT_SECRET=secreto-jwt-de-pruebas
  -e PRODUCTORES_A_PRODUCTOS_SECRETO=secreto-prueba-1 -e PEDIDOS_A_PRODUCTOS_SECRETO=secreto-prueba-2
  -e PAGOS_A_PRODUCTOS_SECRETO=secreto-prueba-3 -e QA_LIMPIEZA_SECRETO=secreto-prueba-4
  -e PRODUCTORES_A_CERTIFICACION_SECRETO=secreto-prueba-5 -e TRANSPORTE_A_CERTIFICACION_SECRETO=secreto-prueba-6
  -e TRANSPORTE_A_PAGOS_SECRETO=secreto-prueba-7 -e TRANSPORTE_A_PEDIDOS_SECRETO=secreto-prueba-8
  -e PRODUCTOS_A_CERTIFICACION_SECRETO=secreto-prueba-9)

# Postgres desechable con las 8 bases (se reutiliza entre corridas).
docker network create "$RED" >/dev/null 2>&1
if ! docker ps --format '{{.Names}}' | grep -q "^${PG}$"; then
  docker rm -f "$PG" >/dev/null 2>&1
  docker run -d --name "$PG" --network "$RED" -e POSTGRES_USER=admin -e POSTGRES_PASSWORD=pruebas postgres:16 >/dev/null || exit 1
  for _ in $(seq 1 30); do docker exec "$PG" pg_isready -U admin >/dev/null 2>&1 && break; sleep 1; done
  sleep 2
  for db in "${SERVICIOS_TODOS[@]}"; do docker exec "$PG" psql -U admin -d postgres -qc "CREATE DATABASE $db" >/dev/null 2>&1; done
fi

FALLARON=()
for s in "${SERVICIOS[@]}"; do
  echo "=== $s"
  if ! docker build -q -t "chakrashop-pruebas/$s" "$s" >/dev/null; then
    echo "No se pudo construir la imagen de $s"; FALLARON+=("$s"); continue
  fi
  if [ "$COBERTURA" -eq 1 ]; then
    comando="pip install -q pytest-cov >/dev/null 2>&1; printf '[run]\nomit =\n    test_*.py\n    */site-packages/*\n    venv/*\n' > /tmp/covrc;
             python -m pytest -q -p no:cacheprovider -p no:warnings --cov=. --cov-config=/tmp/covrc --cov-report=term"
    docker run --rm --user root --network "$RED" "${ENVS[@]}" "chakrashop-pruebas/$s" sh -c "$comando" || FALLARON+=("$s")
  else
    docker run --rm --network "$RED" "${ENVS[@]}" "chakrashop-pruebas/$s" python -m pytest -q -p no:cacheprovider -p no:warnings || FALLARON+=("$s")
  fi
done

echo
if [ ${#FALLARON[@]} -eq 0 ]; then
  echo "Todas las suites pasaron (${SERVICIOS[*]})."
else
  echo "Con fallas: ${FALLARON[*]}"; exit 1
fi
