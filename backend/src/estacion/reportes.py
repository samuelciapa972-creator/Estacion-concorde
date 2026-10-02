"""Reportes de ventas en Excel (diario, mensual y por turno) a partir de los despachos.

El libro tiene una hoja `Datos` (un renglón por despacho) y las hojas de resumen usan
fórmulas (SUMIFS/COUNTIFS) sobre ella: si se corrige o se agrega un renglón en `Datos`,
todo se recalcula. Las etiquetas de fila (fechas, meses, turnos) son valores, no fórmulas.

Uso:
    python -m estacion.reportes speed_solutions D260928_084701.xls -o reporte.xlsx
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .calidad import analizar
from .modelos import Anomalia, Despacho, FormaPago, TurnoDerivado
from .parsers import PARSERS

FUENTE = "Arial"
F_NORMAL = Font(name=FUENTE, size=10)
F_NEGRITA = Font(name=FUENTE, size=10, bold=True)
F_TITULO = Font(name=FUENTE, size=14, bold=True)
F_ENCABEZADO = Font(name=FUENTE, size=10, bold=True, color="FFFFFF")
RELLENO_ENC = PatternFill("solid", start_color="1F3864")
RELLENO_TOTAL = PatternFill("solid", start_color="D9E1F2")
DINERO = '#,##0;(#,##0);"-"'
GALONES = '#,##0.000;(#,##0.000);"-"'
ENTERO = '#,##0;(#,##0);"-"'


def generar_excel(
    despachos: list[Despacho],
    anomalias: list[Anomalia],
    turnos: list[TurnoDerivado],
    ruta: str | Path,
    *,
    archivo_origen: str = "",
    sha256: str = "",
) -> Path:
    wb = Workbook()
    n = len(despachos)
    filas = sorted(despachos, key=lambda d: (d.inicio, d.id_externo))

    ws_diario = cast(Worksheet, wb.active)  # un libro nuevo siempre trae una hoja activa
    ws_diario.title = "Diario"
    ws_mensual = wb.create_sheet("Mensual")
    ws_turnos = wb.create_sheet("Turnos")
    ws_datos = wb.create_sheet("Datos")
    ws_alertas = wb.create_sheet("Alertas")
    ws_notas = wb.create_sheet("Notas")

    _hoja_datos(ws_datos, filas)
    ultima = n + 1  # última fila con datos en `Datos`

    def rango(col: str) -> str:
        return f"Datos!${col}$2:${col}${ultima}"

    # --- Diario: una fila por (fecha, producto)
    combos = sorted({(d.inicio.date(), d.producto) for d in filas})
    _titulo(ws_diario, "Ventas por día", "Fechas efectivas del surtidor (ver hoja Notas). Valores en pesos.")
    enc = ["Fecha", "Producto", "Despachos", "Galones", "Contado ($)", "Crédito ($)", "Total ($)"]
    _encabezado(ws_diario, 4, enc)
    for i, (fecha, prod) in enumerate(combos, start=5):
        ws_diario.cell(i, 1, fecha).number_format = "yyyy-mm-dd"
        ws_diario.cell(i, 2, prod)
        crit = f"{rango('B')},$A{i},{rango('G')},$B{i}"
        ws_diario.cell(i, 3, f"=COUNTIFS({crit})").number_format = ENTERO
        ws_diario.cell(i, 4, f"=SUMIFS({rango('H')},{crit})").number_format = GALONES
        ws_diario.cell(i, 5, f'=SUMIFS({rango("I")},{crit},{rango("J")},"CONTADO")').number_format = DINERO
        ws_diario.cell(i, 6, f'=SUMIFS({rango("I")},{crit},{rango("J")},"CREDITO")').number_format = DINERO
        ws_diario.cell(i, 7, f"=E{i}+F{i}").number_format = DINERO
    fin_d = 4 + len(combos)
    _fila_total(ws_diario, fin_d + 1, 3, fin_d, {3: ENTERO, 4: GALONES, 5: DINERO, 6: DINERO, 7: DINERO}, 4)
    _anchos(ws_diario, [13, 12, 11, 14, 16, 16, 16])
    ws_diario.freeze_panes = "A5"

    # --- Mensual
    meses = sorted({(d.inicio.year, d.inicio.month, d.producto) for d in filas})
    _titulo(ws_mensual, "Ventas por mes", "Suma de los días calendario del mes. Valores en pesos.")
    _encabezado(ws_mensual, 4, ["Mes", "Producto", "Despachos", "Galones", "Contado ($)", "Crédito ($)", "Total ($)"])
    for i, (anio, mes, prod) in enumerate(meses, start=5):
        ws_mensual.cell(i, 1, date(anio, mes, 1)).number_format = "mmm yyyy"
        ws_mensual.cell(i, 2, prod)
        crit = f'{rango("B")},">="&$A{i},{rango("B")},"<"&EDATE($A{i},1),{rango("G")},$B{i}'
        ws_mensual.cell(i, 3, f"=COUNTIFS({crit})").number_format = ENTERO
        ws_mensual.cell(i, 4, f"=SUMIFS({rango('H')},{crit})").number_format = GALONES
        ws_mensual.cell(i, 5, f'=SUMIFS({rango("I")},{crit},{rango("J")},"CONTADO")').number_format = DINERO
        ws_mensual.cell(i, 6, f'=SUMIFS({rango("I")},{crit},{rango("J")},"CREDITO")').number_format = DINERO
        ws_mensual.cell(i, 7, f"=E{i}+F{i}").number_format = DINERO
    fin_m = 4 + len(meses)
    _fila_total(ws_mensual, fin_m + 1, 3, fin_m, {3: ENTERO, 4: GALONES, 5: DINERO, 6: DINERO, 7: DINERO}, 4)
    _anchos(ws_mensual, [13, 12, 11, 14, 18, 18, 18])
    ws_mensual.freeze_panes = "A5"

    # --- Turnos
    _titulo(
        ws_turnos,
        "Ventas por turno (ID-CIERRE del surtidor)",
        "Inicio, fin y estado son etiquetas derivadas de los despachos; el archivo no trae la hora de cierre.",
    )
    _encabezado(
        ws_turnos,
        4,
        ["Turno", "Estado", "Inicio", "Fin", "Despachos", "Galones", "Contado ($)", "Crédito ($)", "Total ($)"],
    )
    for i, t in enumerate(turnos, start=5):
        ws_turnos.cell(i, 1, t.id_cierre)
        ws_turnos.cell(i, 2, t.estado)
        ws_turnos.cell(i, 3, t.inicio).number_format = "yyyy-mm-dd hh:mm"
        ws_turnos.cell(i, 4, t.fin).number_format = "yyyy-mm-dd hh:mm"
        ws_turnos.cell(i, 5, f"=COUNTIFS({rango('D')},$A{i})").number_format = ENTERO
        ws_turnos.cell(i, 6, f"=SUMIFS({rango('H')},{rango('D')},$A{i})").number_format = GALONES
        ws_turnos.cell(i, 7, f'=SUMIFS({rango("I")},{rango("D")},$A{i},{rango("J")},"CONTADO")').number_format = DINERO
        ws_turnos.cell(i, 8, f'=SUMIFS({rango("I")},{rango("D")},$A{i},{rango("J")},"CREDITO")').number_format = DINERO
        ws_turnos.cell(i, 9, f"=G{i}+H{i}").number_format = DINERO
    fin_t = 4 + len(turnos)
    _fila_total(ws_turnos, fin_t + 1, 5, fin_t, {5: ENTERO, 6: GALONES, 7: DINERO, 8: DINERO, 9: DINERO}, 4)
    _anchos(ws_turnos, [9, 11, 17, 17, 11, 14, 16, 16, 16])
    ws_turnos.freeze_panes = "A5"

    _hoja_alertas(ws_alertas, anomalias)
    _hoja_notas(ws_notas, n, fin_d + 1, fin_m + 1, fin_t + 1, ultima, filas, archivo_origen, sha256)

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)
    return ruta


# --------------------------------------------------------------------------
def _hoja_datos(ws, filas: list[Despacho]) -> None:
    enc = [
        "ID despacho",
        "Fecha",
        "Hora",
        "Turno",
        "Lado",
        "Pistola",
        "Producto",
        "Galones (bruto)",
        "Valor ($)",
        "Forma de pago",
        "Vehículo",
        "Tipo identif.",
        "Fecha corregida",
    ]
    _encabezado(ws, 1, enc)
    for i, d in enumerate(filas, start=2):
        ws.cell(i, 1, d.id_externo)
        ws.cell(i, 2, d.inicio.date()).number_format = "yyyy-mm-dd"
        ws.cell(i, 3, d.inicio.time().replace(microsecond=0)).number_format = "hh:mm"
        ws.cell(i, 4, d.id_cierre)
        ws.cell(i, 5, d.lado)
        ws.cell(i, 6, d.pistola)
        ws.cell(i, 7, d.producto)
        ws.cell(i, 8, float(d.volumen_bruto)).number_format = GALONES
        ws.cell(i, 9, int(d.valor)).number_format = DINERO
        ws.cell(i, 10, d.forma_pago.value if isinstance(d.forma_pago, FormaPago) else str(d.forma_pago))
        ws.cell(i, 11, d.ref_vehiculo or "")
        ws.cell(i, 12, d.placa_tipo.value)
        ws.cell(i, 13, "SI" if d.fecha_corregida else "NO")
        for c in range(1, 14):
            ws.cell(i, c).font = F_NORMAL
    ws.auto_filter.ref = f"A1:M{len(filas) + 1}"
    ws.freeze_panes = "A2"
    _anchos(ws, [12, 12, 8, 8, 6, 8, 11, 16, 14, 14, 16, 14, 15])


def _hoja_alertas(ws, anomalias: list[Anomalia]) -> None:
    _titulo(ws, "Alertas para revisión", "Generadas al importar el archivo. Las ALTA afectan facturación o cuadre.")
    _encabezado(ws, 4, ["Severidad", "Tipo", "ID despacho", "Detalle"])
    orden = {"ALTA": 0, "AVISO": 1, "INFO": 2}
    for i, a in enumerate(
        sorted(anomalias, key=lambda x: (orden[x.severidad.value], x.tipo, x.id_despacho or 0)), start=5
    ):
        ws.cell(i, 1, a.severidad.value)
        ws.cell(i, 2, a.tipo)
        ws.cell(i, 3, a.id_despacho)
        detalle = "; ".join(f"{k}={v}" for k, v in a.detalle.items())
        ws.cell(i, 4, detalle)
        for c in range(1, 5):
            ws.cell(i, c).font = F_NORMAL
    if not anomalias:
        ws.cell(5, 1, "Sin alertas").font = F_NORMAL
    _anchos(ws, [11, 30, 13, 110])
    ws.freeze_panes = "A5"


def _hoja_notas(ws, n, fila_diario, fila_mensual, fila_turnos, ultima, filas, archivo, sha) -> None:
    _titulo(ws, "Notas y control", "Lea esto antes de usar los números.")
    corregidas = sum(1 for d in filas if d.fecha_corregida)
    notas = [
        ("Archivo de origen", archivo or "-"),
        ("Huella (sha256, 12 primeros)", sha[:12] if sha else "-"),
        ("Generado", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Despachos", n),
        ("Fechas corregidas", corregidas),
        ("", ""),
        ("Galones", "Volumen bruto (VOLG): es el que el surtidor multiplica por el precio para calcular el valor."),
        (
            "Fecha de cada venta",
            "Fecha calendario del inicio del despacho. Un turno puede cruzar la medianoche: vea la hoja Turnos.",
        ),
        (
            "Fechas corregidas",
            "Cuando el reloj del surtidor falló, la fecha se estimó (hoja Alertas, tipo FECHA_CORREGIDA). "
            "La columna 'Fecha corregida' de Datos marca esos renglones.",
        ),
        ("Vehículo", "Placa normalizada o nombre de equipo digitado en el surtidor. Vacío = sin dato (0, 1, etc.)."),
        ("Impuestos", "Este reporte NO desglosa impuestos; los valores son los totales que registra el surtidor."),
    ]
    for i, (k, v) in enumerate(notas, start=4):
        ws.cell(i, 1, k).font = F_NEGRITA
        ws.cell(i, 2, v).font = F_NORMAL
        ws.cell(i, 2).alignment = Alignment(wrap_text=True, vertical="top")

    base = 4 + len(notas) + 1
    ws.cell(base, 1, "Controles de cuadre (deben dar 0)").font = F_NEGRITA
    controles = [
        ("Datos vs Diario ($)", f"=SUM(Datos!$I$2:$I${ultima})-Diario!G{fila_diario}"),
        ("Datos vs Mensual ($)", f"=SUM(Datos!$I$2:$I${ultima})-Mensual!G{fila_mensual}"),
        ("Datos vs Turnos ($)", f"=SUM(Datos!$I$2:$I${ultima})-Turnos!I{fila_turnos}"),
        ("Despachos Datos vs Diario", f"=COUNTA(Datos!$A$2:$A${ultima})-Diario!C{fila_diario}"),
    ]
    for j, (k, f) in enumerate(controles, start=base + 1):
        ws.cell(j, 1, k).font = F_NORMAL
        c = ws.cell(j, 2, f)
        c.font = F_NORMAL
        c.number_format = DINERO
        c.alignment = Alignment(horizontal="left")
    _anchos(ws, [32, 110])


def _titulo(ws, titulo: str, subtitulo: str) -> None:
    ws["A1"] = titulo
    ws["A1"].font = F_TITULO
    ws["A2"] = subtitulo
    ws["A2"].font = Font(name=FUENTE, size=9, italic=True, color="595959")


def _encabezado(ws, fila: int, columnas: list[str]) -> None:
    for c, texto in enumerate(columnas, start=1):
        cel = ws.cell(fila, c, texto)
        cel.font = F_ENCABEZADO
        cel.fill = RELLENO_ENC
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _fila_total(ws, fila: int, col_desde: int, ultima_dato: int, formatos: dict[int, str], primera_dato: int) -> None:
    ws.cell(fila, 1, "TOTAL").font = F_NEGRITA
    for c, fmt in formatos.items():
        letra = get_column_letter(c)
        cel = ws.cell(fila, c, f"=SUM({letra}{primera_dato + 1}:{letra}{ultima_dato})")
        cel.number_format = fmt
        cel.font = F_NEGRITA
    for c in range(1, max(formatos) + 1):
        ws.cell(fila, c).fill = RELLENO_TOTAL
    # fuente normal para el resto de celdas de datos
    for fila_datos in ws.iter_rows(min_row=primera_dato + 1, max_row=ultima_dato):
        for cel in fila_datos:
            if cel.font is None or cel.font.name != FUENTE:
                cel.font = F_NORMAL


def _anchos(ws, anchos: list[int]) -> None:
    for i, a in enumerate(anchos, start=1):
        ws.column_dimensions[get_column_letter(i)].width = a


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="estacion.reportes", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("marca", choices=sorted(PARSERS))
    ap.add_argument("archivo", type=Path)
    ap.add_argument("-o", "--salida", type=Path, default=Path("reporte_ventas.xlsx"))
    args = ap.parse_args(argv)

    lectura = PARSERS[args.marca](args.archivo)
    despachos, anomalias, turnos = analizar(lectura.despachos)
    ruta = generar_excel(
        despachos, anomalias, turnos, args.salida, archivo_origen=args.archivo.name, sha256=lectura.archivo_sha256
    )
    print(f"Reporte generado: {ruta}  ({len(despachos)} despachos, {len(turnos)} turnos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
