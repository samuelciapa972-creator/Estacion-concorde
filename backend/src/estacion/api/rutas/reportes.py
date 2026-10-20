"""Reportes y anomalías (solo administrador). Ver el alcance en `servicios/reportes_bd.py`: hoy suman los
despachos de los surtidores (incluido el SIMULADOR si no se filtra), no las ventas manuales."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from ...servicios import exportacion_contador, reportes_bd, reportes_pdf
from ...servicios._bd import auditar
from ..dependencias import Conn, SesionAdmin, exigir_rango
from ..esquemas import AnomaliaSalida, Error, FilaTurnoSalida, FilaVentasSalida, PosibleDuplicadoSalida, Severidad

router = APIRouter(tags=["reportes"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"


@router.get(
    "/reportes/diario", response_model=list[FilaVentasSalida], summary="Ventas por día, producto y forma de pago"
)
def diario(conn: Conn, _: SesionAdmin, desde: date, hasta: date, surtidor_id: int | None = None):
    exigir_rango(desde, hasta, hasta_incluido=True)
    return [asdict(f) for f in reportes_bd.ventas_diarias(conn, desde, hasta, surtidor_id)]


@router.get(
    "/reportes/mensual", response_model=list[FilaVentasSalida], summary="Ventas por mes, producto y forma de pago"
)
def mensual(conn: Conn, _: SesionAdmin, desde: date, hasta: date, surtidor_id: int | None = None):
    exigir_rango(desde, hasta, hasta_incluido=True)
    return [asdict(f) for f in reportes_bd.ventas_mensuales(conn, desde, hasta, surtidor_id)]


@router.get("/reportes/turno", response_model=list[FilaTurnoSalida], summary="Resumen por turno (ID-CIERRE) y lado")
def turno(
    conn: Conn,
    _: SesionAdmin,
    desde: date,
    hasta: date,
    surtidor_id: int | None = None,
    turno_id: int | None = None,
):
    exigir_rango(desde, hasta, hasta_incluido=True)
    return [asdict(f) for f in reportes_bd.resumen_turnos(conn, desde, hasta, surtidor_id, turno_id)]


@router.get(
    "/reportes/posibles-duplicados",
    response_model=list[PosibleDuplicadoSalida],
    summary="Ventas manuales sin enlace que parecen ser un despacho ya contado (posible doble conteo)",
)
def posibles_duplicados(conn: Conn, _: SesionAdmin, desde: date, hasta: date):
    exigir_rango(desde, hasta, hasta_incluido=True)
    return [asdict(x) for x in reportes_bd.posibles_duplicados(conn, desde, hasta)]


@router.get(
    "/reportes/exportar",
    response_class=Response,
    responses={200: {"content": {XLSX: {}}}, 404: {"model": Error}},
    summary="Excel de ventas de un surtidor (Diario, Mensual, Turnos, Datos, Alertas, Notas)",
)
def exportar(conn: Conn, _: SesionAdmin, surtidor_id: int, desde: date, hasta: date) -> Response:
    exigir_rango(desde, hasta, hasta_incluido=True)
    try:
        contenido = reportes_bd.excel_ventas(conn, surtidor_id, desde, hasta)
    except reportes_bd.SurtidorDesconocido as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None
    nombre = f"ventas_surtidor{surtidor_id}_{desde:%Y%m%d}_{hasta:%Y%m%d}.xlsx"
    return Response(contenido, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get(
    "/reportes/contador",
    response_class=Response,
    responses={200: {"content": {XLSX: {}, "text/csv": {}, PDF: {}}}, 403: {"model": Error}},
    summary="Ventas y facturas para la contabilidad (formato PROVISIONAL; lleva datos personales)",
)
def contador(
    conn: Conn, sesion: SesionAdmin, desde: date, hasta: date, formato: Literal["xlsx", "csv", "pdf"] = "xlsx"
) -> Response:
    exigir_rango(desde, hasta, hasta_incluido=True)
    if formato == "csv":
        contenido, tipo = exportacion_contador.csv_facturas(conn, desde, hasta), "text/csv; charset=utf-8"
    elif formato == "pdf":
        contenido, tipo = reportes_pdf.contador(conn, desde, hasta), PDF
    else:
        contenido, tipo = exportacion_contador.excel(conn, desde, hasta), XLSX
    # Lleva datos de clientes: queda quién lo descargó y qué rango (sin datos personales en la auditoría).
    auditar(conn, "EXPORTE_CONTADOR", "factura", f"{desde}/{hasta}", usuario_id=sesion.usuario_id,
            detalle={"desde": desde.isoformat(), "hasta": hasta.isoformat(), "formato": formato})  # fmt: skip
    nombre = f"contador_{desde:%Y%m%d}_{hasta:%Y%m%d}.{formato}"
    return Response(
        contenido,
        media_type=tipo,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"', "Cache-Control": "no-store"},
    )


@router.get(
    "/reportes/pdf",
    response_class=Response,
    responses={200: {"content": {PDF: {}}}, 403: {"model": Error}},
    summary="Reporte diario, mensual o por turno en PDF (sin datos personales)",
)
def reporte_pdf(
    conn: Conn,
    _: SesionAdmin,
    tipo: Literal["diario", "mensual", "turno"],
    desde: date,
    hasta: date,
    surtidor_id: int | None = None,
) -> Response:
    exigir_rango(desde, hasta, hasta_incluido=True)
    contenido = reportes_pdf.reporte(conn, tipo, desde, hasta, surtidor_id)
    nombre = f"ventas_{tipo}_{desde:%Y%m%d}_{hasta:%Y%m%d}.pdf"
    return Response(contenido, media_type=PDF, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/anomalias", response_model=list[AnomaliaSalida], summary="Anomalías para revisión humana")
def listar_anomalias(
    conn: Conn,
    _: SesionAdmin,
    estado: Literal["abiertas", "resueltas", "todas"] = "abiertas",
    severidad: Severidad | None = None,
    limite: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    resuelta = {"abiertas": False, "resueltas": True, "todas": None}[estado]
    return [asdict(a) for a in reportes_bd.anomalias(conn, resuelta=resuelta, severidad=severidad, limite=limite)]
