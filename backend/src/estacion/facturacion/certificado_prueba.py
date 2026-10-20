"""Certificado autofirmado SOLO para el banco de pruebas.

No lo emite una entidad de certificación: la DIAN lo rechazaría. Sirve para conectar la firma al flujo (y armar el
ZIP de entrega) antes de tener el certificado real de la empresa. Su nombre lo dice: `MARCA`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID

from .firma import Certificado

MARCA = "CERTIFICADO DE PRUEBA SIN VALIDEZ"


def generar_certificado_prueba(nombre: str, *, dias: int = 365, ahora: datetime | None = None) -> Certificado:
    ahora = ahora or datetime.now(UTC)
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"{nombre} - {MARCA}"[-64:]),  # CN: máximo 64 caracteres
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CO"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=5))
        .not_valid_after(ahora + timedelta(days=dias))
        .sign(clave, hashes.SHA256())
    )
    return Certificado(clave, cert)


def guardar_pfx(c: Certificado, ruta: Path, clave: str) -> None:
    """Guarda llave + certificado en un .pfx protegido con `clave`, legible solo por el dueño del archivo."""
    datos = pkcs12.serialize_key_and_certificates(
        b"prueba", c.clave, c.cert, None, BestAvailableEncryption(clave.encode("utf-8"))
    )
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    ruta.chmod(0o600)
