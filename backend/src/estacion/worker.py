"""Worker de la cola de envíos a la DIAN: `python -m estacion.worker [--una-vez] [--espera 2]`.

Lee DATABASE_URL del entorno. HOY SOLO EXISTE EL PROVEEDOR SIMULADO: no envía nada a la DIAN.
El cliente de habilitación llega en la Fase 6 (y producción queda bloqueada en el código).
Varios workers pueden correr a la vez: `FOR UPDATE SKIP LOCKED` evita que dos tomen el mismo trabajo.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import psycopg

from .facturacion.proveedor import ProveedorSimulado
from .servicios.outbox import procesar_pendientes

log = logging.getLogger("estacion.worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m estacion.worker", description=__doc__.splitlines()[0])
    parser.add_argument("--una-vez", action="store_true", help="procesa lo que haya en la cola y termina")
    parser.add_argument("--espera", type=float, default=2.0, help="segundos entre revisiones de la cola")
    parser.add_argument("--proveedor", choices=["simulado"], default="simulado")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        log.error("falta la variable de entorno DATABASE_URL")
        return 2
    proveedor = ProveedorSimulado()
    log.warning("proveedor SIMULADO: las facturas NO se envían a la DIAN")

    with psycopg.connect(dsn, autocommit=True) as conn:
        while True:
            try:
                for hecho in procesar_pendientes(conn, proveedor):
                    # Solo identificadores y estados: nada de datos personales en el log.
                    log.info("trabajo %s (%s) factura %s -> %s", hecho.trabajo_id, hecho.tipo,
                             hecho.factura_id, hecho.estado_factura)  # fmt: skip
            except Exception:
                log.exception("error procesando la cola; se reintenta en la próxima vuelta")
                if args.una_vez:
                    return 1
            if args.una_vez:
                return 0
            time.sleep(args.espera)


if __name__ == "__main__":
    sys.exit(main())
