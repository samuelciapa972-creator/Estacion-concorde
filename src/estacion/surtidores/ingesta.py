"""De una fuente a la base: calidad (`calidad.analizar`) y carga idempotente (`carga.cargar`), lote por lote."""

from __future__ import annotations

from dataclasses import dataclass

from ..calidad import analizar
from ..carga import ResumenCarga, cargar
from .fuente import FuenteDespachos, Lote


class LoteSinDespachos(ValueError):
    """Ninguna fila del lote se pudo leer: no hay surtidor al cual atribuir la importación."""


@dataclass(frozen=True, slots=True)
class ResultadoIngesta:
    lote: str
    resumen: ResumenCarga


def ingerir_lote(dsn: str, lote: Lote) -> ResumenCarga:
    despachos, anomalias, turnos = analizar(list(lote.despachos))
    if not despachos:
        raise LoteSinDespachos(f"el lote {lote.nombre} no tiene despachos válidos ({len(lote.rechazos)} rechazados)")
    return cargar(
        dsn,
        marca=lote.marca,
        archivo_nombre=lote.nombre,
        archivo_sha256=lote.sha256,
        despachos=despachos,
        turnos=turnos,
        anomalias=anomalias,
        rechazos=list(lote.rechazos),
    )


def ingerir(dsn: str, fuente: FuenteDespachos) -> list[ResultadoIngesta]:
    """Guarda cada lote pendiente de la fuente y lo confirma. Si uno falla, la excepción sale y ese lote (y los
    siguientes) quedan sin confirmar: la próxima llamada los vuelve a intentar."""
    hechos: list[ResultadoIngesta] = []
    for lote in fuente.leer():
        resumen = ingerir_lote(dsn, lote)
        fuente.confirmar(lote)
        hechos.append(ResultadoIngesta(lote.nombre, resumen))
    return hechos
