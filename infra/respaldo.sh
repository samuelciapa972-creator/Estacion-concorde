#!/usr/bin/env bash
# Respaldo de la base de PRODUCCIÓN: `make prod-respaldo` o, cada noche, desde el cron del servidor
# (docs/despliegue-produccion.md). Lo corre el usuario del despliegue desde la raíz del repositorio.
#
#   RESPALDO_DIR      carpeta de los respaldos (por defecto /srv/estacion/respaldos). Fuera del repositorio.
#   RESPALDO_DIAS     días que se conservan en el servidor (por defecto 30)
#   RESPALDO_REMOTO   destino de rclone para la copia FUERA del servidor (p. ej. "b2cifrado:estacion"). Vacío = no
#                     se copia, y el script lo advierte: un respaldo que solo vive en el servidor se pierde con él.
#
# El respaldo contiene datos personales (Ley 1581): la carpeta es 0700 y los archivos 0600; el remoto debe ser cifrado.
# Sale con código distinto de 0 si algo falla, para que el cron lo reporte.
set -euo pipefail

cd "$(dirname "$0")/.."
# Lee RESPALDO_* del .env si están ahí (sin exportar el resto de secretos al entorno del script).
if [[ -f .env ]]; then
    while IFS='=' read -r clave valor; do
        [[ -z ${!clave:-} ]] && export "$clave=$valor"
    done < <(grep -E '^RESPALDO_[A-Z_]+=' .env || true)
fi

DIR=${RESPALDO_DIR:-/srv/estacion/respaldos}
DIAS=${RESPALDO_DIAS:-30}
COMPOSE=(docker compose --project-directory . -f infra/docker-compose.prod.yml)

umask 077
mkdir -p "$DIR"
archivo="$DIR/estacion_$(date +%F_%H%M).dump"
parcial="$archivo.parcial"

# -Fc: formato propio de pg_dump (comprimido; pg_restore puede restaurar tablas sueltas).
"${COMPOSE[@]}" exec -T db pg_dump -U estacion -d estacion -Fc > "$parcial"
# Un volcado vacío o truncado no debe pasar por bueno: pg_restore --list lee el índice completo del archivo.
"${COMPOSE[@]}" exec -T db pg_restore --list < "$parcial" > /dev/null
mv "$parcial" "$archivo"
echo "respaldo: $archivo ($(du -h "$archivo" | cut -f1))"

find "$DIR" -maxdepth 1 -name 'estacion_*.dump' -mtime +"$DIAS" -print -delete
find "$DIR" -maxdepth 1 -name 'estacion_*.dump.parcial' -mmin +60 -delete

if [[ -n ${RESPALDO_REMOTO:-} ]]; then
    rclone copy "$archivo" "$RESPALDO_REMOTO"
    echo "copiado a $RESPALDO_REMOTO"
else
    echo "ADVERTENCIA: RESPALDO_REMOTO vacío: el respaldo solo está en este servidor" >&2
fi
