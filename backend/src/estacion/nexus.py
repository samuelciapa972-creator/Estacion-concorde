"""Lector de Nexus: `python -m estacion.nexus --url http://192.168.10.101 --cada 15s`.

Consulta la web de Nexus (IMED OIL) cada cierto tiempo y guarda en la base (DATABASE_URL) las ventas NUEVAS del Wayne
como despachos del surtidor WAYNE HELIX-1, para facturarlas desde la pista. SOLO LECTURA: la misma consulta que hace
la tablet al tocar "Actualizar" (ver `surtidores/nexus.py`). Provisional, para pruebas en habilitación, mientras no
existe la conexión directa con el surtidor.

Nexus solo muestra las 3 últimas ventas: no subir `--cada` de 30 s. `--en-seco` las muestra sin tocar la base.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .simulador import segundos
from .surtidores import ingerir
from .surtidores.nexus import CODIGO_WAYNE, FuenteNexus, WebNexus

log = logging.getLogger("estacion.nexus")
CADA_MAXIMO = 30.0


def ultima_guardada(dsn: str) -> int | None:
    """La última venta de Nexus ya guardada en la base (para retomar sin repetir ni saltar)."""
    import psycopg

    with psycopg.connect(dsn) as conn:
        fila = conn.execute(
            """SELECT max(d.id_externo) FROM despacho d JOIN surtidor s ON s.id = d.surtidor_id
               WHERE s.marca = 'WAYNE' AND s.codigo_externo = %s""",
            (CODIGO_WAYNE,),
        ).fetchone()
    return None if fila is None or fila[0] is None else int(fila[0])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m estacion.nexus", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default=os.environ.get("NEXUS_URL", "http://192.168.10.101"), help="web de Nexus")
    ap.add_argument("--cada", type=segundos, default=15.0, help=f"tiempo entre consultas (máximo {CADA_MAXIMO:.0f}s)")
    ap.add_argument("--en-seco", action="store_true", help="solo muestra las ventas nuevas; no escribe en la base")
    ap.add_argument("--una-vez", action="store_true", help="una sola consulta y termina")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not 1 <= args.cada <= CADA_MAXIMO:
        ap.error(f"--cada debe estar entre 1 y {CADA_MAXIMO:.0f} segundos: Nexus solo muestra las 3 últimas ventas")

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn and not args.en_seco:
        log.error("falta la variable de entorno DATABASE_URL (o use --en-seco)")
        return 2

    web = WebNexus(args.url)
    try:
        productos = web.productos()
    except OSError as e:
        log.error("no se pudo leer la configuración de Nexus en %s (%s): ¿el equipo está en la red de la pista?",
                  args.url, type(e).__name__)  # fmt: skip
        return 1
    fuente = FuenteNexus(web.ultimas_ventas, productos=productos)
    fuente.iniciar(None if args.en_seco else ultima_guardada(dsn))
    log.warning("lector de Nexus en %s (productos: %s), cada %.0f s%s", args.url, ", ".join(productos), args.cada,
                " EN SECO: no se guarda nada" if args.en_seco else "")  # fmt: skip
    while True:
        try:
            if fuente.sondear():
                for lote in fuente.leer():
                    # Solo datos operativos: ni placa ni documento en el registro (Ley 1581).
                    for d in lote.despachos:
                        log.info("venta %s lado %s %s %s gal $%s", d.id_externo, d.lado, d.producto,
                                 d.volumen_bruto, d.valor)  # fmt: skip
                    for r in lote.rechazos:
                        log.warning("venta %s no se pudo leer: %s", r.id_externo, r.motivo)
                    if args.en_seco:
                        fuente.confirmar(lote)
            if not args.en_seco:  # también lo que quedó sin guardar en una vuelta anterior
                for hecho in ingerir(dsn, fuente):
                    s = hecho.resumen
                    log.info("  guardado: %s nuevos, %s repetidos, %s anomalías", s.nuevos, s.duplicados,
                                 s.anomalias)  # fmt: skip
        except OSError as e:  # red de la pista caída o Nexus sin responder: se reintenta en la próxima vuelta
            log.warning("Nexus no respondió (%s); se reintenta", type(e).__name__)
        except Exception:
            log.exception("no se pudo guardar; se reintenta en la próxima vuelta")
        if args.una_vez:
            return 0
        time.sleep(args.cada)


if __name__ == "__main__":
    sys.exit(main())
