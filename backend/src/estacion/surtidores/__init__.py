"""Fuentes de despachos: interfaz común, archivo de Speed Solutions y simulador de surtidor."""

from .archivo import ArchivoSpeedSolutions
from .fuente import FuenteDespachos, Lote
from .ingesta import LoteSinDespachos, ResultadoIngesta, ingerir, ingerir_lote
from .simulador import FALLAS, SimuladorSurtidor

__all__ = [
    "FALLAS",
    "ArchivoSpeedSolutions",
    "FuenteDespachos",
    "Lote",
    "LoteSinDespachos",
    "ResultadoIngesta",
    "SimuladorSurtidor",
    "ingerir",
    "ingerir_lote",
]
