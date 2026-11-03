"""Surtidor Wayne Helix de la isla de gasolina.

  * Los surtidores Speed Solutions se crean solos al importar su archivo; el Wayne todavía no tiene conexión, así que
    nadie lo creaba y el bombero no podía escogerlo en la venta manual. `HELIX-1` es solo el identificador interno
    (cuando exista el controlador propio, sus despachos llegarán con este mismo surtidor).
  * Bajar la migración solo lo quita si no tiene ventas, despachos, turnos ni importaciones: nunca se pierden datos.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
INSERT INTO surtidor (marca, codigo_externo, descripcion)
VALUES ('WAYNE', 'HELIX-1', 'Wayne Helix, gasolina y diésel')
ON CONFLICT (marca, codigo_externo) DO NOTHING;
"""

DOWNGRADE = """
DELETE FROM surtidor s
 WHERE s.marca = 'WAYNE' AND s.codigo_externo = 'HELIX-1'
   AND NOT EXISTS (SELECT 1 FROM venta_manual WHERE surtidor_id = s.id)
   AND NOT EXISTS (SELECT 1 FROM despacho WHERE surtidor_id = s.id)
   AND NOT EXISTS (SELECT 1 FROM turno WHERE surtidor_id = s.id)
   AND NOT EXISTS (SELECT 1 FROM importacion WHERE surtidor_id = s.id);
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros (mismo patrón que la 0003 a la 0008).
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
