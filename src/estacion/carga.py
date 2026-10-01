"""Carga a PostgreSQL de despachos ya normalizados y analizados.

Propiedades
  * Una sola transacción: si algo falla, no queda nada a medias.
  * Idempotente: el mismo archivo (sha256) no se procesa dos veces, y como el surtidor
    exporta ventanas que se solapan, un despacho ya existente (mismo id) se cuenta como
    duplicado y no se toca. Si el mismo id llega con contenido distinto -> conflicto (anomalía ALTA).
  * Las anomalías se registran solo para despachos nuevos.

Requiere psycopg 3:  pip install "psycopg[binary]"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .calidad import TOLERANCIA_TOTALIZADOR
from .modelos import Anomalia, Despacho, RechazoFila, Severidad, TurnoDerivado

LOTE = 1000


@dataclass(slots=True)
class ResumenCarga:
    archivo_repetido: bool = False
    importacion_id: int | None = None
    nuevos: int = 0
    duplicados: int = 0
    conflictos: int = 0
    rechazados: int = 0
    anomalias: int = 0


def cargar(
    dsn: str,
    *,
    marca: str,
    archivo_nombre: str,
    archivo_sha256: str,
    despachos: list[Despacho],
    turnos: list[TurnoDerivado],
    anomalias: list[Anomalia],
    rechazos: list[RechazoFila],
) -> ResumenCarga:
    import psycopg
    from psycopg.types.json import Jsonb

    codigos = {d.codigo_surtidor for d in despachos}
    if len(codigos) != 1:
        raise ValueError(f"se esperaba un solo surtidor por archivo, hay: {sorted(codigos)}")
    (codigo,) = codigos
    resumen = ResumenCarga(rechazados=len(rechazos))

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:  # commit al salir; rollback si hay excepción
        cur.execute(
            """INSERT INTO surtidor (marca, codigo_externo) VALUES (%s, %s)
               ON CONFLICT (marca, codigo_externo) DO UPDATE SET marca = EXCLUDED.marca
               RETURNING id""",
            (marca, codigo),
        )
        surtidor_id = _id_devuelto(cur)

        cur.execute(
            """INSERT INTO importacion (surtidor_id, archivo_nombre, archivo_sha256)
               VALUES (%s, %s, %s)
               ON CONFLICT (surtidor_id, archivo_sha256) DO NOTHING RETURNING id""",
            (surtidor_id, archivo_nombre, archivo_sha256),
        )
        fila = cur.fetchone()
        if fila is None:
            resumen.archivo_repetido = True
            return resumen
        importacion_id = resumen.importacion_id = fila[0]

        # Productos
        producto_id: dict[str, int] = {}
        for codigo_prod in {d.producto for d in despachos}:
            cur.execute(
                """INSERT INTO producto (codigo, nombre) VALUES (%s, %s)
                   ON CONFLICT (codigo) DO UPDATE SET codigo = EXCLUDED.codigo RETURNING id""",
                (codigo_prod, codigo_prod.title()),
            )
            producto_id[codigo_prod] = _id_devuelto(cur)

        # Turnos: se amplía el rango si ya existían; un turno solo pasa a CERRADO, nunca reabre.
        turno_id: dict[int, int] = {}
        for t in turnos:
            cur.execute(
                """INSERT INTO turno (surtidor_id, id_cierre, inicio, fin, estado)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (surtidor_id, id_cierre) DO UPDATE SET
                       inicio = LEAST(turno.inicio, EXCLUDED.inicio),
                       fin    = GREATEST(turno.fin, EXCLUDED.fin),
                       estado = CASE WHEN turno.estado = 'CERRADO' THEN 'CERRADO' ELSE EXCLUDED.estado END
                   RETURNING id""",
                (surtidor_id, t.id_cierre, t.inicio, t.fin, t.estado),
            )
            turno_id[t.id_cierre] = _id_devuelto(cur)
        cur.execute(
            """UPDATE turno SET estado = 'CERRADO'
               WHERE surtidor_id = %(s)s AND estado = 'ABIERTO'
                 AND id_cierre < (SELECT max(id_cierre) FROM turno WHERE surtidor_id = %(s)s)""",
            {"s": surtidor_id},
        )

        # Clasificar: nuevo / duplicado / conflicto
        existentes: dict[int, str] = {}
        ids = [d.id_externo for d in despachos]
        for i in range(0, len(ids), LOTE):
            cur.execute(
                """SELECT id_externo, hash_contenido FROM despacho
                   WHERE surtidor_id = %s AND id_externo = ANY(%s::bigint[])""",
                (surtidor_id, ids[i : i + LOTE]),
            )
            existentes.update(cur.fetchall())

        nuevos = [d for d in despachos if d.id_externo not in existentes]
        conflictos = [
            d for d in despachos if d.id_externo in existentes and existentes[d.id_externo] != d.hash_contenido
        ]
        resumen.nuevos = len(nuevos)
        resumen.conflictos = len(conflictos)
        resumen.duplicados = len(despachos) - len(nuevos) - len(conflictos)

        cur.executemany(
            """INSERT INTO despacho (
                   surtidor_id, id_externo, turno_id, lado, pistola, producto_id,
                   inicio, fin, inicio_reloj, fin_reloj, fecha_corregida,
                   volumen_bruto, volumen_neto, valor, ppu, forma_pago,
                   placa_raw, placa_tipo, ref_vehiculo, vehiculo_id,
                   kilometraje_raw, kilometraje, totalizador_vol, totalizador_valor,
                   hash_contenido, crudo, importacion_id)
               VALUES (
                   %(surtidor)s, %(id_externo)s, %(turno)s, %(lado)s, %(pistola)s, %(producto)s,
                   %(inicio)s, %(fin)s, %(inicio_reloj)s, %(fin_reloj)s, %(corregida)s,
                   %(vol_bruto)s, %(vol_neto)s, %(valor)s, %(ppu)s, %(pago)s,
                   %(placa_raw)s, %(placa_tipo)s, %(ref)s,
                   (SELECT id FROM vehiculo WHERE identificador = %(ref)s),
                   %(km_raw)s, %(km)s, %(tot_vol)s, %(tot_valor)s,
                   %(hash)s, %(crudo)s, %(importacion)s)""",
            [
                {
                    "surtidor": surtidor_id,
                    "id_externo": d.id_externo,
                    "turno": turno_id[d.id_cierre],
                    "lado": d.lado,
                    "pistola": d.pistola,
                    "producto": producto_id[d.producto],
                    "inicio": d.inicio,
                    "fin": d.fin,
                    "inicio_reloj": d.inicio_reloj,
                    "fin_reloj": d.fin_reloj,
                    "corregida": d.fecha_corregida,
                    "vol_bruto": d.volumen_bruto,
                    "vol_neto": d.volumen_neto,
                    "valor": d.valor,
                    "ppu": d.ppu,
                    "pago": d.forma_pago.value,
                    "placa_raw": d.placa_raw,
                    "placa_tipo": d.placa_tipo.value,
                    "ref": d.ref_vehiculo,
                    "km_raw": d.kilometraje_raw,
                    "km": d.kilometraje,
                    "tot_vol": d.totalizador_vol,
                    "tot_valor": d.totalizador_valor,
                    "hash": d.hash_contenido,
                    "crudo": Jsonb(d.crudo),
                    "importacion": importacion_id,
                }
                for d in nuevos
            ],
        )

        # ids internos de los despachos nuevos (para enlazar anomalías)
        cur.execute("SELECT id_externo, id FROM despacho WHERE importacion_id = %s", (importacion_id,))
        interno: dict[int, int] = dict(cur.fetchall())

        registradas = 0
        for a in anomalias:
            if a.id_despacho is not None and a.id_despacho not in interno:
                continue  # despacho ya importado antes: su anomalía ya se registró
            cur.execute(
                """INSERT INTO anomalia (importacion_id, despacho_id, turno_id, tipo, severidad, detalle)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (
                    importacion_id,
                    None if a.id_despacho is None else interno[a.id_despacho],
                    None if a.id_cierre is None else turno_id.get(a.id_cierre),
                    a.tipo,
                    a.severidad.value,
                    Jsonb(a.detalle),
                ),
            )
            registradas += cur.rowcount
        for d in conflictos:
            cur.execute(
                """INSERT INTO anomalia (importacion_id, despacho_id, tipo, severidad, detalle)
                   SELECT %s, id, 'DESPACHO_CAMBIADO_EN_REEXPORTACION', 'ALTA', %s
                   FROM despacho WHERE surtidor_id = %s AND id_externo = %s
                   ON CONFLICT DO NOTHING""",
                (importacion_id, Jsonb({"hash_nuevo": d.hash_contenido}), surtidor_id, d.id_externo),
            )
            registradas += cur.rowcount

        # Continuidad del contador CONTRA LO YA GUARDADO: `calidad` solo ve el lote, y una fuente en tiempo real
        # (simulador, Wayne) entrega lotes de un despacho. Mismo tipo de anomalía: no se duplica con la del lote.
        cur.execute(
            """INSERT INTO anomalia (importacion_id, despacho_id, tipo, severidad, detalle)
               SELECT %(i)s, x.id,
                      CASE WHEN x.dif > 0 THEN 'GAP_TOTALIZADOR' ELSE 'RETROCESO_TOTALIZADOR' END, %(sev)s,
                      jsonb_build_object('lado', x.lado, 'pistola', x.pistola, 'despacho_previo', x.previo,
                                         'diferencia_galones', x.dif::text,
                                         'nota', 'continuidad contra los despachos ya guardados')
               FROM (SELECT d.id, d.importacion_id, d.lado, d.pistola,
                            lag(d.id_externo) OVER w AS previo,
                            d.totalizador_vol - lag(d.totalizador_vol) OVER w - d.volumen_bruto AS dif
                     FROM despacho d
                     WHERE d.surtidor_id = %(s)s AND d.totalizador_vol IS NOT NULL
                     WINDOW w AS (PARTITION BY d.lado, d.pistola ORDER BY d.id_externo)) x
               WHERE x.importacion_id = %(i)s AND abs(x.dif) > %(tol)s
               ON CONFLICT DO NOTHING""",
            {"i": importacion_id, "s": surtidor_id, "sev": Severidad.ALTA.value, "tol": TOLERANCIA_TOTALIZADOR},
        )
        registradas += cur.rowcount

        # Crédito con placa/equipo que aún no está asignado a un cliente
        cur.execute(
            """INSERT INTO anomalia (importacion_id, despacho_id, tipo, severidad, detalle)
               SELECT %(i)s, d.id, 'VEHICULO_SIN_CLIENTE', %(sev)s,
                      jsonb_build_object('ref_vehiculo', d.ref_vehiculo)
               FROM despacho d
               WHERE d.importacion_id = %(i)s AND d.forma_pago = 'CREDITO'
                 AND d.ref_vehiculo IS NOT NULL AND d.vehiculo_id IS NULL
               ON CONFLICT DO NOTHING""",
            {"i": importacion_id, "sev": Severidad.ALTA.value},
        )
        registradas += cur.rowcount
        resumen.anomalias = registradas

        for r in rechazos:
            cur.execute(
                "INSERT INTO fila_rechazada (importacion_id, id_externo, motivo, crudo) VALUES (%s, %s, %s, %s)",
                (importacion_id, r.id_externo, r.motivo, Jsonb(r.crudo)),
            )

        cur.execute(
            """UPDATE importacion SET terminada_en = now(), filas_leidas = %s, filas_nuevas = %s,
                      filas_duplicadas = %s, filas_conflicto = %s, filas_rechazadas = %s
               WHERE id = %s""",
            (
                len(despachos) + len(rechazos),
                resumen.nuevos,
                resumen.duplicados,
                resumen.conflictos,
                resumen.rechazados,
                importacion_id,
            ),
        )
    return resumen


def _id_devuelto(cur: Any) -> int:
    """El id de un `INSERT ... RETURNING id` que siempre devuelve una fila."""
    fila = cur.fetchone()
    if fila is None:
        raise RuntimeError("la base no devolvió el id esperado")
    return int(fila[0])
