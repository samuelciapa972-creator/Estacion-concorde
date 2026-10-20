"""Entrega de la factura al cliente (tabla `entrega`): el ZIP que se le mandó y el estado del envío.

El ZIP se guarda tal cual se entregó: un reintento o un reenvío manual mandan el MISMO archivo (es lo que el
cliente recibió y lo que hay que poder mostrar después). El destinatario no se guarda: se lee del cliente al enviar.
Todas las funciones corren dentro de la transacción de quien llama.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..facturacion.modelos import EstadoEntrega
from ._bd import Conexion


@dataclass(frozen=True, slots=True)
class EntregaGuardada:
    id: int
    factura_id: int
    nombre_zip: str
    zip: bytes
    estado: EstadoEntrega
    intentos: int
    enviada_en: datetime | None


_COLUMNAS = "id, factura_id, nombre_zip, zip, estado, intentos, enviada_en"


def _desde_fila(fila: tuple) -> EntregaGuardada:
    eid, factura_id, nombre, zip_, estado, intentos, enviada = fila
    return EntregaGuardada(eid, factura_id, nombre, bytes(zip_), EstadoEntrega(estado), intentos, enviada)


def reservar_consecutivo(conn: Conexion, anio: int) -> int:
    """Siguiente consecutivo de entrega del año (bloquea la fila del año: dos workers no repiten número)."""
    conn.execute("INSERT INTO entrega_consecutivo (anio, siguiente) VALUES (%s, 1) ON CONFLICT DO NOTHING", (anio,))
    fila = conn.execute("SELECT siguiente FROM entrega_consecutivo WHERE anio = %s FOR UPDATE", (anio,)).fetchone()
    assert fila is not None  # se acaba de asegurar
    conn.execute("UPDATE entrega_consecutivo SET siguiente = siguiente + 1 WHERE anio = %s", (anio,))
    return int(fila[0])


def obtener(conn: Conexion, factura_id: int) -> EntregaGuardada | None:
    fila = conn.execute(f"SELECT {_COLUMNAS} FROM entrega WHERE factura_id = %s", (factura_id,)).fetchone()
    return None if fila is None else _desde_fila(fila)


def guardar(
    conn: Conexion, factura_id: int, anio: int, consecutivo: int, nombre_zip: str, zip_: bytes
) -> EntregaGuardada:
    fila = conn.execute(
        f"""INSERT INTO entrega (factura_id, anio, consecutivo, nombre_zip, zip)
            VALUES (%s, %s, %s, %s, %s) RETURNING {_COLUMNAS}""",
        (factura_id, anio, consecutivo, nombre_zip, zip_),
    ).fetchone()
    assert fila is not None
    return _desde_fila(fila)


def marcar(
    conn: Conexion, factura_id: int, estado: EstadoEntrega, *, error: str | None = None, intento: bool = True
) -> None:
    """Registra el resultado de un intento de envío (`intento=False`: solo cambia el estado, p. ej. al reenviar)."""
    conn.execute(
        """UPDATE entrega SET estado = %s, ultimo_error = %s, intentos = intentos + %s,
                  enviada_en = CASE WHEN %s = 'ENVIADA' THEN now() ELSE NULL END
           WHERE factura_id = %s""",
        (estado, error, 1 if intento else 0, estado, factura_id),
    )
