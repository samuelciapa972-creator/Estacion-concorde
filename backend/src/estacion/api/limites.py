"""Límite de peticiones por clave (p. ej. la IP), en memoria y con ventana deslizante.

Suficiente para UNA instancia de la API (alcance actual: una estación). Con varias instancias habría que
llevarlo a la base o a un servicio compartido.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class LimitadorTasa:
    def __init__(self, maximo: int, ventana_segundos: float = 60.0, reloj: Callable[[], float] = time.monotonic):
        self.maximo = maximo
        self.ventana = ventana_segundos
        self._reloj = reloj
        self._eventos: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, clave: str) -> bool:
        """Registra un intento y dice si está dentro del límite."""
        ahora = self._reloj()
        with self._lock:
            eventos = self._eventos[clave]
            while eventos and eventos[0] <= ahora - self.ventana:
                eventos.popleft()
            if len(eventos) >= self.maximo:
                return False
            eventos.append(ahora)
            return True
