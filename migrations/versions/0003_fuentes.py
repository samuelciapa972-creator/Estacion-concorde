"""Fuentes de despachos: surtidor SIMULADOR y una sola factura por venta aunque haya venta manual enlazada.

  * surtidor.marca admite 'SIMULADOR' (banco de pruebas): lo simulado nunca se confunde con un equipo real.
    Fuente de verdad: `modelos.MarcaSurtidor`.
  * Una venta manual enlazada a su despacho (`venta_manual.despacho_id`) es LA MISMA venta. La base impide
    que las dos tengan factura (pendiente que dejó la 0002):
      - trigger en `factura`: no se crea (ni se cambia de origen) una factura para un despacho que ya tiene
        factura directa o por su venta manual enlazada;
      - trigger en `venta_manual`: no se enlaza si las dos ya tienen factura, y un enlace no se cambia.
    Los dos triggers bloquean la fila del DESPACHO (FOR UPDATE): así dos transacciones concurrentes (facturar el
    despacho y facturar su venta manual) se serializan y la segunda ve la factura de la primera.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = r"""
ALTER TABLE surtidor DROP CONSTRAINT surtidor_marca_check;
ALTER TABLE surtidor ADD CONSTRAINT surtidor_marca_check CHECK (marca IN ('SPEED_SOLUTIONS', 'WAYNE', 'SIMULADOR'));

-- ¿El despacho `d` ya tiene factura, directa o por la venta manual enlazada? (sin contar la factura `excepto`)
CREATE FUNCTION despacho_tiene_factura(d BIGINT, excepto BIGINT) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM factura f WHERE f.despacho_id = d AND f.id IS DISTINCT FROM excepto)
        OR EXISTS (SELECT 1 FROM factura f JOIN venta_manual v ON v.id = f.venta_manual_id
                   WHERE v.despacho_id = d AND f.id IS DISTINCT FROM excepto)
$$;

CREATE FUNCTION factura_una_por_venta() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    d BIGINT := NEW.despacho_id;
BEGIN
    IF d IS NULL THEN
        SELECT despacho_id INTO d FROM venta_manual WHERE id = NEW.venta_manual_id;
    END IF;
    IF d IS NULL THEN
        RETURN NEW;
    END IF;
    PERFORM 1 FROM despacho WHERE id = d FOR UPDATE;
    IF despacho_tiene_factura(d, NEW.id) THEN
        RAISE EXCEPTION 'el despacho % ya tiene factura (directa o por su venta manual enlazada)', d
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER factura_una_por_venta BEFORE INSERT OR UPDATE OF despacho_id, venta_manual_id ON factura
    FOR EACH ROW EXECUTE FUNCTION factura_una_por_venta();

CREATE FUNCTION venta_manual_enlace() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.despacho_id IS NOT NULL THEN
        RAISE EXCEPTION 'la venta manual % ya está enlazada al despacho %: el enlace no se cambia',
            OLD.id, OLD.despacho_id;
    END IF;
    PERFORM 1 FROM despacho WHERE id = NEW.despacho_id FOR UPDATE;
    IF EXISTS (SELECT 1 FROM factura WHERE venta_manual_id = NEW.id)
       AND despacho_tiene_factura(NEW.despacho_id, NULL) THEN
        RAISE EXCEPTION 'la venta manual % y el despacho % ya tienen factura cada uno: no se pueden enlazar',
            NEW.id, NEW.despacho_id USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER venta_manual_enlace BEFORE INSERT OR UPDATE OF despacho_id ON venta_manual
    FOR EACH ROW WHEN (NEW.despacho_id IS NOT NULL) EXECUTE FUNCTION venta_manual_enlace();
"""

DOWNGRADE = r"""
DROP TRIGGER venta_manual_enlace ON venta_manual;
DROP FUNCTION venta_manual_enlace();
DROP TRIGGER factura_una_por_venta ON factura;
DROP FUNCTION factura_una_por_venta();
DROP FUNCTION despacho_tiene_factura(BIGINT, BIGINT);
-- Si quedan surtidores SIMULADOR, el CHECK falla y la bajada se detiene: no se borran datos en silencio.
ALTER TABLE surtidor DROP CONSTRAINT surtidor_marca_check;
ALTER TABLE surtidor ADD CONSTRAINT surtidor_marca_check CHECK (marca IN ('SPEED_SOLUTIONS', 'WAYNE'));
"""


def _sql(texto: str) -> None:
    # SQL crudo por psycopg, sin parámetros: admite varias sentencias, '%' y cuerpos $$...$$.
    op.get_bind().connection.driver_connection.execute(texto)  # type: ignore[union-attr]


def upgrade() -> None:
    _sql(UPGRADE)


def downgrade() -> None:
    _sql(DOWNGRADE)
