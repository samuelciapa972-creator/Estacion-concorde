"""Integración del cargador (`carga.py`) contra PostgreSQL real, con XML sintéticos.

Casos: el mismo archivo dos veces, exportaciones que se solapan (ids repetidos), un id que vuelve con
otro contenido (conflicto), atomicidad ante un error a mitad de la carga y cierre de turnos.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from estacion.calidad import analizar
from estacion.carga import ResumenCarga, cargar
from estacion.parsers import speed_solutions
from test_speed_solutions import Surtidor

pytestmark = pytest.mark.bd

INICIO = datetime(2026, 9, 1, 6, 0)


def xml_de(libros: list[str]) -> str:
    return '<?xml version="1.0"?><catalog>' + "".join(libros) + "</catalog>"


def serie(s: Surtidor, n: int, desde: datetime = INICIO, **kw) -> None:
    for i in range(n):
        s.despacho(desde + timedelta(minutes=45 * i), lado="AB"[i % 2], **kw)


@pytest.fixture
def cargar_xml(bd, tmp_path: Path):
    contador = iter(range(1, 1000))

    def _cargar(xml: str) -> ResumenCarga:
        ruta = tmp_path / f"D{next(contador):03d}.xls"
        ruta.write_text(xml, encoding="utf-8")
        lectura = speed_solutions.leer(ruta)
        despachos, anomalias, turnos = analizar(lectura.despachos)
        return cargar(
            bd.dsn,
            marca=lectura.marca,
            archivo_nombre=ruta.name,
            archivo_sha256=lectura.archivo_sha256,
            despachos=despachos,
            turnos=turnos,
            anomalias=anomalias,
            rechazos=lectura.rechazos,
        )

    return _cargar


def test_el_mismo_archivo_dos_veces_no_hace_nada_la_segunda(bd, cargar_xml):
    s = Surtidor()
    serie(s, 10)
    xml = s.xml()

    primera = cargar_xml(xml)
    assert (primera.archivo_repetido, primera.nuevos, primera.duplicados, primera.conflictos) == (False, 10, 0, 0)

    segunda = cargar_xml(xml)  # mismo contenido, otro nombre: lo identifica el sha256
    assert segunda.archivo_repetido
    assert segunda.importacion_id is None
    assert bd.valor("SELECT count(*) FROM despacho") == 10
    assert bd.valor("SELECT count(*) FROM importacion") == 1


def test_exportaciones_solapadas_cuentan_duplicados_sin_tocarlos(bd, cargar_xml):
    s = Surtidor()
    serie(s, 10)
    s.despacho(INICIO + timedelta(hours=8), lado="A", salto_contador="25.000")  # hueco en el contador
    primeros = list(s.libros)
    serie(s, 5, desde=INICIO + timedelta(hours=9))

    r1 = cargar_xml(xml_de(primeros))
    assert r1.nuevos == 11
    assert bd.valor("SELECT count(*) FROM anomalia WHERE tipo = 'GAP_TOTALIZADOR'") == 1

    r2 = cargar_xml(xml_de(s.libros))  # la ventana nueva repite los 11 anteriores
    assert (r2.nuevos, r2.duplicados, r2.conflictos) == (5, 11, 0)
    assert bd.valor("SELECT count(*) FROM despacho") == 16
    # la anomalía del despacho repetido no se vuelve a registrar
    assert bd.valor("SELECT count(*) FROM anomalia WHERE tipo = 'GAP_TOTALIZADOR'") == 1
    # cada despacho quedó con la importación que lo trajo primero
    assert bd.consultar("SELECT importacion_id, count(*) FROM despacho GROUP BY 1 ORDER BY 1") == [
        (r1.importacion_id, 11),
        (r2.importacion_id, 5),
    ]
    assert bd.consultar("SELECT filas_leidas, filas_nuevas, filas_duplicadas FROM importacion WHERE id = %s",
                        (r2.importacion_id,)) == [(16, 5, 11)]  # fmt: skip


def test_id_repetido_con_otro_contenido_es_conflicto_y_no_se_sobrescribe(bd, cargar_xml):
    s = Surtidor()
    serie(s, 4, placa="GZY848")
    cargar_xml(s.xml())
    id_cambiado = 1002
    valor_original = bd.valor("SELECT valor FROM despacho WHERE id_externo = %s", (id_cambiado,))

    cambiados = [
        libro.replace("<INFO-PLACA>GZY848</INFO-PLACA>", "<INFO-PLACA>ABC123</INFO-PLACA>")
        if f'ID-DESPACHO="{id_cambiado}"' in libro
        else libro
        for libro in s.libros
    ]
    r = cargar_xml(xml_de(cambiados))

    assert (r.nuevos, r.duplicados, r.conflictos) == (0, 3, 1)
    assert bd.consultar(
        """SELECT d.id_externo, a.severidad FROM anomalia a JOIN despacho d ON d.id = a.despacho_id
           WHERE a.tipo = 'DESPACHO_CAMBIADO_EN_REEXPORTACION'"""
    ) == [(id_cambiado, "ALTA")]
    fila = bd.consultar("SELECT placa_raw, valor FROM despacho WHERE id_externo = %s", (id_cambiado,))
    assert fila == [("GZY848", valor_original)]  # se conserva lo que ya estaba guardado

    # Otra exportación con el mismo conflicto (otro sha256 por el salto de línea) no duplica la anomalía.
    cargar_xml(xml_de(cambiados) + "\n")
    assert bd.valor("SELECT count(*) FROM anomalia WHERE tipo = 'DESPACHO_CAMBIADO_EN_REEXPORTACION'") == 1


def test_un_error_a_mitad_de_la_carga_no_deja_nada(bd, cargar_xml):
    import psycopg

    s = Surtidor()
    serie(s, 6)
    # LADO de dos letras: el parser lo acepta, la base (CHAR(1)) no. Falla después de crear la importación.
    libros = [s.libros[0], s.libros[1].replace("<LADO>B</LADO>", "<LADO>BX</LADO>"), *s.libros[2:]]
    with pytest.raises(psycopg.errors.StringDataRightTruncation):
        cargar_xml(xml_de(libros))

    for tabla in ("importacion", "despacho", "turno", "anomalia", "surtidor", "producto"):
        assert bd.valor(f"SELECT count(*) FROM {tabla}") == 0, tabla

    assert cargar_xml(s.xml()).nuevos == 6  # corregido el archivo, la carga entra completa


def test_un_turno_nuevo_cierra_el_anterior_y_amplia_su_rango(bd, cargar_xml):
    s = Surtidor()
    serie(s, 3, cierre=1)
    primeros = list(s.libros)
    serie(s, 2, desde=INICIO + timedelta(hours=3), cierre=1)  # el turno 1 siguió vendiendo
    serie(s, 2, desde=INICIO + timedelta(hours=6), cierre=2)

    cargar_xml(xml_de(primeros))
    assert bd.consultar("SELECT id_cierre, estado FROM turno") == [(1, "ABIERTO")]

    cargar_xml(s.xml())
    turnos = bd.consultar("SELECT id_cierre, estado, inicio, fin FROM turno ORDER BY id_cierre")
    assert [(t[0], t[1]) for t in turnos] == [(1, "CERRADO"), (2, "ABIERTO")]
    assert turnos[0][2] == INICIO
    assert turnos[0][3] == INICIO + timedelta(hours=3, minutes=45 + 2)  # fin del último despacho del turno 1


def test_las_vistas_de_ventas_cuadran_con_lo_cargado(bd, cargar_xml):
    s = Surtidor()
    serie(s, 8, galones="12.345")
    serie(s, 4, desde=INICIO + timedelta(days=1), pago="CREDITO", placa="GZY848")
    cargar_xml(s.xml())

    total = bd.consultar("SELECT count(*), sum(volumen_bruto), sum(valor) FROM despacho")[0]
    vistas = bd.consultar("SELECT sum(despachos), sum(galones), sum(valor) FROM v_ventas_diarias")[0]
    assert vistas == total
    assert total[1] == Decimal("12.345") * 8 + Decimal("10.000") * 4
    por_pago = dict(bd.consultar("SELECT forma_pago, sum(despachos) FROM v_ventas_diarias GROUP BY 1"))
    assert por_pago == {"CONTADO": 8, "CREDITO": 4}


def test_cli_carga_y_reconoce_el_archivo_repetido(bd, tmp_path: Path, capsys):
    from estacion import cli

    s = Surtidor()
    serie(s, 5)
    ruta = tmp_path / "D260901_060000.xls"
    ruta.write_text(s.xml(), encoding="utf-8")

    assert cli.main(["speed_solutions", str(ruta), "--dsn", bd.dsn]) == 0
    assert "5 nuevos" in capsys.readouterr().out
    assert cli.main(["speed_solutions", str(ruta), "--dsn", bd.dsn]) == 0
    assert "ya fue importado" in capsys.readouterr().out
    assert bd.valor("SELECT count(*) FROM despacho") == 5


def test_cli_archivo_sin_despachos_validos_falla_con_mensaje_claro(bd, tmp_path: Path, capsys):
    from estacion import cli

    ruta = tmp_path / "malo.xls"
    ruta.write_text(xml_de(['<book ID-DESPACHO="7"><ID-CIERRE>1</ID-CIERRE></book>']), encoding="utf-8")

    assert cli.main(["speed_solutions", str(ruta), "--dsn", bd.dsn]) == 2
    assert "ningún despacho válido" in capsys.readouterr().err
    assert bd.valor("SELECT count(*) FROM importacion") == 0
