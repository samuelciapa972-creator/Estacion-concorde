"""Cliente del web service de la DIAN.

  * `factura.dian_track_id`: el ZipKey de un envío asíncrono (set de pruebas de habilitación); con él se consulta el
    resultado (`GetStatusZip`). NULL en envíos síncronos y con el proveedor simulado síncrono. Un reenvío de una
    factura rechazada lo limpia.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE factura ADD COLUMN dian_track_id TEXT
    CONSTRAINT factura_dian_track_id_check CHECK (btrim(dian_track_id) <> '');
"""

DOWNGRADE = """
ALTER TABLE factura DROP COLUMN dian_track_id;
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros (mismo patrón que la 0003 a la 0005).
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
