"""Fixtures compartidas.

`bd`: PostgreSQL real (el del docker-compose), con un esquema temporal propio por prueba al que se le
aplican las migraciones de Alembic (`upgrade head`). Se borra al terminar: no toca las tablas de la base. Sin ESTACION_TEST_DSN la
prueba se salta y lo dice (`make test` la define; `make test-rapido` no).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def migrar(dsn: str, destino: str = "head", *, bajar: bool = False) -> None:
    """Corre Alembic contra `dsn` (que ya trae el search_path del esquema temporal)."""
    import psycopg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    config = Config(str(RAIZ / "alembic.ini"))
    motor = create_engine("postgresql+psycopg://", creator=lambda: psycopg.connect(dsn), poolclass=NullPool)
    with motor.begin() as conexion:
        config.attributes["connection"] = conexion
        (command.downgrade if bajar else command.upgrade)(config, destino)
    motor.dispose()


@dataclass(frozen=True)
class BaseDePrueba:
    dsn: str  # apunta al esquema temporal (search_path)
    esquema: str

    def consultar(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            return conn.execute(sql, params).fetchall()

    def ejecutar(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql, params)

    def valor(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        filas = self.consultar(sql, params)
        return filas[0][0] if filas else None


@pytest.fixture
def esquema_vacio() -> Iterator[BaseDePrueba]:
    """Esquema temporal SIN migraciones (para probar `upgrade`/`downgrade` desde cero)."""
    base = os.environ.get("ESTACION_TEST_DSN", "").strip()
    if not base:
        pytest.skip("defina ESTACION_TEST_DSN (o use `make test`) para probar contra PostgreSQL")
    import psycopg
    from psycopg.conninfo import make_conninfo

    esquema = f"prueba_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {esquema}")
        try:
            yield BaseDePrueba(dsn=make_conninfo(base, options=f"-c search_path={esquema}"), esquema=esquema)
        finally:
            admin.execute(f"DROP SCHEMA {esquema} CASCADE")


@pytest.fixture
def bd(esquema_vacio: BaseDePrueba) -> BaseDePrueba:
    migrar(esquema_vacio.dsn)
    return esquema_vacio
