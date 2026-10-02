from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..dependencias import AjustesApi, Conn, SesionActual, ip_cliente
from ..esquemas import Error, LoginEntrada, TokenSalida, UsuarioSalida
from ..seguridad import CredencialesInvalidas, UsuarioBloqueado, autenticar, emitir_token

router = APIRouter(prefix="/auth", tags=["sesión"])


def _usuario(s) -> UsuarioSalida:
    return UsuarioSalida(id=s.usuario_id, usuario=s.usuario, nombre=s.nombre, rol=s.rol.value)


@router.post(
    "/login",
    response_model=TokenSalida,
    responses={401: {"model": Error}, 423: {"model": Error}, 429: {"model": Error}},
    summary="Ingreso con usuario y PIN",
)
def login(datos: LoginEntrada, request: Request, conn: Conn, aj: AjustesApi) -> TokenSalida:
    if not request.app.state.limite_login.permitir(ip_cliente(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "demasiados intentos desde este equipo; espere")
    try:
        sesion = autenticar(conn, datos.usuario, datos.pin, aj)
    except CredencialesInvalidas:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuario o PIN incorrectos") from None
    except UsuarioBloqueado:
        raise HTTPException(
            status.HTTP_423_LOCKED, "demasiados intentos fallidos: usuario bloqueado temporalmente"
        ) from None
    token, segundos = emitir_token(sesion, aj)
    return TokenSalida(access_token=token, expira_en=segundos, usuario=_usuario(sesion))


@router.get("/yo", response_model=UsuarioSalida, summary="Usuario de la sesión actual")
def yo(sesion: SesionActual) -> UsuarioSalida:
    return _usuario(sesion)
