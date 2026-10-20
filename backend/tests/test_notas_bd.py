"""Migración 0007 contra PostgreSQL real: notas crédito en `factura`, ambiente y vistas de ventas.

Todos los datos son sintéticos.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from psycopg import errors

from conftest import migrar

pytestmark = pytest.mark.bd


def _factura(e: Any, **cambios: Any) -> int:
    campos = dict(
        clave_emision=uuid.uuid4().hex, cliente_id=e.cliente, usuario_id=e.usuario, numeracion_id=e.numeracion,
        prefijo="SETP", consecutivo=1, forma_pago="CONTADO", medio_pago="EFECTIVO", total=50000,
        fecha_emision="2026-09-30 10:05", ambiente="2",
    )  # fmt: skip
    return e.datos.insertar("factura", **{**campos, **cambios})


def _nota(e: Any, referencia: int | None, **cambios: Any) -> int:
    campos = dict(
        clave_emision=uuid.uuid4().hex, tipo="NOTA_CREDITO", referencia_id=referencia, motivo="Error en el cliente",
        cliente_id=e.cliente, usuario_id=e.usuario, prefijo="NCP", consecutivo=1, forma_pago="CONTADO",
        medio_pago="EFECTIVO", total=50000, fecha_emision="2026-09-30 11:05", ambiente="2",
    )  # fmt: skip
    return e.datos.insertar("factura", **{**campos, **cambios})


def _factura_manual(e: Any, consecutivo: int = 1, **cambios: Any) -> tuple[int, int]:
    venta = e.datos.venta_manual(e.usuario)
    return venta, _factura(e, venta_manual_id=venta, consecutivo=consecutivo, **cambios)


def test_nota_sin_referencia_o_sin_motivo_se_rechaza(escenario):
    _, f = _factura_manual(escenario)
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _nota(escenario, None)
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _nota(escenario, f, motivo="  ")
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _nota(escenario, f, motivo=None)


def test_nota_con_origen_se_rechaza(escenario):
    venta, f = _factura_manual(escenario)
    otra = escenario.datos.venta_manual(escenario.usuario)
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _nota(escenario, f, venta_manual_id=otra)
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _nota(escenario, f, despacho_id=escenario.datos.despacho())


def test_nota_con_numeracion_de_factura_se_rechaza(escenario):
    _, f = _factura_manual(escenario)
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _nota(escenario, f, numeracion_id=escenario.numeracion, prefijo="SETP")


def test_factura_no_puede_llevar_referencia_ni_ir_sin_numeracion(escenario):
    _, f = _factura_manual(escenario)
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _factura(escenario, venta_manual_id=escenario.datos.venta_manual(escenario.usuario), referencia_id=f,
                 consecutivo=2)  # fmt: skip
    with pytest.raises(errors.CheckViolation, match="factura_origen_check"):
        _factura(escenario, venta_manual_id=escenario.datos.venta_manual(escenario.usuario), numeracion_id=None,
                 consecutivo=3)  # fmt: skip


def test_nota_valida_se_guarda(escenario):
    _, f = _factura_manual(escenario)
    n = _nota(escenario, f)
    assert escenario.datos.uno("SELECT numero FROM factura WHERE id = %s", (n,)) == "NCP1"


def test_una_sola_nota_por_factura(escenario):
    _, f = _factura_manual(escenario)
    _nota(escenario, f)
    with pytest.raises(errors.UniqueViolation, match="referencia_id"):
        _nota(escenario, f, consecutivo=2)


def test_el_ambiente_solo_admite_1_o_2_y_no_tiene_valor_por_defecto(escenario):
    with pytest.raises(errors.CheckViolation, match="ambiente"):
        _factura_manual(escenario, ambiente="3")
    with pytest.raises(errors.NotNullViolation):
        _factura_manual(escenario, ambiente=None)


def test_la_numeracion_sin_ambiente_es_habilitacion(datos):
    e = datos.estacion()
    n = datos.numeracion(e)
    assert datos.uno("SELECT ambiente FROM numeracion WHERE id = %s", (n,)) == "2"


def test_numeracion_nota_restricciones(datos):
    e = datos.estacion()
    datos.insertar("numeracion_nota", estacion_id=e, ambiente="2", prefijo="NCP")
    assert datos.uno("SELECT siguiente FROM numeracion_nota WHERE prefijo = 'NCP'") == 1
    datos.insertar("numeracion_nota", estacion_id=e, ambiente="1", prefijo="NCE")
    with pytest.raises(errors.UniqueViolation):  # una serie por ambiente
        datos.insertar("numeracion_nota", estacion_id=e, ambiente="2", prefijo="NCQ")
    with pytest.raises(errors.UniqueViolation):  # prefijo único
        datos.insertar("numeracion_nota", estacion_id=e, ambiente="1", prefijo="NCP")
    for cambio in ({"ambiente": "3"}, {"prefijo": "nc"}, {"prefijo": "ABCDE"}, {"siguiente": 0}):
        campos = {"estacion_id": e, "ambiente": "2", "prefijo": "NCZ", **cambio}
        with pytest.raises(errors.CheckViolation):
            datos.insertar("numeracion_nota", **campos)


def test_filas_existentes_quedan_en_habilitacion(esquema_vacio, request):
    bd = esquema_vacio
    migrar(bd.dsn, "0006")
    from conftest import Datos

    d = Datos(bd)
    try:
        e = d.estacion()
        num = d.numeracion(e)
        u, c = d.usuario(), d.cliente()
        f = d.insertar(
            "factura", clave_emision=uuid.uuid4().hex, despacho_id=d.despacho(), cliente_id=c, usuario_id=u,
            numeracion_id=num, prefijo="SETP", consecutivo=1, forma_pago="CONTADO", medio_pago="EFECTIVO",
            total=29757, fecha_emision="2026-09-30 10:05",
        )  # fmt: skip
        migrar(bd.dsn)
        assert d.uno("SELECT ambiente FROM factura WHERE id = %s", (f,)) == "2"
        assert d.uno("SELECT ambiente FROM numeracion WHERE id = %s", (num,)) == "2"
        assert d.uno("SELECT tipo FROM factura WHERE id = %s", (f,)) == "FACTURA"
    finally:
        d.conn.close()


def test_venta_anulada_no_cuenta_en_ventas(escenario):
    d = escenario.datos
    venta, f = _factura_manual(escenario, estado="FACTURADO", cufe="ab" * 48)
    d.conn.execute("UPDATE venta_manual SET estado_facturacion = 'FACTURADO' WHERE id = %s", (venta,))
    assert d.uno("SELECT count(*) FROM v_ventas_origen WHERE origen = 'MANUAL'") == 1
    assert d.uno("SELECT factura_anulada(%s)", (f,)) is False

    n = _nota(escenario, f, estado="EN_PROCESO")
    assert d.uno("SELECT count(*) FROM v_ventas_origen WHERE origen = 'MANUAL'") == 1
    assert d.uno("SELECT factura_anulada(%s)", (f,)) is False

    d.conn.execute("UPDATE factura SET estado = 'FACTURADO', cufe = %s WHERE id = %s", ("cd" * 48, n))
    assert d.uno("SELECT count(*) FROM v_ventas_origen WHERE origen = 'MANUAL'") == 0
    assert d.uno("SELECT factura_anulada(%s)", (f,)) is True
    # y las vistas que dependen de ella siguen vivas
    assert d.uno("SELECT count(*) FROM v_ventas_diarias") == 0
    assert d.uno("SELECT count(*) FROM v_ventas_mensuales") == 0
