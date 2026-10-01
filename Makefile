# Banco de pruebas de la estación. Requiere: uv (o Python 3.12 + venv) y Docker.
#   make install   entorno .venv con Python 3.12 y dependencias de desarrollo
#   make up        PostgreSQL 16 del docker-compose y migraciones (alembic upgrade head)
#   make migrate   solo las migraciones
#   make test      pruebas (incluidas las de integración contra ese PostgreSQL)
#   make lint      ruff (lint + formato) y mypy
#   make format    aplica el formato de ruff
#   make db-reset  BORRA la base de desarrollo y la recrea con las migraciones
#   make simulador surtidor simulado: un despacho cada 20 s en la base de desarrollo (Ctrl+C para parar)
#   make worker    procesa la cola de envíos con el proveedor SIMULADO (no envía nada a la DIAN)

VENV   ?= .venv
PY     := $(VENV)/bin/python
# Las pruebas crean y borran su propio esquema temporal: no tocan las tablas de la base.
ESTACION_TEST_DSN ?= postgresql://estacion:estacion@localhost:5432/estacion
export ESTACION_TEST_DSN
# Base de desarrollo (la de la aplicación). Se puede sobrescribir desde el entorno o el .env.
DATABASE_URL ?= postgresql://estacion:estacion@localhost:5432/estacion
export DATABASE_URL

.PHONY: install up migrate down db-reset test test-rapido lint format simulador worker

install:
	uv venv --python 3.12 $(VENV)
	uv pip install --python $(PY) -e ".[dev]"

up:
	docker compose up -d --wait db
	$(VENV)/bin/alembic upgrade head

migrate:
	$(VENV)/bin/alembic upgrade head

down:
	docker compose down

db-reset:
	docker compose down -v
	$(MAKE) up

test:
	docker compose up -d --wait db
	$(PY) -m pytest

# Sin PostgreSQL: las pruebas de integración se saltan (y lo dicen).
test-rapido:
	ESTACION_TEST_DSN= $(PY) -m pytest

lint:
	$(PY) -m ruff check src tests migrations
	$(PY) -m ruff format --check src tests migrations
	$(PY) -m mypy

format:
	$(PY) -m ruff check --fix src tests migrations
	$(PY) -m ruff format src tests migrations

simulador:
	$(PY) -m estacion.simulador --cada 20s

worker:
	$(PY) -m estacion.worker
