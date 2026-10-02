"""Fuente: archivos exportados por el programa del surtidor Speed Solutions (XML con extensión `.xls`)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from ..parsers import speed_solutions
from .fuente import Lote


class ArchivoSpeedSolutions:
    """Entrega un lote por archivo. Un archivo truncado o mal formado lanza `ArchivoInvalido` y no entrega nada
    de ese archivo (los anteriores ya entregados no se afectan)."""

    def __init__(self, *rutas: str | Path):
        self._rutas = [Path(r) for r in rutas]
        self.confirmados: set[str] = set()  # sha256 de los archivos ya guardados

    def leer(self) -> Iterator[Lote]:
        for ruta in self._rutas:
            lectura = speed_solutions.leer(ruta)
            if lectura.archivo_sha256 in self.confirmados:
                continue
            yield Lote(
                marca=lectura.marca,
                nombre=ruta.name,
                sha256=lectura.archivo_sha256,
                despachos=tuple(lectura.despachos),
                rechazos=tuple(lectura.rechazos),
            )

    def confirmar(self, lote: Lote) -> None:
        self.confirmados.add(lote.sha256)
