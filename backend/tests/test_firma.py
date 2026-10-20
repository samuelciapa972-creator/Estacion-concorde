"""Pruebas de la firma XAdES (sin conexión a la DIAN).

    PYTHONPATH=src python -m unittest discover -s tests -v

Qué garantizan: la firma es matemáticamente válida, sobrevive a serializar y volver a leer el XML, y cualquier
cambio al documento la rompe. Qué NO garantizan: que la DIAN la acepte (la política, el formato del emisor y el
rol solo los valida el ambiente de habilitación). Usan un certificado autofirmado generado al vuelo.
"""

import base64
import copy
import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from estacion.facturacion.firma import (
    POLITICA_HASH,
    POLITICA_URL,
    Certificado,
    FirmaError,
    cargar_certificado,
    certificado_desde_entorno,
    firmar,
    firmar_xml,
)
from test_xml_factura import construir

NS = {
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xades": "http://uri.etsi.org/01903/v1.3.2#",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def _certificado(desde=-1, hasta=365) -> Certificado:
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name(
        [x509.NameAttribute(NameOID.COUNTRY_NAME, "CO"), x509.NameAttribute(NameOID.COMMON_NAME, "EMISOR DE PRUEBA")]
    )
    ahora = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora + timedelta(days=desde))
        .not_valid_after(ahora + timedelta(days=hasta))
        .sign(llave, hashes.SHA256())
    )
    return Certificado(llave, cert)


CERT = _certificado()


def verificar(xml: bytes, cert: Certificado) -> list[str]:
    """Verificación INDEPENDIENTE: recalcula cada digest y comprueba la firma RSA. Devuelve los problemas."""
    raiz = etree.fromstring(xml)
    firma = raiz.xpath("//ds:Signature", namespaces=NS)[0]
    problemas = []
    for ref in firma.xpath("ds:SignedInfo/ds:Reference", namespaces=NS):
        uri = ref.get("URI")
        if uri == "":
            copia = copy.deepcopy(raiz)
            sobrante = copia.xpath("//ds:Signature", namespaces=NS)[0]
            sobrante.getparent().remove(sobrante)  # transformación enveloped
            canon = etree.tostring(copia, method="c14n", exclusive=False, with_comments=False)
        else:
            nodo = raiz.xpath(f"//*[@Id='{uri[1:]}']")[0]
            canon = etree.tostring(nodo, method="c14n", exclusive=False, with_comments=False)
        esperado = base64.b64encode(hashlib.sha256(canon).digest()).decode()
        if ref.find("ds:DigestValue", NS).text != esperado:
            problemas.append(f"digest distinto en {uri!r}")
    info = firma.find("ds:SignedInfo", NS)
    valor = base64.b64decode(firma.find("ds:SignatureValue", NS).text)
    try:
        cert.cert.public_key().verify(
            valor,
            etree.tostring(info, method="c14n", exclusive=False, with_comments=False),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature:
        problemas.append("la firma RSA no coincide")
    return problemas


class TestFirma(unittest.TestCase):
    def setUp(self):
        self.f = construir()
        self.firmado = firmar(self.f.arbol, CERT)
        self.raiz = etree.fromstring(self.firmado)

    def test_la_firma_va_en_el_segundo_extension_content(self):
        hueco = self.raiz.xpath("ext:UBLExtensions/ext:UBLExtension[2]/ext:ExtensionContent", namespaces=NS)[0]
        self.assertEqual([c.tag for c in hueco], [f"{{{NS['ds']}}}Signature"])
        primero = self.raiz.xpath(
            "ext:UBLExtensions/ext:UBLExtension[1]/ext:ExtensionContent//ds:Signature", namespaces=NS
        )
        self.assertEqual(primero, [])

    def test_estructura_xades(self):
        r = self.raiz
        self.assertEqual(len(r.xpath("//ds:SignedInfo/ds:Reference", namespaces=NS)), 3)
        self.assertEqual(
            r.xpath("//ds:Reference[@URI='']/ds:Transforms/ds:Transform/@Algorithm", namespaces=NS),
            ["http://www.w3.org/2000/09/xmldsig#enveloped-signature"],
        )
        props = r.xpath("//ds:Reference[@Type='http://uri.etsi.org/01903#SignedProperties']", namespaces=NS)
        self.assertEqual(len(props), 1)
        self.assertEqual(r.xpath("//xades:SigPolicyId/xades:Identifier", namespaces=NS)[0].text, POLITICA_URL)
        self.assertEqual(r.xpath("//xades:SigPolicyHash/ds:DigestValue", namespaces=NS)[0].text, POLITICA_HASH)
        self.assertEqual(r.xpath("//xades:ClaimedRole", namespaces=NS)[0].text, "supplier")
        hora = r.xpath("//xades:SigningTime", namespaces=NS)[0].text
        self.assertTrue(hora.endswith("-05:00"), hora)
        serie = r.xpath("//xades:IssuerSerial/ds:X509SerialNumber", namespaces=NS)[0].text
        self.assertEqual(serie, str(CERT.cert.serial_number))
        der = CERT.cert.public_bytes(Encoding.DER)
        digest = r.xpath("//xades:CertDigest/ds:DigestValue", namespaces=NS)[0].text
        self.assertEqual(digest, base64.b64encode(hashlib.sha256(der).digest()).decode())

    def test_la_firma_es_valida_despues_de_serializar_y_leer_de_nuevo(self):
        self.assertEqual(verificar(self.firmado, CERT), [])

    def test_cambiar_el_documento_rompe_la_firma(self):
        raiz = etree.fromstring(self.firmado)
        monto = raiz.xpath("//cac:LegalMonetaryTotal/cbc:PayableAmount", namespaces=NS)[0]
        monto.text = "1.000000"
        problemas = verificar(etree.tostring(raiz), CERT)
        self.assertIn("digest distinto en ''", problemas)

    def test_cambiar_la_firma_la_rompe(self):
        raiz = etree.fromstring(self.firmado)
        raiz.xpath("//xades:SigningTime", namespaces=NS)[0].text = "2020-01-01T00:00:00-05:00"
        problemas = verificar(etree.tostring(raiz), CERT)
        self.assertTrue(any("SignedProperties" in p or "signedprops" in p for p in problemas), problemas)

    def test_no_se_firma_dos_veces(self):
        with self.assertRaises(FirmaError):
            firmar(self.f.arbol, CERT)
        with self.assertRaises(FirmaError):
            firmar_xml(self.firmado, CERT)

    def test_firmar_desde_bytes_da_una_firma_valida(self):
        firmado = firmar_xml(construir().xml, CERT)
        self.assertEqual(verificar(firmado, CERT), [])

    def test_otro_certificado_no_valida(self):
        self.assertIn("la firma RSA no coincide", verificar(self.firmado, _certificado()))

    def test_documento_sin_lugar_de_firma(self):
        raiz = etree.fromstring(construir().xml)
        exts = raiz.xpath("ext:UBLExtensions", namespaces=NS)[0]
        exts.remove(exts.xpath("ext:UBLExtension[2]", namespaces=NS)[0])
        with self.assertRaises(FirmaError):
            firmar(raiz, CERT)

    def test_si_falla_no_deja_el_documento_a_medio_firmar(self):
        f = construir()
        vencido = _certificado(desde=-400, hasta=-10)
        with self.assertRaises(FirmaError):
            firmar(f.arbol, vencido)
        self.assertEqual(f.arbol.xpath("count(//ds:Signature)", namespaces=NS), 0)
        self.assertEqual(verificar(firmar(f.arbol, CERT), CERT), [])  # y todavía se puede firmar bien


class TestCertificado(unittest.TestCase):
    def _pfx(self, carpeta: str, clave: bytes) -> str:
        datos = pkcs12.serialize_key_and_certificates(b"x", CERT.clave, CERT.cert, None, BestAvailableEncryption(clave))
        ruta = Path(carpeta) / "c.pfx"
        ruta.write_bytes(datos)
        return str(ruta)

    def test_carga_con_la_clave_correcta(self):
        with tempfile.TemporaryDirectory() as d:
            c = cargar_certificado(self._pfx(d, b"clave-buena"), "clave-buena")
            self.assertEqual(c.cert.serial_number, CERT.cert.serial_number)

    def test_clave_incorrecta_no_aparece_en_el_error(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = self._pfx(d, b"clave-buena")
            with self.assertRaises(FirmaError) as ctx:
                cargar_certificado(ruta, "clave-mala")
            self.assertNotIn("clave-mala", str(ctx.exception))
            self.assertNotIn("clave-buena", str(ctx.exception))

    def test_archivo_inexistente(self):
        with self.assertRaises(FirmaError):
            cargar_certificado("/no/existe.pfx", "x")

    def test_entorno_incompleto_dice_que_falta(self):
        with self.assertRaises(FirmaError) as ctx:
            certificado_desde_entorno({"DIAN_CERTIFICADO_RUTA": "/algo.pfx"})
        self.assertIn("DIAN_CERTIFICADO_CLAVE", str(ctx.exception))
        self.assertNotIn("DIAN_CERTIFICADO_RUTA", str(ctx.exception))

    def test_certificado_vencido_se_rechaza(self):
        with self.assertRaises(FirmaError):
            firmar(construir().arbol, _certificado(desde=-400, hasta=-10))


try:
    from signxml import XMLVerifier
except ImportError:  # signxml es opcional: solo da una segunda opinión sobre la firma
    XMLVerifier = None


@unittest.skipIf(XMLVerifier is None, "signxml no está instalado")
class TestContraUnaLibreriaAjena(unittest.TestCase):
    def test_signxml_valida_la_firma(self):
        firmado = firmar(construir().arbol, CERT)
        pem = CERT.cert.public_bytes(Encoding.PEM)
        resultados = XMLVerifier().verify(firmado, x509_cert=pem, expect_references=3)  # lanza si algo no cuadra
        self.assertEqual(len(resultados), 3)  # documento, KeyInfo y SignedProperties


if __name__ == "__main__":
    unittest.main()
