"""Uso:

# Informe sin tocar la base de datos (por defecto)
python -m estacion.cli speed_solutions D260928_084701.xls

# Cargar a PostgreSQL
python -m estacion.cli speed_solutions D260928_084701.xls --dsn postgresql://user:pass@localhost/estacion
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .calidad import analizar
from .modelos import ArchivoInvalido, PlacaTipo, Severidad
from .parsers import PARSERS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="estacion", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("marca", choices=sorted(PARSERS))
    ap.add_argument("archivo", type=Path)
    ap.add_argument("--dsn", help="cadena de conexión PostgreSQL; sin ella solo se imprime el informe")
    args = ap.parse_args(argv)

    try:
        lectura = PARSERS[args.marca](args.archivo)
    except ArchivoInvalido as e:
        print(f"ERROR: {e}\nNo se importó nada.", file=sys.stderr)
        return 2

    despachos, anomalias, turnos = analizar(lectura.despachos)
    _imprimir_informe(args.archivo, lectura, despachos, anomalias, turnos)

    if not args.dsn:
        print("\n(informe en seco: no se escribió nada en la base de datos)")
        return 0
    if not despachos:
        print("\nERROR: el archivo no tiene ningún despacho válido. No se importó nada.", file=sys.stderr)
        return 2

    from .carga import cargar

    r = cargar(
        args.dsn,
        marca=lectura.marca,
        archivo_nombre=args.archivo.name,
        archivo_sha256=lectura.archivo_sha256,
        despachos=despachos,
        turnos=turnos,
        anomalias=anomalias,
        rechazos=lectura.rechazos,
    )
    if r.archivo_repetido:
        print("\nEste archivo exacto ya fue importado antes. No se hizo nada.")
        return 0
    print(
        f"\nCarga #{r.importacion_id}: {r.nuevos} nuevos, {r.duplicados} ya existían, "
        f"{r.conflictos} conflictos, {r.rechazados} rechazados, {r.anomalias} anomalías registradas."
    )
    return 1 if r.conflictos else 0


def _imprimir_informe(archivo, lectura, despachos, anomalias, turnos) -> None:
    print(f"Archivo: {archivo.name}  (sha256 {lectura.archivo_sha256[:12]}…)")
    print(f"Despachos leídos: {len(despachos)}   filas rechazadas: {len(lectura.rechazos)}")
    for r in lectura.rechazos[:10]:
        print(f"   rechazada id={r.id_externo}: {r.motivo}")
    if not despachos:
        return

    inicio = min(d.inicio for d in despachos)
    fin = max(d.fin for d in despachos)
    total = sum(d.valor for d in despachos)
    galones = sum(d.volumen_bruto for d in despachos)
    print(f"Rango (fechas efectivas): {inicio:%Y-%m-%d %H:%M} → {fin:%Y-%m-%d %H:%M}")
    print(f"Ventas: ${total:,.0f}   Galones (bruto): {galones:,.3f}")
    abiertos = [t.id_cierre for t in turnos if t.estado == "ABIERTO"]
    print(f"Turnos: {len(turnos)}   abierto(s): {abiertos}")

    tipos = Counter(d.placa_tipo for d in despachos)
    print("Identificación del vehículo: " + ", ".join(f"{t.value}={tipos.get(t, 0)}" for t in PlacaTipo))
    print(f"Fechas corregidas: {sum(d.fecha_corregida for d in despachos)}")

    print("\nAnomalías:")
    if not anomalias:
        print("   ninguna")
    resumen = Counter((a.severidad, a.tipo) for a in anomalias)
    orden = {Severidad.ALTA: 0, Severidad.AVISO: 1, Severidad.INFO: 2}
    for (sev, tipo), n in sorted(resumen.items(), key=lambda kv: (orden[kv[0][0]], kv[0][1])):
        print(f"   [{sev.value:5}] {tipo}: {n}")


if __name__ == "__main__":
    raise SystemExit(main())
