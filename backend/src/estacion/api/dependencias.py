"""Dependencias comunes: conexión a la base (del pool), sesión del usuario y control de rol."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..modelos import Rol
from ..servicios._bd import Conexion
from .ajustes import Ajustes
from .seguridad import Sesion, TokenInvalido, leer_token, sesion_vigente

_bearer = HTTPBearer(auto_error=False, description="token de POST /auth/login")
_NO_AUTENTICADO = {"WWW-Authenticate": "Bearer"}


def conexion(request: Request) -> Iterator[Conexion]:
    """Conexión en modo autocommit: cada servicio abre su propia transacción."""
    with request.app.state.pool.connection() as conn:
        yield conn


def ajustes(request: Request) -> Ajustes:
    return request.app.state.ajustes


Conn = Annotated[Conexion, Depends(conexion)]
AjustesApi = Annotated[Ajustes, Depends(ajustes)]


def sesion_actual(
    conn: Conn, aj: AjustesApi, credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Sesion:
    if credenciales is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "falta el token de sesión", headers=_NO_AUTENTICADO)
    try:
        usuario_id = leer_token(credenciales.credentials, aj)
    except TokenInvalido:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "sesión vencida o inválida: ingrese de nuevo", headers=_NO_AUTENTICADO
        ) from None
    sesion = sesion_vigente(conn, usuario_id)
    if sesion is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "el usuario está inactivo o bloqueado", headers=_NO_AUTENTICADO
        )
    return sesion


SesionActual = Annotated[Sesion, Depends(sesion_actual)]


def requiere_admin(sesion: SesionActual) -> Sesion:
    if sesion.rol is not Rol.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "solo un administrador puede hacer esto")
    return sesion


SesionAdmin = Annotated[Sesion, Depends(requiere_admin)]


def ip_cliente(request: Request) -> str:
    # Sin proxy delante (banco de pruebas). Detrás de uno, usar la cabecera que ese proxy garantice.
    return request.client.host if request.client else "desconocida"


MAX_DIAS = 400  # rango máximo de una consulta o reporte


def exigir_rango(desde: date, hasta: date, *, hasta_incluido: bool) -> None:
    """422 si el rango está al revés o pasa de MAX_DIAS. `hasta_incluido`: fechas de reporte (desde == hasta
    es un día); con horas, `hasta` es excluida y debe ser posterior a `desde`."""
    if hasta < desde if hasta_incluido else hasta <= desde:
        detalle = "`hasta` es anterior a `desde`" if hasta_incluido else "`hasta` debe ser posterior a `desde`"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detalle)
    if (hasta - desde).days > MAX_DIAS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"el rango máximo es de {MAX_DIAS} días")
