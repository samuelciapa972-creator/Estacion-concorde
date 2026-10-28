"""Placa (opcional) de la venta manual.

  * `venta_manual.placa`: la placa o el equipo que digitó el bombero, normalizada como la del surtidor
    (`normalizacion.normalizar_placa`: mayúsculas, sin espacios ni guiones). NULL = sin placa: a un particular no se
    le pide. Sale en el recibo, como la del despacho.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE venta_manual ADD COLUMN placa TEXT
    CONSTRAINT venta_manual_placa_check CHECK (placa ~ '^[A-Z0-9]{1,20}$');
"""

DOWNGRADE = """
ALTER TABLE venta_manual DROP COLUMN placa;
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros (mismo patrón que la 0003 a la 0007).
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
