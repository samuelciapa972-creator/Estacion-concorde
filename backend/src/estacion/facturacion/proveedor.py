"""Interfaz con el proveedor tecnológico (o con un motor propio) y un simulador para pruebas."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Protocol

from .modelos import EnvioPendiente, ResultadoEmision, SolicitudFactura
from .respuesta_simulada import generar_respuesta


class ProveedorRechazo(Exception):
    """El proveedor rechazó la factura de forma definitiva (datos inválidos, numeración vencida...).
    Se sabe con certeza que NO se emitió."""


class ProveedorIncierto(Exception):
    """No se sabe si la factura se emitió (timeout, corte de red tras enviar...)."""


class ProveedorNoConfigurado(Exception):
    """El proveedor no aceptó la conexión (certificado, firma del mensaje, reloj): es configuración corregible y no
    dice nada de la factura. La cola reintenta y avisa."""


class ProveedorFacturacion(Protocol):
    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision | EnvioPendiente: ...

    def consultar(
        self, *, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        """Estado de un documento ya enviado (o que pudo enviarse): el resultado, `EnvioPendiente` si sigue en
        validación, o None si el proveedor no lo tiene (se sabe que NO se emitió). Lanza ProveedorRechazo si la
        validación lo rechazó, ProveedorIncierto si no responde, ProveedorNoConfigurado si no acepta la conexión."""
        ...


class ProveedorSimulado:
    """Proveedor falso para pruebas y demostraciones. NO habla con la DIAN.

    `falla_siguiente` fuerza el comportamiento de la próxima llamada a `emitir`:
      "rechazo"          -> ProveedorRechazo, no emite
      "timeout_antes"    -> ProveedorIncierto, no emite (la petición nunca llegó)
      "timeout_despues"  -> ProveedorIncierto, pero SÍ emite (se perdió la respuesta)
    `falla_consulta` = True: `consultar` lanza ProveedorIncierto (servicio caído).
    `falla_seguridad` = True: la próxima llamada lanza ProveedorNoConfigurado (certificado no aceptado).
    `asincrono` = True imita el set de pruebas: `emitir` devuelve EnvioPendiente y el resultado llega por `consultar`
    (tras `en_proceso_restantes` consultas "en proceso"; con `rechazo_en_consulta`, como ProveedorRechazo).

    Si la solicitud ya trae número y CUFE (motor propio), los respeta; si no, numera con su propio contador.
    """

    def __init__(self, prefijo: str = "SETP", primer_numero: int = 1, reloj=datetime.now, *, asincrono: bool = False):
        self.prefijo = prefijo
        self._siguiente = primer_numero
        self._emitidas: dict[str, ResultadoEmision] = {}
        self._reloj = reloj
        self._lock = threading.Lock()
        self.asincrono = asincrono
        self.falla_siguiente: str | None = None
        self.falla_consulta = False
        self.falla_seguridad = False
        self.rechazo_en_consulta = False
        self.en_proceso_restantes = 0
        self.llamadas = 0

    def _seguridad(self) -> None:
        if self.falla_seguridad:
            self.falla_seguridad = False
            raise ProveedorNoConfigurado("el proveedor no aceptó la seguridad del mensaje (simulado)")

    def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision | EnvioPendiente:
        with self._lock:
            self._seguridad()
            self.llamadas += 1
            falla, self.falla_siguiente = self.falla_siguiente, None
            if falla == "rechazo":
                raise ProveedorRechazo("documento rechazado por el simulador")
            if falla == "timeout_antes":
                raise ProveedorIncierto("timeout antes de enviar")
            emitida = self._emitidas.get(solicitud.clave)
            if emitida is None:
                emitida = self._emitidas[solicitud.clave] = self._emitida(solicitud)
            if falla == "timeout_despues":
                raise ProveedorIncierto("timeout esperando la respuesta")
            return EnvioPendiente(f"sim-{solicitud.clave}") if self.asincrono else emitida

    def _emitida(self, solicitud: SolicitudFactura) -> ResultadoEmision:
        if solicitud.numero is not None and solicitud.cufe is not None:
            numero, cufe = solicitud.numero, solicitud.cufe
        else:
            numero = str(self._siguiente)
            self._siguiente += 1
            cufe = hashlib.sha384(f"{self.prefijo}{numero}{solicitud.total}".encode()).hexdigest()
        emitida_en = self._reloj()
        return ResultadoEmision(
            clave=solicitud.clave,
            prefijo=self.prefijo,
            numero=numero,
            cufe=cufe,
            emitida_en=emitida_en,
            detalle={"simulado": True},
            # Con el documento (motor propio) devuelve la "validación", SIMULADA y marcada como tal.
            application_response=None
            if solicitud.documento is None
            else generar_respuesta(solicitud.documento, validada_en=emitida_en),
        )

    def consultar(
        self, *, clave: str, numero: str, cufe: str, track_id: str | None
    ) -> ResultadoEmision | EnvioPendiente | None:
        with self._lock:
            self._seguridad()
            if self.falla_consulta:
                raise ProveedorIncierto("el servicio de consulta no responde (simulado)")
            emitida = self._emitidas.get(clave)
            if emitida is not None and self.asincrono:
                if self.en_proceso_restantes > 0:
                    self.en_proceso_restantes -= 1
                    return EnvioPendiente(track_id or f"sim-{clave}")
                if self.rechazo_en_consulta:
                    del self._emitidas[clave]
                    raise ProveedorRechazo("documento rechazado por el simulador")
            return emitida
