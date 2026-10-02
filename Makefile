# Banco de pruebas de la estación. Requiere: uv (o Python 3.12 + venv), Node 22 y Docker.
# Estructura: backend/ (Python), web/ (React), infra/ (docker-compose). Todo se maneja desde aquí.
#
#   make install       entornos de backend (backend/.venv) y web (web/node_modules)
#   make up            PostgreSQL 16 del compose y migraciones (alembic upgrade head)
#   make migrate       solo las migraciones
#   make test          pruebas de backend (incluidas las de integración contra PostgreSQL) y de web
#   make lint          ruff + mypy (backend) y eslint + tsc (web)
#   make format        aplica el formato de ruff
#   make db-reset      BORRA la base de desarrollo y la recrea con las migraciones
#   make simulador     surtidor simulado: un despacho cada 20 s en la base de desarrollo (Ctrl+C para parar)
#   make api           API en http://127.0.0.1:8000 (documentación en /docs); necesita JWT_SECRETO
#   make worker        procesa la cola de envíos con el proveedor SIMULADO (no envía nada a la DIAN)
#   make web           frontend en http://localhost:5173 (usa la API de `make api` bajo /api)
#   make web-build     compilado de la PWA (web/dist)
#   make tipos         regenera web/src/api/esquema.ts desde el OpenAPI de la API
#
# Todo en contenedores (como quedaría en el servidor de la estación; necesita JWT_SECRETO en .env):
#   make stack            db + migraciones + api + worker + web (Caddy, HTTPS local) en https://localhost
#   make stack-simulador  lo mismo y además el surtidor simulado
#   make stack-logs       registros de todos los servicios;  make stack-down: detiene todo (conserva los datos)
#   make stack-usuario U=bombero1 N="Bombero Uno" R=BOMBERO   crea un usuario (pide el PIN)
#   make ca               copia el certificado raíz de Caddy a infra/caddy-root.crt (para instalar en la tablet)

BACK    := backend
WEB     := web
VENV    ?= .venv
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
        simulador worker api web web-build tipos stack stack-simulador stack-logs stack-down stack-usuario ca

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
	cd $(WEB) && npm test

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

worker:
	cd $(BACK) && $(PY) -m estacion.worker

api:
	cd $(BACK) && $(PY) -m estacion.api --recargar

web:
	cd $(WEB) && npm run dev

web-build:
	cd $(WEB) && npm run build

tipos:
	cd $(BACK) && $(PY) -m estacion.api.openapi > ../$(WEB)/openapi.json
	cd $(WEB) && npm run tipos

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
