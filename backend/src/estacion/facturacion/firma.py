"""Firma XAdES-EPES del XML de la factura electrónica (formato DIAN). NO se conecta a la DIAN.

QUÉ HACE: toma el documento sin firmar que produce `xml_factura.construir_factura` y llena el SEGUNDO
`ext:ExtensionContent` (el que `xml_factura` deja vacío a propósito) con un `ds:Signature` XAdES-EPES:
RSA-SHA256, canonicalización C14N 1.0, tres referencias (el documento, el KeyInfo y las SignedProperties)
y la política de firma de la DIAN.

SIN VERIFICAR (solo el ambiente de habilitación lo confirma; si la DIAN rechaza la firma, empezar por aquí):
    · POLITICA_URL y POLITICA_HASH: escritos de memoria del Anexo Técnico. Compárense con el documento oficial
      de la política de firma (el hash es el SHA-256 del PDF, en base64).
    · El formato de `X509IssuerName` (aquí el de `cryptography`, RFC 4514).
    · El rol `supplier` y la forma exacta de `SigningTime`.
LO QUE SÍ SE PRUEBA (tests/test_firma.py): que la firma es matemáticamente válida, que sobrevive a serializar y
volver a leer el XML, y que cualquier cambio al documento la rompe.

El certificado (.pfx) y su clave NUNCA se escriben en el código ni en el log: salen de las variables de entorno
DIAN_CERTIFICADO_RUTA y DIAN_CERTIFICADO_CLAVE.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from lxml import etree

from .xmldsig import NS_DS, RSA_SHA256, SHA256, firmar_rsa_sha256
from .xmldsig import b64 as _b64
from .xmldsig import sha256_b64 as _sha256_b64
from .xmldsig import sub as _sub

NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"
NS_EXT = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"

C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
TIPO_SIGNED_PROPERTIES = "http://uri.etsi.org/01903#SignedProperties"

# SIN VERIFICAR contra el documento oficial de la política de firma de la DIAN (ver el docstring del módulo).
POLITICA_URL = "https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf"
POLITICA_HASH = "dMoMvtcG5aIzgYo0tIsSQeVJBDnUnfSOfBpxXrmor0Y="

ZONA_COLOMBIA = timezone(timedelta(hours=-5))  # sin horario de verano


class FirmaError(Exception):
    """No se pudo firmar. El mensaje no incluye la clave del certificado."""


@dataclass(frozen=True, slots=True)
class Certificado:
    clave: rsa.RSAPrivateKey
    cert: x509.Certificate


# ------------------------------------------------------------------------------------------ certificado
def cargar_certificado(ruta: str, clave: str) -> Certificado:
    try:
        datos = Path(ruta).read_bytes()
    except OSError as e:
        raise FirmaError(f"no se pudo leer el certificado en {ruta!r}: {e.strerror}") from None
    try:
        llave, cert, _ = pkcs12.load_key_and_certificates(datos, clave.encode("utf-8"))
    except ValueError:
        raise FirmaError("no se pudo abrir el certificado: clave incorrecta o archivo dañado") from None
    if llave is None or cert is None:
        raise FirmaError("el archivo .pfx no trae la llave privada y el certificado")
    if not isinstance(llave, rsa.RSAPrivateKey):
        raise FirmaError("la llave del certificado no es RSA")
    return Certificado(llave, cert)


def certificado_desde_entorno(entorno: Mapping[str, str] | None = None) -> Certificado:
    entorno = os.environ if entorno is None else entorno
    ruta = entorno.get("DIAN_CERTIFICADO_RUTA", "").strip()
    clave = entorno.get("DIAN_CERTIFICADO_CLAVE", "")
    faltan = [n for n, v in (("DIAN_CERTIFICADO_RUTA", ruta), ("DIAN_CERTIFICADO_CLAVE", clave)) if not v]
    if faltan:
        raise FirmaError("falta configurar: " + ", ".join(faltan))
    return cargar_certificado(ruta, clave)


def _validar_vigencia(cert: x509.Certificate, ahora: datetime) -> None:
    if ahora < cert.not_valid_before_utc:
        raise FirmaError(f"el certificado aún no es válido (desde {cert.not_valid_before_utc:%Y-%m-%d})")
    if ahora > cert.not_valid_after_utc:
        raise FirmaError(f"el certificado venció el {cert.not_valid_after_utc:%Y-%m-%d}")


# ------------------------------------------------------------------------------------------ utilidades XML
def _ds(nombre: str) -> str:
    return f"{{{NS_DS}}}{nombre}"


def _xa(nombre: str) -> str:
    return f"{{{NS_XADES}}}{nombre}"


def _c14n(el: etree._Element) -> bytes:
    """C14N 1.0 inclusiva, sin comentarios (el algoritmo que declara la firma)."""
    return etree.tostring(el, method="c14n", exclusive=False, with_comments=False)


def _hueco_de_la_firma(raiz: etree._Element, extension: int) -> etree._Element:
    """El `ext:ExtensionContent` vacío donde va la firma: el 2.º en la factura (el 1.º lleva DianExtensions), el 1.º
    en el AttachedDocument."""
    huecos = raiz.xpath(
        f"ext:UBLExtensions/ext:UBLExtension[{extension}]/ext:ExtensionContent", namespaces={"ext": NS_EXT}
    )
    hueco = huecos[0] if isinstance(huecos, list) and huecos else None
    if not isinstance(hueco, etree._Element):
        raise FirmaError(f"el documento no tiene el lugar de la firma (ext:UBLExtension[{extension}])")
    if len(hueco) > 0:
        raise FirmaError("el documento ya está firmado")
    return hueco


def _referencia(
    info: etree._Element, uri: str, *, id_: str | None = None, tipo: str | None = None, enveloped: bool = False
) -> etree._Element:
    atributos = {"URI": uri}
    if id_:
        atributos["Id"] = id_
    if tipo:
        atributos["Type"] = tipo
    ref = _sub(info, _ds("Reference"), **atributos)
    if enveloped:
        _sub(_sub(ref, _ds("Transforms")), _ds("Transform"), Algorithm=ENVELOPED)
    _sub(ref, _ds("DigestMethod"), Algorithm=SHA256)
    _sub(ref, _ds("DigestValue"), "")  # se llena cuando el nodo ya está en su lugar definitivo
    return ref


def _poner_digest(ref: etree._Element, nodo: etree._Element) -> None:
    ref.find(_ds("DigestValue")).text = _sha256_b64(_c14n(nodo))  # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------ firma
def firmar(
    arbol: etree._Element, certificado: Certificado, *, ahora: datetime | None = None, extension: int = 2
) -> bytes:
    """Firma el documento (MODIFICA `arbol`: agrega el `ds:Signature`) y devuelve el XML firmado en bytes.

    Después de firmar NO se debe cambiar nada del documento, ni reformatearlo (sangrías, espacios):
    la firma cubre el contenido exacto.
    """
    ahora = ahora or datetime.now(ZONA_COLOMBIA)
    cert = certificado.cert
    _validar_vigencia(cert, ahora)
    hueco = _hueco_de_la_firma(arbol, extension)

    # Lo que deja la transformación "enveloped": el documento SIN la firma (el hueco sigue ahí, vacío).
    digest_documento = _sha256_b64(_c14n(arbol))

    id_firma = f"xmldsig-{uuid.uuid4()}"
    id_keyinfo = f"{id_firma}-keyinfo"
    id_props = f"{id_firma}-signedprops"
    der = cert.public_bytes(Encoding.DER)

    firma = _sub(hueco, _ds("Signature"), Id=id_firma)
    try:
        info = _sub(firma, _ds("SignedInfo"))
        _sub(info, _ds("CanonicalizationMethod"), Algorithm=C14N)
        _sub(info, _ds("SignatureMethod"), Algorithm=RSA_SHA256)
        ref_doc = _referencia(info, "", id_=f"{id_firma}-ref0", enveloped=True)
        ref_doc.find(_ds("DigestValue")).text = digest_documento  # type: ignore[union-attr]
        ref_keyinfo = _referencia(info, f"#{id_keyinfo}")
        ref_props = _referencia(info, f"#{id_props}", tipo=TIPO_SIGNED_PROPERTIES)

        valor = _sub(firma, _ds("SignatureValue"), Id=f"{id_firma}-sigvalue")

        keyinfo = _sub(firma, _ds("KeyInfo"), Id=id_keyinfo)
        _sub(_sub(keyinfo, _ds("X509Data")), _ds("X509Certificate"), _b64(der))

        objeto = _sub(firma, _ds("Object"))
        calificadas = _sub(objeto, _xa("QualifyingProperties"), Target=f"#{id_firma}")
        props = _sub(calificadas, _xa("SignedProperties"), Id=id_props)
        sig_props = _sub(props, _xa("SignedSignatureProperties"))
        _sub(sig_props, _xa("SigningTime"), ahora.astimezone(ZONA_COLOMBIA).isoformat(timespec="seconds"))

        cert_xa = _sub(_sub(sig_props, _xa("SigningCertificate")), _xa("Cert"))
        digest_cert = _sub(cert_xa, _xa("CertDigest"))
        _sub(digest_cert, _ds("DigestMethod"), Algorithm=SHA256)
        _sub(digest_cert, _ds("DigestValue"), _sha256_b64(der))
        emisor_serie = _sub(cert_xa, _xa("IssuerSerial"))
        _sub(emisor_serie, _ds("X509IssuerName"), cert.issuer.rfc4514_string())
        _sub(emisor_serie, _ds("X509SerialNumber"), str(cert.serial_number))

        politica = _sub(_sub(sig_props, _xa("SignaturePolicyIdentifier")), _xa("SignaturePolicyId"))
        id_politica = _sub(politica, _xa("SigPolicyId"))
        _sub(id_politica, _xa("Identifier"), POLITICA_URL)
        _sub(id_politica, _xa("Description"))
        hash_politica = _sub(politica, _xa("SigPolicyHash"))
        _sub(hash_politica, _ds("DigestMethod"), Algorithm=SHA256)
        _sub(hash_politica, _ds("DigestValue"), POLITICA_HASH)

        _sub(_sub(_sub(sig_props, _xa("SignerRole")), _xa("ClaimedRoles")), _xa("ClaimedRole"), "supplier")

        # Los digests se calculan con los nodos ya en su sitio (la C14N inclusiva depende de los
        # espacios de nombres heredados) y SignedInfo se firma al final, cuando ya está completo.
        _poner_digest(ref_keyinfo, keyinfo)
        _poner_digest(ref_props, props)
        valor.text = firmar_rsa_sha256(certificado.clave, _c14n(info))
    except Exception:
        hueco.remove(firma)  # no dejar el documento a medio firmar
        raise

    return etree.tostring(arbol, xml_declaration=True, encoding="UTF-8")


def firmar_xml(
    documento: bytes, certificado: Certificado, *, ahora: datetime | None = None, extension: int = 2
) -> bytes:
    """Igual que `firmar`, partiendo del XML en bytes (como lo guarda `factura.xml_sin_firmar`)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        raiz = etree.fromstring(documento, parser)
    except etree.XMLSyntaxError as e:
        raise FirmaError(f"el documento no es un XML válido: {e}") from None
    return firmar(raiz, certificado, ahora=ahora, extension=extension)
