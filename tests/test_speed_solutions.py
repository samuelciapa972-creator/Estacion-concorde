"""Pruebas del parser y del análisis de calidad. Solo librería estándar:

    PYTHONPATH=src python -m unittest discover -s tests -v

Los XML son sintéticos y reproducen los casos vistos en el archivo real
(reloj en 2005, 31-ago grabado como 1-ago, contador con hueco, crédito sin placa).
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from estacion.calidad import analizar
from estacion.modelos import ArchivoInvalido, FormaPago, PlacaTipo, Severidad
from estacion.normalizacion import normalizar_kilometraje, normalizar_placa
from estacion.parsers import speed_solutions

FMT = "%Y/%m/%d %H:%M:%S"


class Surtidor:
    """Genera despachos con contadores acumulados coherentes, como el equipo real."""

    def __init__(self, ppu=11150):
        self.ppu = ppu
        self.id = 1000
        self.tot = {"A": Decimal("5000.000"), "B": Decimal("9000.000")}
        self.tot_money = {"A": 50_000_000, "B": 90_000_000}
        self.libros = []

    def despacho(
        self,
        inicio,
        *,
        lado="A",
        galones="10.000",
        cierre=1,
        pago="CONTADO",
        placa="0",
        km="1",
        dur_min=2,
        salto_contador="0",
        id_=None,
        ppu=None,
    ):
        ppu = ppu or self.ppu
        self.id = id_ if id_ is not None else self.id + 1
        vol = Decimal(galones)
        dinero = int((vol * ppu).quantize(Decimal("1")))
        self.tot[lado] += vol + Decimal(salto_contador)
        self.tot_money[lado] += dinero + int(Decimal(salto_contador) * ppu)
        fin = inicio + timedelta(minutes=dur_min)
        self.libros.append(
            f'<book ID-DESPACHO="{self.id}"><ID-CIERRE>{cierre}</ID-CIERRE>'
            f"<ID-SURTIDOR>SURT1</ID-SURTIDOR><LADO>{lado}</LADO><PISTOLA>1</PISTOLA>"
            f"<PRD>DIESEL</PRD><TIME-INI>{inicio.strftime(FMT)}</TIME-INI>"
            f"<TIME-FIN>{fin.strftime(FMT)}</TIME-FIN><VOLG>{vol}</VOLG><VOLN>{vol}</VOLN>"
            f"<MONEY>{dinero}</MONEY><PPU>{ppu}</PPU><FORMA-PAGO>{pago}</FORMA-PAGO>"
            f"<INFO-PLACA>{placa}</INFO-PLACA><INFO-KILOMETRAJE>{km}</INFO-KILOMETRAJE>"
            f"<TOT-GROS-1>{self.tot[lado]}</TOT-GROS-1><TOT-NET-1>{self.tot[lado]}</TOT-NET-1>"
            f"<TOT-MONEY-1>{self.tot_money[lado]}</TOT-MONEY-1></book>"
        )

    def xml(self):
        return '<?xml version="1.0"?><catalog>' + "".join(self.libros) + "</catalog>"


def leer_texto(xml: str):
    with tempfile.NamedTemporaryFile("w", suffix=".xls", delete=False, encoding="utf-8") as f:
        f.write(xml)
    try:
        return speed_solutions.leer(Path(f.name))
    finally:
        os.unlink(f.name)


def analizar_xml(xml: str):
    lectura = leer_texto(xml)
    return lectura, *analizar(lectura.despachos)


def serie(s: Surtidor, desde: datetime, n: int, cada_min=90, **kw):
    for i in range(n):
        s.despacho(desde + timedelta(minutes=cada_min * i), lado="AB"[i % 2], **kw)


class TestNormalizacion(unittest.TestCase):
    def test_placas(self):
        casos = {
            "GZY848": (PlacaTipo.PLACA, "GZY848"),
            "gzy-848": (PlacaTipo.PLACA, "GZY848"),
            "ABC12D": (PlacaTipo.PLACA, "ABC12D"),  # moto
            "R12345": (PlacaTipo.PLACA, "R12345"),  # remolque
            "0": (PlacaTipo.SIN_PLACA, None),
            "1": (PlacaTipo.SIN_PLACA, None),
            "": (PlacaTipo.SIN_PLACA, None),
            "  ": (PlacaTipo.SIN_PLACA, None),
            "MOTONIVELADORA": (PlacaTipo.EQUIPO_TEXTO, "MOTONIVELADORA"),
            "pc200": (PlacaTipo.EQUIPO_TEXTO, "PC200"),
        }
        for raw, esperado in casos.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalizar_placa(raw), esperado)

    def test_kilometraje(self):
        self.assertIsNone(normalizar_kilometraje("1"))
        self.assertIsNone(normalizar_kilometraje("0"))
        self.assertIsNone(normalizar_kilometraje("D.MAQUINAS"))
        self.assertIsNone(normalizar_kilometraje("-113927"))
        self.assertIsNone(normalizar_kilometraje("8003345478"))
        self.assertEqual(normalizar_kilometraje("893529"), 893529)


class TestParser(unittest.TestCase):
    def test_lee_campos(self):
        s = Surtidor()
        s.despacho(datetime(2026, 9, 28, 8, 14, 17), galones="80.000", pago="CREDITO", placa="esx-901", cierre=3392)
        lectura = leer_texto(s.xml())
        (d,) = lectura.despachos
        self.assertEqual(d.codigo_surtidor, "SURT1")
        self.assertEqual(d.forma_pago, FormaPago.CREDITO)
        self.assertEqual(d.ref_vehiculo, "ESX901")
        self.assertEqual(d.volumen_bruto, Decimal("80.000"))
        self.assertEqual(d.valor, Decimal("892000"))
        self.assertEqual(d.totalizador_vol, Decimal("5080.000"))
        self.assertEqual(d.inicio, d.inicio_reloj)
        self.assertEqual(len(lectura.archivo_sha256), 64)

    def test_fila_danada_se_rechaza_sin_perder_las_demas(self):
        s = Surtidor()
        serie(s, datetime(2026, 9, 1, 8), 3)
        xml = s.xml().replace("</catalog>", '<book ID-DESPACHO="77"><ID-CIERRE>1</ID-CIERRE></book></catalog>')
        lectura = leer_texto(xml)
        self.assertEqual(len(lectura.despachos), 3)
        self.assertEqual(len(lectura.rechazos), 1)
        self.assertEqual(lectura.rechazos[0].id_externo, 77)

    def test_valores_no_numericos_se_rechazan(self):
        s = Surtidor()
        s.despacho(datetime(2026, 9, 1, 8))
        lectura = leer_texto(s.xml().replace("<MONEY>111500</MONEY>", "<MONEY>abc</MONEY>"))
        self.assertEqual((len(lectura.despachos), len(lectura.rechazos)), (0, 1))

    def test_archivo_truncado_aborta(self):
        s = Surtidor()
        serie(s, datetime(2026, 9, 1, 8), 3)
        with self.assertRaises(ArchivoInvalido):
            leer_texto(s.xml()[:-40])


class TestCalidad(unittest.TestCase):
    def test_serie_limpia_no_genera_anomalias(self):
        s = Surtidor()
        serie(s, datetime(2026, 9, 1, 6), 30)
        _, despachos, anomalias, turnos = analizar_xml(s.xml())
        self.assertEqual(anomalias, [])
        self.assertFalse(any(d.fecha_corregida for d in despachos))
        self.assertEqual(len(turnos), 1)

    def test_reloj_en_2005_se_interpola_y_conserva_el_original(self):
        s = Surtidor()
        serie(s, datetime(2026, 7, 11, 14), 6)
        s.despacho(datetime(2005, 5, 5, 5, 8, 51), lado="A")  # reloj reiniciado
        serie(s, datetime(2026, 7, 11, 23), 6)
        _, despachos, anomalias, _ = analizar_xml(s.xml())
        malo = next(d for d in despachos if d.inicio_reloj.year == 2005)
        self.assertTrue(malo.fecha_corregida)
        self.assertEqual(malo.inicio.date(), datetime(2026, 7, 11).date())
        self.assertEqual(sum(d.fecha_corregida for d in despachos), 1)  # nadie más se toca
        a = next(x for x in anomalias if x.tipo == "FECHA_CORREGIDA")
        self.assertEqual(a.detalle["metodo"], "INTERPOLACION")
        self.assertEqual(a.severidad, Severidad.AVISO)

    def test_31_agosto_grabado_como_1_agosto_se_desplaza_30_dias(self):
        """Caso real: 47 despachos del 31-ago quedaron con fecha 1-ago."""
        s = Surtidor()
        serie(s, datetime(2026, 8, 30, 0, 0), 20, cada_min=60)  # 30-ago (00:00 → 19:00)
        s.despacho(datetime(2026, 8, 30, 23, 41), lado="B")
        # el reloj "salta" a 1-ago y recorre todo el día 31 con fecha equivocada
        serie(s, datetime(2026, 8, 1, 0, 17), 12, cada_min=110)  # 00:17 → ~20:30 del "1-ago"
        serie(s, datetime(2026, 9, 1, 0, 52), 10, cada_min=90)  # vuelve a la normalidad
        _, despachos, anomalias, _ = analizar_xml(s.xml())

        corregidos = [d for d in despachos if d.fecha_corregida]
        self.assertEqual(len(corregidos), 12)
        for d in corregidos:
            self.assertEqual(d.inicio - d.inicio_reloj, timedelta(days=30))
            self.assertEqual(d.inicio.date(), datetime(2026, 8, 31).date())
        metodos = {a.detalle["metodo"] for a in anomalias if a.tipo == "FECHA_CORREGIDA"}
        self.assertEqual(metodos, {"DESPLAZAMIENTO_DIAS"})
        # los vecinos correctos NO se marcan (el detector viejo se equivocaba justo aquí)
        for d in despachos:
            if d.inicio_reloj.date() in (datetime(2026, 8, 30).date(), datetime(2026, 9, 1).date()):
                self.assertFalse(d.fecha_corregida, d.id_externo)

    def test_isla_grande_sin_desplazamiento_exacto_no_se_inventa(self):
        s = Surtidor()
        serie(s, datetime(2026, 8, 10, 8), 10)
        serie(s, datetime(2026, 1, 5, 8), 8, cada_min=1000)  # isla larga (~5 días) en enero
        serie(s, datetime(2026, 8, 10, 23), 10)
        _, despachos, anomalias, _ = analizar_xml(s.xml())
        self.assertFalse(any(d.fecha_corregida for d in despachos))
        tipos = [a.tipo for a in anomalias]
        self.assertEqual(tipos.count("FECHA_INVALIDA_NO_CORREGIBLE"), 8)
        self.assertTrue(all(a.severidad is Severidad.ALTA for a in anomalias if a.tipo.startswith("FECHA_INVALIDA")))

    def test_ventana_diminuta_ambigua_falla_de_forma_segura(self):
        """Con muy pocas filas la cadena "más larga" puede elegir el bloque equivocado.
        Lo importante: nunca desplaza fechas por su cuenta; lo deja marcado ALTA."""
        s = Surtidor()
        serie(s, datetime(2026, 8, 30, 8), 4, cada_min=90)
        serie(s, datetime(2026, 8, 1, 0, 17), 12, cada_min=110)
        serie(s, datetime(2026, 9, 1, 0, 52), 10, cada_min=90)
        _, despachos, anomalias, _ = analizar_xml(s.xml())
        self.assertTrue(all(d.inicio.month != 8 or d.inicio.day != 31 for d in despachos))
        self.assertTrue(
            any(a.tipo == "FECHA_INVALIDA_NO_CORREGIBLE" and a.severidad is Severidad.ALTA for a in anomalias)
        )

    def test_cierre_de_estacion_varios_dias_no_es_anomalia(self):
        s = Surtidor()
        serie(s, datetime(2026, 9, 1, 8), 20)
        serie(s, datetime(2026, 9, 6, 8), 20)  # 4 días sin ventas
        _, despachos, anomalias, _ = analizar_xml(s.xml())
        self.assertFalse(any(a.tipo.startswith("FECHA") for a in anomalias))

    def test_hueco_en_totalizador(self):
        s = Surtidor()
        serie(s, datetime(2026, 9, 1, 6), 4)
        s.despacho(datetime(2026, 9, 1, 13), lado="A", salto_contador="25.000")  # se perdió un despacho de 25 gal
        serie(s, datetime(2026, 9, 1, 15), 4)
        _, _, anomalias, _ = analizar_xml(s.xml())
        gaps = [a for a in anomalias if a.tipo == "GAP_TOTALIZADOR"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].detalle["diferencia_galones"], "25.000")
        self.assertEqual(gaps[0].detalle["diferencia_pesos"], "278750")
        self.assertEqual(gaps[0].severidad, Severidad.ALTA)

    def test_contador_que_retrocede(self):
        s = Surtidor()
        serie(s, datetime(2026, 9, 1, 6), 4)
        s.despacho(datetime(2026, 9, 1, 13), lado="A", salto_contador="-500.000")
        _, _, anomalias, _ = analizar_xml(s.xml())
        self.assertEqual([a.tipo for a in anomalias], ["RETROCESO_TOTALIZADOR"])

    def test_credito_sin_placa_es_alta_y_contado_sin_placa_no(self):
        s = Surtidor()
        s.despacho(datetime(2026, 9, 1, 8), pago="CREDITO", placa="0")
        s.despacho(datetime(2026, 9, 1, 9), pago="CONTADO", placa="1")
        s.despacho(datetime(2026, 9, 1, 10), pago="CREDITO", placa="BOBCAT")
        s.despacho(datetime(2026, 9, 1, 11), pago="CREDITO", placa="GZY848")
        _, _, anomalias, _ = analizar_xml(s.xml())
        self.assertEqual([(a.tipo, a.id_despacho) for a in anomalias], [("CREDITO_SIN_IDENTIFICACION", 1001)])

    def test_valor_inconsistente(self):
        s = Surtidor()
        s.despacho(datetime(2026, 9, 1, 8))
        xml = s.xml().replace("<MONEY>111500</MONEY>", "<MONEY>150000</MONEY>")
        _, _, anomalias, _ = analizar_xml(xml)
        self.assertEqual([a.tipo for a in anomalias], ["VALOR_INCONSISTENTE"])

    def test_duplicados_en_el_mismo_archivo(self):
        s = Surtidor()
        s.despacho(datetime(2026, 9, 1, 8))
        libro = s.libros[0]
        s.libros.append(libro)  # idéntico: se descarta en silencio
        s.libros.append(libro.replace("<PPU>11150</PPU>", "<PPU>11200</PPU>"))  # mismo id, otro contenido
        lectura, despachos, anomalias, _ = analizar_xml(s.xml())
        self.assertEqual(len(lectura.despachos), 3)
        self.assertEqual(len(despachos), 1)
        self.assertEqual([a.tipo for a in anomalias], ["DUPLICADO_CONFLICTIVO"])

    def test_turnos_el_mayor_id_cierre_esta_abierto(self):
        s = Surtidor()
        serie(s, datetime(2026, 9, 1, 6), 4, cierre=10)
        serie(s, datetime(2026, 9, 1, 18), 4, cierre=11)
        serie(s, datetime(2026, 9, 2, 6), 2, cierre=12)
        _, _, _, turnos = analizar_xml(s.xml())
        self.assertEqual(
            [(t.id_cierre, t.estado, t.despachos) for t in turnos],
            [(10, "CERRADO", 4), (11, "CERRADO", 4), (12, "ABIERTO", 2)],
        )
        self.assertEqual(turnos[0].valor, Decimal("446000"))  # 4 x 10 gal x 11150


@unittest.skipUnless(os.environ.get("ARCHIVO_REAL"), "defina ARCHIVO_REAL para correr contra el export real")
class TestArchivoReal(unittest.TestCase):
    def test_export_real(self):
        lectura = speed_solutions.leer(os.environ["ARCHIVO_REAL"])
        self.assertEqual(lectura.rechazos, [])
        despachos, anomalias, turnos = analizar(lectura.despachos)
        self.assertFalse([a for a in anomalias if a.tipo in ("GAP_TOTALIZADOR", "RETROCESO_TOTALIZADOR")])
        self.assertFalse([a for a in anomalias if a.tipo == "FECHA_INVALIDA_NO_CORREGIBLE"])
        self.assertEqual(sum(1 for d in despachos if d.inicio.year < 2026), 0)


if __name__ == "__main__":
    unittest.main()
