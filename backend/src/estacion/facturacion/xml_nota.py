"""Generador del XML de la nota crédito de ANULACIÓN TOTAL (UBL 2.1 `CreditNote`, formato DIAN), SIN FIRMAR.

ALCANCE: la nota anula el valor total de una factura ya aceptada: mismas líneas, mismos valores, mismo cliente.
Por eso arma el vendedor, el comprador, el pago, los totales y las líneas con los mismos helpers que la factura
(`xml_factura.py`) y con la misma `SolicitudFactura`; solo cambian el encabezado, la referencia a la factura y el
concepto de corrección. Notas parciales y notas débito no están en el alcance.

Lo propio de la nota (Anexo Técnico 1.9):
    · CustomizationID 20 (nota que referencia una factura electrónica), CreditNoteTypeCode 91.
    · DiscrepancyResponse con ResponseCode 2 ("Anulación de factura electrónica") y el motivo como descripción.
    · BillingReference/InvoiceDocumentReference con número, CUFE y fecha de la factura anulada.
    · CUDE (no CUFE): usa el PIN del software, no la clave técnica de la resolución.
    · Sin `sts:InvoiceControl`: las notas no llevan resolución de numeración (su serie es propia: NCE/NCP).

SIN VERIFICAR (lo dirá la nota de prueba en habilitación):
    · `CorporateRegistrationScheme/ID` del vendedor lleva el prefijo de la NOTA (como la factura lleva el suyo).
    · El código de seguridad del software se calcula igual que en la factura, con el número de la nota.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from lxml import etree

from .configuracion import Emisor, SoftwareDian
from .modelos import SolicitudFactura
from .xml_factura import (
    NS,
    URL_QR,
    FacturaXml,
    _dec2,
    _sha384,
    _sub,
    agregar_extensiones,
    agregar_lineas,
    agregar_partes_y_totales,
    codigo_seguridad_software,
    documento_adquirente,
    fecha_y_hora,
    validar_solicitud,
)

# Lo que XML 1.0 no admite en el texto (lxml lanza ValueError): controles C0 salvo tabulación y saltos de línea,
# sustitutos sueltos y los no-caracteres U+FFFE/U+FFFF. Llegan pegados desde Word u otros programas.
CARACTERES_NO_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")

NS_NOTA = {**NS, None: "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"}
_ESQUEMA_LOCACION = (
    "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2 "
    "http://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-CreditNote-2.1.xsd"
)
_CONCEPTO_ANULACION = "2"  # Anulación de factura electrónica


class NotaInvalida(ValueError):
    """Faltan datos de la nota (motivo, factura de referencia) o el consecutivo no sirve."""


@dataclass(frozen=True)
class ReferenciaFactura:
    """La factura que la nota anula, tal como la aceptó la DIAN."""

    numero: str  # prefijo + consecutivo, p. ej. "EDSC1"
    cufe: str
    fecha_emision: datetime


def calcular_cude(
    *,
    numero: str,
    fecha: str,
    hora: str,
    valor: Decimal,
    valor_total: Decimal,
    nit_emisor: str,
    num_doc_adquirente: str,
    pin_software: str,
    ambiente: str,
    iva: Decimal = Decimal(0),
    inc: Decimal = Decimal(0),
    ica: Decimal = Decimal(0),
) -> str:
    """CUDE = SHA-384 de la concatenación del Anexo Técnico 1.9 para notas crédito:

    NumNC + FecNC + HorNC + ValNC + 01 + ValIVA + 04 + ValINC + 03 + ValICA + ValTot + NitOFE + NumAdq
    + Software-PIN + TipoAmbiente
    Igual que el CUFE (valores a 2 decimales, punto decimal) salvo que en lugar de la clave técnica va el PIN del
    software: la nota no tiene resolución de numeración, así que no hay clave técnica.
    """
    cadena = (
        f"{numero}{fecha}{hora}{_dec2(valor)}"
        f"01{_dec2(iva)}04{_dec2(inc)}03{_dec2(ica)}{_dec2(valor_total)}"
        f"{nit_emisor}{num_doc_adquirente}{pin_software}{ambiente}"
    )
    return _sha384(cadena)


def _validar(prefijo: str, consecutivo: int, referencia: ReferenciaFactura, motivo: str) -> None:
    if not prefijo or consecutivo < 1:
        raise NotaInvalida(f"el consecutivo de la nota {prefijo!r}{consecutivo} no es válido (la serie empieza en 1)")
    if not motivo.strip():
        raise NotaInvalida("el motivo de la anulación es obligatorio")
    if not referencia.numero or not referencia.cufe:
        raise NotaInvalida("falta el número o el CUFE de la factura que se anula")


def construir_nota_credito(
    sol: SolicitudFactura,
    *,
    prefijo: str,
    consecutivo: int,
    referencia: ReferenciaFactura,
    motivo: str,
    emisor: Emisor,
    software: SoftwareDian,
) -> FacturaXml:
    """`sol` es la venta de la factura anulada, con `fecha_emision` = momento de la NOTA. En `cufe` va el CUDE."""
    _validar(prefijo, consecutivo, referencia, motivo)
    validar_solicitud(sol)

    numero = f"{prefijo}{consecutivo}"
    fecha, hora = fecha_y_hora(sol.fecha_emision)
    total = sol.total
    cude = calcular_cude(
        numero=numero,
        fecha=fecha,
        hora=hora,
        valor=total,
        valor_total=total,
        nit_emisor=emisor.nit,
        num_doc_adquirente=documento_adquirente(sol),
        pin_software=software.pin,
        ambiente=software.ambiente,
    )
    codigo_sw = codigo_seguridad_software(software, numero)
    qr = URL_QR[software.ambiente] + cude

    raiz = etree.Element(f"{{{NS_NOTA[None]}}}CreditNote", nsmap=NS_NOTA)  # type: ignore[arg-type]  # lxml acepta None (espacio por defecto)
    raiz.set(f"{{{NS['xsi']}}}schemaLocation", _ESQUEMA_LOCACION)
    agregar_extensiones(raiz, software, codigo_sw=codigo_sw, qr=qr, numeracion=None)

    # ---- Encabezado (el orden lo fija el XSD de CreditNote) ----
    _sub(raiz, "cbc:UBLVersionID", "UBL 2.1")
    _sub(raiz, "cbc:CustomizationID", "20")
    _sub(raiz, "cbc:ProfileID", "DIAN 2.1: Nota Crédito de Factura Electrónica de Venta")
    _sub(raiz, "cbc:ProfileExecutionID", software.ambiente)
    _sub(raiz, "cbc:ID", numero)
    _sub(raiz, "cbc:UUID", cude, schemeID=software.ambiente, schemeName="CUDE-SHA384")
    _sub(raiz, "cbc:IssueDate", fecha)
    _sub(raiz, "cbc:IssueTime", hora)
    _sub(raiz, "cbc:CreditNoteTypeCode", "91")
    _sub(raiz, "cbc:DocumentCurrencyCode", "COP")
    _sub(raiz, "cbc:LineCountNumeric", str(len(sol.lineas)))

    # ---- Concepto de corrección y factura anulada (DiscrepancyResponse va ANTES de BillingReference) ----
    discrepancia = _sub(raiz, "cac:DiscrepancyResponse")
    _sub(discrepancia, "cbc:ReferenceID", referencia.numero)
    _sub(discrepancia, "cbc:ResponseCode", _CONCEPTO_ANULACION)
    _sub(discrepancia, "cbc:Description", motivo.strip())
    factura = _sub(_sub(raiz, "cac:BillingReference"), "cac:InvoiceDocumentReference")
    _sub(factura, "cbc:ID", referencia.numero)
    _sub(factura, "cbc:UUID", referencia.cufe, schemeName="CUFE-SHA384")
    _sub(factura, "cbc:IssueDate", referencia.fecha_emision.strftime("%Y-%m-%d"))

    agregar_partes_y_totales(raiz, sol, emisor=emisor, prefijo=prefijo, fecha=fecha)
    agregar_lineas(raiz, sol, etiqueta="cac:CreditNoteLine", etiqueta_cantidad="cbc:CreditedQuantity")

    xml = etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")
    return FacturaXml(xml=xml, arbol=raiz, numero=numero, cufe=cude, qr=qr, codigo_seguridad_software=codigo_sw)
