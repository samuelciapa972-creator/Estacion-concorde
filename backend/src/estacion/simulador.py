"""Simulador de surtidor: `python -m estacion.simulador --lado A --cada 20s`.

Genera un despacho cada cierto tiempo y lo guarda en la base (DATABASE_URL), como si lo enviara un surtidor
real. Los despachos quedan con marca SIMULADOR. Con `--en-seco` solo los muestra, sin tocar la base.

Ejemplos:
    python -m estacion.simulador --lado A --lado B --cada 20s
    python -m estacion.simulador --cantidad 30 --cada 0 --prob-falla 0.1 --semilla 7
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time

from .surtidores import FALLAS, SimuladorSurtidor, ingerir

log = logging.getLogger("estacion.simulador")
_DURACION = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)?$")


def segundos(texto: str) -> float:
    """'20s', '2m', '1h' o un número de segundos.

    >>> segundos("20s"), segundos("1.5m"), segundos("0")
    (20.0, 90.0, 0.0)
    """
    m = _DURACION.match(texto.strip().lower())
    if m is None:
        raise argparse.ArgumentTypeError(f"duración inválida: {texto!r} (use p. ej. 20s, 2m o 1h)")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2) or "s"]


def retomar(sim: SimuladorSurtidor, dsn: str) -> None:
    """Si el surtidor simulado ya tiene despachos en la base, sigue desde su último id, turno y contadores."""
    import psycopg

    with psycopg.connect(dsn) as conn:
        ultimo = conn.execute(
            """SELECT max(d.id_externo), max(t.id_cierre) FROM despacho d
               JOIN surtidor s ON s.id = d.surtidor_id JOIN turno t ON t.id = d.turno_id
               WHERE s.marca = 'SIMULADOR' AND s.codigo_externo = %s""",
            (sim.codigo,),
        ).fetchone()
        if ultimo is None or ultimo[0] is None:
            return
        contadores = conn.execute(
            """SELECT DISTINCT ON (d.lado, d.pistola) d.lado, d.pistola, d.totalizador_vol, d.totalizador_valor
               FROM despacho d JOIN surtidor s ON s.id = d.surtidor_id
               WHERE s.marca = 'SIMULADOR' AND s.codigo_externo = %s AND d.totalizador_vol IS NOT NULL
               ORDER BY d.lado, d.pistola, d.id_externo DESC""",
            (sim.codigo,),
        ).fetchall()
    sim.retomar(
        ultimo_id=ultimo[0], id_cierre=ultimo[1], contadores={(lado, p): (v, m) for lado, p, v, m in contadores}
    )
    log.info("se retoma el surtidor simulado %s desde el despacho %s", sim.codigo, ultimo[0])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m estacion.simulador", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lado", action="append", help="lado del surtidor (se puede repetir; por defecto A y B)")
    ap.add_argument("--cada", type=segundos, default=20.0, help="tiempo entre despachos (20s, 2m...)")
    ap.add_argument("--cantidad", type=int, help="termina después de N despachos (por defecto, sin fin)")
    ap.add_argument("--codigo", default="SIM1", help="código del surtidor simulado")
    ap.add_argument("--semilla", type=int, help="semilla del azar, para repetir una corrida")
    ap.add_argument("--prob-falla", type=float, default=0.0, help=f"probabilidad de una falla ({', '.join(FALLAS)})")
    ap.add_argument("--en-seco", action="store_true", help="solo muestra los despachos; no escribe en la base")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not 0 <= args.prob_falla <= 1:
        ap.error("--prob-falla debe estar entre 0 y 1")

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn and not args.en_seco:
        log.error("falta la variable de entorno DATABASE_URL (o use --en-seco)")
        return 2

    sim = SimuladorSurtidor(codigo=args.codigo, lados=args.lado or ("A", "B"), semilla=args.semilla)
    if not args.en_seco:
        retomar(sim, dsn)
    log.warning("surtidor SIMULADO %s: estas ventas no son reales", args.codigo)
    hechos = 0
    while args.cantidad is None or hechos < args.cantidad:
        falla = sim.sortear_falla(args.prob_falla) if hechos else None
        fila = sim.despachar(falla=falla)
        hechos += 1
        # Solo datos operativos: ni placa ni identificación en el log.
        log.info("despacho %s lado %s %s %s gal $%s %s%s", fila["ID-DESPACHO"], fila["LADO"], fila["PRD"],
                 fila["VOLG"], fila["MONEY"], fila["FORMA-PAGO"], f" [falla: {falla}]" if falla else "")  # fmt: skip
        if args.en_seco:
            sim.confirmar(next(sim.leer()))
        else:
            try:
                for r in ingerir(dsn, sim):
                    s = r.resumen
                    log.info("  guardado: %s nuevos, %s repetidos, %s anomalías", s.nuevos, s.duplicados, s.anomalias)
            except Exception:
                log.exception("no se pudo guardar; se reintenta con el próximo despacho")
        if args.cantidad is None or hechos < args.cantidad:
            time.sleep(args.cada)
    return 0


if __name__ == "__main__":
    sys.exit(main())
