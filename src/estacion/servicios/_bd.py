"""Utilidades comunes de acceso a la base de datos para los servicios."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

Conexion = psycopg.Connection[Any]
ZONA_ESTACION = ZoneInfo("America/Bogota")


def ahora_local() -> datetime:
    """Hora local de la estación, sin zona (Colombia no tiene horario de verano)."""
    return datetime.now(ZONA_ESTACION).replace(tzinfo=None)


def exigir_autocommit(conn: Conexion) -> None:
    """Con autocommit, `conn.transaction()` es una transacción real (no un savepoint dentro de otra que el
    llamador olvidó cerrar). Así el commit de cada operación es explícito."""
    if not conn.autocommit:
        raise ValueError("la conexión debe estar en modo autocommit: el servicio maneja sus propias transacciones")


def auditar(
    conn: Conexion,
    accion: str,
    entidad: str,
    entidad_id: object,
    *,
    usuario_id: int | None = None,
    detalle: dict[str, Any] | None = None,
) -> None:
    """Agrega una fila a `auditoria`. `detalle` NO debe llevar datos personales (cédula, nombre, correo)."""
    conn.execute(
        "INSERT INTO auditoria (usuario_id, accion, entidad, entidad_id, detalle) VALUES (%s, %s, %s, %s, %s)",
        (usuario_id, accion, entidad, str(entidad_id), Jsonb(detalle or {})),
    )
