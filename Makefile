# Banco de pruebas de la estación. Requiere: uv (o Python 3.12 + venv), Node 22 y Docker.
# Estructura: backend/ (Python), Frontend/ (React), infra/ (docker-compose). Todo se maneja desde aquí.
#
#   make install       entornos de backend (backend/.venv) y web (Frontend/node_modules)
#   make up            PostgreSQL 16 del compose y migraciones (alembic upgrade head)
#   make migrate       solo las migraciones
#   make test          pruebas de backend (incluidas las de integración contra PostgreSQL) y de web (vitest + compilado)
#   make lint          ruff + mypy (backend) y eslint (web)
#   make format        aplica el formato de ruff
#   make db-reset      BORRA la base de desarrollo y la recrea con las migraciones
#   make simulador     surtidor simulado: un despacho cada 20 s en la base de desarrollo (Ctrl+C para parar)
#   make nexus         lee las ventas REALES del Wayne desde Nexus (IMED, solo lectura) cada 15 s y las guarda como
#                      despachos para facturarlas en la pista (pruebas en habilitación; el PC en la red de la pista)
#   make nexus-seco    lo mismo, pero solo las muestra: no guarda nada
#   make api           API en http://127.0.0.1:8000 (documentación en /docs); necesita JWT_SECRETO
#   make worker        procesa la cola de envíos con el proveedor SIMULADO (no envía nada a la DIAN)
#   make worker-dian   procesa la cola enviando a la DIAN según DIAN_AMBIENTE (2 habilitación, 1 producción; certificado real en .env)
#   make habilitacion-probar   prueba certificado, firma y conexión con la DIAN (no envía documentos)
#   make verificar-produccion  revisa si todo está listo para producción (DIAN_AMBIENTE=1; no envía documentos)
#   make guardar-clave-tecnica PREFIJO=EDSC ARCHIVO=ruta   escribe DIAN_CLAVE_TECNICA en un archivo NUEVO (0600), sin imprimirla
#   make configurar-produccion PREFIJO=EDSC   activa la numeración de producción que la DIAN tiene asociada
#   make mail          buzón de PRUEBA (Mailpit): recibe los correos de las facturas en http://localhost:8025
#   make correo-prueba DESTINO=x@correo.com   envía un correo de prueba (sin factura) con la configuración del .env
#   make certificado   genera el certificado de PRUEBA para firmar (datos/certificado-prueba.pfx, fuera de git)
#   make web           frontend en http://localhost:5173 (usa la API de `make api` bajo /api)
#   make web-build     compilado del frontend (Frontend/dist)
#   make tipos         regenera Frontend/openapi.json (contrato de la API; lo verifica test_openapi)
#
# Todo en contenedores (como quedaría en el servidor de la estación; necesita JWT_SECRETO en .env):
#   make stack            db + migraciones + api + web (Caddy, HTTPS local) en https://localhost; las facturas
#                         las envía `make worker-dian` (fuera de Docker)
#   make stack-simulador  lo mismo y además el worker SIMULADO y el surtidor simulado (banco de pruebas)
#   make stack-logs       registros de todos los servicios;  make stack-down: detiene todo (conserva los datos)
#   make stack-usuario U=bombero1 N="Bombero Uno" R=BOMBERO   crea un usuario (pide el PIN)
#   make ca               copia el certificado raíz de Caddy a infra/caddy-root.crt (para instalar en la tablet)
#
# PRODUCCIÓN en el servidor de internet (infra/docker-compose.prod.yml; ver docs/despliegue-produccion.md):
#   make prod             construye y levanta db + migraciones + api + worker-dian + web (Let's Encrypt)
#   make prod-ps | prod-logs | prod-down
#   make prod-reiniciar   reinicia api y worker-dian (p. ej. tras cambiar el certificado .pfx: solo se lee al arrancar)
#   make prod-usuario U=admin1 N="Admin Uno" R=ADMIN   crea un usuario (pide el PIN)
#   make prod-estado      facturas de hoy por estado y la cola (lo que hay que mirar cada día en el piloto)
#   make prod-respaldo    respaldo de la base (infra/respaldo.sh; el cron lo corre cada noche)
#   make prod-restaurar-prueba [ARCHIVO=...]   restaura un respaldo en una base temporal y la borra
#   make prod-habilitacion-probar | prod-verificar | prod-configurar PREFIJO=EDSC | prod-correo-prueba DESTINO=...
#   make prod-clave-tecnica PREFIJO=EDSC ARCHIVO=$HOME/clave-edsc.env
#   make emisor-exportar ARCHIVO=datos/emisor.csv   (en desarrollo) los datos de la estación activa del banco de pruebas
#   make prod-emisor-importar ARCHIVO=$HOME/emisor.csv   (en el servidor) los carga en una base nueva

BACK    := backend
WEB     := Frontend
# backend/.venv; si no ejecuta (p. ej. copiado desde Windows), ~/venvs/estacion (`uv venv -p 3.12 ~/venvs/estacion`
# y `VIRTUAL_ENV=~/venvs/estacion uv pip install -e backend`).
VENV    ?= $(if $(shell test -x $(BACK)/.venv/bin/python && echo si),.venv,$(HOME)/venvs/estacion)
# Rutas relativas a backend/: las recetas de Python corren con `cd backend`.
PY      := $(VENV)/bin/python
COMPOSE := docker compose --project-directory . -f infra/docker-compose.yml

# Las pruebas crean y borran su propio esquema temporal: no tocan las tablas de la base.
ESTACION_TEST_DSN ?= postgresql://estacion:estacion@localhost:5432/estacion
export ESTACION_TEST_DSN
# Base de desarrollo (la de la aplicación). Se puede sobrescribir desde el entorno o el .env.
DATABASE_URL ?= postgresql://estacion:estacion@localhost:5432/estacion
export DATABASE_URL

.PHONY: install up migrate down db-reset test test-back test-rapido test-web lint lint-back lint-web format \
        simulador nexus nexus-seco worker worker-dian habilitacion-probar verificar-produccion guardar-clave-tecnica configurar-produccion mail correo-prueba certificado api web web-build tipos stack stack-simulador stack-logs stack-down stack-usuario ca

install:
	cd $(BACK) && uv venv --python 3.12 $(VENV) && uv pip install --python $(PY) -e ".[dev]"
	cd $(WEB) && npm ci

up:
	$(COMPOSE) up -d --wait db
	cd $(BACK) && $(PY) -m alembic upgrade head

migrate:
	cd $(BACK) && $(PY) -m alembic upgrade head

down:
	$(COMPOSE) down

# Solo el volumen de PostgreSQL: la CA de Caddy (caddy_data) se conserva, así la tablet no pierde la confianza.
db-reset:
	$(COMPOSE) rm --stop --force db
	docker volume rm --force estacion-banco_pgdata
	$(MAKE) up

test: test-back test-web

test-back:
	$(COMPOSE) up -d --wait db
	cd $(BACK) && $(PY) -m pytest

# Sin PostgreSQL: las pruebas de integración se saltan (y lo dicen).
test-rapido:
	cd $(BACK) && ESTACION_TEST_DSN= $(PY) -m pytest

test-web:
	# Pruebas de formato y documentos (vitest) y que el frontend compile.
	cd $(WEB) && npm test && npm run build

lint: lint-back lint-web

lint-back:
	cd $(BACK) && $(PY) -m ruff check src tests migrations
	cd $(BACK) && $(PY) -m ruff format --check src tests migrations
	cd $(BACK) && $(PY) -m mypy

lint-web:
	cd $(WEB) && npm run lint

format:
	cd $(BACK) && $(PY) -m ruff check --fix src tests migrations
	cd $(BACK) && $(PY) -m ruff format src tests migrations

simulador:
	cd $(BACK) && $(PY) -m estacion.simulador --cada 20s

# NEXUS_URL en el .env si la Raspberry no está en http://192.168.10.101
nexus:
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.nexus --cada 15s

nexus-seco:
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.nexus --cada 15s --en-seco

# Leen DIAN_* y DIAN_CERTIFICADO_* del .env (si existe). El worker no arranca sin DIAN_AMBIENTE válido.
worker:
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.worker

worker-dian:
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.worker --proveedor dian

habilitacion-probar:
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.habilitacion probar-conexion

# Salida a producción (DIAN_AMBIENTE=1 en el .env): ver docs/salida-a-produccion.md
verificar-produccion:
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.habilitacion verificar-produccion

guardar-clave-tecnica:
	@test -n "$(PREFIJO)" && test -n "$(ARCHIVO)" || { echo "uso: make guardar-clave-tecnica PREFIJO=EDSC ARCHIVO=ruta/nuevo.env"; exit 2; }
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.habilitacion guardar-clave-tecnica --prefijo "$(PREFIJO)" --archivo "$(abspath $(ARCHIVO))"

configurar-produccion:
	@test -n "$(PREFIJO)" || { echo "uso: make configurar-produccion PREFIJO=EDSC"; exit 2; }
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.habilitacion configurar-produccion --prefijo "$(PREFIJO)"

mail:
	$(COMPOSE) up -d mailpit

# Lee ESTACION_SMTP_* del .env (si existe) y manda un correo sin factura a DESTINO.
correo-prueba:
	@test -n "$(DESTINO)" || { echo "uso: make correo-prueba DESTINO=su@correo.com"; exit 2; }
	cd $(BACK) && set -a && { [ ! -f ../.env ] || . ../.env; } && set +a && $(PY) -m estacion.entrega.probar_correo "$(DESTINO)"

certificado:
	cd $(BACK) && $(PY) -m estacion.certificado --salida ../datos/certificado-prueba.pfx

api:
	cd $(BACK) && $(PY) -m estacion.api --recargar

web:
	cd $(WEB) && npm run dev

web-build:
	cd $(WEB) && npm run build

tipos:
	cd $(BACK) && $(PY) -m estacion.api.openapi > ../$(WEB)/openapi.json

stack:
	$(COMPOSE) up -d --build --wait

stack-simulador:
	$(COMPOSE) --profile simulador up -d --build --wait

stack-logs:
	$(COMPOSE) logs -f --tail 100

stack-down:
	$(COMPOSE) --profile simulador down

stack-usuario:
	$(COMPOSE) exec api python -m estacion.usuarios crear --usuario "$(U)" --nombre "$(N)" --rol "$(R)"

ca:
	$(COMPOSE) cp web:/data/caddy/pki/authorities/local/root.crt infra/caddy-root.crt
	@echo "Instale infra/caddy-root.crt en la tablet (ver docs/despliegue.md). No es secreto, pero no va al repositorio."

# ---------------------------------------------------------------- producción (servidor de internet)
PROD     := docker compose --project-directory . -f infra/docker-compose.prod.yml
# Comandos sueltos con la imagen del backend, el .env y el certificado (no arrancan otro worker).
PROD_RUN := $(PROD) run --rm --no-deps worker-dian
PSQL_PROD = $(PROD) exec -T db psql -U estacion -d estacion -P pager=off -c

.PHONY: prod prod-ps prod-logs prod-down prod-reiniciar prod-usuario prod-estado prod-respaldo prod-restaurar-prueba \
        prod-habilitacion-probar prod-verificar prod-configurar prod-correo-prueba prod-clave-tecnica \
        emisor-exportar prod-emisor-importar

prod:
	@test -f .env || { echo "falta el .env (ver docs/despliegue-produccion.md)"; exit 2; }
	$(PROD) up -d --build --wait --remove-orphans

prod-ps:
	$(PROD) ps

prod-logs:
	$(PROD) logs -f --tail 100

prod-down:
	$(PROD) down

# El certificado y el .env se leen al arrancar. `make prod` recrea lo que cambió en el .env, pero no ve un .pfx
# reemplazado en la misma ruta: por eso este reinicio.
prod-reiniciar:
	$(PROD) restart api worker-dian

prod-usuario:
	$(PROD) exec api python -m estacion.usuarios crear --usuario "$(U)" --nombre "$(N)" --rol "$(R)"

prod-estado:
	@$(PSQL_PROD) "SELECT estado, count(*) AS facturas_hoy FROM factura WHERE fecha_emision >= current_date GROUP BY estado ORDER BY estado"
	@$(PSQL_PROD) "SELECT tipo, estado, count(*), min(creado_en) AS desde FROM outbox WHERE estado IN ('PENDIENTE','EN_CURSO','FALLIDO') AND (estado <> 'FALLIDO' OR terminado_en > now() - interval '7 days') GROUP BY tipo, estado ORDER BY estado"

prod-respaldo:
	infra/respaldo.sh

prod-restaurar-prueba:
	infra/restaurar-prueba.sh $(ARCHIVO)

prod-habilitacion-probar:
	$(PROD_RUN) python -m estacion.habilitacion probar-conexion

prod-verificar:
	$(PROD_RUN) python -m estacion.habilitacion verificar-produccion

prod-configurar:
	@test -n "$(PREFIJO)" || { echo "uso: make prod-configurar PREFIJO=EDSC"; exit 2; }
	$(PROD_RUN) python -m estacion.habilitacion configurar-produccion --prefijo "$(PREFIJO)"

prod-correo-prueba:
	@test -n "$(DESTINO)" || { echo "uso: make prod-correo-prueba DESTINO=su@correo.com"; exit 2; }
	$(PROD_RUN) python -m estacion.entrega.probar_correo "$(DESTINO)"

# El contenedor escribe la clave en una carpeta temporal NUEVA y vacía (no se le monta $HOME entero), con el usuario de
# quien corre make (para que quede suyo y 0600); luego se mueve a ARCHIVO. Por eso el certificado debe poderlo leer ese
# grupo (docs/despliegue-produccion.md, "Certificado").
prod-clave-tecnica:
	@test -n "$(PREFIJO)" && test -n "$(ARCHIVO)" || { echo "uso: make prod-clave-tecnica PREFIJO=EDSC ARCHIVO=\$$HOME/clave-edsc.env"; exit 2; }
	@test ! -e "$(abspath $(ARCHIVO))" || { echo "ya existe $(abspath $(ARCHIVO)): use una ruta nueva"; exit 2; }
	@tmp=$$(mktemp -d "$$HOME/.clave-tecnica.XXXXXX") && trap 'rm -rf "$$tmp"' EXIT && \
	$(PROD) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$$tmp:/salida" worker-dian \
	    python -m estacion.habilitacion guardar-clave-tecnica --prefijo "$(PREFIJO)" --archivo /salida/clave.env && \
	mv -n "$$tmp/clave.env" "$(abspath $(ARCHIVO))" && echo "clave técnica en $(abspath $(ARCHIVO)) (cópiela al .env y borre el archivo)"

emisor-exportar:
	@test -n "$(ARCHIVO)" || { echo "uso: make emisor-exportar ARCHIVO=datos/emisor.csv"; exit 2; }
	infra/emisor.sh exportar "$(ARCHIVO)"

prod-emisor-importar:
	@test -n "$(ARCHIVO)" || { echo "uso: make prod-emisor-importar ARCHIVO=$$HOME/emisor.csv"; exit 2; }
	infra/emisor.sh importar "$(ARCHIVO)"
