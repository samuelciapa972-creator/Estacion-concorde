"""Arranca la API: `python -m estacion.api [--host 127.0.0.1] [--puerto 8000]`.

Necesita DATABASE_URL y JWT_SECRETO en el entorno. Detrás de un proxy (Caddy en infra/), `--proxy-confiable`
dice de quién se acepta X-Forwarded-For: sin eso, todas las peticiones parecerían venir del proxy y el límite de
intentos por equipo sería uno solo para toda la estación. El registro de accesos NO guarda la cadena de consulta
(`?tipo=CC&numero=...` lleva la cédula): Ley 1581, los logs no llevan datos personales.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from .ajustes import Ajustes, AjustesInvalidos


class SinConsulta(logging.Filter):
    """Quita la cadena de consulta de la ruta en el log de accesos de uvicorn."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m estacion.api", description=__doc__)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 para que la tablet de la red local la vea")
    ap.add_argument("--puerto", type=int, default=8000)
    ap.add_argument("--recargar", action="store_true", help="recarga al cambiar el código (desarrollo)")
    ap.add_argument(
        "--proxy-confiable",
        default=os.environ.get("ESTACION_PROXY_CONFIABLE") or None,
        help="IPs (separadas por coma) o '*' cuyo X-Forwarded-For se acepta. '*' SOLO si la API no está expuesta "
        "más que al proxy (así en el docker-compose). Por defecto: solo 127.0.0.1",
    )
    args = ap.parse_args(argv)
    try:
        Ajustes.desde_entorno()  # fallar antes de arrancar el servidor, con un mensaje claro
    except AjustesInvalidos as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    logging.getLogger("uvicorn.access").addFilter(SinConsulta())
    uvicorn.run(
        "estacion.api.app:crear_app",
        factory=True,
        host=args.host,
        port=args.puerto,
        reload=args.recargar,
        proxy_headers=True,
        forwarded_allow_ips=args.proxy_confiable,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
