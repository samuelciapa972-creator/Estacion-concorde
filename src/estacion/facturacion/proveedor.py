"""Interfaz con el proveedor tecnológico (o con un motor propio) y un simulador para pruebas."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Protocol

from .modelos import ResultadoEmision, SolicitudFactura


class ProveedorRechazo(Exception):
    """El proveedor rechazó la factura de forma definitiva (datos inválidos, numeración vencida...).
    Se sabe con certeza que NO se emitió."""


class ProveedorIncierto(Exception):
    """No se sabe si la factura se emitió (timeout, corte de red tras enviar...)."""


class ProveedorFacturacion(Protocol):
    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision: ...

    def buscar_por_clave(self, clave: str) -> ResultadoEmision | None:
        """Consulta si una emisión con esa clave existe. Necesario para conciliar los casos inciertos."""
        ...


class ProveedorSimulado:
    """Proveedor falso para pruebas y demostraciones. NO habla con la DIAN.

    `falla_siguiente` fuerza el comportamiento de la próxima llamada:
      "rechazo"          -> ProveedorRechazo, no emite
      "timeout_antes"    -> ProveedorIncierto, no emite (la petición nunca llegó)
      "timeout_despues"  -> ProveedorIncierto, pero SÍ emite (se perdió la respuesta)
    """

    def __init__(self, prefijo: str = "SETP", primer_numero: int = 1, reloj=datetime.now):
        self.prefijo = prefijo
        self._siguiente = primer_numero
        self._emitidas: dict[str, ResultadoEmision] = {}
        self._reloj = reloj
        self._lock = threading.Lock()
        self.falla_siguiente: str | None = None
        self.llamadas = 0

    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision:
        with self._lock:
            self.llamadas += 1
            falla, self.falla_siguiente = self.falla_siguiente, None
            if falla == "rechazo":
                raise ProveedorRechazo("documento rechazado por el simulador")
            if falla == "timeout_antes":
                raise ProveedorIncierto("timeout antes de enviar")
            previa = self._emitidas.get(solicitud.clave)
            if previa is not None:
                return previa
            numero = self._siguiente
            self._siguiente += 1
            cufe = hashlib.sha384(f"{self.prefijo}{numero}{solicitud.total}".encode()).hexdigest()
            resultado = ResultadoEmision(
                clave=solicitud.clave,
                prefijo=self.prefijo,
                numero=str(numero),
                cufe=cufe,
                emitida_en=self._reloj(),
                detalle={"simulado": True},
            )
            self._emitidas[solicitud.clave] = resultado
            if falla == "timeout_despues":
                raise ProveedorIncierto("timeout esperando la respuesta")
            return resultado

    def buscar_por_clave(self, clave: str) -> ResultadoEmision | None:
        with self._lock:
            return self._emitidas.get(clave)
