#!/usr/bin/env bash
# Datos del emisor (tabla `estacion`: NIT, razón social, dirección, responsabilidades) de una base a otra. Una base
# nueva no los tiene y sin ellos no se emite. Se copian los mismos que la DIAN ya aceptó en habilitación, en vez de
# volver a digitarlos (docs/despliegue-produccion.md, "Primer arranque").
#
#   infra/emisor.sh exportar ARCHIVO   en el equipo de desarrollo: la estación ACTIVA del banco de pruebas a un CSV
#   infra/emisor.sh importar ARCHIVO   en el servidor: la carga en producción; se niega si ya hay una estación activa
#
# El CSV lleva datos de la empresa (no de personas); igual va fuera del repositorio (datos/ o $HOME).
set -euo pipefail

cd "$(dirname "$0")/.."
accion=${1:-}
archivo=${2:-}
[[ $accion =~ ^(exportar|importar)$ && -n $archivo ]] || { echo "uso: infra/emisor.sh exportar|importar ARCHIVO" >&2; exit 2; }

if [[ $accion == exportar ]]; then
    COMPOSE=(docker compose --project-directory . -f infra/docker-compose.yml)
else
    COMPOSE=(docker compose --project-directory . -f infra/docker-compose.prod.yml)
fi
psql() { "${COMPOSE[@]}" exec -T db psql -U estacion -d estacion -v ON_ERROR_STOP=1 -qAt "$@"; }

# Todas las columnas menos `id` (identidad) y `creada_en` (la pone la base), en el orden de la tabla.
columnas=$(psql -c "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'estacion' AND column_name NOT IN ('id', 'creada_en')")

if [[ $accion == exportar ]]; then
    [[ ! -e $archivo ]] || { echo "ya existe $archivo: no se sobrescribe" >&2; exit 1; }
    n=$(psql -c "SELECT count(*) FROM estacion WHERE activa")
    [[ $n == 1 ]] || { echo "se esperaba UNA estación activa en el banco de pruebas y hay $n" >&2; exit 1; }
    psql -c "COPY (SELECT $columnas FROM estacion WHERE activa) TO STDOUT WITH (FORMAT csv, HEADER)" > "$archivo"
    echo "exportada a $archivo: $(psql -c "SELECT razon_social || ' (NIT ' || nit || '-' || dv || ')' FROM estacion WHERE activa")"
    exit 0
fi

[[ -f $archivo ]] || { echo "no existe $archivo" >&2; exit 1; }
cabecera=$(head -1 "$archivo")
[[ $cabecera == "$columnas" ]] || {
    echo "las columnas del archivo no coinciden con las de esta base (¿versiones distintas?)" >&2
    echo "  archivo: $cabecera" >&2
    echo "  base:    $columnas" >&2
    exit 1
}
n=$(psql -c "SELECT count(*) FROM estacion WHERE activa")
[[ $n == 0 ]] || { echo "esta base ya tiene una estación activa: no se importa otra" >&2; exit 1; }
psql -c "COPY estacion ($columnas) FROM STDIN WITH (FORMAT csv, HEADER)" < "$archivo"
echo "importada: $(psql -c "SELECT razon_social || ' (NIT ' || nit || '-' || dv || ')' FROM estacion WHERE activa")"
