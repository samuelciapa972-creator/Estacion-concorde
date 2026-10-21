"""Mensaje SOAP 1.2 firmado con WS-Security (X.509) para el web service de la DIAN. Función pura: no hay red.

Lo que exige la política del WSDL (tests/fixtures/dian, verificado): transporte HTTPS, Timestamp, un token X.509 v3
incluido en el mensaje (EndorsingSupportingTokens) que firma el encabezado `wsa:To` (WS-Addressing 2005/08), con
la suite Basic256Sha256Rsa15 (RSA-SHA256, digest SHA-256).

    soap:Header
        wsse:Security  → wsu:Timestamp (60 s), wsse:BinarySecurityToken (el certificado), ds:Signature
        wsa:Action     → http://wcf.dian.colombia/IWcfDianCustomerServices/<Operación>
        wsa:To         → la URL del servicio, con wsu:Id; es lo que firma la ds:Signature
    soap:Body → wcf:<Operación> con un hijo por parámetro
SIN VERIFICAR: que la DIAN acepte la referencia directa al token (la política menciona referencia por huella);
lo confirma `python -m estacion.habilitacion probar-conexion`.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree

from ..firma import Certificado
from ..xmldsig import NS_DS, RSA_SHA256, SHA256, b64, firmar_rsa_sha256, sha256_b64, sub, verificar_rsa_sha256

URL_HABILITACION = "https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc"
URL_PRODUCCION = "https://vpfe.dian.gov.co/WcfDianCustomerServices.svc"


def url_servicio(ambiente: str) -> str:
    """URL del web service según el ambiente DIAN: "1" producción, "2" habilitación."""
    if ambiente == "1":
        return URL_PRODUCCION
    if ambiente == "2":
        return URL_HABILITACION
    raise ValueError(f"ambiente DIAN desconocido: {ambiente!r}")


NS_SOAP = "http://www.w3.org/2003/05/soap-envelope"
NS_WSA = "http://www.w3.org/2005/08/addressing"
NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
NS_WCF = "http://wcf.dian.colombia"
NS_EC = "http://www.w3.org/2001/10/xml-exc-c14n#"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
X509V3 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3"
BASE64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"
VIGENCIA = timedelta(seconds=60)
_PREFIJOS_TO = ["soap", "wcf"]
_PREFIJOS_INFO = ["wsa", "soap", "wcf"]
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


class SobreInvalido(Exception):
    """El mensaje firmado no verifica (lo usan las pruebas y el diagnóstico)."""


def accion(operacion: str) -> str:
    return f"http://wcf.dian.colombia/IWcfDianCustomerServices/{operacion}"


def _q(ns: str, nombre: str) -> str:
    return f"{{{ns}}}{nombre}"


def _hora(t: datetime) -> str:
    t = t.astimezone(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _exc_c14n(el: etree._Element, prefijos: list[str]) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=True, with_comments=False, inclusive_ns_prefixes=prefijos)


def _inclusivos(padre: etree._Element, prefijos: list[str]) -> None:
    etree.SubElement(padre, _q(NS_EC, "InclusiveNamespaces"), nsmap={"ec": NS_EC}, PrefixList=" ".join(prefijos))


def sobre(
    operacion: str,
    parametros: Sequence[tuple[str, str]],
    *,
    destino: str,
    certificado: Certificado,
    ahora: datetime,
) -> bytes:
    """El sobre firmado de `operacion` con sus `parametros` (en orden), dirigido a `destino`."""
    sufijo = uuid.uuid4().hex
    id_to, id_token = f"id-{sufijo}", f"X509-{sufijo}"

    raiz = etree.Element(_q(NS_SOAP, "Envelope"), nsmap={"soap": NS_SOAP, "wcf": NS_WCF})
    # wsu va en el Header: lo usan wsse:Security y también wsa:To (hermano de Security), que necesita el MISMO
    # prefijo en el árbol y en el texto serializado para que su forma canónica (lo que se firma) no cambie.
    cabecera = etree.SubElement(raiz, _q(NS_SOAP, "Header"), nsmap={"wsa": NS_WSA, "wsu": NS_WSU})
    seguridad = etree.SubElement(cabecera, _q(NS_WSSE, "Security"), nsmap={"wsse": NS_WSSE})
    seguridad.set(_q(NS_SOAP, "mustUnderstand"), "true")
    ts = sub(seguridad, _q(NS_WSU, "Timestamp"))
    ts.set(_q(NS_WSU, "Id"), f"TS-{sufijo}")
    sub(ts, _q(NS_WSU, "Created"), _hora(ahora))
    sub(ts, _q(NS_WSU, "Expires"), _hora(ahora + VIGENCIA))
    der = certificado.cert.public_bytes(Encoding.DER)
    token = sub(seguridad, _q(NS_WSSE, "BinarySecurityToken"), b64(der), EncodingType=BASE64, ValueType=X509V3)
    token.set(_q(NS_WSU, "Id"), id_token)

    firma = etree.SubElement(seguridad, _q(NS_DS, "Signature"), nsmap={"ds": NS_DS}, Id=f"SIG-{sufijo}")
    info = sub(firma, _q(NS_DS, "SignedInfo"))
    _inclusivos(sub(info, _q(NS_DS, "CanonicalizationMethod"), Algorithm=EXC_C14N), _PREFIJOS_INFO)
    sub(info, _q(NS_DS, "SignatureMethod"), Algorithm=RSA_SHA256)
    ref = sub(info, _q(NS_DS, "Reference"), URI=f"#{id_to}")
    _inclusivos(sub(sub(ref, _q(NS_DS, "Transforms")), _q(NS_DS, "Transform"), Algorithm=EXC_C14N), _PREFIJOS_TO)
    sub(ref, _q(NS_DS, "DigestMethod"), Algorithm=SHA256)
    sub(ref, _q(NS_DS, "DigestValue"), "")
    sub(firma, _q(NS_DS, "SignatureValue"), "")
    referencia = sub(sub(firma, _q(NS_DS, "KeyInfo"), Id=f"KI-{sufijo}"), _q(NS_WSSE, "SecurityTokenReference"))
    referencia.set(_q(NS_WSU, "Id"), f"STR-{sufijo}")
    sub(referencia, _q(NS_WSSE, "Reference"), URI=f"#{id_token}", ValueType=X509V3)

    sub(cabecera, _q(NS_WSA, "Action"), accion(operacion))
    to = sub(cabecera, _q(NS_WSA, "To"), destino)
    to.set(_q(NS_WSU, "Id"), id_to)

    op = sub(etree.SubElement(raiz, _q(NS_SOAP, "Body")), _q(NS_WCF, operacion))
    for nombre, texto in parametros:
        sub(op, _q(NS_WCF, nombre), texto)

    # Se firma sobre el documento SERIALIZADO y vuelto a leer, que es lo que canonicaliza quien lo recibe: en un árbol
    # recién construido lxml omite los prefijos de InclusiveNamespaces heredados de los ancestros (soap, wcf), y el
    # digest no coincidiría con el que calcula la DIAN.
    leido = etree.fromstring(etree.tostring(raiz), _PARSER)
    ns = {"ds": NS_DS, "wsa": NS_WSA}
    to_leido = leido.find(".//wsa:To", namespaces=ns)
    info_leido = leido.find(".//ds:SignedInfo", namespaces=ns)
    digest = leido.find(".//ds:SignedInfo/ds:Reference/ds:DigestValue", namespaces=ns)
    valor = leido.find(".//ds:SignatureValue", namespaces=ns)
    assert to_leido is not None and info_leido is not None and digest is not None and valor is not None
    digest.text = sha256_b64(_exc_c14n(to_leido, _PREFIJOS_TO))
    valor.text = firmar_rsa_sha256(certificado.clave, _exc_c14n(info_leido, _PREFIJOS_INFO))
    return etree.tostring(leido, xml_declaration=True, encoding="UTF-8")


def verificar_sobre(datos: bytes, *, ahora: datetime) -> None:
    """Comprueba vigencia, digest de `wsa:To` y firma del SignedInfo con el certificado del token."""
    raiz = etree.fromstring(datos, _PARSER)
    ns = {"ds": NS_DS, "wsse": NS_WSSE, "wsu": NS_WSU, "wsa": NS_WSA}
    expira = raiz.findtext(".//wsu:Timestamp/wsu:Expires", namespaces=ns)
    if expira is None or datetime.fromisoformat(expira.replace("Z", "+00:00")) < ahora:
        raise SobreInvalido("el sobre venció (Timestamp/Expires)")
    ref = raiz.find(".//ds:SignedInfo/ds:Reference", namespaces=ns)
    to = raiz.find(".//wsa:To", namespaces=ns)
    if ref is None or to is None or ref.get("URI") != f"#{to.get(_q(NS_WSU, 'Id'))}":
        raise SobreInvalido("la firma no apunta a wsa:To")
    if ref.findtext("ds:DigestValue", namespaces=ns) != sha256_b64(_exc_c14n(to, _PREFIJOS_TO)):
        raise SobreInvalido("el digest de wsa:To no coincide")
    token = raiz.findtext(".//wsse:BinarySecurityToken", namespaces=ns) or ""
    cert = x509.load_der_x509_certificate(base64.b64decode(token))
    info = raiz.find(".//ds:SignedInfo", namespaces=ns)
    firma = raiz.findtext(".//ds:SignatureValue", namespaces=ns) or ""
    if info is None or not verificar_rsa_sha256(cert, _exc_c14n(info, _PREFIJOS_INFO), firma):
        raise SobreInvalido("la firma del SignedInfo no verifica")
