"""Pruebas del generador de XML de factura electrónica (sin firma).

    PYTHONPATH=src python -m unittest discover -s tests -v

IMPORTANTE: estas pruebas garantizan que la ESTRUCTURA coincide con una factura de gasolina real que la DIAN
validó, y que las reglas de negocio funcionan. NO garantizan que la DIAN acepte el documento: el CUFE, el
código de seguridad y la firma solo los valida el ambiente de habilitación de la DIAN.
Los datos son sintéticos (NIT, nombres y correos de mentira).
"""

import hashlib
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from lxml import etree

from _rutas import rutas
from estacion.facturacion.configuracion import Emisor, Numeracion, SoftwareDian
from estacion.facturacion.documentos import ClienteInvalido, calcular_dv
from estacion.facturacion.modelos import Cliente, LineaFactura, SolicitudFactura
from estacion.facturacion.xml_factura import (
    FacturaNoSoportada,
    NumeracionInvalida,
    calcular_cufe,
    codigo_seguridad_software,
    construir_factura,
)

FIXTURE = Path(__file__).parent / "fixtures" / "rutas_referencia.txt"
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "i": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
}

EMISOR = Emisor(
    nit="900123456",
    razon_social="ESTACION DE PRUEBA S A S",
    direccion="CL 1 # 2-3",
    telefono="3000000000",
    email="estacion@example.com",
    actividades_ciiu=("4731", "4732"),
    responsabilidades="O-23;R-99-PN",
    responsable_iva=True,
    codigo_municipio="15001",
    municipio="TUNJA",
    codigo_departamento="15",
    departamento="BOYACÁ",
    codigo_postal="150001",
    matricula_mercantil="12345",
)
NUMERACION = Numeracion(
    "SETP", 1, 5_000_000, "18760000001", date(2026, 1, 1), date(2027, 12, 31), "clave-tecnica-de-prueba"
)
SOFTWARE = SoftwareDian("00000000-0000-0000-0000-000000000001", "12345", "900123456", "2")
CLIENTE = Cliente("CC", "1000000001", "Ana María", "Pérez Gómez", "ana@example.com", autoriza_tratamiento_datos=True)
FECHA = datetime(2026, 9, 29, 10, 22, 22)


def linea(cantidad="1.861", precio="15990", valor="29757", codigo="18096", desc="GASOLINA CORRIENTE"):
    return LineaFactura(codigo, desc, "GLL", Decimal(cantidad), Decimal(precio), Decimal(valor), Decimal(0), "SURT1:1")


def solicitud(lineas=None, cliente=CLIENTE, forma="CONTADO", medio="EFECTIVO", fecha=FECHA):
    return SolicitudFactura("clave-1", cliente, tuple(lineas or [linea()]), forma, medio, fecha)


def construir(sol=None, consecutivo=215802, software=SOFTWARE, numeracion=NUMERACION):
    return construir_factura(
        sol or solicitud(), consecutivo=consecutivo, emisor=EMISOR, numeracion=numeracion, software=software
    )


def texto(f, xpath):
    return f.arbol.xpath(xpath, namespaces=NS)[0].text


class TestEstructura(unittest.TestCase):
    def test_misma_estructura_que_la_factura_real_validada_por_la_dian(self):
        esperadas = {r.rstrip("\n") for r in FIXTURE.read_text(encoding="utf-8").splitlines() if r.strip()}
        obtenidas = rutas(construir().arbol)
        self.assertEqual(sorted(esperadas - obtenidas), [], "faltan elementos que la referencia sí tiene")
        self.assertEqual(sorted(obtenidas - esperadas), [], "sobran elementos que la referencia no tiene")

    def test_es_xml_bien_formado_y_no_esta_firmado(self):
        f = construir()
        raiz = etree.fromstring(f.xml)
        self.assertEqual(raiz.xpath("count(//ds:Signature)", namespaces=NS), 0)
        extensiones = raiz.xpath("/i:Invoice/ext:UBLExtensions/ext:UBLExtension", namespaces=NS)
        self.assertEqual(len(extensiones), 2)
        self.assertEqual(len(extensiones[1].xpath("ext:ExtensionContent/*", namespaces=NS)), 0)  # lugar de la firma

    def test_caracteres_especiales_se_escapan(self):
        cliente = Cliente("CC", "1000000001", "Ana & <Co>", "Pérez", "ana@example.com", autoriza_tratamiento_datos=True)
        f = construir(solicitud(cliente=cliente))
        nombre = (
            etree.fromstring(f.xml)
            .xpath("//cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name", namespaces=NS)[0]
            .text
        )
        self.assertEqual(nombre, "ANA & <CO> PÉREZ")


class TestValores(unittest.TestCase):
    def setUp(self):
        self.f = construir()

    def test_numero_fecha_y_hora(self):
        self.assertEqual(self.f.numero, "SETP215802")
        self.assertEqual(texto(self.f, "/i:Invoice/cbc:ID"), "SETP215802")
        self.assertEqual(texto(self.f, "/i:Invoice/cbc:IssueDate"), "2026-09-29")
        self.assertEqual(texto(self.f, "/i:Invoice/cbc:IssueTime"), "10:22:22-05:00")

    def test_el_valor_de_la_linea_es_lo_que_cobro_el_surtidor(self):
        # 1,861 x 15.990 = 29.757,39, pero el surtidor cobró 29.757: ese es el valor que se factura.
        self.assertEqual(Decimal("1.861") * Decimal("15990"), Decimal("29757.39"))
        self.assertEqual(texto(self.f, "//cac:InvoiceLine/cbc:LineExtensionAmount"), "29757.000000")
        self.assertEqual(texto(self.f, "//cac:LegalMonetaryTotal/cbc:PayableAmount"), "29757.000000")
        self.assertEqual(texto(self.f, "//cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"), "0.000000")

    def test_cantidad_con_tres_decimales_y_precio(self):
        self.assertEqual(texto(self.f, "//cac:InvoiceLine/cbc:InvoicedQuantity"), "1.861000")
        self.assertEqual(texto(self.f, "//cac:InvoiceLine/cac:Price/cbc:PriceAmount"), "15990.000000")
        unidad = self.f.arbol.xpath("//cac:InvoiceLine/cbc:InvoicedQuantity/@unitCode", namespaces=NS)[0]
        self.assertEqual(unidad, "GLL")

    def test_digito_de_verificacion_del_vendedor(self):
        dv = str(calcular_dv("900123456"))
        atributo = self.f.arbol.xpath(
            "//cac:AccountingSupplierParty//cac:PartyTaxScheme/cbc:CompanyID/@schemeID", namespaces=NS
        )[0]
        self.assertEqual(atributo, dv)

    def test_pago_contado_efectivo_vence_el_mismo_dia(self):
        self.assertEqual(texto(self.f, "//cac:PaymentMeans/cbc:ID"), "1")
        self.assertEqual(texto(self.f, "//cac:PaymentMeans/cbc:PaymentMeansCode"), "10")
        self.assertEqual(texto(self.f, "//cac:PaymentMeans/cbc:PaymentDueDate"), "2026-09-29")

    def test_comprador_persona_natural_con_cedula(self):
        self.assertEqual(texto(self.f, "//cac:AccountingCustomerParty/cbc:AdditionalAccountID"), "2")
        tipo = self.f.arbol.xpath(
            "//cac:AccountingCustomerParty//cac:PartyIdentification/cbc:ID/@schemeName", namespaces=NS
        )[0]
        self.assertEqual(tipo, "13")

    def test_varias_lineas(self):
        f = construir(solicitud([linea(), linea("10.000", "15990", "159900", "18097", "GASOLINA EXTRA")]))
        self.assertEqual(texto(f, "/i:Invoice/cbc:LineCountNumeric"), "2")
        ids = f.arbol.xpath("//cac:InvoiceLine/cbc:ID/text()", namespaces=NS)
        self.assertEqual(ids, ["1", "2"])
        self.assertEqual(texto(f, "//cac:LegalMonetaryTotal/cbc:PayableAmount"), "189657.000000")

    def test_ambiente_de_produccion(self):
        sw = SoftwareDian(SOFTWARE.software_id, SOFTWARE.pin, SOFTWARE.nit_proveedor, ambiente="1")
        f = construir(software=sw)
        self.assertEqual(texto(f, "/i:Invoice/cbc:ProfileExecutionID"), "1")
        self.assertEqual(f.arbol.xpath("/i:Invoice/cbc:UUID/@schemeID", namespaces=NS)[0], "1")
        self.assertTrue(f.qr.startswith("https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey="))


class TestCufe(unittest.TestCase):
    """Documentan la fórmula ESCRITA; que sea la correcta lo confirma la DIAN en habilitación."""

    def test_el_cufe_del_xml_es_el_de_la_formula(self):
        f = construir()
        cadena = (
            "SETP215802"
            "2026-09-29"
            "10:22:22-05:00"
            "29757.00"
            "01"
            "0.00"
            "04"
            "0.00"
            "03"
            "0.00"
            "29757.00"
            "900123456"
            "13"
            "1000000001"
            "clave-tecnica-de-prueba"
            "2"
        )
        esperado = hashlib.sha384(cadena.encode()).hexdigest()
        self.assertEqual(f.cufe, esperado)
        self.assertEqual(texto(f, "/i:Invoice/cbc:UUID"), esperado)
        self.assertEqual(len(f.cufe), 96)
        self.assertTrue(f.qr.endswith(esperado))

    def test_cambia_si_cambia_cualquier_dato(self):
        base = dict(
            numero="A1",
            fecha="2026-01-01",
            hora="10:00:00-05:00",
            valor_factura=Decimal("100"),
            valor_total=Decimal("100"),
            nit_emisor="900123456",
            tipo_doc_adquirente="13",
            num_doc_adquirente="1",
            clave_tecnica="k",
            ambiente="2",
        )
        original = calcular_cufe(**base)
        for campo, nuevo in [
            ("numero", "A2"),
            ("fecha", "2026-01-02"),
            ("hora", "10:00:01-05:00"),
            ("valor_factura", Decimal("101")),
            ("valor_total", Decimal("101")),
            ("nit_emisor", "900123457"),
            ("num_doc_adquirente", "2"),
            ("clave_tecnica", "otra"),
            ("ambiente", "1"),
        ]:
            with self.subTest(campo=campo):
                self.assertNotEqual(calcular_cufe(**{**base, campo: nuevo}), original)

    def test_codigo_de_seguridad_del_software(self):
        esperado = hashlib.sha384(b"00000000-0000-0000-0000-00000000000112345SETP1").hexdigest()
        self.assertEqual(codigo_seguridad_software(SOFTWARE, "SETP1"), esperado)


class TestRechazos(unittest.TestCase):
    """Lo que no está verificado se rechaza a propósito, en vez de generar un documento dudoso."""

    def test_comprador_con_nit(self):
        c = Cliente(
            "NIT",
            "900123456",
            "Empresa de prueba",
            "",
            "e@example.com",
            str(calcular_dv("900123456")),
            autoriza_tratamiento_datos=True,
        )
        with self.assertRaises(FacturaNoSoportada):
            construir(solicitud(cliente=c))

    def test_credito_y_otros_medios_de_pago(self):
        with self.assertRaises(FacturaNoSoportada):
            construir(solicitud(forma="CREDITO"))
        with self.assertRaises(FacturaNoSoportada):
            construir(solicitud(medio="TARJETA"))

    def test_linea_con_iva(self):
        con_iva = LineaFactura("1", "PRODUCTO", "UND", Decimal(1), Decimal(100), Decimal(119), Decimal("0.19"), "X")
        with self.assertRaises(FacturaNoSoportada):
            construir(solicitud([con_iva]))

    def test_sin_lineas_y_valores_no_positivos(self):
        sol_vacia = SolicitudFactura("k", CLIENTE, (), "CONTADO", "EFECTIVO", FECHA)
        with self.assertRaises(FacturaNoSoportada):
            construir(sol_vacia)
        with self.assertRaises(FacturaNoSoportada):
            construir(solicitud([linea(valor="0")]))

    def test_cliente_sin_autorizacion_de_datos(self):
        c = Cliente("CC", "1000000001", "Ana", "Pérez", "ana@example.com", autoriza_tratamiento_datos=False)
        with self.assertRaises(ClienteInvalido):
            construir(solicitud(cliente=c))

    def test_consecutivo_fuera_del_rango(self):
        with self.assertRaises(NumeracionInvalida):
            construir(consecutivo=0)
        with self.assertRaises(NumeracionInvalida):
            construir(consecutivo=5_000_001)
        construir(consecutivo=5_000_000)  # el último del rango sí es válido

    def test_fecha_fuera_de_la_vigencia(self):
        with self.assertRaises(NumeracionInvalida):
            construir(solicitud(fecha=datetime(2028, 1, 1, 9, 0, 0)))
        with self.assertRaises(NumeracionInvalida):
            construir(solicitud(fecha=datetime(2025, 12, 31, 23, 59, 59)))
        construir(solicitud(fecha=datetime(2027, 12, 31, 23, 59, 59)))  # el último día de vigencia sí


if __name__ == "__main__":
    unittest.main()


class TestFallarCerrado(unittest.TestCase):
    def test_el_emisor_no_tiene_datos_fiscales_por_defecto(self):
        """Sin responsabilidades, actividades o municipio no hay emisor (antes se asumía Tunja y R-99-PN)."""
        with self.assertRaises(TypeError):
            Emisor(  # type: ignore[call-arg]
                nit="900123456",
                razon_social="ESTACION DE PRUEBA S A S",
                direccion="CL 1 # 2-3",
                telefono="3000000000",
                email="estacion@example.com",
            )
