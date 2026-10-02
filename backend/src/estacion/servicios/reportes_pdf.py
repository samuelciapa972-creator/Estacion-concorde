"""Reportes en PDF: ventas diarias, mensuales y por turno, y la exportación del contador.

Son documentos INTERNOS (no fiscales): no llevan la marca de "sin validez", pero si los datos incluyen ventas del
surtidor SIMULADOR lo advierten en el encabezado. Cada página repite el encabezado de la estación y de la tabla y
lleva "Página X de Y". Los números salen de las mismas consultas que la pantalla y el Excel.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..facturacion.representacion import fecha_co, numero_es_co
from . import exportacion_contador, reportes_bd
from ._bd import Conexion

Tipo = Literal["diario", "mensual", "turno"]
AVISO_SIMULADO = "Incluye ventas SIMULADAS (banco de pruebas): no son ventas reales."
_TITULO: dict[str, str] = {"diario": "Ventas diarias", "mensual": "Ventas mensuales", "turno": "Ventas por turno"}
_NEGRO = colors.HexColor("#0e0e0e")
_DORADO = colors.HexColor("#c9a227")
_GRIS = colors.HexColor("#f3f1ec")


def _pesos(x: Decimal) -> str:
    entero = x == x.to_integral_value()
    return "$ " + numero_es_co(x, 0 if entero else 2)


def _galones(x: Decimal) -> str:
    return numero_es_co(x, 3)


class _Numerado(Canvas):
    """Canvas que conoce el total de páginas al final para escribir "Página X de Y"."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._paginas: list[dict[str, Any]] = []

    # `_startPage`, `_pagesize` y `_pageNumber` son internos de reportlab (receta habitual de "Página X de Y");
    # sus tipos no los declaran.
    def showPage(self) -> None:  # noqa: N802 (API de reportlab)
        self._paginas.append(dict(self.__dict__))
        self._startPage()  # type: ignore[attr-defined]

    def save(self) -> None:
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            ancho, _ = self._pagesize  # type: ignore[attr-defined]
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.black)
            self.drawRightString(ancho - 15 * mm, 8 * mm, f"Página {self._pageNumber} de {total}")  # type: ignore[attr-defined]
            super().showPage()
        super().save()


def _emisor(conn: Conexion) -> tuple[str, str, str]:
    fila = conn.execute("SELECT razon_social, nit || '-' || dv, nombre_comercial FROM estacion WHERE activa").fetchone()
    if fila is None:
        return "Estación", "", ""
    return fila[0], f"NIT {fila[1]}", fila[2] or ""


def _documento(
    titulo: str,
    rango: str,
    emisor: tuple[str, str, str],
    simulado: bool,
    cuerpo: list[Any],
    *,
    horizontal: bool,
    comprimir: bool,
) -> bytes:
    salida = io.BytesIO()
    tamano = landscape(letter) if horizontal else letter
    generado = datetime.now().strftime("%d/%m/%Y %H:%M")
    razon, nit, comercial = emisor

    def encabezado(c: Canvas, _doc: Any) -> None:
        ancho, alto = tamano
        c.saveState()
        c.setFillColor(_NEGRO)
        c.rect(0, alto - 24 * mm, ancho, 24 * mm, fill=1, stroke=0)
        c.setFillColor(_DORADO)
        c.rect(0, alto - 24.8 * mm, ancho, 0.8 * mm, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(15 * mm, alto - 9 * mm, razon)
        c.setFont("Helvetica", 8.5)
        c.setFillColor(colors.white)
        c.drawString(15 * mm, alto - 14 * mm, "   ".join(x for x in (nit, comercial) if x))
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(_DORADO)
        c.drawString(15 * mm, alto - 20 * mm, f"{titulo} {rango}")
        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor("#57534e"))
        c.drawString(15 * mm, 8 * mm, f"Generado: {generado}")  # al pie: arriba no cabe con una razón social larga
        if simulado:
            c.setFillColor(colors.HexColor("#fde68a"))
            c.drawRightString(ancho - 15 * mm, alto - 20 * mm, AVISO_SIMULADO)
        c.restoreState()

    doc = SimpleDocTemplate(
        salida, pagesize=tamano, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=30 * mm, bottomMargin=15 * mm,
        title=f"{titulo} {rango}", author=razon, creator="estacion", invariant=1, pageCompression=1 if comprimir else 0,
    )  # fmt: skip
    doc.build(cuerpo, onFirstPage=encabezado, onLaterPages=encabezado, canvasmaker=_Numerado)
    return salida.getvalue()


def _tabla(encabezados: Sequence[str], filas: list[list[str]], total: list[str] | None, numericas: set[int]) -> Table:
    datos = [list(encabezados), *filas, *([total] if total else [])]
    t = Table(datos, repeatRows=1, hAlign="LEFT")
    estilo: list[Any] = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), _NEGRO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1 if not total else -2), [colors.white, _GRIS]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, _DORADO),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in numericas:
        estilo.append(("ALIGN", (i, 0), (i, -1), "RIGHT"))
    if total:
        estilo += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8), ("LINEABOVE", (0, -1), (-1, -1), 1, _NEGRO)]
    t.setStyle(TableStyle(estilo))
    return t


def _nota(texto: str) -> Paragraph:
    gris = colors.HexColor("#57534e")
    estilo = getSampleStyleSheet()["Normal"].clone("nota", fontSize=7.5, leading=9.5, textColor=gris)
    return Paragraph(texto, estilo)


def _rango(desde: date, hasta: date) -> str:
    return f"del {fecha_co(desde.isoformat())} al {fecha_co(hasta.isoformat())}"


def _hay_simulado(conn: Conexion, surtidores: set[int]) -> bool:
    if not surtidores:
        return False
    fila = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM surtidor WHERE marca = 'SIMULADOR' AND id = ANY(%s))", (list(surtidores),)
    ).fetchone()
    return bool(fila and fila[0])


def _nombres_surtidor(conn: Conexion) -> dict[int, str]:
    return {f[0]: f[1] for f in conn.execute("SELECT id, marca FROM surtidor").fetchall()}


def reporte(
    conn: Conexion, tipo: Tipo, desde: date, hasta: date, surtidor_id: int | None, *, comprimir: bool = True
) -> bytes:
    nombres = _nombres_surtidor(conn)
    cuerpo: list[Any] = []
    if tipo == "turno":
        turnos = reportes_bd.resumen_turnos(conn, desde, hasta, surtidor_id)
        surtidores = {t.surtidor_id for t in turnos}
        filas = [
            [
                str(t.id_cierre),
                nombres.get(t.surtidor_id, str(t.surtidor_id)),
                t.lado,
                t.estado,
                t.inicio.strftime("%d/%m/%Y %H:%M"),
                t.fin.strftime("%d/%m/%Y %H:%M"),
                str(t.despachos),
                _galones(t.galones),
                _pesos(t.valor),
            ]  # fmt: skip
            for t in turnos
        ]
        total = ["Total", "", "", "", "", "", str(sum(t.despachos for t in turnos)),
                 _galones(sum((t.galones for t in turnos), Decimal(0))),
                 _pesos(sum((t.valor for t in turnos), Decimal(0)))]  # fmt: skip
        encabezados: tuple[str, ...] = (
            "Cierre", "Surtidor", "Lado", "Estado", "Desde", "Hasta", "Despachos", "Galones", "Valor",
        )  # fmt: skip
        numericas = {6, 7, 8}
        nota = "Por turno solo cuentan los despachos del surtidor: las ventas manuales no tienen turno."
    else:
        ventas = (reportes_bd.ventas_diarias if tipo == "diario" else reportes_bd.ventas_mensuales)(
            conn, desde, hasta, surtidor_id
        )
        surtidores = {v.surtidor_id for v in ventas}
        filas = [
            [
                fecha_co(v.periodo.isoformat()) if tipo == "diario" else v.periodo.isoformat()[:7],
                nombres.get(v.surtidor_id, str(v.surtidor_id)),
                v.producto,
                v.forma_pago,
                v.origen,
                str(v.despachos),
                _galones(v.galones),
                _pesos(v.valor),
            ]  # fmt: skip
            for v in ventas
        ]
        total = ["Total", "", "", "", "", str(sum(v.despachos for v in ventas)),
                 _galones(sum((v.galones for v in ventas), Decimal(0))),
                 _pesos(sum((v.valor for v in ventas), Decimal(0)))]  # fmt: skip
        encabezados = ("Día" if tipo == "diario" else "Mes", "Surtidor", "Producto", "Pago", "Origen", "Ventas",
                       "Galones", "Valor")  # fmt: skip
        numericas = {5, 6, 7}
        nota = ("Suma los despachos del surtidor y las ventas MANUALES sin enlace; una manual enlazada a su despacho "
                "es la misma venta y no se cuenta dos veces.")  # fmt: skip
    if filas:
        cuerpo.append(_tabla(encabezados, filas, total, numericas))
    else:
        cuerpo.append(_nota("Sin ventas en este rango."))
    cuerpo += [Spacer(1, 4 * mm), _nota(nota)]
    return _documento(
        _TITULO[tipo], _rango(desde, hasta), _emisor(conn), _hay_simulado(conn, surtidores), cuerpo,
        horizontal=False, comprimir=comprimir,
    )  # fmt: skip


def contador(conn: Conexion, desde: date, hasta: date, *, comprimir: bool = True) -> bytes:
    """Libro de facturas y resumen diario, en horizontal. El CUFE completo va en el Excel (aquí no cabe)."""
    facturas = exportacion_contador.facturas(conn, desde, hasta)
    dias = exportacion_contador.resumen_diario(conn, desde, hasta)
    cuerpo: list[Any] = [_nota("<b>Facturas</b> (por fecha de emisión, en todos los estados)"), Spacer(1, 2 * mm)]
    if facturas:
        filas = [
            [
                f.fecha.strftime("%d/%m/%Y %H:%M"),
                f.numero,
                f.estado,
                f"{f.tipo_documento} {f.documento}",
                f.cliente[:40],
                f.producto,
                _galones(f.galones),
                _pesos(f.base),
                _pesos(f.impuestos),
                _pesos(f.total),
                f.medio_pago,
                f.origen,
            ]  # fmt: skip
            for f in facturas
        ]
        validadas = [f for f in facturas if f.estado == "FACTURADO"]
        total = ["Total validadas", "", "", "", "", "", _galones(sum((f.galones for f in validadas), Decimal(0))),
                 _pesos(sum((f.base for f in validadas), Decimal(0))), _pesos(Decimal(0)),
                 _pesos(sum((f.total for f in validadas), Decimal(0))), "", ""]  # fmt: skip
        cuerpo.append(_tabla(("Fecha", "Número", "Estado", "Documento", "Cliente", "Producto", "Galones", "Base",
                              "Impuestos", "Total", "Medio", "Origen"), filas, total, {6, 7, 8, 9}))  # fmt: skip
    else:
        cuerpo.append(_nota("Sin facturas en este rango."))
    cuerpo += [Spacer(1, 6 * mm), _nota("<b>Resumen diario</b> (por fecha de la venta)"), Spacer(1, 2 * mm)]
    if dias:
        filas = [[fecha_co(d.fecha.isoformat()), str(d.ventas), _galones(d.galones), _pesos(d.valor_vendido),
                  _pesos(d.valor_facturado), _pesos(d.valor_sin_factura)] for d in dias]  # fmt: skip
        total = ["Total", str(sum(d.ventas for d in dias)), _galones(sum((d.galones for d in dias), Decimal(0))),
                 _pesos(sum((d.valor_vendido for d in dias), Decimal(0))),
                 _pesos(sum((d.valor_facturado for d in dias), Decimal(0))),
                 _pesos(sum((d.valor_sin_factura for d in dias), Decimal(0)))]  # fmt: skip
        cuerpo.append(_tabla(("Fecha", "Ventas", "Galones", "Vendido", "Facturado", "Sin factura"), filas, total,
                             {1, 2, 3, 4, 5}))  # fmt: skip
    else:
        cuerpo.append(_nota("Sin ventas en este rango."))
    cuerpo += [
        Spacer(1, 4 * mm),
        _nota(
            "FORMATO PROVISIONAL (lo reemplaza el que defina el contador). Impuestos en 0: el tratamiento del "
            "combustible lo define el contador. Contiene datos personales (Ley 1581 de 2012)."
        ),  # fmt: skip
    ]
    surtidores = {f[0] for f in conn.execute(
        "SELECT DISTINCT surtidor_id FROM v_ventas_origen WHERE fecha BETWEEN %s AND %s", (desde, hasta)
    ).fetchall()}  # fmt: skip
    return _documento(
        "Ventas y facturas para la contabilidad", _rango(desde, hasta), _emisor(conn),
        _hay_simulado(conn, surtidores), cuerpo, horizontal=True, comprimir=comprimir,
    )  # fmt: skip
