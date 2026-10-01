"""Generador del XML de la factura electrónica de venta (UBL 2.1, formato DIAN), SIN FIRMAR.

ALCANCE DE ESTA PRIMERA VERSIÓN (lo único que hay una factura real validada para comparar):
    contado · pago en efectivo · comprador persona natural con cédula · combustible sin impuestos
Todo lo demás lanza `FacturaNoSoportada` a propósito, en vez de adivinar: crédito, otros medios de pago,
comprador con NIT, líneas con IVA/impuestos y otros tipos de documento se agregan cuando se verifiquen
contra el anexo técnico de la DIAN.

REFERENCIA: la estructura calca la de una factura de gasolina real (Terpel/Masser, ambiente de producción)
que la DIAN validó ("Documento validado por la DIAN", con notificaciones). La prueba
`tests/test_xml_factura.py` compara las rutas de elementos y atributos contra ese modelo.

SIN VERIFICAR (revisar contra el anexo técnico v1.9 y con el set de pruebas de habilitación):
    · `calcular_cufe` y el código de seguridad del software: fórmulas escritas de memoria del anexo.
      La DIAN rechazará el documento si están mal; el ambiente de habilitación lo dirá.
    · `BaseQuantity` = cantidad (igual que la factura de referencia). Otros implementadores usan 1.
    · La URL del QR de habilitación (la de producción sí sale de la factura de referencia).
    · Falta la responsabilidad fiscal del comprador (la referencia tampoco la lleva y la DIAN aceptó
      el documento con una notificación sobre la responsabilidad del receptor).
NO INCLUYE: la firma XAdES (queda la extensión vacía donde va), ni el envío a la DIAN, ni PDF/QR gráfico.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from .configuracion import Emisor, Numeracion, SoftwareDian
from .documentos import calcular_dv, validar_cliente
from .modelos import SolicitudFactura

NS = {
    None: "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
    "xades": "http://uri.etsi.org/01903/v1.3.2#",
    "xades141": "http://uri.etsi.org/01903/v1.4.1#",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}
_AGENCIA = {
    "schemeAgencyID": "195",
    "schemeAgencyName": "CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)",
}
_NIT_DIAN, _DV_DIAN = "800197268", "4"
_ZONA_HORARIA = "-05:00"  # Colombia, sin horario de verano
_URL_QR = {
    "1": "https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=",
    "2": "https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey=",  # SIN VERIFICAR
}
_ESQUEMA_LOCACION = (
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 "
    "http://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-Invoice-2.1.xsd"
)
# Solo los códigos que aparecen en la factura real de referencia; el resto se agrega verificando el anexo.
_TIPO_DOCUMENTO_DIAN = {"CC": "13"}
_MEDIO_PAGO_DIAN = {"EFECTIVO": "10"}
_FORMA_PAGO_DIAN = {"CONTADO": "1"}


class FacturaNoSoportada(ValueError):
    """Un caso que este generador todavía no cubre (a propósito)."""


class NumeracionInvalida(ValueError):
    """El consecutivo o la fecha quedan fuera de la resolución de numeración."""


@dataclass(frozen=True, slots=True)
class FacturaXml:
    xml: bytes  # documento sin firmar
    arbol: etree._Element  # el mismo documento, para que el firmador agregue la firma
    numero: str  # prefijo + consecutivo, p. ej. "SETP1"
    cufe: str
    qr: str
    codigo_seguridad_software: str


def _q(nombre: str) -> str:
    prefijo, local = nombre.split(":")
    return f"{{{NS[prefijo]}}}{local}"


def _sub(padre: etree._Element, nombre: str, texto: str | None = None, **atributos: str) -> etree._Element:
    el = etree.SubElement(padre, _q(nombre), attrib=atributos)
    if texto is not None:
        el.text = texto
    return el


def _dec2(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _dec6(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _sha384(texto: str) -> str:
    return hashlib.sha384(texto.encode("utf-8")).hexdigest()


def calcular_cufe(
    *,
    numero: str,
    fecha: str,
    hora: str,
    valor_factura: Decimal,
    valor_total: Decimal,
    nit_emisor: str,
    tipo_doc_adquirente: str,
    num_doc_adquirente: str,
    clave_tecnica: str,
    ambiente: str,
    iva: Decimal = Decimal(0),
    inc: Decimal = Decimal(0),
    ica: Decimal = Decimal(0),
) -> str:
    """CUFE = SHA-384 de la concatenación (SIN VERIFICAR contra el anexo técnico; ver el docstring del módulo):

    NumFac + FecFac + HorFac + ValFac + 01 + ValIVA + 04 + ValINC + 03 + ValICA + ValTot
    + NitOFE + TipAdq + NumAdq + ClTec + TipoAmbiente
    con los valores a 2 decimales y punto decimal.
    """
    cadena = (
        f"{numero}{fecha}{hora}{_dec2(valor_factura)}"
        f"01{_dec2(iva)}04{_dec2(inc)}03{_dec2(ica)}{_dec2(valor_total)}"
        f"{nit_emisor}{tipo_doc_adquirente}{num_doc_adquirente}{clave_tecnica}{ambiente}"
    )
    return _sha384(cadena)


def codigo_seguridad_software(software: SoftwareDian, numero: str) -> str:
    """SHA-384(SoftwareID + PIN + número de factura). SIN VERIFICAR contra el anexo técnico."""
    return _sha384(f"{software.software_id}{software.pin}{numero}")


def _validar(sol: SolicitudFactura, consecutivo: int, numeracion: Numeracion) -> None:
    validar_cliente(sol.cliente)  # lanza ClienteInvalido con TODOS los problemas
    if sol.cliente.tipo_documento not in _TIPO_DOCUMENTO_DIAN:
        raise FacturaNoSoportada(
            f"tipo de documento {sol.cliente.tipo_documento!r} todavía no soportado "
            f"(solo {sorted(_TIPO_DOCUMENTO_DIAN)}); falta verificar su código y la responsabilidad del comprador"
        )
    if sol.forma_pago not in _FORMA_PAGO_DIAN:
        raise FacturaNoSoportada(f"forma de pago {sol.forma_pago!r} todavía no soportada (solo contado)")
    if sol.medio_pago not in _MEDIO_PAGO_DIAN:
        raise FacturaNoSoportada(f"medio de pago {sol.medio_pago!r} todavía no soportado (solo efectivo)")
    if not sol.lineas:
        raise FacturaNoSoportada("la factura no tiene líneas")
    for linea in sol.lineas:
        if linea.tarifa_iva != 0:
            raise FacturaNoSoportada(
                "líneas con IVA o impuestos todavía no soportadas: el tratamiento del combustible lo define el contador"
            )
        if linea.cantidad <= 0 or linea.precio_unitario <= 0 or linea.valor_total <= 0:
            raise FacturaNoSoportada("cantidad, precio y valor de cada línea deben ser positivos")
    if not numeracion.desde <= consecutivo <= numeracion.hasta:
        raise NumeracionInvalida(
            f"el consecutivo {consecutivo} está fuera del rango autorizado "
            f"{numeracion.prefijo}{numeracion.desde}-{numeracion.hasta}"
        )
    if not numeracion.vigente_desde <= sol.fecha_emision.date() <= numeracion.vigente_hasta:
        raise NumeracionInvalida(
            f"la fecha {sol.fecha_emision.date()} está fuera de la vigencia de la resolución "
            f"({numeracion.vigente_desde} a {numeracion.vigente_hasta})"
        )


def _direccion(padre: etree._Element, nombre: str, e: Emisor) -> None:
    d = _sub(padre, nombre)
    _sub(d, "cbc:ID", e.codigo_municipio)
    _sub(d, "cbc:CityName", e.municipio)
    if e.codigo_postal:
        _sub(d, "cbc:PostalZone", e.codigo_postal)
    _sub(d, "cbc:CountrySubentity", e.departamento)
    _sub(d, "cbc:CountrySubentityCode", e.codigo_departamento)
    _sub(_sub(d, "cac:AddressLine"), "cbc:Line", e.direccion)
    pais = _sub(d, "cac:Country")
    _sub(pais, "cbc:IdentificationCode", "CO")
    _sub(pais, "cbc:Name", "Colombia", languageID="es")


def construir_factura(
    sol: SolicitudFactura,
    *,
    consecutivo: int,
    emisor: Emisor,
    numeracion: Numeracion,
    software: SoftwareDian,
) -> FacturaXml:
    _validar(sol, consecutivo, numeracion)

    numero = f"{numeracion.prefijo}{consecutivo}"
    fecha = sol.fecha_emision.strftime("%Y-%m-%d")
    hora = sol.fecha_emision.strftime("%H:%M:%S") + _ZONA_HORARIA
    total = sol.total
    dv_emisor = str(calcular_dv(emisor.nit))
    cliente = sol.cliente
    nombre_cliente = " ".join(f"{cliente.nombres} {cliente.apellidos}".split()).upper()
    doc_cliente = cliente.numero_documento.strip()
    tipo_doc = _TIPO_DOCUMENTO_DIAN[cliente.tipo_documento]

    cufe = calcular_cufe(
        numero=numero,
        fecha=fecha,
        hora=hora,
        valor_factura=total,
        valor_total=total,
        nit_emisor=emisor.nit,
        tipo_doc_adquirente=tipo_doc,
        num_doc_adquirente=doc_cliente,
        clave_tecnica=numeracion.clave_tecnica,
        ambiente=software.ambiente,
    )
    codigo_sw = codigo_seguridad_software(software, numero)
    qr = _URL_QR[software.ambiente] + cufe

    raiz = etree.Element(f"{{{NS[None]}}}Invoice", nsmap=NS)  # type: ignore[arg-type]  # lxml acepta None (espacio por defecto)
    raiz.set(f"{{{NS['xsi']}}}schemaLocation", _ESQUEMA_LOCACION)

    # ---- Extensiones: 1) datos DIAN  2) lugar de la firma (la llena el firmador) ----
    extensiones = _sub(raiz, "ext:UBLExtensions")
    dian = _sub(_sub(_sub(extensiones, "ext:UBLExtension"), "ext:ExtensionContent"), "sts:DianExtensions")
    control = _sub(dian, "sts:InvoiceControl")
    _sub(control, "sts:InvoiceAuthorization", numeracion.numero_resolucion)
    periodo = _sub(control, "sts:AuthorizationPeriod")
    _sub(periodo, "cbc:StartDate", numeracion.vigente_desde.isoformat())
    _sub(periodo, "cbc:EndDate", numeracion.vigente_hasta.isoformat())
    autorizadas = _sub(control, "sts:AuthorizedInvoices")
    _sub(autorizadas, "sts:Prefix", numeracion.prefijo)
    _sub(autorizadas, "sts:From", str(numeracion.desde))
    _sub(autorizadas, "sts:To", str(numeracion.hasta))
    _sub(
        _sub(dian, "sts:InvoiceSource"),
        "cbc:IdentificationCode",
        "CO",
        listAgencyID="6",
        listAgencyName="United Nations Economic Commission for Europe",
        listSchemeURI="urn:oasis:names:specification:ubl:codelist:gc:CountryIdentificationCode-2.1",
    )
    proveedor = _sub(dian, "sts:SoftwareProvider")
    _sub(
        proveedor,
        "sts:ProviderID",
        software.nit_proveedor,
        schemeID=str(calcular_dv(software.nit_proveedor)),
        schemeName="31",
        **_AGENCIA,
    )
    _sub(proveedor, "sts:SoftwareID", software.software_id, **_AGENCIA)
    _sub(dian, "sts:SoftwareSecurityCode", codigo_sw, **_AGENCIA)
    _sub(
        _sub(dian, "sts:AuthorizationProvider"),
        "sts:AuthorizationProviderID",
        _NIT_DIAN,
        schemeID=_DV_DIAN,
        schemeName="31",
        **_AGENCIA,
    )
    _sub(dian, "sts:QRCode", qr)
    _sub(_sub(extensiones, "ext:UBLExtension"), "ext:ExtensionContent")  # aquí va <ds:Signature>

    # ---- Encabezado ----
    _sub(raiz, "cbc:UBLVersionID", "UBL 2.1")
    _sub(raiz, "cbc:CustomizationID", "10")
    _sub(raiz, "cbc:ProfileID", "DIAN 2.1: Factura Electrónica de Venta")
    _sub(raiz, "cbc:ProfileExecutionID", software.ambiente)
    _sub(raiz, "cbc:ID", numero)
    _sub(raiz, "cbc:UUID", cufe, schemeID=software.ambiente, schemeName="CUFE-SHA384")
    _sub(raiz, "cbc:IssueDate", fecha)
    _sub(raiz, "cbc:IssueTime", hora)
    _sub(raiz, "cbc:InvoiceTypeCode", "01")
    _sub(raiz, "cbc:DocumentCurrencyCode", "COP")
    _sub(raiz, "cbc:LineCountNumeric", str(len(sol.lineas)))

    # ---- Vendedor ----
    vendedor = _sub(raiz, "cac:AccountingSupplierParty")
    _sub(vendedor, "cbc:AdditionalAccountID", "1")  # 1 = persona jurídica
    parte = _sub(vendedor, "cac:Party")
    _sub(parte, "cbc:IndustryClassificationCode", ";".join(emisor.actividades_ciiu))
    _sub(_sub(parte, "cac:PartyName"), "cbc:Name", emisor.nombre_comercial or emisor.razon_social)
    _direccion(_sub(parte, "cac:PhysicalLocation"), "cac:Address", emisor)
    fiscal = _sub(parte, "cac:PartyTaxScheme")
    _sub(fiscal, "cbc:RegistrationName", emisor.razon_social)
    _sub(fiscal, "cbc:CompanyID", emisor.nit, schemeID=dv_emisor, schemeName="31", **_AGENCIA)
    _sub(fiscal, "cbc:TaxLevelCode", emisor.responsabilidades, listName="No aplica")
    _direccion(fiscal, "cac:RegistrationAddress", emisor)
    esquema = _sub(fiscal, "cac:TaxScheme")
    _sub(esquema, "cbc:ID", "01" if emisor.responsable_iva else "ZZ")
    _sub(esquema, "cbc:Name", "IVA" if emisor.responsable_iva else "No aplica")
    legal = _sub(parte, "cac:PartyLegalEntity")
    _sub(legal, "cbc:RegistrationName", emisor.razon_social)
    _sub(legal, "cbc:CompanyID", emisor.nit, schemeID=dv_emisor, schemeName="31", **_AGENCIA)
    if emisor.matricula_mercantil:
        registro = _sub(legal, "cac:CorporateRegistrationScheme")
        _sub(registro, "cbc:ID", numeracion.prefijo)
        _sub(registro, "cbc:Name", emisor.matricula_mercantil)
    contacto = _sub(parte, "cac:Contact")
    _sub(contacto, "cbc:Telephone", emisor.telefono)
    _sub(contacto, "cbc:ElectronicMail", emisor.email)

    # ---- Comprador (persona natural con cédula) ----
    comprador = _sub(raiz, "cac:AccountingCustomerParty")
    _sub(comprador, "cbc:AdditionalAccountID", "2")  # 2 = persona natural
    parte_c = _sub(comprador, "cac:Party")
    _sub(_sub(parte_c, "cac:PartyIdentification"), "cbc:ID", doc_cliente, schemeName=tipo_doc)
    _sub(_sub(parte_c, "cac:PartyName"), "cbc:Name", nombre_cliente)
    fiscal_c = _sub(parte_c, "cac:PartyTaxScheme")
    _sub(fiscal_c, "cbc:RegistrationName", nombre_cliente)
    _sub(fiscal_c, "cbc:CompanyID", doc_cliente, schemeName=tipo_doc, **_AGENCIA)
    esquema_c = _sub(fiscal_c, "cac:TaxScheme")
    _sub(esquema_c, "cbc:ID", "ZZ")
    _sub(esquema_c, "cbc:Name", "No aplica")
    legal_c = _sub(parte_c, "cac:PartyLegalEntity")
    _sub(legal_c, "cbc:RegistrationName", nombre_cliente)
    _sub(legal_c, "cbc:CompanyID", doc_cliente, schemeName=tipo_doc, **_AGENCIA)
    _sub(_sub(parte_c, "cac:Contact"), "cbc:ElectronicMail", cliente.email.strip())

    # ---- Pago (contado, efectivo) ----
    pago = _sub(raiz, "cac:PaymentMeans")
    _sub(pago, "cbc:ID", _FORMA_PAGO_DIAN[sol.forma_pago])
    _sub(pago, "cbc:PaymentMeansCode", _MEDIO_PAGO_DIAN[sol.medio_pago])
    _sub(pago, "cbc:PaymentDueDate", fecha)

    # ---- Totales (sin impuestos, como la factura de referencia) ----
    monetario = _sub(raiz, "cac:LegalMonetaryTotal")
    _sub(monetario, "cbc:LineExtensionAmount", _dec6(total), currencyID="COP")
    _sub(monetario, "cbc:TaxExclusiveAmount", _dec6(Decimal(0)), currencyID="COP")
    _sub(monetario, "cbc:TaxInclusiveAmount", _dec6(total), currencyID="COP")
    _sub(monetario, "cbc:AllowanceTotalAmount", _dec6(Decimal(0)), currencyID="COP")
    _sub(monetario, "cbc:ChargeTotalAmount", _dec6(Decimal(0)), currencyID="COP")
    _sub(monetario, "cbc:PrepaidAmount", _dec6(Decimal(0)), currencyID="COP")
    _sub(monetario, "cbc:PayableAmount", _dec6(total), currencyID="COP")

    # ---- Líneas ----
    for i, linea in enumerate(sol.lineas, start=1):
        el = _sub(raiz, "cac:InvoiceLine")
        _sub(el, "cbc:ID", str(i))
        _sub(el, "cbc:Note", linea.descripcion)
        _sub(el, "cbc:InvoicedQuantity", _dec6(linea.cantidad), unitCode=linea.unidad)
        # El valor de la línea es lo que COBRÓ el surtidor; cantidad x precio puede diferir en pesos por redondeo.
        _sub(el, "cbc:LineExtensionAmount", _dec6(linea.valor_total), currencyID="COP")
        item = _sub(el, "cac:Item")
        _sub(item, "cbc:Description", linea.descripcion)
        _sub(_sub(item, "cac:SellersItemIdentification"), "cbc:ID", linea.codigo)
        _sub(
            _sub(item, "cac:StandardItemIdentification"),
            "cbc:ID",
            linea.codigo,
            schemeID="999",
            schemeName="Estándar de adopción del contribuyente",
        )
        precio = _sub(el, "cac:Price")
        _sub(precio, "cbc:PriceAmount", _dec6(linea.precio_unitario), currencyID="COP")
        _sub(precio, "cbc:BaseQuantity", _dec6(linea.cantidad), unitCode=linea.unidad)

    xml = etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")
    return FacturaXml(xml=xml, arbol=raiz, numero=numero, cufe=cufe, qr=qr, codigo_seguridad_software=codigo_sw)
