"""AttachedDocument (Anexo Técnico 1.9, §6.4): el contenedor firmado que se le entrega al cliente."""

from __future__ import annotations

from datetime import datetime

import pytest
from lxml import etree
from signxml import XMLVerifier

from estacion.facturacion.attached import RESPONSABILIDAD_RECEPTOR_SIN_DATO, AttachedInvalido, construir_attached
from estacion.facturacion.firma import firmar_xml
from estacion.facturacion.respuesta_simulada import generar_respuesta
from test_xml_factura import construir
from test_xml_nota import NUMERACION_EDSC, SOFTWARE_PRODUCCION, nota

AHORA = datetime(2026, 9, 29, 10, 30)
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


@pytest.fixture
def piezas(certificado):
    f = construir()
    firmada = firmar_xml(f.xml, certificado)
    return f, firmada, generar_respuesta(firmada, validada_en=datetime(2026, 9, 29, 10, 25, 7))


def test_valido_contra_el_xsd_y_firmado(piezas, certificado, esquema_ubl):
    _, firmada, ar = piezas
    xml = construir_attached(firmada, ar, consecutivo=10, ahora=AHORA, certificado=certificado)
    xsd = esquema_ubl("AttachedDocument")
    assert xsd.validate(etree.fromstring(xml)), xsd.error_log
    XMLVerifier().verify(xml, x509_cert=certificado.cert, expect_references=3)


def test_campos_del_anexo(piezas, certificado):
    f, firmada, ar = piezas
    raiz = etree.fromstring(construir_attached(firmada, ar, consecutivo=10, ahora=AHORA, certificado=certificado))
    t = lambda p: raiz.findtext(p, namespaces=NS)  # noqa: E731
    assert t("cbc:UBLVersionID") == "UBL 2.1"
    assert t("cbc:CustomizationID") == "Documentos adjuntos"
    assert t("cbc:ProfileID") == "Factura Electrónica de Venta"
    assert t("cbc:ProfileExecutionID") == "2"
    assert t("cbc:ID") == "10"
    assert t("cbc:IssueDate") == "2026-09-29" and t("cbc:IssueTime") == "10:30:00-05:00"
    assert t("cbc:DocumentType") == "Contenedor de Factura Electrónica"
    assert t("cbc:ParentDocumentID") == f.numero
    assert t("cac:SenderParty/cac:PartyTaxScheme/cbc:CompanyID") == "900123456"
    assert t("cac:ReceiverParty/cac:PartyTaxScheme/cbc:CompanyID") == "1000000001"
    assert t("cac:ReceiverParty/cac:PartyTaxScheme/cbc:TaxLevelCode") == RESPONSABILIDAD_RECEPTOR_SIN_DATO
    ref = "cac:ParentDocumentLineReference/cac:DocumentReference/"
    assert t("cac:ParentDocumentLineReference/cbc:LineID") == "1"
    assert t(ref + "cbc:ID") == f.numero and t(ref + "cbc:UUID") == f.cufe
    assert t(ref + "cbc:DocumentType") == "ApplicationResponse"
    assert t(ref + "cac:ResultOfVerification/cbc:ValidationResultCode") == "02"
    assert t(ref + "cac:ResultOfVerification/cbc:ValidationDate") == "2026-09-29"
    assert t(ref + "cac:ResultOfVerification/cbc:ValidationTime") == "10:25:07-05:00"


def test_lleva_la_factura_y_la_respuesta_byte_a_byte(piezas, certificado):
    _, firmada, ar = piezas
    raiz = etree.fromstring(construir_attached(firmada, ar, consecutivo=1, ahora=AHORA, certificado=certificado))
    factura = raiz.findtext("cac:Attachment/cac:ExternalReference/cbc:Description", namespaces=NS)
    respuesta = raiz.findtext(
        "cac:ParentDocumentLineReference/cac:DocumentReference/cac:Attachment/cac:ExternalReference/cbc:Description",
        namespaces=NS,
    )
    assert factura.encode("utf-8") == firmada
    assert respuesta.encode("utf-8") == ar
    # La factura de adentro sigue con su firma válida (no se tocó al meterla en el contenedor)
    XMLVerifier().verify(factura.encode("utf-8"), x509_cert=certificado.cert, expect_references=3)


def test_rechaza_factura_sin_firmar(piezas, certificado):
    f, _, ar = piezas
    with pytest.raises(AttachedInvalido, match="sin firmar"):
        construir_attached(f.xml, ar, consecutivo=1, ahora=AHORA, certificado=certificado)


def test_fecha_del_contenedor_no_anterior_a_la_factura(piezas, certificado):
    _, firmada, ar = piezas
    with pytest.raises(AttachedInvalido, match="anterior"):
        construir_attached(firmada, ar, consecutivo=1, ahora=datetime(2020, 1, 1), certificado=certificado)


def test_respuesta_de_otra_factura(piezas, certificado):
    _, firmada, _ = piezas
    otra = construir(consecutivo=99)
    ar_otra = generar_respuesta(otra.xml, validada_en=AHORA)
    with pytest.raises(AttachedInvalido, match="otra factura"):
        construir_attached(firmada, ar_otra, consecutivo=1, ahora=AHORA, certificado=certificado)


def test_attached_de_una_nota_valida_contra_xsd(certificado, esquema_ubl):
    n = nota(construir(consecutivo=1, software=SOFTWARE_PRODUCCION, numeracion=NUMERACION_EDSC))
    firmada = firmar_xml(n.xml, certificado)
    ar = generar_respuesta(firmada, validada_en=datetime(2026, 10, 2, 8, 10))
    xml = construir_attached(firmada, ar, consecutivo=11, ahora=datetime(2026, 10, 2, 8, 30), certificado=certificado)
    xsd = esquema_ubl("AttachedDocument")
    assert xsd.validate(etree.fromstring(xml)), xsd.error_log
    XMLVerifier().verify(xml, x509_cert=certificado.cert, expect_references=3)
    raiz = etree.fromstring(xml)
    assert raiz.findtext("cbc:ParentDocumentID", namespaces=NS) == "NCE1"
    uuid = raiz.find("cac:ParentDocumentLineReference/cac:DocumentReference/cbc:UUID", NS)
    assert (uuid.text, uuid.get("schemeName")) == (n.cufe, "CUDE-SHA384")
    assert (
        b"<CreditNote"
        in raiz.findtext(".//cac:Attachment/cac:ExternalReference/cbc:Description", namespaces=NS).encode()
    )
