"""Exportación al contador con notas crédito y ventas anuladas. Contra PostgreSQL real; datos sintéticos."""

from __future__ import annotations

import io
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import load_workbook

from estacion.servicios import exportacion_contador, reportes_pdf

pytestmark = pytest.mark.bd


def _cufe() -> str:
    return uuid.uuid4().hex * 3  # 96 hexadecimales, como un SHA-384


def _factura(
    e: Any, venta: int, emision: str, estado: str = "FACTURADO", ambiente: str = "2", consecutivo: int = 1
) -> int:
    return e.datos.insertar(
        "factura", clave_emision=uuid.uuid4().hex, cliente_id=e.cliente, usuario_id=e.usuario,
        numeracion_id=e.numeracion, prefijo="SETP", consecutivo=consecutivo, forma_pago="CONTADO",
        medio_pago="EFECTIVO", total=50000, fecha_emision=emision, ambiente=ambiente, venta_manual_id=venta,
        estado=estado, cufe=_cufe(),
    )  # fmt: skip


def _nota(e: Any, referencia: int, emision: str, estado: str = "FACTURADO", ambiente: str = "2") -> int:
    return e.datos.insertar(
        "factura", clave_emision=uuid.uuid4().hex, tipo="NOTA_CREDITO", referencia_id=referencia,
        motivo="Error en el cliente", cliente_id=e.cliente, usuario_id=e.usuario, prefijo="NCP", consecutivo=1,
        forma_pago="CONTADO", medio_pago="EFECTIVO", total=50000, fecha_emision=emision, ambiente=ambiente,
        estado=estado, cufe=_cufe(),
    )  # fmt: skip


def test_exportacion_nota_negativa_en_su_fecha(escenario, conn):
    e = escenario
    venta = e.datos.venta_manual(e.usuario)
    f = _factura(e, venta, "2026-10-31 10:00")
    _nota(e, f, "2026-11-02 09:00")
    octubre = exportacion_contador.facturas(conn, date(2026, 10, 1), date(2026, 10, 31), ambiente="2")
    assert [(x.tipo, x.anulada, x.total) for x in octubre] == [("Factura", True, Decimal("50000"))]
    noviembre = exportacion_contador.facturas(conn, date(2026, 11, 1), date(2026, 11, 30), ambiente="2")
    assert len(noviembre) == 1
    nota = noviembre[0]
    assert (nota.tipo, nota.anulada, nota.referencia) == ("Nota crédito", False, "SETP1")
    assert (nota.total, nota.base, nota.galones) == (Decimal("-50000"), Decimal("-50000"), Decimal("-3.120"))
    assert nota.producto == "CORRIENTE" and nota.origen == "MANUAL"
    # Excel y CSV llevan las columnas nuevas y el valor negativo.
    libro = load_workbook(io.BytesIO(exportacion_contador.excel(conn, date(2026, 11, 1), date(2026, 11, 30), "2")))
    encabezado, fila = list(libro["Facturas"].iter_rows(values_only=True))
    datos = dict(zip(encabezado, fila, strict=True))
    assert (datos["Tipo"], datos["Factura referenciada"], datos["Anulada"]) == ("Nota crédito", "SETP1", "No")
    assert Decimal(str(datos["Total"])) == Decimal("-50000")
    assert any("fecha de la nota" in (c[0] or "") for c in libro["Notas"].iter_rows(values_only=True))
    csv = exportacion_contador.csv_facturas(conn, date(2026, 11, 1), date(2026, 11, 30), "2").decode("utf-8-sig")
    assert csv.splitlines()[0].endswith("Origen;Tipo;Factura referenciada;Anulada") and "-50000" in csv
    oct_csv = exportacion_contador.csv_facturas(conn, date(2026, 10, 1), date(2026, 10, 31), "2").decode("utf-8-sig")
    assert oct_csv.splitlines()[1].endswith("MANUAL;Factura;;Sí")


def test_nota_no_aceptada_no_anula_la_factura(escenario, conn):
    e = escenario
    f = _factura(e, e.datos.venta_manual(e.usuario), "2026-10-31 10:00")
    _nota(e, f, "2026-11-02 09:00", estado="EN_PROCESO")
    (fac,) = exportacion_contador.facturas(conn, date(2026, 10, 1), date(2026, 10, 31), ambiente="2")
    assert fac.anulada is False


def test_resumen_no_cuenta_la_venta_anulada(escenario, conn):
    e = escenario
    e.datos.despacho()  # 29.757 sin factura
    anulada = e.datos.venta_manual(e.usuario)
    f = _factura(e, anulada, "2026-09-30 11:05")
    otra = e.datos.venta_manual(e.usuario)
    _factura(e, otra, "2026-09-30 11:10", consecutivo=2)
    sin_nota = exportacion_contador.resumen_diario(conn, date(2026, 9, 1), date(2026, 9, 30), "2")
    assert [(d.ventas, d.valor_vendido, d.valor_facturado) for d in sin_nota] == [(3, 129757, 100000)]
    _nota(e, f, "2026-09-30 12:00")
    (dia,) = exportacion_contador.resumen_diario(conn, date(2026, 9, 1), date(2026, 9, 30), "2")
    assert (dia.ventas, dia.valor_vendido, dia.valor_facturado, dia.valor_sin_factura) == (2, 79757, 50000, 29757)


def test_exportacion_excluye_otro_ambiente(escenario, conn):
    e = escenario
    _factura(e, e.datos.venta_manual(e.usuario), "2026-09-30 10:00", ambiente="2")
    rango = (date(2026, 9, 1), date(2026, 9, 30))
    assert len(exportacion_contador.facturas(conn, *rango, ambiente="2")) == 1
    assert exportacion_contador.facturas(conn, *rango, ambiente="1") == []
    pdf = reportes_pdf.contador(conn, *rango, ambiente="1", comprimir=False)
    assert b"(Sin facturas en este rango.) Tj" in pdf


def test_aviso_de_anulaciones_en_notas_y_pdf(escenario, conn):
    e = escenario
    _factura(e, e.datos.venta_manual(e.usuario), "2026-09-30 10:00")
    rango = (date(2026, 9, 1), date(2026, 9, 30))
    libro = load_workbook(io.BytesIO(exportacion_contador.excel(conn, *rango, "2")))
    textos = [c[0] for c in libro["Notas"].iter_rows(values_only=True)]
    assert exportacion_contador.AVISO_ANULACIONES in textos
    pdf = reportes_pdf.contador(conn, *rango, "2", comprimir=False)
    assert b"neto de notas" in pdf  # fragmento corto: no depende de dónde corte la línea ReportLab
    assert sum(t.count("con el contador") for t in textos) == 1  # una sola vez en Notas
    assert b"anulaciones" not in exportacion_contador.csv_facturas(conn, *rango, "2")  # el CSV es solo datos


def test_pdf_sin_facturas_no_lleva_el_aviso(escenario, conn):
    pdf = reportes_pdf.contador(conn, date(2026, 9, 1), date(2026, 9, 30), "2", comprimir=False)
    assert b"(Sin facturas en este rango.) Tj" in pdf
    assert b"neto de notas" not in pdf


def test_resumen_diario_no_cuenta_facturas_de_otro_ambiente(escenario, conn):
    e = escenario
    _factura(e, e.datos.venta_manual(e.usuario), "2026-09-30 10:00", ambiente="2")
    rango = (date(2026, 9, 1), date(2026, 9, 30))
    (hab,) = exportacion_contador.resumen_diario(conn, *rango, "2")
    assert (hab.valor_facturado, hab.valor_sin_factura) == (50000, 0)
    (prod,) = exportacion_contador.resumen_diario(conn, *rango, "1")  # la venta existe, pero sin factura de producción
    assert (prod.valor_vendido, prod.valor_facturado, prod.valor_sin_factura) == (50000, 0, 50000)
