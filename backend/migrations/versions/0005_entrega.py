"""Entrega de la factura al cliente: ZIP (AttachedDocument + PDF) por correo.

  * `factura.application_response`: la respuesta de validación de la DIAN (ApplicationResponse) que va dentro del
    AttachedDocument. Con el proveedor simulado, una respuesta SIMULADA y marcada como tal.
  * `outbox.tipo` admite `ENVIAR_CORREO`.
  * `entrega`: el ZIP que se le entregó al cliente (se conserva tal cual: un reenvío manda el MISMO archivo) y el
    estado del envío. No guarda el correo del destinatario: se lee del cliente al enviar (Ley 1581).
  * `entrega_consecutivo`: consecutivo propio por año para los nombres de archivo (la DIAN no los reglamenta;
    convención `z<NIT 10><000><AA><hex 8>.zip`).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
ALTER TABLE factura ADD COLUMN application_response TEXT;

ALTER TABLE outbox DROP CONSTRAINT outbox_tipo_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_tipo_check
    CHECK (tipo IN ('ENVIAR_DIAN', 'CONSULTAR_DIAN', 'ENVIAR_CORREO'));

CREATE TABLE entrega_consecutivo (
    anio       SMALLINT PRIMARY KEY,
    siguiente  BIGINT   NOT NULL,
    CONSTRAINT entrega_consecutivo_anio_check CHECK (anio BETWEEN 2000 AND 2099),
    CONSTRAINT entrega_consecutivo_siguiente_check CHECK (siguiente BETWEEN 1 AND 4294967296)
);

CREATE TABLE entrega (
    id            BIGINT   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    factura_id    BIGINT   NOT NULL UNIQUE REFERENCES factura (id),
    anio          SMALLINT NOT NULL,
    consecutivo   BIGINT   NOT NULL,
    nombre_zip    TEXT     NOT NULL,
    zip           BYTEA    NOT NULL,
    estado        TEXT     NOT NULL DEFAULT 'PENDIENTE',
    intentos      SMALLINT NOT NULL DEFAULT 0 CHECK (intentos >= 0),
    ultimo_error  TEXT,
    creada_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    enviada_en    TIMESTAMPTZ,
    CONSTRAINT entrega_estado_check CHECK (estado IN ('PENDIENTE', 'ENVIADA', 'FALLIDA')),
    CONSTRAINT entrega_consecutivo_rango CHECK (consecutivo BETWEEN 1 AND 4294967295),   -- 8 dígitos hex
    CONSTRAINT entrega_nombre_check CHECK (nombre_zip ~ '^z[0-9]{10}000[0-9]{2}[0-9a-f]{8}\\.zip$'),
    CONSTRAINT entrega_zip_maximo CHECK (octet_length(zip) <= 2097152),                  -- 2 MB (anexo)
    CONSTRAINT entrega_enviada_check CHECK ((estado = 'ENVIADA') = (enviada_en IS NOT NULL)),
    CONSTRAINT entrega_consecutivo_unico UNIQUE (anio, consecutivo)
);
"""

DOWNGRADE = """
DROP TABLE entrega;
DROP TABLE entrega_consecutivo;
DELETE FROM outbox WHERE tipo = 'ENVIAR_CORREO';
ALTER TABLE outbox DROP CONSTRAINT outbox_tipo_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_tipo_check CHECK (tipo IN ('ENVIAR_DIAN', 'CONSULTAR_DIAN'));
ALTER TABLE factura DROP COLUMN application_response;
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros: admite varias sentencias (mismo patrón que la 0003 y la 0004).
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
