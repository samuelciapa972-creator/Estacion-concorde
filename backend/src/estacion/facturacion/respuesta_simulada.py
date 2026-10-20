"""ApplicationResponse SIMULADO: lo que la DIAN devolvería al validar una factura (código 02, documento validado).

Solo existe porque el proveedor simulado no habla con la DIAN, y el AttachedDocument que se entrega al cliente
debe llevar esa respuesta. Va marcado en `cbc:Note` (`NOTA_SIMULADA`) y en la descripción de la respuesta: nadie
puede confundirlo con uno real. Cuando se conecte el web service de la DIAN se guarda la respuesta real y este
generador deja de usarse.

Se arma a partir del XML de la factura (no de la base): dice exactamente lo que dice el documento.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from lxml import etree

NS_AR = "urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_LEER = {"cac": NS_CAC, "cbc": NS_CBC}

NOTA_SIMULADA = "SIMULADO – banco de pruebas, no emitido por la DIAN"
NOMBRE_DIAN = "Unidad Especial Dirección de Impuestos y Aduanas Nacionales"
_NIT_DIAN, _DV_DIAN = "800197268", "4"
_AGENCIA = {"schemeAgencyID": "195", "schemeAgencyName": "CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)"}


def _q(prefijo: str, local: str) -> str:
    return f"{{{NS_CAC if prefijo == 'cac' else NS_CBC}}}{local}"


def _sub(padre: etree._Element, nombre: str, texto: str | None = None, **atributos: str) -> etree._Element:
    prefijo, local = nombre.split(":")
    el = etree.SubElement(padre, _q(prefijo, local), attrib=atributos)
    if texto is not None:
        el.text = texto
    return el


def _requerido(raiz: etree._Element, ruta: str) -> etree._Element:
    el = raiz.find(ruta, _LEER)
    if el is None or not (el.text or "").strip():
        raise ValueError(f"la factura no tiene {ruta}")
    return el


def _parte(
    padre: etree._Element, nombre: str, razon_social: str, nit: etree._Element | None, *, nit_dian: bool
) -> None:
    esquema = _sub(_sub(padre, nombre), "cac:PartyTaxScheme")
    _sub(esquema, "cbc:RegistrationName", razon_social)
    if nit_dian:
        _sub(esquema, "cbc:CompanyID", _NIT_DIAN, schemeID=_DV_DIAN, schemeName="31", **_AGENCIA)
    else:
        assert nit is not None
        _sub(esquema, "cbc:CompanyID", nit.text, **{str(k): str(v) for k, v in nit.attrib.items()})
    impuesto = _sub(esquema, "cac:TaxScheme")
    _sub(impuesto, "cbc:ID", "01")
    _sub(impuesto, "cbc:Name", "IVA")


def generar_respuesta(factura_xml: bytes, *, validada_en: datetime) -> bytes:
    """ApplicationResponse (UBL 2.1) de "documento validado" para la factura `factura_xml` (firmada o no)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    factura = etree.fromstring(factura_xml, parser)
    numero = _requerido(factura, "cbc:ID").text or ""
    cufe = _requerido(factura, "cbc:UUID").text or ""
    ambiente = _requerido(factura, "cbc:ProfileExecutionID").text or ""
    emisor = "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/"
    razon_social = _requerido(factura, emisor + "cbc:RegistrationName").text or ""
    nit = _requerido(factura, emisor + "cbc:CompanyID")
    fecha, hora = validada_en.strftime("%Y-%m-%d"), validada_en.strftime("%H:%M:%S") + "-05:00"

    raiz = etree.Element(f"{{{NS_AR}}}ApplicationResponse", nsmap={None: NS_AR, "cac": NS_CAC, "cbc": NS_CBC})  # type: ignore[dict-item]
    _sub(raiz, "cbc:UBLVersionID", "UBL 2.1")
    _sub(raiz, "cbc:CustomizationID", "1")
    _sub(raiz, "cbc:ProfileID", "DIAN 2.1")
    _sub(raiz, "cbc:ProfileExecutionID", ambiente)
    _sub(raiz, "cbc:ID", f"SIM{numero}")
    cude = hashlib.sha384(f"{numero}{cufe}{fecha}{hora}".encode()).hexdigest()
    _sub(raiz, "cbc:UUID", cude, schemeName="CUDE-SHA384")
    _sub(raiz, "cbc:IssueDate", fecha)
    _sub(raiz, "cbc:IssueTime", hora)
    _sub(raiz, "cbc:Note", NOTA_SIMULADA)
    _parte(raiz, "cac:SenderParty", NOMBRE_DIAN, None, nit_dian=True)
    _parte(raiz, "cac:ReceiverParty", razon_social, nit, nit_dian=False)
    documento = _sub(raiz, "cac:DocumentResponse")
    respuesta = _sub(documento, "cac:Response")
    _sub(respuesta, "cbc:ResponseCode", "02")
    _sub(respuesta, "cbc:Description", "Documento validado por la DIAN (SIMULADO)")
    referencia = _sub(documento, "cac:DocumentReference")
    _sub(referencia, "cbc:ID", numero)
    _sub(referencia, "cbc:UUID", cufe, schemeName="CUFE-SHA384")
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")
