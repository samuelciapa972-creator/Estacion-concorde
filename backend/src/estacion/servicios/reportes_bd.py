"""Reportes de ventas leídos de la base (vistas de la migración 0001) y exportación a Excel con `reportes.py`.

ALCANCE: los reportes diario y mensual suman los despachos del surtidor MÁS las ventas manuales sin enlace (columna
`origen`); una manual enlazada a su despacho es la misma venta y no se cuenta dos veces (migración 0004). El reporte
por turno es solo del surtidor (las manuales no tienen turno). Los despachos del SIMULADOR sí entran: filtrar por
`surtidor_id` para separarlos.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..calidad import TOLERANCIA_TOTALIZADOR, TOLERANCIA_VALOR
from ..modelos import Anomalia, Despacho, FormaPago, PlacaTipo, Severidad, TurnoDerivado
from ..reportes import generar_excel
from ._bd import Conexion


class SurtidorDesconocido(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class FilaVentas:
    surtidor_id: int
    periodo: date  # día o primer día del mes
    producto: str
    forma_pago: str
    origen: str  # SURTIDOR | MANUAL
    despachos: int
    galones: Decimal
    valor: Decimal


@dataclass(frozen=True, slots=True)
class FilaTurno:
    turno_id: int
    surtidor_id: int
    id_cierre: int
    estado: str
    inicio: datetime
    fin: datetime
    lado: str
    despachos: int
    galones: Decimal
    valor: Decimal
    lectura_final_vol: Decimal | None
    lectura_final_valor: Decimal | None


@dataclass(frozen=True, slots=True)
class FilaAnomalia:
    id: int
    tipo: str
    severidad: str
    surtidor_id: int | None
    despacho_id_externo: int | None
    detalle: dict[str, Any]
    creada_en: datetime
    resuelta: bool


def _ventas(conn: Conexion, vista: str, columna: str, desde: date, hasta: date, surtidor_id: int | None):
    filas = conn.execute(
        f"""SELECT surtidor_id, {columna}, producto, forma_pago, origen, despachos, galones, valor FROM {vista}
            WHERE {columna} BETWEEN %(d)s AND %(h)s AND (%(s)s::smallint IS NULL OR surtidor_id = %(s)s)
            ORDER BY {columna}, surtidor_id, producto, forma_pago, origen DESC""",
        {"d": desde, "h": hasta, "s": surtidor_id},
    ).fetchall()
    return [FilaVentas(*f) for f in filas]


def ventas_diarias(conn: Conexion, desde: date, hasta: date, surtidor_id: int | None = None) -> list[FilaVentas]:
    return _ventas(conn, "v_ventas_diarias", "fecha", desde, hasta, surtidor_id)


def ventas_mensuales(conn: Conexion, desde: date, hasta: date, surtidor_id: int | None = None) -> list[FilaVentas]:
    """Meses cuyo primer día cae entre el primer día del mes de `desde` y `hasta`."""
    return _ventas(conn, "v_ventas_mensuales", "mes", desde.replace(day=1), hasta, surtidor_id)


def resumen_turnos(
    conn: Conexion, desde: date, hasta: date, surtidor_id: int | None = None, turno_id: int | None = None
) -> list[FilaTurno]:
    filas = conn.execute(
        """SELECT * FROM v_turno_resumen
           WHERE inicio::date <= %(h)s AND fin::date >= %(d)s
             AND (%(s)s::smallint IS NULL OR surtidor_id = %(s)s)
             AND (%(t)s::bigint IS NULL OR turno_id = %(t)s)
           ORDER BY inicio, surtidor_id, lado""",
        {"d": desde, "h": hasta, "s": surtidor_id, "t": turno_id},
    ).fetchall()
    return [FilaTurno(*f) for f in filas]


@dataclass(frozen=True, slots=True)
class PosibleDuplicado:
    venta_manual_id: int
    despacho_id: int
    surtidor_id: int
    lado: str
    producto: str
    galones: Decimal
    valor: Decimal
    registrada_en: datetime  # venta manual
    inicio_despacho: datetime


VENTANA_DUPLICADOS = "2 hours"


def posibles_duplicados(conn: Conexion, desde: date, hasta: date) -> list[PosibleDuplicado]:
    """Ventas manuales SIN enlace que coinciden con un despacho tampoco enlazado: mismo surtidor, lado y producto,
    galones y valor dentro de las tolerancias del enlace, y a menos de 2 horas. Probablemente es la misma venta
    contada dos veces en los reportes. Solo informa: el enlace lo decide una persona."""
    filas = conn.execute(
        f"""SELECT v.id, d.id, v.surtidor_id, v.lado, p.codigo, v.volumen, v.valor, v.registrada_en, d.inicio
            FROM venta_manual v
            JOIN despacho d ON d.surtidor_id = v.surtidor_id AND d.lado = v.lado AND d.producto_id = v.producto_id
                AND abs(d.volumen_bruto - v.volumen) <= %(tol_vol)s AND abs(d.valor - v.valor) <= %(tol_valor)s
                AND d.inicio BETWEEN v.registrada_en - interval '{VENTANA_DUPLICADOS}'
                                 AND v.registrada_en + interval '{VENTANA_DUPLICADOS}'
            JOIN producto p ON p.id = v.producto_id
            WHERE v.despacho_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM venta_manual otra WHERE otra.despacho_id = d.id)
              AND v.registrada_en::date BETWEEN %(d)s AND %(h)s
            ORDER BY v.registrada_en, v.id, d.inicio""",
        {"tol_vol": TOLERANCIA_TOTALIZADOR, "tol_valor": TOLERANCIA_VALOR, "d": desde, "h": hasta},
    ).fetchall()
    return [PosibleDuplicado(*f) for f in filas]


def anomalias(
    conn: Conexion, *, resuelta: bool | None = False, severidad: str | None = None, limite: int = 100
) -> list[FilaAnomalia]:
    filas = conn.execute(
        """SELECT a.id, a.tipo, a.severidad, i.surtidor_id, d.id_externo, a.detalle, a.creada_en, a.resuelta
           FROM anomalia a JOIN importacion i ON i.id = a.importacion_id
           LEFT JOIN despacho d ON d.id = a.despacho_id
           WHERE (%(r)s::boolean IS NULL OR a.resuelta = %(r)s)
             AND (%(sev)s::text IS NULL OR a.severidad = %(sev)s)
           ORDER BY CASE a.severidad WHEN 'ALTA' THEN 0 WHEN 'AVISO' THEN 1 ELSE 2 END, a.creada_en DESC, a.id DESC
           LIMIT %(n)s""",
        {"r": resuelta, "sev": severidad, "n": limite},
    ).fetchall()
    return [FilaAnomalia(*f) for f in filas]


# ------------------------------------------------------------------------------------------ Excel
def datos_para_excel(
    conn: Conexion, surtidor_id: int, desde: date, hasta: date
) -> tuple[list[Despacho], list[Anomalia], list[TurnoDerivado]]:
    """Reconstruye los `Despacho` (y sus anomalías y turnos) de un surtidor para `reportes.generar_excel`."""
    if conn.execute("SELECT 1 FROM surtidor WHERE id = %s", (surtidor_id,)).fetchone() is None:
        raise SurtidorDesconocido(f"no existe el surtidor {surtidor_id}")
    filas = conn.execute(
        """SELECT s.marca, s.codigo_externo, d.id_externo, t.id_cierre, d.lado, d.pistola, p.codigo,
                  d.inicio_reloj, d.fin_reloj, d.inicio, d.fin, d.volumen_bruto, d.volumen_neto, d.valor, d.ppu,
                  d.forma_pago, d.placa_raw, d.placa_tipo, d.ref_vehiculo, d.kilometraje_raw, d.kilometraje,
                  d.totalizador_vol, d.totalizador_valor, d.hash_contenido, d.crudo, d.fecha_corregida
           FROM despacho d JOIN surtidor s ON s.id = d.surtidor_id JOIN turno t ON t.id = d.turno_id
           JOIN producto p ON p.id = d.producto_id
           WHERE d.surtidor_id = %s AND d.fecha_operativa BETWEEN %s AND %s
           ORDER BY d.inicio, d.id_externo""",
        (surtidor_id, desde, hasta),
    ).fetchall()
    despachos = [
        Despacho(
            surtidor_marca=f[0],
            codigo_surtidor=f[1],
            id_externo=f[2],
            id_cierre=f[3],
            lado=f[4],
            pistola=f[5],
            producto=f[6],
            inicio_reloj=f[7],
            fin_reloj=f[8],
            inicio=f[9],
            fin=f[10],
            volumen_bruto=f[11],
            volumen_neto=f[12],
            valor=f[13],
            ppu=f[14],
            forma_pago=FormaPago(f[15]),
            placa_raw=f[16],
            placa_tipo=PlacaTipo(f[17]),
            ref_vehiculo=f[18],
            kilometraje_raw=f[19],
            kilometraje=f[20],
            totalizador_vol=f[21],
            totalizador_valor=f[22],
            hash_contenido=f[23],
            crudo=f[24],
            fecha_corregida=f[25],
        )  # fmt: skip
        for f in filas
    ]
    anomalias_bd = conn.execute(
        """SELECT a.tipo, a.severidad, d.id_externo, t.id_cierre, a.detalle
           FROM anomalia a JOIN importacion i ON i.id = a.importacion_id
           LEFT JOIN despacho d ON d.id = a.despacho_id LEFT JOIN turno t ON t.id = a.turno_id
           WHERE i.surtidor_id = %s
             AND (d.id IS NULL OR d.fecha_operativa BETWEEN %s AND %s)""",
        (surtidor_id, desde, hasta),
    ).fetchall()
    lista_anomalias = [Anomalia(f[0], Severidad(f[1]), f[2], f[3], f[4]) for f in anomalias_bd]

    por_turno: dict[int, list[Despacho]] = {}
    for d in despachos:
        por_turno.setdefault(d.id_cierre, []).append(d)
    estados = dict(
        conn.execute("SELECT id_cierre, estado FROM turno WHERE surtidor_id = %s", (surtidor_id,)).fetchall()
    )
    turnos = [
        TurnoDerivado(
            id_cierre=c,
            inicio=min(d.inicio for d in ds),
            fin=max(d.fin for d in ds),
            estado=estados[c],
            despachos=len(ds),
            volumen_bruto=sum((d.volumen_bruto for d in ds), Decimal(0)),
            valor=sum((d.valor for d in ds), Decimal(0)),
        )
        for c, ds in sorted(por_turno.items())
    ]
    return despachos, lista_anomalias, turnos


def excel_ventas(conn: Conexion, surtidor_id: int, desde: date, hasta: date) -> bytes:
    """El mismo libro de `reportes.py` (Diario, Mensual, Turnos, Datos, Alertas, Notas), desde la base."""
    despachos, lista_anomalias, turnos = datos_para_excel(conn, surtidor_id, desde, hasta)
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / "reporte.xlsx"
        generar_excel(
            despachos, lista_anomalias, turnos, ruta, archivo_origen=f"base de datos, {desde} a {hasta}", sha256=""
        )
        return ruta.read_bytes()
