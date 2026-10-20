"""Paquete de entrega al cliente: un ZIP con el AttachedDocument y el PDF, y el asunto del correo.

Normativa (Anexo Técnico v1.8 §9.1, vigente en v1.9):
    · Un único .zip con el AttachedDocument; el PDF de la representación gráfica es opcional y va en el mismo ZIP.
      Peso máximo por envío: 2 MB.
    · Asunto: NIT;Nombre del facturador;Número;Código del tipo de documento;Nombre comercial — campos del XML,
      separados por ";" (el buzón del adquiriente los lee por posición).
    · Los nombres de archivo NO los reglamenta la DIAN ("acuerdo entre las partes"). Se usa la convención común:
      <prefijo><NIT a 10 dígitos><000 = software propio><año 2 dígitos><consecutivo hexadecimal de 8>.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from lxml import etree

from .attached import construir_attached
from .firma import Certificado
from .representacion import generar_pdf, leer_xml

LIMITE_ZIP = 2 * 1024 * 1024  # 2 MB por envío (anexo)
_NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
_VENDEDOR = "cac:AccountingSupplierParty/cac:Party/"


class PaqueteDemasiadoGrande(ValueError):
    """El ZIP supera el máximo de 2 MB que admite el correo de recepción."""


@dataclass(frozen=True, slots=True)
class Paquete:
    nombre_zip: str
    zip: bytes
    asunto: str


def nombre_archivo(prefijo: Literal["ad", "fv", "z"], nit: str, anio: int, consecutivo: int, ext: str) -> str:
    if not 1 <= consecutivo <= 0xFFFFFFFF:
        raise ValueError(f"consecutivo de entrega fuera de rango (1 a FFFFFFFF): {consecutivo}")
    return f"{prefijo}{int(nit):010d}000{anio % 100:02d}{consecutivo:08x}.{ext}"


def _campo(raiz: etree._Element, *rutas: str) -> str:
    for ruta in rutas:
        valor = raiz.findtext(ruta, namespaces=_NS)
        if valor and valor.strip():
            # Un ";" o un salto de línea dentro de un campo correría los demás: se neutralizan.
            return " ".join(valor.replace(";", ",").split())
    raise ValueError(f"la factura no tiene {rutas[0]}")


def asunto(factura_xml: bytes) -> str:
    raiz = etree.fromstring(factura_xml, etree.XMLParser(resolve_entities=False, no_network=True))
    return ";".join(
        [
            _campo(raiz, _VENDEDOR + "cac:PartyTaxScheme/cbc:CompanyID"),
            _campo(raiz, _VENDEDOR + "cac:PartyTaxScheme/cbc:RegistrationName"),
            _campo(raiz, "cbc:ID"),
            _campo(raiz, "cbc:InvoiceTypeCode"),
            _campo(raiz, _VENDEDOR + "cac:PartyName/cbc:Name", _VENDEDOR + "cac:PartyTaxScheme/cbc:RegistrationName"),
        ]
    )


def armar(
    factura_firmada: bytes,
    application_response: bytes,
    *,
    consecutivo: int,
    ahora: datetime,
    certificado: Certificado,
    validacion: str,
) -> Paquete:
    """Arma el ZIP de entrega. `ahora`: hora local (fecha del contenedor y de los archivos dentro del ZIP)."""
    nit = asunto(factura_firmada).split(";")[0]
    nombre = lambda prefijo, ext: nombre_archivo(prefijo, nit, ahora.year, consecutivo, ext)  # noqa: E731
    contenedor = construir_attached(
        factura_firmada, application_response, consecutivo=consecutivo, ahora=ahora, certificado=certificado
    )
    pdf = generar_pdf(leer_xml(factura_firmada), validacion=validacion)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for archivo, contenido in ((nombre("ad", "xml"), contenedor), (nombre("fv", "pdf"), pdf)):
            info = zipfile.ZipInfo(archivo, date_time=ahora.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, contenido)
    datos = buffer.getvalue()
    if len(datos) > LIMITE_ZIP:
        raise PaqueteDemasiadoGrande(f"el ZIP pesa {len(datos)} bytes; el máximo por correo es {LIMITE_ZIP}")
    return Paquete(nombre_zip=nombre("z", "zip"), zip=datos, asunto=asunto(factura_firmada))
