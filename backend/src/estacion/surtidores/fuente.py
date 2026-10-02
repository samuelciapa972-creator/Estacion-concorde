"""Interfaz común de las fuentes de despachos (archivo del surtidor, simulador; más adelante el Wayne en vivo).

Una fuente entrega LOTES de despachos ya normalizados. El lote se guarda (`ingesta.ingerir`) y solo entonces
se confirma a la fuente: si la carga falla, la fuente lo vuelve a entregar y la carga, que es idempotente
(sha256 del lote + id del despacho), no duplica nada. Así una fuente en vivo nunca pierde un despacho.

La venta digitada por el bombero (modo manual) NO es una fuente: no viene de un equipo, la escribe una
persona y vive en su propia tabla (`servicios/ventas.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from ..modelos import Despacho, RechazoFila


@dataclass(frozen=True, slots=True)
class Lote:
    marca: str  # MarcaSurtidor
    nombre: str  # nombre del archivo o identificador legible del lote
    sha256: str  # identifica el contenido: el mismo lote no se carga dos veces
    despachos: tuple[Despacho, ...]
    rechazos: tuple[RechazoFila, ...] = ()


class FuenteDespachos(Protocol):
    def leer(self) -> Iterator[Lote]:
        """Lotes todavía no confirmados (puede volver a entregar uno cuya carga falló)."""
        ...

    def confirmar(self, lote: Lote) -> None:
        """El lote quedó guardado en la base: la fuente no debe volver a entregarlo."""
        ...
