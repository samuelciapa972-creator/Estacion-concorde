"""Producción DIAN y notas crédito de anulación.

  * `factura.ambiente` ('1' producción | '2' habilitación): el ambiente con el que se emitió el documento. Las filas
    existentes eran todas de habilitación ('2'). Después de rellenarlas se quita el DEFAULT: de aquí en adelante el
    código siempre dice el ambiente (no hay uno "por omisión" que se cuele en producción).
  * `numeracion.ambiente`: el ambiente al que pertenece la resolución. AQUÍ el DEFAULT '2' se conserva: una
    numeración insertada sin ambiente es de habilitación y, con la estación en producción, falla cerrado.
  * Notas crédito como filas de `factura` (`tipo = 'NOTA_CREDITO'`): `referencia_id` es la factura que anulan (una sola
    nota por factura), `motivo` es obligatorio y no usan la numeración de facturas (`numeracion_id` NULL; su serie
    sale de `numeracion_nota`). `factura_origen_check` pasa a distinguir los dos tipos.
  * `numeracion_nota`: una serie por estación y ambiente (NCE en producción, NCP en habilitación), desde 1, sin huecos.
  * `factura_anulada(f)`: la factura tiene una nota crédito FACTURADO. `v_ventas_origen` (y con ella las vistas de
    ventas diarias y mensuales) deja de contar las ventas manuales cuya factura fue anulada.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
-- Ambiente: rellenar con '2' y quitar el DEFAULT en factura (en numeracion se queda).
ALTER TABLE factura ADD COLUMN ambiente CHAR(1) NOT NULL DEFAULT '2'
    CONSTRAINT factura_ambiente_check CHECK (ambiente IN ('1', '2'));
ALTER TABLE factura ALTER COLUMN ambiente DROP DEFAULT;
ALTER TABLE numeracion ADD COLUMN ambiente CHAR(1) NOT NULL DEFAULT '2'
    CONSTRAINT numeracion_ambiente_check CHECK (ambiente IN ('1', '2'));

-- Notas crédito dentro de `factura`.
ALTER TABLE factura ADD COLUMN tipo TEXT NOT NULL DEFAULT 'FACTURA'
    CONSTRAINT factura_tipo_check CHECK (tipo IN ('FACTURA', 'NOTA_CREDITO'));
ALTER TABLE factura ADD COLUMN referencia_id BIGINT
    CONSTRAINT factura_referencia_unica UNIQUE REFERENCES factura (id);
ALTER TABLE factura ADD COLUMN motivo TEXT;
ALTER TABLE factura ALTER COLUMN numeracion_id DROP NOT NULL;

ALTER TABLE factura DROP CONSTRAINT factura_origen_check;
ALTER TABLE factura ADD CONSTRAINT factura_origen_check CHECK (
    (tipo = 'FACTURA' AND num_nonnulls(despacho_id, venta_manual_id) = 1
                      AND referencia_id IS NULL AND numeracion_id IS NOT NULL)
    OR
    (tipo = 'NOTA_CREDITO' AND num_nonnulls(despacho_id, venta_manual_id) = 0
                           AND referencia_id IS NOT NULL AND numeracion_id IS NULL
                           AND motivo IS NOT NULL AND btrim(motivo) <> '')
);

-- Numeración de las notas crédito: una serie por estación y ambiente.
CREATE TABLE numeracion_nota (
    id          SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    estacion_id SMALLINT NOT NULL REFERENCES estacion (id),
    ambiente    CHAR(1)  NOT NULL CHECK (ambiente IN ('1', '2')),
    prefijo     TEXT     NOT NULL CHECK (prefijo ~ '^[A-Z0-9]{1,4}$'),
    siguiente   BIGINT   NOT NULL DEFAULT 1 CHECK (siguiente >= 1),
    UNIQUE (estacion_id, ambiente),
    UNIQUE (prefijo)
);

-- ¿La factura `f` tiene una nota crédito aceptada (FACTURADO)?
CREATE FUNCTION factura_anulada(f BIGINT) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM factura n WHERE n.referencia_id = f AND n.estado = 'FACTURADO')
$$;

-- Las ventas manuales anuladas dejan de contar en los reportes.
DROP VIEW v_ventas_diarias, v_ventas_mensuales, v_ventas_origen;

CREATE VIEW v_ventas_origen AS
SELECT 'SURTIDOR'::text AS origen, d.surtidor_id, d.fecha_operativa AS fecha, d.producto_id, d.forma_pago,
       d.volumen_bruto AS galones, d.valor
FROM despacho d
UNION ALL
SELECT 'MANUAL'::text, v.surtidor_id, v.registrada_en::date, v.producto_id, v.forma_pago, v.volumen, v.valor
FROM venta_manual v
WHERE v.despacho_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM factura f WHERE f.venta_manual_id = v.id AND factura_anulada(f.id));

CREATE VIEW v_ventas_diarias AS
SELECT o.surtidor_id, o.fecha, p.codigo AS producto, o.forma_pago, o.origen,
       count(*) AS despachos, sum(o.galones) AS galones, sum(o.valor) AS valor
FROM v_ventas_origen o
JOIN producto p ON p.id = o.producto_id
GROUP BY o.surtidor_id, o.fecha, p.codigo, o.forma_pago, o.origen;

CREATE VIEW v_ventas_mensuales AS
SELECT o.surtidor_id, date_trunc('month', o.fecha)::date AS mes, p.codigo AS producto, o.forma_pago, o.origen,
       count(*) AS despachos, sum(o.galones) AS galones, sum(o.valor) AS valor
FROM v_ventas_origen o
JOIN producto p ON p.id = o.producto_id
GROUP BY o.surtidor_id, date_trunc('month', o.fecha), p.codigo, o.forma_pago, o.origen;
"""

# Las vistas de la 0004, tal cual. Las notas crédito que existan se borran (con su cola y entregas): el esquema
# anterior no sabe representarlas.
DOWNGRADE = """
DROP VIEW v_ventas_diarias, v_ventas_mensuales, v_ventas_origen;

CREATE VIEW v_ventas_origen AS
SELECT 'SURTIDOR'::text AS origen, d.surtidor_id, d.fecha_operativa AS fecha, d.producto_id, d.forma_pago,
       d.volumen_bruto AS galones, d.valor
FROM despacho d
UNION ALL
SELECT 'MANUAL'::text, v.surtidor_id, v.registrada_en::date, v.producto_id, v.forma_pago, v.volumen, v.valor
FROM venta_manual v
WHERE v.despacho_id IS NULL;

CREATE VIEW v_ventas_diarias AS
SELECT o.surtidor_id, o.fecha, p.codigo AS producto, o.forma_pago, o.origen,
       count(*) AS despachos, sum(o.galones) AS galones, sum(o.valor) AS valor
FROM v_ventas_origen o
JOIN producto p ON p.id = o.producto_id
GROUP BY o.surtidor_id, o.fecha, p.codigo, o.forma_pago, o.origen;

CREATE VIEW v_ventas_mensuales AS
SELECT o.surtidor_id, date_trunc('month', o.fecha)::date AS mes, p.codigo AS producto, o.forma_pago, o.origen,
       count(*) AS despachos, sum(o.galones) AS galones, sum(o.valor) AS valor
FROM v_ventas_origen o
JOIN producto p ON p.id = o.producto_id
GROUP BY o.surtidor_id, date_trunc('month', o.fecha), p.codigo, o.forma_pago, o.origen;

DROP FUNCTION factura_anulada(BIGINT);
DROP TABLE numeracion_nota;

DELETE FROM outbox WHERE factura_id IN (SELECT id FROM factura WHERE tipo = 'NOTA_CREDITO');
DELETE FROM entrega WHERE factura_id IN (SELECT id FROM factura WHERE tipo = 'NOTA_CREDITO');
DELETE FROM factura WHERE tipo = 'NOTA_CREDITO';

ALTER TABLE factura DROP CONSTRAINT factura_origen_check;
ALTER TABLE factura DROP COLUMN motivo;
ALTER TABLE factura DROP COLUMN referencia_id;
ALTER TABLE factura DROP COLUMN tipo;
ALTER TABLE factura DROP COLUMN ambiente;
ALTER TABLE factura ALTER COLUMN numeracion_id SET NOT NULL;
ALTER TABLE factura ADD CONSTRAINT factura_origen_check CHECK (num_nonnulls(despacho_id, venta_manual_id) = 1);
ALTER TABLE numeracion DROP COLUMN ambiente;
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros (mismo patrón que la 0003 a la 0006).
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
