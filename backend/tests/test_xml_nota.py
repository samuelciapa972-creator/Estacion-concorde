"""Pruebas del XML de la nota crédito de anulación total y su CUDE (sin conexión a la DIAN).

Qué garantizan: la nota cumple el XSD oficial UBL 2.1 de CreditNote, referencia la factura anulada, lleva los
mismos totales y se puede firmar. Qué NO garantizan: que la DIAN la acepte (el CUDE y las reglas de la nota solo
los valida el ambiente de habilitación).
"""

import hashlib
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest
from lxml import etree
from signxml import XMLVerifier

from estacion.facturacion.configuracion import SoftwareDian
from estacion.facturacion.firma import firmar_xml
from estacion.facturacion.xml_factura import FacturaNoSoportada
from estacion.facturacion.xml_nota import NotaInvalida, ReferenciaFactura, calcular_cude, construir_nota_credito
from test_xml_factura import EMISOR, NUMERACION, construir, linea, solicitud

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
    "n": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
}
SOFTWARE_PRODUCCION = SoftwareDian("00000000-0000-0000-0000-000000000001", "12345", "900123456", "1")
NUMERACION_EDSC = replace(NUMERACION, prefijo="EDSC")
MOTIVO = "Datos del cliente equivocados"
FECHA_NOTA = datetime(2026, 10, 2, 8, 5, 0)
CUDE_BASE = dict(
    numero="NCE1",
    fecha="2026-10-02",
    hora="08:05:00-05:00",
    valor=Decimal("29757"),
    valor_total=Decimal("29757"),
    nit_emisor="900123456",
    num_doc_adquirente="1000000001",
    pin_software="12345",
    ambiente="1",
)


@pytest.fixture
def factura():
    return construir(consecutivo=1, software=SOFTWARE_PRODUCCION, numeracion=NUMERACION_EDSC)


def nota(factura, *, sol=None, motivo=MOTIVO, consecutivo=1, prefijo="NCE", software=SOFTWARE_PRODUCCION):
    return construir_nota_credito(
        sol or solicitud(fecha=FECHA_NOTA),
        prefijo=prefijo,
        consecutivo=consecutivo,
        referencia=ReferenciaFactura(factura.numero, factura.cufe, datetime(2026, 9, 29, 10, 22, 22)),
        motivo=motivo,
        emisor=EMISOR,
        software=software,
    )


def texto(n, xpath):
    return n.arbol.xpath(xpath, namespaces=NS)[0].text


def contenido(el):
    """Etiquetas, textos y atributos de un subárbol (sin las declaraciones de espacios de nombres)."""
    return [(etree.QName(e).localname, e.text, dict(e.attrib)) for e in el.iter()]


# ------------------------------------------------------------------------------------------ CUDE
def test_cude_cambia_con_el_pin_y_no_con_la_clave_tecnica():
    base = calcular_cude(**CUDE_BASE)
    assert len(base) == 96  # SHA-384 en hexadecimal
    assert calcular_cude(**{**CUDE_BASE, "pin_software": "54321"}) != base
    with pytest.raises(TypeError):
        calcular_cude(**CUDE_BASE, clave_tecnica="clave-tecnica-de-prueba")  # type: ignore[call-arg]


def test_cude_sigue_la_cadena_del_anexo():
    """NumNC + FecNC + HorNC + ValNC + 01 + ValIVA + 04 + ValINC + 03 + ValICA + ValTot + NitOFE + NumAdq + PIN
    + TipoAmbiente, con los valores a 2 decimales."""

    partes = ["NCE1", "2026-10-02", "08:05:00-05:00", "29757.00", "01", "0.00", "04", "0.00", "03", "0.00"]
    partes += ["29757.00", "900123456", "1000000001", "12345", "1"]
    cadena = "".join(partes)
    assert calcular_cude(**CUDE_BASE) == hashlib.sha384(cadena.encode()).hexdigest()


def test_nota_usa_el_cude_calculado(factura):
    n = nota(factura)
    esperado = calcular_cude(**CUDE_BASE)
    assert n.cufe == esperado
    assert texto(n, "cbc:UUID") == esperado
    uuid = n.arbol.find("cbc:UUID", namespaces=NS)
    assert uuid.get("schemeName") == "CUDE-SHA384" and uuid.get("schemeID") == "1"
    assert n.qr == "https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=" + esperado
    assert texto(n, "//sts:QRCode") == n.qr


# ------------------------------------------------------------------------------------------ estructura
def test_nota_valida_contra_xsd_creditnote(factura, esquema_ubl, certificado):
    # Se valida firmada: sin firma, el `ext:ExtensionContent` reservado está vacío y el XSD exige un hijo.
    xsd = esquema_ubl("CreditNote")
    n = nota(factura)
    assert xsd.validate(etree.fromstring(firmar_xml(n.xml, certificado))), xsd.error_log
    assert n.arbol.tag == f"{{{NS['n']}}}CreditNote"


def test_nota_referencia_la_factura(factura):
    n = nota(factura)
    ref = "cac:BillingReference/cac:InvoiceDocumentReference/"
    assert texto(n, ref + "cbc:ID") == "EDSC1"
    assert texto(n, ref + "cbc:UUID") == factura.cufe
    assert n.arbol.find(ref + "cbc:UUID", namespaces=NS).get("schemeName") == "CUFE-SHA384"
    assert texto(n, ref + "cbc:IssueDate") == "2026-09-29"
    assert texto(n, "cac:DiscrepancyResponse/cbc:ReferenceID") == "EDSC1"
    assert texto(n, "cac:DiscrepancyResponse/cbc:ResponseCode") == "2"
    assert texto(n, "cac:DiscrepancyResponse/cbc:Description") == MOTIVO
    assert texto(n, "cbc:CustomizationID") == "20"
    assert texto(n, "cbc:ProfileID") == "DIAN 2.1: Nota Crédito de Factura Electrónica de Venta"
    assert texto(n, "cbc:ProfileExecutionID") == "1"
    assert texto(n, "cbc:CreditNoteTypeCode") == "91"
    assert texto(n, "cbc:ID") == "NCE1" and n.numero == "NCE1"
    assert texto(n, "cbc:IssueDate") == "2026-10-02" and texto(n, "cbc:IssueTime") == "08:05:00-05:00"


def test_nota_sin_resolucion_y_con_codigo_de_seguridad(factura):
    n = nota(factura)
    assert n.arbol.xpath("//sts:InvoiceControl", namespaces=NS) == []
    assert texto(n, "//sts:SoftwareSecurityCode") == n.codigo_seguridad_software
    assert n.arbol.xpath("cac:InvoiceLine", namespaces=NS) == []


def test_nota_totales_iguales_a_la_factura(factura):
    sol = solicitud([linea(), linea("2", "10000", "20000", "18097", "ACPM")], fecha=FECHA_NOTA)
    f = construir(sol, consecutivo=1, software=SOFTWARE_PRODUCCION, numeracion=NUMERACION_EDSC)
    n = nota(f, sol=sol)
    total_factura = f.arbol.xpath("cac:LegalMonetaryTotal/cbc:PayableAmount", namespaces=NS)[0].text
    assert texto(n, "cac:LegalMonetaryTotal/cbc:PayableAmount") == total_factura == "49757.000000"
    lineas = n.arbol.xpath("cac:CreditNoteLine", namespaces=NS)
    assert len(lineas) == 2 and texto(n, "cbc:LineCountNumeric") == "2"
    assert lineas[1].findtext("cbc:CreditedQuantity", namespaces=NS) == "2.000000"
    assert lineas[1].find("cbc:CreditedQuantity", namespaces=NS).get("unitCode") == "GLL"
    # Mismo comprador, pago y totales que la factura; el vendedor solo cambia el prefijo del punto de facturación.
    for parte in ("cac:AccountingCustomerParty", "cac:PaymentMeans", "cac:LegalMonetaryTotal"):
        assert contenido(n.arbol.find(parte, namespaces=NS)) == contenido(f.arbol.find(parte, namespaces=NS))
    registro = "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:CorporateRegistrationScheme/cbc:ID"
    assert texto(n, registro) == "NCE" and texto(f, registro) == "EDSC"
    vendedor_n = contenido(n.arbol.find("cac:AccountingSupplierParty", namespaces=NS))
    vendedor_f = contenido(f.arbol.find("cac:AccountingSupplierParty", namespaces=NS))
    assert [x for x in vendedor_n if x[1] != "NCE"] == [x for x in vendedor_f if x[1] != "EDSC"]


def test_nota_en_habilitacion_usa_el_catalogo_de_habilitacion(factura):
    hab = SoftwareDian("00000000-0000-0000-0000-000000000001", "12345", "900123456", "2")
    n = nota(factura, prefijo="NCP", software=hab)
    assert n.qr.startswith("https://catalogo-vpfe-hab.dian.gov.co/") and n.qr.endswith(n.cufe)
    assert texto(n, "cbc:ProfileExecutionID") == "2" and n.numero == "NCP1"


# ------------------------------------------------------------------------------------------ validaciones
@pytest.mark.parametrize("motivo", ["", "   "])
def test_nota_sin_motivo_no_se_construye(factura, motivo):
    with pytest.raises(NotaInvalida, match="motivo"):
        nota(factura, motivo=motivo)


def test_nota_con_consecutivo_invalido_no_se_construye(factura):
    with pytest.raises(NotaInvalida, match="consecutivo"):
        nota(factura, consecutivo=0)


def test_nota_reutiliza_las_validaciones_de_la_factura(factura):
    with pytest.raises(FacturaNoSoportada, match="medio de pago"):
        nota(factura, sol=solicitud(medio="TARJETA", fecha=FECHA_NOTA))


# ------------------------------------------------------------------------------------------ firma
def test_nota_firmada_valida(factura, certificado):
    firmada = firmar_xml(nota(factura).xml, certificado)
    XMLVerifier().verify(firmada, x509_cert=certificado.cert, expect_references=3)
    assert etree.fromstring(firmada).tag == f"{{{NS['n']}}}CreditNote"
