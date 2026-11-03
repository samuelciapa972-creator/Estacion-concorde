"""Personal (usuarios de la API) administrado desde la pantalla: alta y baja lógica.

No hay borrado: cada factura y venta manual guarda quién la hizo, así que un usuario con historial no puede
desaparecer. Desactivarlo le impide entrar y corta sus sesiones abiertas (`sesion_vigente`). El PIN se guarda solo
como hash argon2id y nunca va a la auditoría.
"""

from __future__ import annotations

from psycopg import errors

from ..api.seguridad import hash_pin
from ..modelos import Rol
from ._bd import Conexion, auditar, exigir_autocommit
from .consulta_ventas import UsuarioListado

_COLUMNAS = "id, usuario, nombre, rol, activo"


class UsuarioYaExiste(Exception):
    pass


class UsuarioInvalido(ValueError):
    pass


class DesactivarseASiMismo(Exception):
    pass


def crear(conn: Conexion, usuario: str, nombre: str, rol: Rol, pin: str, *, por: int) -> UsuarioListado:
    """Lanza PinInvalido, UsuarioInvalido o UsuarioYaExiste."""
    exigir_autocommit(conn)
    pin_hash = hash_pin(pin, rol)
    try:
        with conn.transaction():
            fila = conn.execute(
                f"INSERT INTO usuario (usuario, nombre, rol, pin_hash) VALUES (%s, %s, %s, %s) RETURNING {_COLUMNAS}",
                (usuario.strip().lower(), nombre.strip(), rol.value, pin_hash),
            ).fetchone()
            assert fila is not None
            auditar(conn, "USUARIO_CREADO", "usuario", fila[0], usuario_id=por, detalle={"rol": rol.value})
    except errors.UniqueViolation:
        raise UsuarioYaExiste(f"ya existe el usuario {usuario.strip().lower()!r}") from None
    except errors.CheckViolation:
        raise UsuarioInvalido("usuario inválido: 3 a 40 caracteres (minúsculas, números, punto, guion)") from None
    return UsuarioListado(*fila)


def cambiar_activo(conn: Conexion, usuario_id: int, activo: bool, *, por: int) -> UsuarioListado | None:
    """None si no existe. Lanza DesactivarseASiMismo: un administrador no puede dejar la estación sin acceso."""
    if not activo and usuario_id == por:
        raise DesactivarseASiMismo("no puede desactivar su propio usuario")
    exigir_autocommit(conn)
    with conn.transaction():
        fila = conn.execute(
            f"UPDATE usuario SET activo = %s WHERE id = %s RETURNING {_COLUMNAS}", (activo, usuario_id)
        ).fetchone()
        if fila is None:
            return None
        auditar(conn, "USUARIO_ACTIVADO" if activo else "USUARIO_DESACTIVADO", "usuario", usuario_id, usuario_id=por)
    return UsuarioListado(*fila)
