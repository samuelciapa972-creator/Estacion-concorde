"""Historial de facturas por FECHA DE EMISIÓN (la búsqueda de ventas filtra por la fecha del despacho: una venta de
ayer facturada hoy está en "hoy" aquí y en "ayer" allá). Sin XML ni respuesta de la DIAN: solo lo de la lista."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ._bd import Conexion
from .clientes import nombre_para_mostrar


@dataclass(frozen=True, slots=True)
class FacturaListada:
    id: int
    numero: str
    estado: str
    total: Decimal
    fecha_emision: datetime
    medio_pago: str
    cliente_tipo: str
    cliente_documento: str
    cliente_nombre: str
    vendedor: str
    error: str | None


def listar_facturas(
    conn: Conexion, desde: datetime, hasta: datetime, *, estado: str | None, limite: int, desplazamiento: int
) -> tuple[list[FacturaListada], int]:
    """Las más recientes primero. `hasta` excluido. Devuelve (página, total del filtro)."""
    condicion = "f.fecha_emision >= %s AND f.fecha_emision < %s" + (" AND f.estado = %s" if estado else "")
    params: tuple[object, ...] = (desde, hasta, estado) if estado else (desde, hasta)
    total = conn.execute(f"SELECT count(*) FROM factura f WHERE {condicion}", params).fetchone()
    filas = conn.execute(
        f"""SELECT f.id, f.numero, f.estado, f.total, f.fecha_emision, f.medio_pago,
                   c.tipo_documento, c.numero_documento, c.nombres, c.apellidos, u.nombre, f.error
            FROM factura f JOIN cliente c ON c.id = f.cliente_id JOIN usuario u ON u.id = f.usuario_id
            WHERE {condicion}
            ORDER BY f.fecha_emision DESC, f.id DESC
            LIMIT %s OFFSET %s""",
        (*params, limite, desplazamiento),
    ).fetchall()
    return [
        FacturaListada(
            id=f[0],
            numero=f[1],
            estado=f[2],
            total=f[3],
            fecha_emision=f[4],
            medio_pago=f[5],
            cliente_tipo=f[6],
            cliente_documento=f[7],
            cliente_nombre=nombre_para_mostrar(f[6], f[8], f[9]),
            vendedor=f[10],
            error=f[11],
        )  # fmt: skip
        for f in filas
    ], (total[0] if total else 0)
