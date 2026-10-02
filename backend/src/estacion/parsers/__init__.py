"""Un módulo por marca de surtidor. Todos exponen `leer(ruta) -> ResultadoLectura`."""

from . import speed_solutions

PARSERS = {
    "speed_solutions": speed_solutions.leer,
    # "wayne": wayne.leer,   # pendiente: llega con el archivo del Wayne
}
