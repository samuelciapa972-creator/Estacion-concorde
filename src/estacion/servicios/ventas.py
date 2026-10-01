"""Ventas para la pista: modo manual, lista de pendientes por facturar y enlace venta manual ↔ despacho.

Modo manual: cuando no llegan datos del surtidor, el bombero digita lado, producto, galones y valor. Es una
venta como cualquier otra para facturar (`Origen("venta_manual", id)`).

Enlace: si después llega del surtidor el despacho que corresponde a una venta manual, se enlazan; son la
MISMA venta y solo una de las dos se factura:
  * si una ya tiene factura (en cualquier estado, incluso RECHAZADO: su número debe reenviarse), esa manda y
    la otra pasa a NO_FACTURABLE;
  * si ninguna tiene factura, NO se enlaza todavía: cuál de las dos se factura es una decisión PENDIENTE del
    usuario (no se elige una por defecto). Mientras tanto, la primera que se facture define el enlace;
  * si las dos tienen factura, no se enlaza: ya hubo doble facturación y se corrige con nota crédito.
La base lo garantiza además con triggers (migración 0003).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..calidad import TOLERANCIA_TOTALIZADOR, TOLERANCIA_VALOR
from ._bd import Conexion, ahora_local, auditar, exigir_autocommit
from .emision import Origen

FORMAS_DE_PAGO = {"CONTADO", "CREDITO"}
_LADO = re.compile(r"^[A-Z0-9]$")


class VentaInvalida(ValueError):
    """Datos de la venta manual inválidos (el mensaje dice cuáles)."""


class EnlaceInvalido(ValueError):
    """La venta manual y el despacho no se pueden enlazar (no coinciden, ya enlazados o ambos facturados)."""


@dataclass(frozen=True, slots=True)
class VentaPendiente:
    origen: Origen
    surtidor_id: int
    lado: str
    producto: str
    volumen: Decimal
    valor: Decimal
    ppu: Decimal
    forma_pago: str
    fecha: datetime  # fin del despacho o hora de registro de la venta manual
    placa: str | None  # placa/equipo normalizado del despacho (NULL en la venta manual)


# ------------------------------------------------------------------------------------------ modo manual
def _validar_cantidades(volumen: Decimal, valor: Decimal, ppu: Decimal | None) -> list[str]:
    problemas: list[str] = []
    for nombre, v in (("volumen", volumen), ("valor", valor), ("ppu", ppu)):
        if v is not None and not isinstance(v, Decimal):
            problemas.append(f"{nombre} debe ser Decimal (nunca float)")
    if problemas:
        return problemas
    if not volumen.is_finite() or volumen <= 0 or volumen != volumen.quantize(Decimal("0.001")):
        problemas.append("los galones deben ser positivos y con máximo 3 decimales")
    if not valor.is_finite() or valor <= 0 or valor != valor.to_integral_value():
        problemas.append("el valor debe ser un número entero de pesos positivo")
    if ppu is not None and (not ppu.is_finite() or ppu <= 0 or ppu != ppu.quantize(Decimal("0.01"))):
        problemas.append("el precio por galón debe ser positivo y con máximo 2 decimales")
    if not problemas and ppu is not None and abs(volumen * ppu - valor) > TOLERANCIA_VALOR:
        problemas.append(
            f"galones × precio = {(volumen * ppu).quantize(Decimal(1))} no coincide con el valor {valor} "
            f"(tolerancia {TOLERANCIA_VALOR} pesos)"
        )
    return problemas


def registrar_venta_manual(
    conn: Conexion,
    *,
    usuario_id: int,
    surtidor_id: int,
    lado: str,
    producto: str,
    volumen: Decimal,
    valor: Decimal,
    ppu: Decimal | None = None,
    forma_pago: str = "CONTADO",
    reloj: Callable[[], datetime] = ahora_local,
) -> int:
    """Guarda la venta que digitó el bombero y devuelve su id. Sin `ppu`, se calcula como valor / galones."""
    exigir_autocommit(conn)
    lado = lado.strip().upper()
    producto = producto.strip().upper()
    problemas = _validar_cantidades(volumen, valor, ppu)
    if not _LADO.match(lado):
        problemas.append(f"lado inválido: {lado!r}")
    if forma_pago not in FORMAS_DE_PAGO:
        problemas.append(f"forma de pago inválida: {forma_pago!r}")
    if problemas:
        raise VentaInvalida("; ".join(problemas))
    if ppu is None:
        ppu = (valor / volumen).quantize(Decimal("0.01"))

    with conn.transaction():
        if conn.execute("SELECT 1 FROM usuario WHERE id = %s AND activo", (usuario_id,)).fetchone() is None:
            raise VentaInvalida("el usuario no existe o está inactivo")
        if conn.execute("SELECT 1 FROM surtidor WHERE id = %s AND activo", (surtidor_id,)).fetchone() is None:
            raise VentaInvalida("el surtidor no existe o está inactivo")
        fila = conn.execute("SELECT id FROM producto WHERE codigo = %s", (producto,)).fetchone()
        if fila is None:
            raise VentaInvalida(f"producto desconocido: {producto!r}")
        venta_id: int = conn.execute(
            """INSERT INTO venta_manual (surtidor_id, lado, producto_id, volumen, valor, ppu, forma_pago,
                                         usuario_id, registrada_en)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (surtidor_id, lado, fila[0], volumen, valor, ppu, forma_pago, usuario_id, reloj().replace(microsecond=0)),
        ).fetchone()[0]  # type: ignore[index]
        auditar(
            conn,
            "VENTA_MANUAL_REGISTRADA",
            "venta_manual",
            venta_id,
            usuario_id=usuario_id,
            detalle={"lado": lado, "producto": producto, "volumen": str(volumen), "valor": str(valor)},
        )
        return venta_id


# ------------------------------------------------------------------------------------------ pendientes
def listar_pendientes(
    conn: Conexion,
    *,
    surtidor_id: int | None = None,
    lado: str | None = None,
    desde: datetime | None = None,
    forma_pago: str | None = "CONTADO",
    limite: int = 50,
) -> list[VentaPendiente]:
    """Despachos y ventas manuales PENDIENTES de facturar, los más recientes primero.

    Por defecto solo contado (el crédito de flota es una etapa posterior); `forma_pago=None` trae todas.
    """
    filas = conn.execute(
        """SELECT * FROM (
               SELECT 'despacho' AS tipo, d.id, d.surtidor_id, d.lado, p.codigo, d.volumen_bruto, d.valor, d.ppu,
                      d.forma_pago, d.fin AS fecha, d.ref_vehiculo
               FROM despacho d JOIN producto p ON p.id = d.producto_id
               WHERE d.estado_facturacion = 'PENDIENTE'
               UNION ALL
               SELECT 'venta_manual', v.id, v.surtidor_id, v.lado, p.codigo, v.volumen, v.valor, v.ppu,
                      v.forma_pago, v.registrada_en, NULL
               FROM venta_manual v JOIN producto p ON p.id = v.producto_id
               WHERE v.estado_facturacion = 'PENDIENTE'
           ) x
           WHERE (%(s)s::smallint IS NULL OR x.surtidor_id = %(s)s)
             AND (%(l)s::text IS NULL OR x.lado = %(l)s)
             AND (%(d)s::timestamp IS NULL OR x.fecha >= %(d)s)
             AND (%(f)s::text IS NULL OR x.forma_pago = %(f)s)
           ORDER BY x.fecha DESC, x.tipo, x.id DESC
           LIMIT %(n)s""",
        {"s": surtidor_id, "l": None if lado is None else lado.upper(), "d": desde, "f": forma_pago, "n": limite},
    ).fetchall()
    return [
        VentaPendiente(Origen(tipo, id_), s, lado_, prod, vol, val, ppu, pago, fecha, placa)
        for tipo, id_, s, lado_, prod, vol, val, ppu, pago, fecha, placa in filas
    ]


# ------------------------------------------------------------------------------------------ enlace
def enlazar_venta_manual(conn: Conexion, *, venta_id: int, despacho_id: int, usuario_id: int) -> Origen:
    """Enlaza la venta manual con su despacho del surtidor. Devuelve el origen que queda para facturar."""
    exigir_autocommit(conn)
    with conn.transaction():
        # Mismo orden de bloqueo que la base (venta y luego despacho): sin bloqueos mutuos con la emisión.
        venta = conn.execute(
            """SELECT v.surtidor_id, v.lado, v.producto_id, v.volumen, v.valor, v.despacho_id,
                      EXISTS (SELECT 1 FROM factura f WHERE f.venta_manual_id = v.id)
               FROM venta_manual v WHERE v.id = %s FOR UPDATE""",
            (venta_id,),
        ).fetchone()
        despacho = conn.execute(
            """SELECT d.surtidor_id, d.lado, d.producto_id, d.volumen_bruto, d.valor, d.estado_facturacion,
                      EXISTS (SELECT 1 FROM factura f WHERE f.despacho_id = d.id),
                      EXISTS (SELECT 1 FROM venta_manual v WHERE v.despacho_id = d.id)
               FROM despacho d WHERE d.id = %s FOR UPDATE""",
            (despacho_id,),
        ).fetchone()
        if venta is None or despacho is None:
            raise EnlaceInvalido("la venta manual o el despacho no existen")
        v_surt, v_lado, v_prod, v_vol, v_valor, v_enlace, v_facturada = venta
        d_surt, d_lado, d_prod, d_vol, d_valor, d_estado, d_facturado, d_enlazado = despacho

        problemas = []
        if v_enlace is not None or d_enlazado:
            problemas.append("ya está enlazado")
        if (v_surt, v_lado, v_prod) != (d_surt, d_lado, d_prod):
            problemas.append("surtidor, lado o producto no coinciden")
        if abs(v_vol - d_vol) > TOLERANCIA_TOTALIZADOR:
            problemas.append(f"galones distintos ({v_vol} vs {d_vol})")
        if abs(v_valor - d_valor) > TOLERANCIA_VALOR:
            problemas.append(f"valores distintos ({v_valor} vs {d_valor})")
        if v_facturada and d_facturado:
            problemas.append("las dos ya tienen factura: hubo doble facturación (se corrige con nota crédito)")
        if not v_facturada and not d_facturado:
            problemas.append(
                "ninguna de las dos tiene factura: la regla para elegir cuál se factura está pendiente de decisión"
            )
        if problemas:
            raise EnlaceInvalido("; ".join(problemas))

        conn.execute("UPDATE venta_manual SET despacho_id = %s WHERE id = %s", (despacho_id, venta_id))
        if v_facturada:
            queda = Origen("venta_manual", venta_id)
            conn.execute("UPDATE despacho SET estado_facturacion = 'NO_FACTURABLE' WHERE id = %s", (despacho_id,))
        else:  # el despacho ya tiene factura
            queda = Origen("despacho", despacho_id)
            conn.execute("UPDATE venta_manual SET estado_facturacion = 'NO_FACTURABLE' WHERE id = %s", (venta_id,))
        auditar(
            conn,
            "VENTA_MANUAL_ENLAZADA",
            "venta_manual",
            venta_id,
            usuario_id=usuario_id,
            detalle={"despacho_id": despacho_id, "factura_por": queda.tipo},
        )
        return queda
