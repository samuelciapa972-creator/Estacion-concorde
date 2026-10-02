"""PIN de los usuarios (hash argon2id) y tokens JWT de sesión.

Ingreso: usuario + PIN. Tras `max_intentos_pin` fallos seguidos el usuario queda bloqueado `bloqueo_minutos`
(columnas `intentos_fallidos` y `bloqueado_hasta`). El mismo error para "usuario no existe" y "PIN incorrecto",
y un hash de relleno en el primer caso, para no revelar qué usuarios existen.

Solo se verifican hashes argon2. La base también admite bcrypt (migración 0002), pero este código no los
verifica: un usuario con hash bcrypt no puede entrar hasta que se le asigne un PIN nuevo.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..modelos import Rol
from ..servicios._bd import Conexion, auditar, exigir_autocommit
from .ajustes import Ajustes

ALGORITMO = "HS256"
_PIN = re.compile(r"^[0-9]{4,8}$")
_hasher = PasswordHasher()  # argon2id con los parámetros recomendados por la librería


class PinInvalido(ValueError):
    """El PIN no cumple el formato (4 a 8 dígitos)."""


class CredencialesInvalidas(Exception):
    """Usuario inexistente, inactivo o PIN incorrecto (a propósito, sin decir cuál)."""


class UsuarioBloqueado(Exception):
    def __init__(self, hasta: datetime):
        super().__init__("demasiados intentos fallidos: el usuario está bloqueado temporalmente")
        self.hasta = hasta


class TokenInvalido(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Sesion:
    usuario_id: int
    usuario: str
    nombre: str
    rol: Rol


def hash_pin(pin: str) -> str:
    if not _PIN.match(pin):
        raise PinInvalido("el PIN debe tener entre 4 y 8 dígitos")
    return _hasher.hash(pin)


@cache
def _hash_de_relleno() -> str:
    return _hasher.hash("00000000")


def _pin_correcto(pin_hash: str, pin: str) -> bool:
    try:
        return _hasher.verify(pin_hash, pin)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def autenticar(conn: Conexion, usuario: str, pin: str, ajustes: Ajustes) -> Sesion:
    """Verifica usuario y PIN y lleva la cuenta de intentos. Lanza CredencialesInvalidas o UsuarioBloqueado."""
    exigir_autocommit(conn)
    usuario = usuario.strip().lower()
    with conn.transaction():
        fila = conn.execute(
            """SELECT id, nombre, rol, pin_hash, activo, bloqueado_hasta, bloqueado_hasta > now()
               FROM usuario WHERE usuario = %s FOR UPDATE""",
            (usuario,),
        ).fetchone()
        if fila is None or not fila[4]:
            _pin_correcto(_hash_de_relleno(), pin)  # mismo tiempo de respuesta que con un usuario real
            raise CredencialesInvalidas
        usuario_id, nombre, rol, pin_hash, _, bloqueado_hasta, bloqueado = fila
        if bloqueado:
            raise UsuarioBloqueado(bloqueado_hasta)
        if _pin_correcto(pin_hash, pin):
            conn.execute(
                "UPDATE usuario SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = %s", (usuario_id,)
            )
            auditar(conn, "INGRESO", "usuario", usuario_id, usuario_id=usuario_id)
            return Sesion(usuario_id, usuario, nombre, Rol(rol))
        intentos = conn.execute(
            """UPDATE usuario SET intentos_fallidos = intentos_fallidos + 1 WHERE id = %s
               RETURNING intentos_fallidos""",
            (usuario_id,),
        ).fetchone()[0]  # type: ignore[index]
        bloquear = intentos >= ajustes.max_intentos_pin
        if bloquear:
            conn.execute(
                """UPDATE usuario SET intentos_fallidos = 0, bloqueado_hasta = now() + make_interval(mins => %s)
                   WHERE id = %s""",
                (ajustes.bloqueo_minutos, usuario_id),
            )
        auditar(conn, "USUARIO_BLOQUEADO" if bloquear else "INGRESO_FALLIDO", "usuario", usuario_id)
    # Fuera de la transacción: el intento fallido ya quedó guardado (lanzar dentro lo desharía).
    raise CredencialesInvalidas


def emitir_token(sesion: Sesion, ajustes: Ajustes) -> tuple[str, int]:
    """Token firmado y su duración en segundos."""
    ahora = datetime.now(UTC)
    duracion = timedelta(minutes=ajustes.jwt_minutos)
    datos = {
        "sub": str(sesion.usuario_id),
        "rol": sesion.rol.value,
        "iat": ahora,
        "exp": ahora + duracion,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(datos, ajustes.jwt_secreto, algorithm=ALGORITMO), int(duracion.total_seconds())


def leer_token(token: str, ajustes: Ajustes) -> int:
    """Id del usuario del token. Lanza TokenInvalido si la firma no cuadra o venció."""
    try:
        datos = jwt.decode(token, ajustes.jwt_secreto, algorithms=[ALGORITMO], options={"require": ["exp", "sub"]})
        return int(datos["sub"])
    except (jwt.PyJWTError, ValueError) as e:
        raise TokenInvalido(str(e)) from None


def sesion_vigente(conn: Conexion, usuario_id: int) -> Sesion | None:
    """El usuario del token sigue activo y no bloqueado (desactivarlo corta sus sesiones de inmediato)."""
    fila = conn.execute(
        """SELECT id, usuario, nombre, rol FROM usuario
           WHERE id = %s AND activo AND (bloqueado_hasta IS NULL OR bloqueado_hasta <= now())""",
        (usuario_id,),
    ).fetchone()
    return None if fila is None else Sesion(fila[0], fila[1], fila[2], Rol(fila[3]))
