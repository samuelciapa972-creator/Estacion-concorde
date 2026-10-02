"""Reportes de ventas con las ventas manuales (Fase 7).

  * `v_ventas_diarias` y `v_ventas_mensuales` suman los despachos del surtidor MÁS las ventas manuales SIN enlace,
    con la columna `origen` ('SURTIDOR' | 'MANUAL'). Una venta manual enlazada a su despacho es la MISMA venta: ya
    está en el despacho y no se cuenta otra vez.
  * La fecha de una venta manual es la de su registro (`registrada_en`, hora local de la estación).
  * Riesgo aceptado por el usuario: una manual cuyo despacho llega después y nadie enlaza se cuenta dos veces;
    `servicios/reportes_bd.posibles_duplicados` las señala.
  * `v_turno_resumen` no cambia: las ventas manuales no tienen turno.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
DROP VIEW v_ventas_diarias, v_ventas_mensuales;

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
"""

# Las vistas de la 0001, tal cual.
DOWNGRADE = """
DROP VIEW v_ventas_diarias, v_ventas_mensuales, v_ventas_origen;

CREATE VIEW v_ventas_diarias AS
SELECT d.surtidor_id,
       d.fecha_operativa            AS fecha,
       p.codigo                     AS producto,
       d.forma_pago,
       count(*)                     AS despachos,
       sum(d.volumen_bruto)         AS galones,
       sum(d.valor)                 AS valor
FROM despacho d
JOIN producto p ON p.id = d.producto_id
GROUP BY d.surtidor_id, d.fecha_operativa, p.codigo, d.forma_pago;

CREATE VIEW v_ventas_mensuales AS
SELECT d.surtidor_id,
       date_trunc('month', d.fecha_operativa)::date AS mes,
       p.codigo                     AS producto,
       d.forma_pago,
       count(*)                     AS despachos,
       sum(d.volumen_bruto)         AS galones,
       sum(d.valor)                 AS valor
FROM despacho d
JOIN producto p ON p.id = d.producto_id
GROUP BY d.surtidor_id, date_trunc('month', d.fecha_operativa), p.codigo, d.forma_pago;
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros: admite varias sentencias (mismo patrón que la 0003).
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
