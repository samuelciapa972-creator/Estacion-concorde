"""Usuarios de la API: `python -m estacion.usuarios crear|pin|desbloquear|desactivar ...`.

El PIN se pide por teclado sin mostrarlo (o se lee de la entrada estándar con `--pin-stdin`, para scripts) y se
guarda solo como hash argon2id. Nunca va en la línea de comandos (quedaría en el historial del shell).

    python -m estacion.usuarios crear --usuario bombero1 --nombre "Bombero Uno" --rol BOMBERO
    python -m estacion.usuarios pin --usuario bombero1
    python -m estacion.usuarios desbloquear --usuario bombero1
    python -m estacion.usuarios desactivar --usuario bombero1
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import psycopg

from .api.seguridad import PinInvalido, hash_pin
from .modelos import Rol
from .servicios._bd import auditar


def _leer_pin(desde_stdin: bool) -> str:
    if desde_stdin:
        return sys.stdin.readline().strip()
    pin = getpass.getpass("PIN (4 a 8 dígitos): ")
    if getpass.getpass("Repita el PIN: ") != pin:
        raise PinInvalido("los dos PIN no coinciden")
    return pin


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m estacion.usuarios", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="accion", required=True)
    crear = sub.add_parser("crear", help="crea un usuario")
    crear.add_argument("--nombre", required=True)
    crear.add_argument("--rol", choices=[r.value for r in Rol], default=Rol.BOMBERO.value)
    for nombre, ayuda in (
        ("pin", "cambia el PIN (y desbloquea)"),
        ("desbloquear", "quita el bloqueo por intentos fallidos"),
        ("desactivar", "el usuario ya no puede entrar; sus sesiones abiertas dejan de valer"),
    ):
        sub.add_parser(nombre, help=ayuda)
    for p in sub.choices.values():
        p.add_argument("--usuario", required=True)
        if p.prog.endswith(("crear", "pin")):
            p.add_argument("--pin-stdin", action="store_true", help="lee el PIN de la entrada estándar")
    args = ap.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: falta la variable de entorno DATABASE_URL", file=sys.stderr)
        return 2
    usuario = args.usuario.strip().lower()
    try:
        pin_hash = hash_pin(_leer_pin(args.pin_stdin)) if args.accion in ("crear", "pin") else None
    except PinInvalido as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    with psycopg.connect(dsn, autocommit=True) as conn, conn.transaction():
        if args.accion == "crear":
            try:
                fila = conn.execute(
                    "INSERT INTO usuario (usuario, nombre, rol, pin_hash) VALUES (%s, %s, %s, %s) RETURNING id",
                    (usuario, args.nombre.strip(), args.rol, pin_hash),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                print(f"ERROR: ya existe el usuario {usuario!r}", file=sys.stderr)
                return 1
            except psycopg.errors.CheckViolation:
                print("ERROR: usuario inválido (3 a 40 caracteres: minúsculas, números, punto, guion)", file=sys.stderr)
                return 1
        else:
            cambios = {
                "pin": "pin_hash = %s, intentos_fallidos = 0, bloqueado_hasta = NULL",
                "desbloquear": "intentos_fallidos = 0, bloqueado_hasta = NULL",
                "desactivar": "activo = FALSE",
            }[args.accion]
            parametros = (pin_hash, usuario) if args.accion == "pin" else (usuario,)
            fila = conn.execute(f"UPDATE usuario SET {cambios} WHERE usuario = %s RETURNING id", parametros).fetchone()
        if fila is None:
            print(f"ERROR: no existe el usuario {usuario!r}", file=sys.stderr)
            return 1
        accion = {"crear": "USUARIO_CREADO", "pin": "PIN_CAMBIADO", "desbloquear": "USUARIO_DESBLOQUEADO",
                  "desactivar": "USUARIO_DESACTIVADO"}[args.accion]  # fmt: skip
        auditar(conn, accion, "usuario", fila[0], detalle={"por": "consola"})
    print(f"Listo: {args.accion} {usuario}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
