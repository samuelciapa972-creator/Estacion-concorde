"""Lo que usa la pantalla del bombero: catálogos, ventas pendientes y venta manual."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Query, status

from ...facturacion.documentos import TIPOS_DOCUMENTO
from ...servicios.emision import MEDIOS_DE_PAGO
from ...servicios.ventas import listar_pendientes, registrar_venta_manual
from ..dependencias import Conn, SesionActual
from ..esquemas import (
    CatalogosSalida,
    Creado,
    Error,
    FormaPago,
    PendienteSalida,
    ProductoSalida,
    SurtidorSalida,
    VentaManualEntrada,
)

router = APIRouter(tags=["pista"])


@router.get("/catalogos", response_model=CatalogosSalida, summary="Surtidores, productos y opciones de los formularios")
def catalogos(conn: Conn, _: SesionActual) -> CatalogosSalida:
    surtidores = conn.execute(
        "SELECT id, marca, codigo_externo, descripcion FROM surtidor WHERE activo ORDER BY id"
    ).fetchall()
    productos = conn.execute("SELECT id, codigo, nombre FROM producto ORDER BY codigo").fetchall()
    return CatalogosSalida(
        surtidores=[SurtidorSalida(id=f[0], marca=f[1], codigo=f[2], descripcion=f[3]) for f in surtidores],
        productos=[ProductoSalida(id=f[0], codigo=f[1], nombre=f[2]) for f in productos],
        medios_pago=sorted(MEDIOS_DE_PAGO),
        tipos_documento=sorted(TIPOS_DOCUMENTO),
    )


@router.get(
    "/despachos/pendientes",
    response_model=list[PendienteSalida],
    summary="Despachos y ventas manuales pendientes de facturar (los más recientes primero)",
)
def pendientes(
    conn: Conn,
    _: SesionActual,
    surtidor_id: int | None = None,
    lado: Annotated[str | None, Query(min_length=1, max_length=1)] = None,
    desde: Annotated[datetime | None, Query(description="hora local de la estación")] = None,
    forma_pago: Literal["CONTADO", "CREDITO", "TODAS"] = "CONTADO",
    limite: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[PendienteSalida]:
    filas = listar_pendientes(
        conn,
        surtidor_id=surtidor_id,
        lado=lado,
        desde=desde,
        forma_pago=None if forma_pago == "TODAS" else forma_pago,
        limite=limite,
    )
    return [
        PendienteSalida(
            origen_tipo=p.origen.tipo,
            origen_id=p.origen.id,
            surtidor_id=p.surtidor_id,
            lado=p.lado,
            producto=p.producto,
            volumen=p.volumen,
            valor=p.valor,
            ppu=p.ppu,
            forma_pago=cast(FormaPago, p.forma_pago),  # CHECK de la base; Pydantic lo valida igual
            fecha=p.fecha,
            placa=p.placa,
        )  # fmt: skip
        for p in filas
    ]


@router.post(
    "/ventas-manuales",
    response_model=Creado,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": Error}},
    summary="Venta digitada por el bombero (modo manual)",
)
def crear_venta_manual(datos: VentaManualEntrada, conn: Conn, sesion: SesionActual) -> Creado:
    venta_id = registrar_venta_manual(
        conn,
        usuario_id=sesion.usuario_id,
        surtidor_id=datos.surtidor_id,
        lado=datos.lado,
        producto=datos.producto,
        volumen=datos.volumen,
        valor=datos.valor,
        ppu=datos.ppu,
        forma_pago=datos.forma_pago,
    )
    return Creado(id=venta_id)
