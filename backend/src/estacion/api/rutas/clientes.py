"""Clientes: búsqueda y registro desde la pista, y autorregistro público por QR."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request, status

from ...servicios import clientes
from ...servicios.clientes import ClienteRegistrado, ClienteYaExiste
from ..dependencias import Conn, SesionActual, ip_cliente
from ..esquemas import (
    CaptchaSalida,
    ClienteEntrada,
    ClienteSalida,
    ConfiguracionPublicaSalida,
    Error,
    EstacionPublicaSalida,
    RegistroPublicoEntrada,
    RegistroPublicoSalida,
    TipoDocumento,
)

router = APIRouter(tags=["clientes"])

MENSAJE_REGISTRO = "Gracias. Si los datos son válidos, ya puede pedir su factura electrónica en la estación."


def enmascarar_correo(correo: str) -> str:
    """Lo justo para que el cliente reconozca su correo en la pista, sin mostrarlo completo.

    >>> enmascarar_correo("ana.perez@example.com")
    'an*******@example.com'
    >>> enmascarar_correo("")
    ''
    """
    usuario, arroba, dominio = correo.partition("@")
    if not arroba:
        return ""
    return usuario[:2] + "*" * max(len(usuario) - 2, 1) + "@" + dominio


def salida(c: ClienteRegistrado) -> ClienteSalida:
    return ClienteSalida(
        id=c.id, tipo_documento=cast(TipoDocumento, c.tipo_documento), numero_documento=c.numero_documento,
        digito_verificacion=c.digito_verificacion, nombre_mostrar=c.nombre_mostrar, nombres=c.nombres,
        apellidos=c.apellidos, email_enmascarado=enmascarar_correo(c.email), activo=c.activo,
    )  # fmt: skip


def _normalizar(datos: ClienteEntrada):
    return clientes.normalizar(
        tipo_documento=datos.tipo_documento,
        numero_documento=datos.numero_documento,
        nombres=datos.nombres,
        apellidos=datos.apellidos,
        email=datos.email,
        digito_verificacion=datos.digito_verificacion,
        autoriza_tratamiento=datos.autoriza_tratamiento,
    )


@router.get(
    "/clientes/buscar",
    response_model=ClienteSalida,
    responses={404: {"model": Error}},
    summary="Buscar un cliente por tipo y número de documento",
)
def buscar(
    conn: Conn,
    _: SesionActual,
    tipo: TipoDocumento,
    numero: Annotated[str, Query(min_length=3, max_length=20)],
) -> ClienteSalida:
    cliente = clientes.buscar(conn, tipo, numero)
    if cliente is None or not cliente.activo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no hay un cliente registrado con ese documento")
    return salida(cliente)


@router.post(
    "/clientes",
    response_model=ClienteSalida,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": Error}, 422: {"model": Error}},
    summary="Registrar un cliente desde la pista (exige la autorización de datos)",
)
def crear(datos: ClienteEntrada, conn: Conn, sesion: SesionActual) -> ClienteSalida:
    try:
        cliente = clientes.registrar(conn, _normalizar(datos), canal="PISTA", usuario_id=sesion.usuario_id)
    except ClienteYaExiste as e:
        raise HTTPException(status.HTTP_409_CONFLICT, {"mensaje": str(e), "cliente_id": e.cliente_id}) from None
    return salida(cliente)


def verificar_captcha(request: Request, token: str | None) -> bool:
    """Sin captcha configurado (banco de pruebas) se acepta; con captcha, falla cerrado (ver `api/captcha.py`)."""
    captcha = request.app.state.ajustes.captcha
    return captcha is None or captcha.verificar(token, ip_cliente(request))


@router.get(
    "/publico/configuracion",
    response_model=ConfiguracionPublicaSalida,
    summary="Lo que necesita la página pública de registro (captcha)",
    tags=["público"],
)
def configuracion_publica(request: Request, conn: Conn) -> ConfiguracionPublicaSalida:
    c = request.app.state.ajustes.captcha
    fila = conn.execute("SELECT coalesce(nombre_comercial, razon_social) FROM estacion WHERE activa").fetchone()
    return ConfiguracionPublicaSalida(
        captcha=None if c is None else CaptchaSalida(proveedor=c.proveedor, clave_sitio=c.clave_sitio),
        estacion=None if fila is None else EstacionPublicaSalida(nombre=fila[0]),
    )


@router.post(
    "/publico/registro",
    response_model=RegistroPublicoSalida,
    status_code=status.HTTP_202_ACCEPTED,
    responses={422: {"model": Error}, 429: {"model": Error}},
    summary="Autorregistro del cliente por QR (público, con límite de tasa)",
    tags=["público"],
)
def registro_publico(datos: RegistroPublicoEntrada, request: Request, conn: Conn) -> RegistroPublicoSalida:
    """Respuesta IGUAL si el documento ya estaba registrado: así no se puede averiguar quién es cliente.
    Un registro público nunca modifica los datos de un cliente existente."""
    if not request.app.state.limite_registro.permitir(ip_cliente(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "demasiados registros desde este equipo; espere")
    if not verificar_captcha(request, datos.captcha):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no se pudo verificar que es una persona")
    try:
        clientes.registrar(conn, _normalizar(datos), canal="PUBLICO")
    except ClienteYaExiste:
        pass
    return RegistroPublicoSalida(mensaje=MENSAJE_REGISTRO)
