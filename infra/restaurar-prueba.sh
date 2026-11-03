#!/usr/bin/env bash
# Prueba de restauración: `make prod-restaurar-prueba [ARCHIVO=...]`. Restaura un respaldo (por defecto el más
# reciente de RESPALDO_DIR) en una base TEMPORAL dentro del mismo PostgreSQL, cuenta lo principal y la borra.
# NO toca la base `estacion`. Un respaldo que nunca se ha restaurado no es un respaldo: correr esto cada mes.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -z ${RESPALDO_DIR:-} && -f .env ]]; then
    RESPALDO_DIR=$(grep -E '^RESPALDO_DIR=' .env | tail -1 | cut -d= -f2- || true)
fi
DIR=${RESPALDO_DIR:-/srv/estacion/respaldos}
archivo=${1:-$(ls -1t "$DIR"/estacion_*.dump 2>/dev/null | head -1 || true)}
[[ -n $archivo && -f $archivo ]] || { echo "no hay respaldo para probar (carpeta $DIR)" >&2; exit 1; }

COMPOSE=(docker compose --project-directory . -f infra/docker-compose.prod.yml)
psql() { "${COMPOSE[@]}" exec -T db psql -U estacion -v ON_ERROR_STOP=1 -qAt "$@"; }
PRUEBA=restauracion_prueba

trap 'psql -d estacion -c "DROP DATABASE IF EXISTS $PRUEBA" >/dev/null 2>&1 || true' EXIT
psql -d estacion -c "DROP DATABASE IF EXISTS $PRUEBA"
psql -d estacion -c "CREATE DATABASE $PRUEBA"
"${COMPOSE[@]}" exec -T db pg_restore -U estacion -d "$PRUEBA" --no-owner --exit-on-error < "$archivo"

echo "restaurado: $archivo"
psql -d "$PRUEBA" -c "SELECT 'migración ' || version_num FROM alembic_version"
psql -d "$PRUEBA" -c "SELECT 'facturas ' || count(*) || ', última ' || coalesce(max(fecha_emision)::text, '-') FROM factura"
psql -d "$PRUEBA" -c "SELECT 'clientes ' || count(*) FROM cliente"
psql -d "$PRUEBA" -c "SELECT 'usuarios ' || count(*) FROM usuario"
echo "restauración OK (la base temporal $PRUEBA se borra al salir)"
