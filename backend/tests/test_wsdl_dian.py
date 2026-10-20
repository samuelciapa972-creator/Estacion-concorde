"""El WSDL oficial de la DIAN (fijado en tests/fixtures/dian) trae las operaciones y parámetros que usamos."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dian"
OPERACIONES: dict[str, tuple[str, ...]] = {
    "GetNumberingRange": ("accountCode", "accountCodeT", "softwareCode"),
    "SendTestSetAsync": ("fileName", "contentFile", "testSetId"),
    "GetStatusZip": ("trackId",),
    "GetStatus": ("trackId",),
    "SendBillSync": ("fileName", "contentFile"),
}
CAMPOS_RESPUESTA = {
    "DianResponse": ("ErrorMessage", "IsValid", "StatusCode", "StatusDescription", "XmlBase64Bytes", "XmlDocumentKey"),
    "UploadDocumentResponse": ("ErrorMessageList", "ZipKey"),
    "XmlParamsResponseTrackId": ("ProcessedMessage", "Success"),
    "NumberRangeResponseList": ("OperationCode", "OperationDescription", "ResponseList"),
    "NumberRangeResponse": ("ResolutionNumber", "Prefix", "FromNumber", "ToNumber", "ValidDateFrom", "ValidDateTo"),
}
XS = "{http://www.w3.org/2001/XMLSchema}"


def _wsdl() -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.parse(str(FIXTURES / "WcfDianCustomerServices.wsdl"), parser).getroot()


def _parametros(raiz: etree._Element, elemento: str) -> list[str]:
    for e in raiz.iter(f"{XS}element"):
        if e.get("name") == elemento and e.getparent().tag == f"{XS}schema":
            return [c.get("name") for c in e.iter(f"{XS}element") if c is not e]
    raise AssertionError(f"el WSDL no define {elemento}")


def test_operaciones_y_parametros_existen_en_el_wsdl():
    raiz = _wsdl()
    for operacion, parametros in OPERACIONES.items():
        assert _parametros(raiz, operacion) == list(parametros), operacion


def test_campos_de_las_respuestas():
    raiz = _wsdl()
    tipos = {ct.get("name"): [e.get("name") for e in ct.iter(f"{XS}element")] for ct in raiz.iter(f"{XS}complexType")}
    for tipo, campos in CAMPOS_RESPUESTA.items():
        for campo in campos:
            assert campo in tipos[tipo], f"{tipo}.{campo}"


def test_espacio_de_nombres_y_acciones():
    raiz = _wsdl()
    assert raiz.get("targetNamespace") == "http://wcf.dian.colombia"
    acciones = {o.get("soapAction") for o in raiz.iter("{http://schemas.xmlsoap.org/wsdl/soap12/}operation")}
    for operacion in OPERACIONES:
        assert f"http://wcf.dian.colombia/IWcfDianCustomerServices/{operacion}" in acciones


def test_la_politica_firma_wsa_to_con_timestamp():
    texto = etree.tostring(_wsdl()).decode()
    assert '<sp:Header Name="To" Namespace="http://www.w3.org/2005/08/addressing"/>' in texto
    assert "<sp:IncludeTimestamp/>" in texto and "<sp:Basic256Sha256Rsa15/>" in texto
