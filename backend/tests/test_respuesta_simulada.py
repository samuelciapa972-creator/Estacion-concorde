"""ApplicationResponse SIMULADO: lo que la DIAN devolvería al validar, marcado para que nadie lo confunda."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from lxml import etree

from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.facturacion.respuesta_simulada import NOTA_SIMULADA, generar_respuesta
from test_xml_factura import construir, solicitud

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
VALIDADA = datetime(2026, 9, 29, 10, 25, 7)


def test_respuesta_valida_contra_el_xsd_y_marcada(esquema_ubl):
    f = construir()
    r = etree.fromstring(generar_respuesta(f.xml, validada_en=VALIDADA))
    xsd = esquema_ubl("ApplicationResponse")
    assert xsd.validate(r), xsd.error_log
    t = lambda p: r.findtext(p, namespaces=NS)  # noqa: E731
    assert t("cbc:Note") == NOTA_SIMULADA
    assert t("cbc:IssueDate") == "2026-09-29" and t("cbc:IssueTime") == "10:25:07-05:00"
    assert t("cbc:ProfileExecutionID") == "2"
    assert t("cac:DocumentResponse/cac:Response/cbc:ResponseCode") == "02"
    assert t("cac:DocumentResponse/cac:DocumentReference/cbc:ID") == f.numero
    assert t("cac:DocumentResponse/cac:DocumentReference/cbc:UUID") == f.cufe
    assert t("cac:ReceiverParty/cac:PartyTaxScheme/cbc:CompanyID") == "900123456"


def test_el_simulado_la_devuelve_cuando_hay_documento():
    f = construir()
    con_documento = replace(solicitud(), numero=f.numero, cufe=f.cufe, documento=f.xml)
    resultado = ProveedorSimulado(reloj=lambda: VALIDADA).emitir(con_documento)
    assert resultado.application_response is not None
    assert etree.fromstring(resultado.application_response).findtext("cbc:Note", namespaces=NS) == NOTA_SIMULADA


def test_sin_documento_no_hay_respuesta():
    assert ProveedorSimulado().emitir(solicitud()).application_response is None
