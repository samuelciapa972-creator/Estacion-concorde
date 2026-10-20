"""Piezas de XML-DSig que comparten la firma XAdES de la factura (`firma.py`) y la firma WS-Security de los
mensajes al web service de la DIAN (`dian/sobre.py`)."""

from __future__ import annotations

import base64
import hashlib

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

NS_DS = "http://www.w3.org/2000/09/xmldsig#"
RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"


def sub(padre: etree._Element, etiqueta: str, texto: str | None = None, **atributos: str) -> etree._Element:
    el = etree.SubElement(padre, etiqueta, attrib=atributos)
    if texto is not None:
        el.text = texto
    return el


def b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def sha256_b64(datos: bytes) -> str:
    return b64(hashlib.sha256(datos).digest())


def firmar_rsa_sha256(clave: rsa.RSAPrivateKey, datos: bytes) -> str:
    return b64(clave.sign(datos, padding.PKCS1v15(), hashes.SHA256()))


def verificar_rsa_sha256(cert: x509.Certificate, datos: bytes, firma_b64: str) -> bool:
    publica = cert.public_key()
    if not isinstance(publica, rsa.RSAPublicKey):
        return False
    try:
        publica.verify(base64.b64decode(firma_b64), datos, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError):
        return False
    return True
