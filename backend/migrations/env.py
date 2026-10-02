"""Entorno de Alembic.

Las migraciones son SQL explícito (op.execute): el esquema se lee tal cual, sin autogenerar desde modelos.
La conexión sale de:
  1. `config.attributes["connection"]` (las pruebas pasan una conexión a su esquema temporal), o
  2. la variable de entorno DATABASE_URL. Sin ella, se detiene con un mensaje claro.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

config = context.config


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("falta la variable de entorno DATABASE_URL (ver .env.example)")
    # psycopg 3 es el controlador del proyecto
    for prefijo in ("postgresql://", "postgres://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo) :]
    return url


def _migrar(conexion: Connection) -> None:
    context.configure(connection=conexion, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def correr() -> None:
    if context.is_offline_mode():
        raise SystemExit("modo offline no soportado: las migraciones se aplican contra la base")
    conexion = config.attributes.get("connection")
    if conexion is not None:
        _migrar(conexion)
        return
    motor = create_engine(_url())
    with motor.connect() as conexion:
        _migrar(conexion)
        conexion.commit()


correr()
