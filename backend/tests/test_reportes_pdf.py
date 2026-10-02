"""Reportes en PDF (diario, mensual, turno y el del contador). Contra PostgreSQL real; datos sintéticos.

Los PDF se generan SIN compresión para buscar los textos dibujados ("(texto) Tj").
"""

from __future__ import annotations

from datetime import date

import pytest

from estacion.servicios import reportes_pdf

pytestmark = pytest.mark.bd

DESDE, HASTA = date(2026, 9, 1), date(2026, 9, 30)


def _escapado_pdf(texto: str) -> bytes:
    """Como escribe ReportLab el texto en el PDF: \\, ( y ) escapados; lo que no es ASCII, en octal (á -> \\341)."""
    salida = bytearray()
    for byte in texto.encode("cp1252"):
        if byte in b"\\()":
            salida += b"\\" + bytes([byte])
        elif byte > 127:
            salida += b"\\%03o" % byte
        else:
            salida.append(byte)
    return bytes(salida)


def dibujado(pdf: bytes, texto: str) -> bool:
    return b"(" + _escapado_pdf(texto) + b") Tj" in pdf


def test_reporte_diario_lleva_los_totales_de_la_base_y_el_origen(escenario, conn):
    d = escenario.datos
    d.despacho()
    d.despacho()
    d.venta_manual(escenario.usuario)  # 3,120 gal, $50.000, sin enlace
    pdf = reportes_pdf.reporte(conn, "diario", DESDE, HASTA, None, comprimir=False)
    assert pdf.startswith(b"%PDF-")
    assert dibujado(pdf, "$ 59.514") and dibujado(pdf, "$ 50.000")  # surtidor y manual por separado
    assert dibujado(pdf, "$ 109.514")  # total = lo de la base
    assert dibujado(pdf, "MANUAL") and dibujado(pdf, "SURTIDOR")
    assert dibujado(pdf, "ESTACION DE PRUEBA S A S") and dibujado(pdf, "NIT 900123456-8")
    assert dibujado(pdf, "Ventas diarias del 01/09/2026 al 30/09/2026")


def test_reporte_largo_repite_encabezado_y_numera_paginas(escenario, conn):
    d = escenario.datos
    p = d.uno("SELECT id FROM producto LIMIT 1") or d.uno(
        "INSERT INTO producto (codigo, nombre) VALUES ('DIESEL', 'Diesel') RETURNING id"
    )
    s = d.uno("INSERT INTO surtidor (marca, codigo_externo) VALUES ('WAYNE', 'W9') RETURNING id")
    for dia in range(1, 31):  # 30 días x 2 formas de pago = 60 filas
        for forma in ("CONTADO", "CREDITO"):
            d.venta_manual(escenario.usuario, surtidor_id=s, producto_id=p, forma_pago=forma,
                           registrada_en=f"2026-09-{dia:02d} 08:00")  # fmt: skip
    pdf = reportes_pdf.reporte(conn, "diario", DESDE, HASTA, None, comprimir=False)
    assert dibujado(pdf, "Página 2 de 2")
    assert pdf.count(b"(Producto) Tj") == 2  # encabezado de la tabla en cada página


def test_advierte_ventas_simuladas_solo_si_las_hay(escenario, conn):
    d = escenario.datos
    d.despacho()
    sin = reportes_pdf.reporte(conn, "diario", DESDE, HASTA, None, comprimir=False)
    assert not dibujado(sin, reportes_pdf.AVISO_SIMULADO)
    s = d.uno("INSERT INTO surtidor (marca, codigo_externo) VALUES ('SIMULADOR', 'SIM') RETURNING id")
    d.venta_manual(escenario.usuario, surtidor_id=s, registrada_en="2026-09-15 08:00")
    con = reportes_pdf.reporte(conn, "diario", DESDE, HASTA, None, comprimir=False)
    assert dibujado(con, reportes_pdf.AVISO_SIMULADO)


def test_reporte_por_turno_y_mensual(escenario, conn):
    d = escenario.datos
    d.despacho()
    d.conn.execute("UPDATE turno SET inicio = '2026-09-30 10:00', fin = '2026-09-30 10:02'")
    turno = reportes_pdf.reporte(conn, "turno", DESDE, HASTA, None, comprimir=False)
    assert dibujado(turno, "$ 29.757") and dibujado(turno, "Ventas por turno del 01/09/2026 al 30/09/2026")
    mensual = reportes_pdf.reporte(conn, "mensual", DESDE, HASTA, None, comprimir=False)
    assert dibujado(mensual, "2026-09") and dibujado(mensual, "$ 29.757")


def test_reporte_sin_ventas_lo_dice(escenario, conn):
    pdf = reportes_pdf.reporte(conn, "diario", DESDE, HASTA, None, comprimir=False)
    assert dibujado(pdf, "Sin ventas en este rango.")
