"""Administración: búsqueda de ventas (filtros de Nexus), usuarios, clientes y vehículos, e importación CSV.
Todo exige rol ADMIN; las altas y bajas quedan en la auditoría (sin datos personales)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ...modelos import Rol
from ...servicios import clientes, consulta_facturas, personal, vehiculos
from ...servicios.consulta_ventas import FiltroVentas, buscar_ventas, listar_usuarios
from ...servicios.importacion_csv import (
    COLUMNAS_CLIENTES,
    COLUMNAS_VEHICULOS,
    importar_clientes,
    importar_vehiculos,
)
from ..dependencias import Conn, SesionAdmin, exigir_rango
from ..esquemas import (
    BusquedaVentasSalida,
    ClienteSalida,
    ClientesPaginaSalida,
    CorreoClienteEntrada,
    Error,
    EstadoFacturacion,
    FormaPago,
    ImportacionSalida,
    UsuarioEntrada,
    UsuarioListadoSalida,
    VehiculoEntrada,
    VehiculoSalida,
    VehiculosPaginaSalida,
)
from .clientes import salida as cliente_salida

router = APIRouter(tags=["administración"])
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
    exigir_rango(desde, hasta, hasta_incluido=False)
    filtro = FiltroVentas(
        desde=desde, hasta=hasta, surtidor_id=surtidor_id, lado=lado, producto=producto, forma_pago=forma_pago,
        estado_facturacion=estado_facturacion, placa=placa, cliente_documento=cliente, id_externo=id_externo,
        factura=factura, vendedor_id=vendedor_id,
    )  # fmt: skip
    return asdict(buscar_ventas(conn, filtro, limite=limite, desplazamiento=desplazamiento))


@router.get("/usuarios", response_model=list[UsuarioListadoSalida], summary="Usuarios (para filtrar por vendedor)")
def usuarios(conn: Conn, _: SesionAdmin) -> Any:
    return [asdict(u) for u in listar_usuarios(conn)]


@router.post(
    "/usuarios",
    status_code=status.HTTP_201_CREATED,
    response_model=UsuarioListadoSalida,
    responses={409: {"model": Error}, 422: {"model": Error}},
    summary="Crear un usuario (bombero o administrador) con su PIN",
)
def crear_usuario(entrada: UsuarioEntrada, conn: Conn, sesion: SesionAdmin) -> Any:
    # PinInvalido, UsuarioInvalido (422) y UsuarioYaExiste (409) los traduce app.ERRORES; ninguno lleva el PIN.
    u = personal.crear(conn, entrada.usuario, entrada.nombre, Rol(entrada.rol), entrada.pin, por=sesion.usuario_id)
    return asdict(u)


def _usuario_activo(conn: Conn, sesion: SesionAdmin, usuario_id: int, activo: bool) -> Any:
    u = personal.cambiar_activo(conn, usuario_id, activo, por=sesion.usuario_id)  # DesactivarseASiMismo -> 409
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe ese usuario")
    return asdict(u)


@router.post(
    "/usuarios/{usuario_id}/desactivar",
    response_model=UsuarioListadoSalida,
    responses={404: {"model": Error}, 409: {"model": Error}},
    summary="Baja lógica: no puede entrar y sus sesiones abiertas se cortan; conserva su historial",
)
def desactivar_usuario(usuario_id: int, conn: Conn, sesion: SesionAdmin) -> Any:
    return _usuario_activo(conn, sesion, usuario_id, False)


@router.post(
    "/usuarios/{usuario_id}/activar",
    response_model=UsuarioListadoSalida,
    responses={404: {"model": Error}},
    summary="Reactivar un usuario",
)
def activar_usuario(usuario_id: int, conn: Conn, sesion: SesionAdmin) -> Any:
    return _usuario_activo(conn, sesion, usuario_id, True)


# ------------------------------------------------------------------------------------------ facturas
class FacturaFilaSalida(BaseModel):
    id: int
    numero: str
    fecha_emision: datetime
    cliente_nombre: str
    cliente_tipo: str
    cliente_documento: str
    vendedor: str
    estado: str
    error: str | None
    total: Decimal
    tipo: Literal["FACTURA", "NOTA_CREDITO"]
    ambiente: Literal["1", "2"]
    referencia: str | None
    anulada_por: str | None
    anulable: bool


class FacturasPaginaSalida(BaseModel):
    filas: list[FacturaFilaSalida]
    total: int


@router.get("/facturas", response_model=FacturasPaginaSalida, summary="Historial de facturas y notas crédito")
def listar_facturas(
    request: Request,
    conn: Conn,
    _: SesionAdmin,
    desde: Annotated[datetime, Query(description="hora local, incluida")],
    hasta: Annotated[datetime, Query(description="hora local, excluida")],
    estado: Literal["EN_PROCESO", "FACTURADO", "RECHAZADO", "INCIERTO"] | None = None,
    tipo: Literal["FACTURA", "NOTA_CREDITO"] | None = None,
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
    desplazamiento: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    exigir_rango(desde, hasta, hasta_incluido=False)
    filas, total = consulta_facturas.listar_facturas(
        conn, desde, hasta, estado=estado, tipo=tipo, ambiente_actual=request.app.state.anulacion.ambiente_actual,
        limite=limite, desplazamiento=desplazamiento,
    )  # fmt: skip
    return {"total": total, "filas": [asdict(f) for f in filas]}


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


@router.post(
    "/clientes/{cliente_id}/correo",
    response_model=ClienteSalida,
    responses={404: {"model": Error}, 422: {"model": Error}},
    summary="Corregir el correo del cliente (p. ej. tras un rebote); luego se reenvía la factura",
)
def corregir_correo_cliente(
    cliente_id: int, datos: CorreoClienteEntrada, conn: Conn, sesion: SesionAdmin
) -> ClienteSalida:
    c = clientes.cambiar_correo(conn, cliente_id, datos.email, usuario_id=sesion.usuario_id)  # inválido -> 422
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no existe ese cliente")
    return cliente_salida(c)


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
