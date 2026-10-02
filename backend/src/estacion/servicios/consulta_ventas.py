"""Búsqueda de ventas para el administrador, con los filtros que tenía Nexus ("Buscar Ventas"): fecha y hora,
placa, cliente, número (del surtidor o de la factura), vendedor y equipo (surtidor), más lado, producto, forma de
pago y estado de facturación. Devuelve una página de filas, los totales del filtro completo y los totales por
manguera (surtidor + lado + producto).

Una venta = un despacho del surtidor, o una venta manual que TODAVÍA no está enlazada a su despacho. Una venta
manual enlazada es la misma venta que su despacho: no se cuenta dos veces, y su factura aparece en la fila del
despacho (la base garantiza que es una sola, migración 0003).

Cliente de la fila: el de la factura; si no hay factura, el dueño del vehículo (crédito de flota).
Vendedor: quien emitió la factura; si no hay, quien digitó la venta manual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from ._bd import Conexion

ESTADOS_FACTURACION = ("PENDIENTE", "EN_PROCESO", "FACTURADO", "INCIERTO", "NO_FACTURABLE")

_BASE = """
WITH venta AS (
    SELECT 'despacho'::text AS origen_tipo, d.id AS origen_id, d.id_externo, d.surtidor_id, d.lado,
           p.codigo AS producto, d.volumen_bruto AS volumen, d.valor, d.forma_pago, d.fin AS fecha,
           d.ref_vehiculo AS placa, d.estado_facturacion, d.vehiculo_id, NULL::smallint AS usuario_venta
    FROM despacho d JOIN producto p ON p.id = d.producto_id
    WHERE d.fin >= %(desde)s AND d.fin < %(hasta)s
    UNION ALL
    SELECT 'venta_manual', v.id, NULL, v.surtidor_id, v.lado, p.codigo, v.volumen, v.valor, v.forma_pago,
           v.registrada_en, NULL, v.estado_facturacion, NULL, v.usuario_id
    FROM venta_manual v JOIN producto p ON p.id = v.producto_id
    WHERE v.despacho_id IS NULL AND v.registrada_en >= %(desde)s AND v.registrada_en < %(hasta)s
),
filtrada AS (
    SELECT x.*, s.marca AS surtidor, f.numero AS factura_numero, f.estado AS factura_estado,
           c.tipo_documento AS cliente_tipo, c.numero_documento AS cliente_documento,
           CASE WHEN c.tipo_documento IN ('NIT', 'NIT_EXTERIOR') THEN c.nombres
                ELSE btrim(c.nombres || ' ' || c.apellidos) END AS cliente_nombre,
           u.id AS vendedor_id, u.nombre AS vendedor
    FROM venta x
    JOIN surtidor s ON s.id = x.surtidor_id
    LEFT JOIN LATERAL (
        SELECT f.* FROM factura f
        WHERE (x.origen_tipo = 'despacho'
               AND (f.despacho_id = x.origen_id
                    OR f.venta_manual_id IN (SELECT vm.id FROM venta_manual vm WHERE vm.despacho_id = x.origen_id)))
           OR (x.origen_tipo = 'venta_manual' AND f.venta_manual_id = x.origen_id)
        LIMIT 1
    ) f ON TRUE
    LEFT JOIN vehiculo veh ON veh.id = x.vehiculo_id
    LEFT JOIN cliente c ON c.id = coalesce(f.cliente_id, veh.cliente_id)
    LEFT JOIN usuario u ON u.id = coalesce(f.usuario_id, x.usuario_venta)
    WHERE (%(surtidor)s::smallint IS NULL OR x.surtidor_id = %(surtidor)s)
      AND (%(lado)s::text IS NULL OR x.lado = %(lado)s)
      AND (%(producto)s::text IS NULL OR x.producto = %(producto)s)
      AND (%(forma_pago)s::text IS NULL OR x.forma_pago = %(forma_pago)s)
      AND (%(estado)s::text IS NULL OR x.estado_facturacion = %(estado)s)
      AND (%(placa)s::text IS NULL OR x.placa LIKE '%%' || %(placa)s || '%%')
      AND (%(cliente)s::text IS NULL OR c.numero_documento = %(cliente)s)
      AND (%(id_externo)s::bigint IS NULL OR x.id_externo = %(id_externo)s)
      AND (%(factura)s::text IS NULL OR f.numero = %(factura)s)
      AND (%(vendedor)s::smallint IS NULL OR u.id = %(vendedor)s)
)
"""


@dataclass(frozen=True, slots=True)
class FiltroVentas:
    desde: datetime  # incluido (hora local de la estación)
    hasta: datetime  # excluido
    surtidor_id: int | None = None
    lado: str | None = None
    producto: str | None = None
    forma_pago: str | None = None
    estado_facturacion: str | None = None
    placa: str | None = None  # parte de la placa; se normaliza como en el surtidor (sin espacios ni guiones)
    cliente_documento: str | None = None
    id_externo: int | None = None  # número del despacho en el surtidor
    factura: str | None = None  # número completo, p. ej. SETP1
    vendedor_id: int | None = None

    def parametros(self) -> dict[str, Any]:
        def limpio(texto: str | None) -> str | None:
            t = re.sub(r"[\s.\-]", "", texto or "").upper()
            return t or None

        return {
            "desde": self.desde, "hasta": self.hasta, "surtidor": self.surtidor_id,
            "lado": (self.lado or "").strip().upper() or None,
            "producto": (self.producto or "").strip().upper() or None,
            "forma_pago": self.forma_pago, "estado": self.estado_facturacion, "placa": limpio(self.placa),
            "cliente": limpio(self.cliente_documento), "id_externo": self.id_externo,
            "factura": limpio(self.factura), "vendedor": self.vendedor_id,
        }  # fmt: skip


@dataclass(frozen=True, slots=True)
class FilaVenta:
    origen_tipo: str
    origen_id: int
    id_externo: int | None
    surtidor_id: int
    surtidor: str
    lado: str
    producto: str
    volumen: Decimal
    valor: Decimal
    forma_pago: str
    fecha: datetime
    placa: str | None
    estado_facturacion: str
    factura_numero: str | None
    factura_estado: str | None
    cliente_tipo: str | None
    cliente_documento: str | None
    cliente_nombre: str | None
    vendedor_id: int | None
    vendedor: str | None


@dataclass(frozen=True, slots=True)
class Totales:
    ventas: int
    galones: Decimal
    valor: Decimal


@dataclass(frozen=True, slots=True)
class TotalManguera:
    surtidor_id: int
    lado: str
    producto: str
    ventas: int
    galones: Decimal
    valor: Decimal


@dataclass(frozen=True, slots=True)
class ResultadoVentas:
    filas: list[FilaVenta]
    total: Totales
    por_manguera: list[TotalManguera] = field(default_factory=list)


def buscar_ventas(
    conn: Conexion, filtro: FiltroVentas, *, limite: int = 100, desplazamiento: int = 0
) -> ResultadoVentas:
    if filtro.hasta <= filtro.desde:
        raise ValueError("`hasta` debe ser posterior a `desde`")
    p = {**filtro.parametros(), "limite": limite, "desplazamiento": desplazamiento}
    filas = conn.execute(
        _BASE
        + """SELECT origen_tipo, origen_id, id_externo, surtidor_id, surtidor, lado, producto, volumen, valor,
                    forma_pago, fecha, placa, estado_facturacion, factura_numero, factura_estado, cliente_tipo,
                    cliente_documento, cliente_nombre, vendedor_id, vendedor
             FROM filtrada ORDER BY fecha DESC, origen_tipo, origen_id DESC
             LIMIT %(limite)s OFFSET %(desplazamiento)s""",
        p,
    ).fetchall()
    mangueras = conn.execute(
        _BASE
        + """SELECT surtidor_id, lado, producto, count(*), coalesce(sum(volumen), 0), coalesce(sum(valor), 0)
             FROM filtrada GROUP BY surtidor_id, lado, producto ORDER BY surtidor_id, lado, producto""",
        p,
    ).fetchall()
    por_manguera = [TotalManguera(*m) for m in mangueras]
    total = Totales(
        ventas=sum(m.ventas for m in por_manguera),
        galones=sum((m.galones for m in por_manguera), Decimal("0.000")),
        valor=sum((m.valor for m in por_manguera), Decimal("0")),
    )
    return ResultadoVentas(filas=[FilaVenta(*f) for f in filas], total=total, por_manguera=por_manguera)


@dataclass(frozen=True, slots=True)
class UsuarioListado:
    id: int
    usuario: str
    nombre: str
    rol: str
    activo: bool


def listar_usuarios(conn: Conexion) -> list[UsuarioListado]:
    """Para el filtro de vendedor. Sin el hash del PIN."""
    filas = conn.execute("SELECT id, usuario, nombre, rol, activo FROM usuario ORDER BY nombre").fetchall()
    return [UsuarioListado(*f) for f in filas]
