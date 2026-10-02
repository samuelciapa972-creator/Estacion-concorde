"""Administración: búsqueda de ventas (filtros de Nexus), usuarios, clientes y vehículos, e importación CSV.
Todo exige rol ADMIN; las altas y bajas quedan en la auditoría (sin datos personales)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool

from ...servicios import clientes, vehiculos
from ...servicios.consulta_ventas import FiltroVentas, buscar_ventas, listar_usuarios
from ...servicios.importacion_csv import (
    COLUMNAS_CLIENTES,
    COLUMNAS_VEHICULOS,
    importar_clientes,
    importar_vehiculos,
)
from ..dependencias import Conn, SesionAdmin
from ..esquemas import (
    BusquedaVentasSalida,
    ClienteSalida,
    ClientesPaginaSalida,
    Error,
    EstadoFacturacion,
    FormaPago,
    ImportacionSalida,
    UsuarioListadoSalida,
    VehiculoEntrada,
    VehiculoSalida,
    VehiculosPaginaSalida,
)
from .clientes import salida as cliente_salida

router = APIRouter(tags=["administración"])
MAX_DIAS = 400
MAX_CSV = 2 * 1024 * 1024


def _activo(estado: Literal["activos", "inactivos", "todos"]) -> bool | None:
    return {"activos": True, "inactivos": False, "todos": None}[estado]


# ------------------------------------------------------------------------------------------ ventas
@router.get("/ventas", response_model=BusquedaVentasSalida, summary="Buscar ventas (filtros de Nexus) con totales")
def ventas(
    conn: Conn,
    _: SesionAdmin,
    desde: Annotated[datetime, Query(description="hora local, incluida")],
    hasta: Annotated[datetime, Query(description="hora local, excluida")],
    surtidor_id: Annotated[int | None, Query(description="equipo")] = None,
    lado: Annotated[str | None, Query(max_length=1)] = None,
    producto: Annotated[str | None, Query(max_length=40)] = None,
    forma_pago: FormaPago | None = None,
    estado_facturacion: EstadoFacturacion | None = None,
    placa: Annotated[str | None, Query(max_length=40, description="parte de la placa")] = None,
    cliente: Annotated[str | None, Query(max_length=20, description="número de documento")] = None,
    id_externo: Annotated[int | None, Query(description="número del despacho en el surtidor")] = None,
    factura: Annotated[str | None, Query(max_length=40, description="número completo, p. ej. SETP1")] = None,
    vendedor_id: int | None = None,
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
    desplazamiento: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    if hasta <= desde:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "`hasta` debe ser posterior a `desde`")
    if (hasta - desde).days > MAX_DIAS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"el rango máximo es de {MAX_DIAS} días")
    filtro = FiltroVentas(
        desde=desde, hasta=hasta, surtidor_id=surtidor_id, lado=lado, producto=producto, forma_pago=forma_pago,
        estado_facturacion=estado_facturacion, placa=placa, cliente_documento=cliente, id_externo=id_externo,
        factura=factura, vendedor_id=vendedor_id,
    )  # fmt: skip
    return asdict(buscar_ventas(conn, filtro, limite=limite, desplazamiento=desplazamiento))


@router.get("/usuarios", response_model=list[UsuarioListadoSalida], summary="Usuarios (para filtrar por vendedor)")
def usuarios(conn: Conn, _: SesionAdmin) -> Any:
    return [asdict(u) for u in listar_usuarios(conn)]


# ------------------------------------------------------------------------------------------ clientes
@router.get("/clientes", response_model=ClientesPaginaSalida, summary="Listar y buscar clientes")
def listar_clientes(
    conn: Conn,
    _: SesionAdmin,
    texto: Annotated[str | None, Query(max_length=100, description="inicio del documento o parte del nombre")] = None,
    estado: Literal["activos", "inactivos", "todos"] = "activos",
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    desplazamiento: Annotated[int, Query(ge=0)] = 0,
) -> ClientesPaginaSalida:
    filas, total = clientes.listar(
        conn, texto=texto, activo=_activo(estado), limite=limite, desplazamiento=desplazamiento
    )
    return ClientesPaginaSalida(filas=[cliente_salida(c) for c in filas], total=total)


def _cliente_activo(conn: Conn, sesion: SesionAdmin, cliente_id: int, activo: bool) -> ClienteSalida:
    c = clientes.cambiar_activo(conn, cliente_id, activo, usuario_id=sesion.usuario_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe ese cliente")
    return cliente_salida(c)


@router.post(
    "/clientes/{cliente_id}/desactivar",
    response_model=ClienteSalida,
    responses={404: {"model": Error}},
    summary="Baja lógica: no se le factura ni aparece en la pista; se conserva su historial",
)
def desactivar_cliente(cliente_id: int, conn: Conn, sesion: SesionAdmin) -> ClienteSalida:
    return _cliente_activo(conn, sesion, cliente_id, False)


@router.post(
    "/clientes/{cliente_id}/activar",
    response_model=ClienteSalida,
    responses={404: {"model": Error}},
    summary="Reactivar un cliente",
)
def activar_cliente(cliente_id: int, conn: Conn, sesion: SesionAdmin) -> ClienteSalida:
    return _cliente_activo(conn, sesion, cliente_id, True)


# ------------------------------------------------------------------------------------------ vehículos
def _vehiculo(v: vehiculos.VehiculoRegistrado) -> VehiculoSalida:
    return VehiculoSalida(**{**asdict(v), "tipo": cast(Literal["PLACA", "EQUIPO_TEXTO"], v.tipo)})


@router.get("/vehiculos", response_model=VehiculosPaginaSalida, summary="Listar y buscar vehículos")
def listar_vehiculos(
    conn: Conn,
    _: SesionAdmin,
    texto: Annotated[str | None, Query(max_length=60, description="parte de la placa")] = None,
    cliente_id: int | None = None,
    estado: Literal["activos", "inactivos", "todos"] = "activos",
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    desplazamiento: Annotated[int, Query(ge=0)] = 0,
) -> VehiculosPaginaSalida:
    filas, total = vehiculos.listar(
        conn, texto=texto, cliente_id=cliente_id, activo=_activo(estado), limite=limite,
        desplazamiento=desplazamiento,
    )  # fmt: skip
    return VehiculosPaginaSalida(filas=[_vehiculo(v) for v in filas], total=total)


@router.post(
    "/vehiculos",
    response_model=VehiculoSalida,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": Error}, 422: {"model": Error}},
    summary="Registrar un vehículo o equipo de un cliente",
)
def crear_vehiculo(datos: VehiculoEntrada, conn: Conn, sesion: SesionAdmin) -> VehiculoSalida:
    try:
        v = vehiculos.registrar(
            conn, datos.placa, datos.cliente_id, descripcion=datos.descripcion, usuario_id=sesion.usuario_id
        )
    except vehiculos.VehiculoYaExiste as e:
        raise HTTPException(status.HTTP_409_CONFLICT, {"mensaje": str(e), "vehiculo_id": e.vehiculo_id}) from None
    return _vehiculo(v)


def _vehiculo_activo(conn: Conn, sesion: SesionAdmin, vehiculo_id: int, activo: bool) -> VehiculoSalida:
    v = vehiculos.cambiar_activo(conn, vehiculo_id, activo, usuario_id=sesion.usuario_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe ese vehículo")
    return _vehiculo(v)


@router.post("/vehiculos/{vehiculo_id}/desactivar", response_model=VehiculoSalida, responses={404: {"model": Error}})
def desactivar_vehiculo(vehiculo_id: int, conn: Conn, sesion: SesionAdmin) -> VehiculoSalida:
    return _vehiculo_activo(conn, sesion, vehiculo_id, False)


@router.post("/vehiculos/{vehiculo_id}/activar", response_model=VehiculoSalida, responses={404: {"model": Error}})
def activar_vehiculo(vehiculo_id: int, conn: Conn, sesion: SesionAdmin) -> VehiculoSalida:
    return _vehiculo_activo(conn, sesion, vehiculo_id, True)


# ------------------------------------------------------------------------------------------ importación CSV
def _cuerpo_csv(columnas: tuple[str, ...]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {"text/csv": {"schema": {"type": "string"}, "example": ";".join(columnas) + "\n"}},
        }
    }


async def _leer_csv(request: Request) -> bytes:
    if int(request.headers.get("content-length") or 0) > MAX_CSV:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "el archivo supera 2 MB: divídalo")
    contenido = await request.body()
    if len(contenido) > MAX_CSV:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "el archivo supera 2 MB: divídalo")
    return contenido


@router.post(
    "/clientes/importar",
    response_model=ImportacionSalida,
    responses={413: {"model": Error}, 422: {"model": Error}},
    openapi_extra=_cuerpo_csv(COLUMNAS_CLIENTES),
    summary="Importar clientes desde CSV (en seco por defecto; exige autorización de datos con fecha)",
)
async def importar_clientes_csv(request: Request, conn: Conn, sesion: SesionAdmin, en_seco: bool = True) -> Any:
    contenido = await _leer_csv(request)
    r = await run_in_threadpool(importar_clientes, conn, contenido, en_seco=en_seco, usuario_id=sesion.usuario_id)
    return asdict(r)


@router.post(
    "/vehiculos/importar",
    response_model=ImportacionSalida,
    responses={413: {"model": Error}, 422: {"model": Error}},
    openapi_extra=_cuerpo_csv(COLUMNAS_VEHICULOS),
    summary="Importar vehículos desde CSV (en seco por defecto; los dueños deben existir)",
)
async def importar_vehiculos_csv(request: Request, conn: Conn, sesion: SesionAdmin, en_seco: bool = True) -> Any:
    contenido = await _leer_csv(request)
    r = await run_in_threadpool(importar_vehiculos, conn, contenido, en_seco=en_seco, usuario_id=sesion.usuario_id)
    return asdict(r)
