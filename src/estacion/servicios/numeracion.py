"""Asignación atómica del consecutivo de facturación.

El consecutivo se toma con `SELECT ... FOR UPDATE` sobre la fila de `numeracion` y se incrementa en la MISMA
transacción que guarda la factura. Si esa transacción falla, el incremento se deshace con ella: no quedan
números repetidos (bloqueo + UNIQUE(prefijo, consecutivo)) ni huecos por un error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg import pq

from ._bd import Conexion


class NumeracionNoDisponible(Exception):
    """No hay una resolución de numeración utilizable (no existe, vencida o agotada). No se emite."""


@dataclass(frozen=True, slots=True)
class ConsecutivoAsignado:
    numeracion_id: int
    prefijo: str
    consecutivo: int

    @property
    def numero(self) -> str:
        return f"{self.prefijo}{self.consecutivo}"


def asignar_consecutivo(conn: Conexion, estacion_id: int, fecha: date) -> ConsecutivoAsignado:
    """Reserva el siguiente consecutivo de la numeración activa de la estación para una factura de `fecha`.

    Debe llamarse DENTRO de la transacción que guarda la factura (si no, el número se perdería).
    """
    if conn.info.transaction_status != pq.TransactionStatus.INTRANS:
        raise RuntimeError("asignar_consecutivo debe llamarse dentro de la transacción que guarda la factura")
    fila = conn.execute(
        """SELECT id, prefijo, siguiente, hasta, vigente_desde, vigente_hasta
           FROM numeracion WHERE estacion_id = %s AND activa FOR UPDATE""",
        (estacion_id,),
    ).fetchone()
    if fila is None:
        raise NumeracionNoDisponible("la estación no tiene una resolución de numeración activa")
    numeracion_id, prefijo, siguiente, hasta, vigente_desde, vigente_hasta = fila
    if not vigente_desde <= fecha <= vigente_hasta:
        raise NumeracionNoDisponible(
            f"la resolución de numeración {prefijo} no está vigente el {fecha} ({vigente_desde} a {vigente_hasta})"
        )
    if siguiente > hasta:
        raise NumeracionNoDisponible(f"la resolución de numeración {prefijo} está agotada (llegó a {hasta})")
    conn.execute("UPDATE numeracion SET siguiente = siguiente + 1 WHERE id = %s", (numeracion_id,))
    return ConsecutivoAsignado(numeracion_id=numeracion_id, prefijo=prefijo, consecutivo=siguiente)
