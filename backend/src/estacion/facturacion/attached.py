"""AttachedDocument: el contenedor electrónico que se le entrega al adquiriente (Anexo Técnico 1.9, §6.4).

Lleva, en un único XML firmado, la factura firmada (`cac:Attachment`, en CDATA, byte a byte) y la respuesta de
validación de la DIAN (`cac:ParentDocumentLineReference`, también en CDATA) con su resultado. Se arma a partir del
XML firmado y de la respuesta, nunca de la base: el contenedor no puede decir algo distinto de los documentos.

A CONFIRMAR EN HABILITACIÓN:
    · El anexo pide `UBLExtension 2..N` (DianExtensions y Signature), pero su ejemplo solo trae la firma. Aquí va
      como el ejemplo: una sola extensión, con la firma.
    · `TaxLevelCode` del receptor es obligatorio y la factura no trae la responsabilidad fiscal del comprador:
      se usa `RESPONSABILIDAD_RECEPTOR_SIN_DATO` (R-99-PN, "no aplica – otros"). Lo confirma el contador.
"""

from __future__ import annotations

from datetime import datetime

from lxml import etree

from .firma import Certificado, firmar

NS_AD = "urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NS_EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
_NSMAP = {
    None: NS_AD,
    "cac": NS_CAC,
    "cbc": NS_CBC,
    "ext": NS_EXT,
    "ds": NS_DS,
    "xades": "http://uri.etsi.org/01903/v1.3.2#",
    "xades141": "http://uri.etsi.org/01903/v1.4.1#",
}
_LEER = {"cac": NS_CAC, "cbc": NS_CBC, "ds": NS_DS}
_URI = {"cac": NS_CAC, "cbc": NS_CBC, "ext": NS_EXT}

RESPONSABILIDAD_RECEPTOR_SIN_DATO = "R-99-PN"
VALIDADOR = "Unidad Especial Dirección de Impuestos y Aduanas Nacionales"


class AttachedInvalido(ValueError):
    """Los documentos no permiten armar un contenedor válido (factura sin firmar, respuesta de otra factura...)."""


def _sub(
    padre: etree._Element, nombre: str, texto: str | etree.CDATA | None = None, **atributos: str
) -> etree._Element:
    prefijo, local = nombre.split(":")
    el = etree.SubElement(padre, f"{{{_URI[prefijo]}}}{local}", attrib=atributos)
    if texto is not None:
        el.text = texto  # lxml acepta CDATA como texto
    return el


def _leer(xml: bytes, que: str) -> etree._Element:
    try:
        return etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise AttachedInvalido(f"{que} no es un XML válido: {e}") from None


def _texto(raiz: etree._Element, ruta: str, que: str) -> str:
    valor = raiz.findtext(ruta, namespaces=_LEER)
    if not valor or not valor.strip():
        raise AttachedInvalido(f"{que} no tiene {ruta}")
    return valor.strip()


def _parte(padre: etree._Element, nombre: str, origen: etree._Element, *, responsabilidad_si_falta: str) -> None:
    """Copia el PartyTaxScheme de la factura (razón social, documento con sus atributos, responsabilidad, tributo)."""
    esquema = _sub(_sub(padre, nombre), "cac:PartyTaxScheme")
    _sub(esquema, "cbc:RegistrationName", _texto(origen, "cbc:RegistrationName", "la factura"))
    documento = origen.find("cbc:CompanyID", _LEER)
    numero = "" if documento is None else (documento.text or "").strip()
    if documento is None or not numero:
        raise AttachedInvalido("la factura no tiene el documento (CompanyID) de una de las partes")
    _sub(esquema, "cbc:CompanyID", numero, **{str(k): str(v) for k, v in documento.attrib.items()})
    _sub(esquema, "cbc:TaxLevelCode", origen.findtext("cbc:TaxLevelCode", namespaces=_LEER) or responsabilidad_si_falta)
    impuesto = _sub(esquema, "cac:TaxScheme")
    _sub(impuesto, "cbc:ID", _texto(origen, "cac:TaxScheme/cbc:ID", "la factura"))
    _sub(impuesto, "cbc:Name", _texto(origen, "cac:TaxScheme/cbc:Name", "la factura"))


def _referencia_externa(padre: etree._Element, documento: bytes) -> None:
    externa = _sub(_sub(padre, "cac:Attachment"), "cac:ExternalReference")
    _sub(externa, "cbc:MimeCode", "text/xml")
    _sub(externa, "cbc:EncodingCode", "UTF-8")
    _sub(externa, "cbc:Description", etree.CDATA(documento.decode("utf-8")))


def construir_attached(
    factura_firmada: bytes,
    application_response: bytes,
    *,
    consecutivo: int,
    ahora: datetime,
    certificado: Certificado,
) -> bytes:
    """El AttachedDocument firmado (bytes UTF-8). `ahora` es hora local de Colombia, sin zona."""
    factura = _leer(factura_firmada, "la factura")
    respuesta = _leer(application_response, "la respuesta de la DIAN")
    if factura.find(".//ds:Signature", _LEER) is None:
        raise AttachedInvalido("la factura está sin firmar: el cliente solo recibe documentos firmados")

    numero = _texto(factura, "cbc:ID", "la factura")
    cufe = _texto(factura, "cbc:UUID", "la factura")
    fecha_factura = _texto(factura, "cbc:IssueDate", "la factura")
    if _texto(respuesta, "cac:DocumentResponse/cac:DocumentReference/cbc:UUID", "la respuesta") != cufe:
        raise AttachedInvalido("la respuesta de la DIAN es de otra factura (el CUFE no coincide)")
    if ahora.strftime("%Y-%m-%d") < fecha_factura:
        raise AttachedInvalido("la fecha del contenedor es anterior a la factura (el anexo no lo permite)")

    raiz = etree.Element(f"{{{NS_AD}}}AttachedDocument", nsmap=_NSMAP)  # type: ignore[arg-type]
    _sub(_sub(_sub(raiz, "ext:UBLExtensions"), "ext:UBLExtension"), "ext:ExtensionContent")  # aquí va la firma
    _sub(raiz, "cbc:UBLVersionID", "UBL 2.1")
    _sub(raiz, "cbc:CustomizationID", "Documentos adjuntos")
    _sub(raiz, "cbc:ProfileID", "Factura Electrónica de Venta")
    _sub(raiz, "cbc:ProfileExecutionID", _texto(factura, "cbc:ProfileExecutionID", "la factura"))
    _sub(raiz, "cbc:ID", str(consecutivo))
    _sub(raiz, "cbc:IssueDate", ahora.strftime("%Y-%m-%d"))
    _sub(raiz, "cbc:IssueTime", ahora.strftime("%H:%M:%S") + "-05:00")
    _sub(raiz, "cbc:DocumentType", "Contenedor de Factura Electrónica")
    _sub(raiz, "cbc:ParentDocumentID", numero)

    vendedor = factura.find("cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme", _LEER)
    comprador = factura.find("cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme", _LEER)
    if vendedor is None or comprador is None:
        raise AttachedInvalido("la factura no tiene los datos tributarios del vendedor o del comprador")
    _parte(raiz, "cac:SenderParty", vendedor, responsabilidad_si_falta=RESPONSABILIDAD_RECEPTOR_SIN_DATO)
    _parte(raiz, "cac:ReceiverParty", comprador, responsabilidad_si_falta=RESPONSABILIDAD_RECEPTOR_SIN_DATO)
    _referencia_externa(raiz, factura_firmada)

    linea = _sub(raiz, "cac:ParentDocumentLineReference")
    _sub(linea, "cbc:LineID", "1")
    documento = _sub(linea, "cac:DocumentReference")
    _sub(documento, "cbc:ID", numero)
    # La factura se identifica con CUFE; la nota crédito, con CUDE.
    nombre_codigo = "CUDE-SHA384" if etree.QName(factura).localname == "CreditNote" else "CUFE-SHA384"
    _sub(documento, "cbc:UUID", cufe, schemeName=nombre_codigo)
    _sub(documento, "cbc:IssueDate", fecha_factura)
    _sub(documento, "cbc:DocumentType", "ApplicationResponse")
    _referencia_externa(documento, application_response)
    verificacion = _sub(documento, "cac:ResultOfVerification")
    _sub(verificacion, "cbc:ValidatorID", VALIDADOR)
    _sub(verificacion, "cbc:ValidationResultCode",
         _texto(respuesta, "cac:DocumentResponse/cac:Response/cbc:ResponseCode", "la respuesta"))  # fmt: skip
    _sub(verificacion, "cbc:ValidationDate", _texto(respuesta, "cbc:IssueDate", "la respuesta"))
    _sub(verificacion, "cbc:ValidationTime", _texto(respuesta, "cbc:IssueTime", "la respuesta"))

    # La firma usa el reloj real (el certificado debe estar vigente AL FIRMAR); `ahora` solo fecha el contenedor.
    return firmar(raiz, certificado, extension=1)
