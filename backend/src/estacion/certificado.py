"""Genera un certificado de PRUEBA para firmar en el banco de pruebas:
`python -m estacion.certificado --salida ../datos/certificado-prueba.pfx [--nombre "EDS PRUEBA SAS"]`.

Pide la clave del .pfx por teclado (o la lee de la entrada estándar con `--clave-stdin`). Nunca la imprime.
El certificado es autofirmado y SIN VALIDEZ: la DIAN lo rechazaría. Para producción se usa el de la empresa.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .facturacion.certificado_prueba import generar_certificado_prueba, guardar_pfx


def _leer_clave(desde_stdin: bool) -> str:
    if desde_stdin:
        return sys.stdin.readline().strip()
    clave = getpass.getpass("Clave del certificado: ")
    if getpass.getpass("Repita la clave: ") != clave:
        return ""
    return clave


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m estacion.certificado", description=__doc__.splitlines()[0])
    ap.add_argument("--salida", required=True, type=Path, help="ruta del .pfx (fuera del repositorio)")
    ap.add_argument("--nombre", default="ESTACION DE PRUEBA", help="razón social que lleva el certificado")
    ap.add_argument("--dias", type=int, default=365, help="días de vigencia")
    ap.add_argument("--clave-stdin", action="store_true", help="lee la clave de la entrada estándar")
    args = ap.parse_args(argv)

    clave = _leer_clave(args.clave_stdin)
    if not clave:
        print("falta la clave (o las dos no coinciden): no se generó nada", file=sys.stderr)
        return 2
    guardar_pfx(generar_certificado_prueba(args.nombre, dias=args.dias), args.salida, clave)
    print(
        f"Certificado de PRUEBA (sin validez) guardado en {args.salida}. Configure:\n"
        f"  DIAN_CERTIFICADO_RUTA={args.salida.resolve()}\n"
        "  DIAN_CERTIFICADO_CLAVE=<la clave que digitó>"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
