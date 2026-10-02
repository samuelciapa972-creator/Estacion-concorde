"""Representación gráfica (PDF) de la factura electrónica, armada SOLO a partir de su XML.

Por qué del XML y no de la base: el PDF no puede decir algo distinto del documento electrónico. Si el XML tiene un
código que aquí no se conoce, se lanza `RepresentacionInvalida` en vez de rotularlo mal (fallar cerrado).

MODELO: la representación gráfica de la factura real de gasolina de referencia (Masser, validada por la DIAN; ver
CLAUDE.md): encabezado con la resolución de numeración, comprador, forma y medio de pago, ítems, totales, valor en
letras, CUFE, QR y fecha de validación.

SIN VERIFICAR contra el anexo técnico:
    · El contenido del QR (`contenido_qr`) copia el formato del QR IMPRESO en la factura de referencia
      (NumFac, FecFac, HorFac, NitFac, DocAdq, ValFac, ValIva, ValOtroIm, ValTolFac, CUFE y la URL). El nodo
      `sts:QRCode` del XML lleva hoy solo la URL; cuál de los dos contenidos exige el anexo está por confirmar.
    · Los textos legales obligatorios de la representación gráfica (si los hay además de los de la referencia).
Fuera del ambiente de producción ("1") el PDF lleva SIEMPRE la marca "SIN VALIDEZ FISCAL".
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from .xml_factura import _FORMA_PAGO_DIAN, _MEDIO_PAGO_DIAN, _TIPO_DOCUMENTO_DIAN, NS

_NS = {k: v for k, v in NS.items() if k is not None}
_TIPO_DOC = {v: k for k, v in _TIPO_DOCUMENTO_DIAN.items()}
_FORMA_PAGO = {v: k.capitalize() for k, v in _FORMA_PAGO_DIAN.items()}
_MEDIO_PAGO = {v: k.capitalize() for k, v in _MEDIO_PAGO_DIAN.items()}


class RepresentacionInvalida(ValueError):
    """El XML no tiene lo que la representación gráfica necesita, o trae un código desconocido."""


@dataclass(frozen=True, slots=True)
class LineaRepresentacion:
    item: str
    codigo: str
    descripcion: str
    unidad: str
    cantidad: Decimal
    precio_unitario: Decimal
    valor_total: Decimal


@dataclass(frozen=True, slots=True)
class DatosRepresentacion:
    ambiente: str
    numero: str
    fecha: str  # AAAA-MM-DD
    hora: str  # HH:MM:SS-05:00
    cufe: str
    url_qr: str
    # Emisor
    razon_social: str
    nombre_comercial: str
    nit: str
    dv: str
    responsable_iva: bool
    direccion: str
    municipio: str
    departamento: str
    telefono: str
    email: str
    nit_proveedor_software: str
    # Numeración
    resolucion: str
    vigente_desde: str
    vigente_hasta: str
    prefijo: str
    rango_desde: str
    rango_hasta: str
    # Comprador
    comprador_nombre: str
    comprador_tipo: str
    comprador_documento: str
    # Pago y totales (los textos tal cual del XML, para el QR; los Decimal para mostrar)
    forma_pago: str
    medio_pago: str
    vencimiento: str
    valor_bruto_xml: str
    valor_total_xml: str
    valor_bruto: Decimal
    base_imponible: Decimal
    impuestos: Decimal
    descuentos: Decimal
    valor_total: Decimal
    lineas: tuple[LineaRepresentacion, ...]


def _texto(raiz: etree._Element, ruta: str) -> str:
    el = raiz.find(ruta, _NS)
    if el is None or el.text is None or not el.text.strip():
        raise RepresentacionInvalida(f"al XML le falta {ruta}")
    return el.text.strip()


def _atributo(raiz: etree._Element, ruta: str, atributo: str) -> str:
    el = raiz.find(ruta, _NS)
    valor = None if el is None else el.get(atributo)
    if not valor:
        raise RepresentacionInvalida(f"al XML le falta el atributo {atributo} de {ruta}")
    return valor


def _codigo(tabla: dict[str, str], valor: str, que: str) -> str:
    if valor not in tabla:
        raise RepresentacionInvalida(f"{que} con código {valor!r} desconocido: no se rotula a ciegas")
    return tabla[valor]


def leer_xml(xml: bytes) -> DatosRepresentacion:
    """Extrae del XML de la factura todo lo que muestra el PDF."""
    try:
        raiz = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise RepresentacionInvalida(f"XML ilegible: {e}") from None
    dian = "ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/sts:DianExtensions"
    sup = "cac:AccountingSupplierParty/cac:Party"
    cus = "cac:AccountingCustomerParty/cac:Party"
    tot = "cac:LegalMonetaryTotal"

    lineas = []
    for el in raiz.findall("cac:InvoiceLine", _NS):
        lineas.append(
            LineaRepresentacion(
                item=_texto(el, "cbc:ID"),
                codigo=_texto(el, "cac:Item/cac:SellersItemIdentification/cbc:ID"),
                descripcion=_texto(el, "cac:Item/cbc:Description"),
                unidad=_atributo(el, "cbc:InvoicedQuantity", "unitCode"),
                cantidad=Decimal(_texto(el, "cbc:InvoicedQuantity")),
                precio_unitario=Decimal(_texto(el, "cac:Price/cbc:PriceAmount")),
                valor_total=Decimal(_texto(el, "cbc:LineExtensionAmount")),
            )
        )
    if not lineas:
        raise RepresentacionInvalida("la factura no tiene líneas")

    # El generador actual no emite impuestos; si un XML los trae, este PDF todavía no sabe mostrarlos.
    if raiz.find("cac:TaxTotal", _NS) is not None or raiz.find("cac:InvoiceLine/cac:TaxTotal", _NS) is not None:
        raise RepresentacionInvalida("factura con impuestos: la representación todavía no los muestra")

    bruto_xml = _texto(raiz, f"{tot}/cbc:LineExtensionAmount")
    total_xml = _texto(raiz, f"{tot}/cbc:PayableAmount")
    return DatosRepresentacion(
        ambiente=_texto(raiz, "cbc:ProfileExecutionID"),
        numero=_texto(raiz, "cbc:ID"),
        fecha=_texto(raiz, "cbc:IssueDate"),
        hora=_texto(raiz, "cbc:IssueTime"),
        cufe=_texto(raiz, "cbc:UUID"),
        url_qr=_texto(raiz, f"{dian}/sts:QRCode"),
        razon_social=_texto(raiz, f"{sup}/cac:PartyTaxScheme/cbc:RegistrationName"),
        nombre_comercial=_texto(raiz, f"{sup}/cac:PartyName/cbc:Name"),
        nit=_texto(raiz, f"{sup}/cac:PartyTaxScheme/cbc:CompanyID"),
        dv=_atributo(raiz, f"{sup}/cac:PartyTaxScheme/cbc:CompanyID", "schemeID"),
        responsable_iva=_texto(raiz, f"{sup}/cac:PartyTaxScheme/cac:TaxScheme/cbc:ID") == "01",
        direccion=_texto(raiz, f"{sup}/cac:PhysicalLocation/cac:Address/cac:AddressLine/cbc:Line"),
        municipio=_texto(raiz, f"{sup}/cac:PhysicalLocation/cac:Address/cbc:CityName"),
        departamento=_texto(raiz, f"{sup}/cac:PhysicalLocation/cac:Address/cbc:CountrySubentity"),
        telefono=_texto(raiz, f"{sup}/cac:Contact/cbc:Telephone"),
        email=_texto(raiz, f"{sup}/cac:Contact/cbc:ElectronicMail"),
        nit_proveedor_software=_texto(raiz, f"{dian}/sts:SoftwareProvider/sts:ProviderID"),
        resolucion=_texto(raiz, f"{dian}/sts:InvoiceControl/sts:InvoiceAuthorization"),
        vigente_desde=_texto(raiz, f"{dian}/sts:InvoiceControl/sts:AuthorizationPeriod/cbc:StartDate"),
        vigente_hasta=_texto(raiz, f"{dian}/sts:InvoiceControl/sts:AuthorizationPeriod/cbc:EndDate"),
        prefijo=_texto(raiz, f"{dian}/sts:InvoiceControl/sts:AuthorizedInvoices/sts:Prefix"),
        rango_desde=_texto(raiz, f"{dian}/sts:InvoiceControl/sts:AuthorizedInvoices/sts:From"),
        rango_hasta=_texto(raiz, f"{dian}/sts:InvoiceControl/sts:AuthorizedInvoices/sts:To"),
        comprador_nombre=_texto(raiz, f"{cus}/cac:PartyTaxScheme/cbc:RegistrationName"),
        comprador_tipo=_codigo(
            _TIPO_DOC, _atributo(raiz, f"{cus}/cac:PartyIdentification/cbc:ID", "schemeName"), "tipo de documento"
        ),
        comprador_documento=_texto(raiz, f"{cus}/cac:PartyIdentification/cbc:ID"),
        forma_pago=_codigo(_FORMA_PAGO, _texto(raiz, "cac:PaymentMeans/cbc:ID"), "forma de pago"),
        medio_pago=_codigo(_MEDIO_PAGO, _texto(raiz, "cac:PaymentMeans/cbc:PaymentMeansCode"), "medio de pago"),
        vencimiento=_texto(raiz, "cac:PaymentMeans/cbc:PaymentDueDate"),
        valor_bruto_xml=bruto_xml,
        valor_total_xml=total_xml,
        valor_bruto=Decimal(bruto_xml),
        base_imponible=Decimal(_texto(raiz, f"{tot}/cbc:TaxExclusiveAmount")),
        impuestos=Decimal(0),
        descuentos=Decimal(_texto(raiz, f"{tot}/cbc:AllowanceTotalAmount")),
        valor_total=Decimal(total_xml),
        lineas=tuple(lineas),
    )


def contenido_qr(d: DatosRepresentacion) -> str:
    """Texto del QR impreso, con el formato del QR de la factura de referencia (SIN VERIFICAR contra el anexo).

    >>> print(contenido_qr(_EJEMPLO).replace(_EJEMPLO.cufe, "<CUFE>"))
    NumFac: PRUE1
    FecFac: 2026-10-01
    HorFac: 20:13:14-05:00
    NitFac: 900123456
    DocAdq: 1000000017
    ValFac: 29757.000000
    ValIva: 0.00
    ValOtroIm: 0.00
    ValTolFac: 29757.000000
    CUFE: <CUFE>
    https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey=<CUFE>
    """
    return "\n".join(
        [
            f"NumFac: {d.numero}",
            f"FecFac: {d.fecha}",
            f"HorFac: {d.hora}",
            f"NitFac: {d.nit}",
            f"DocAdq: {d.comprador_documento}",
            f"ValFac: {d.valor_bruto_xml}",
            f"ValIva: {_dec2(d.impuestos)}",
            f"ValOtroIm: {_dec2(Decimal(0))}",
            f"ValTolFac: {d.valor_total_xml}",
            f"CUFE: {d.cufe}",
            d.url_qr,
        ]
    )


# ------------------------------------------------------------------------------------------ números
_UNIDADES = ("", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE", "DIEZ", "ONCE", "DOCE",
             "TRECE", "CATORCE", "QUINCE", "DIECISÉIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE", "VEINTE",
             "VEINTIÚN", "VEINTIDÓS", "VEINTITRÉS", "VEINTICUATRO", "VEINTICINCO", "VEINTISÉIS", "VEINTISIETE",
             "VEINTIOCHO", "VEINTINUEVE")  # fmt: skip
_DECENAS = ("", "", "", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA")
_CENTENAS = ("", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS",
             "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS")  # fmt: skip


def _hasta_999(n: int) -> str:
    if n == 100:
        return "CIEN"
    c, resto = divmod(n, 100)
    partes = [_CENTENAS[c]] if c else []
    if resto < 30:
        partes.append(_UNIDADES[resto])
    else:
        d, u = divmod(resto, 10)
        partes.append(_DECENAS[d] + (f" Y {_UNIDADES[u]}" if u else ""))
    return " ".join(p for p in partes if p)


def numero_en_letras(n: int) -> str:
    """Entero en letras (español, apócope "UN" como en "UN MIL… PESOS").

    >>> numero_en_letras(29757)
    'VEINTINUEVE MIL SETECIENTOS CINCUENTA Y SIETE'
    >>> numero_en_letras(1_000_000), numero_en_letras(2_021_001), numero_en_letras(0)
    ('UN MILLÓN', 'DOS MILLONES VEINTIÚN MIL UN', 'CERO')
    """
    if n < 0 or n >= 10**12:
        raise ValueError(f"fuera de rango: {n}")
    if n == 0:
        return "CERO"
    millones, resto = divmod(n, 10**6)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones:
        partes.append("UN MILLÓN" if millones == 1 else f"{_miles(millones)} MILLONES")
    if miles:
        partes.append("MIL" if miles == 1 else f"{_hasta_999(miles)} MIL")
    if unidades:
        partes.append(_hasta_999(unidades))
    return " ".join(partes)


def _miles(n: int) -> str:
    """Hasta 999.999 (para contar millones)."""
    miles, unidades = divmod(n, 1000)
    partes = []
    if miles:
        partes.append("MIL" if miles == 1 else f"{_hasta_999(miles)} MIL")
    if unidades:
        partes.append(_hasta_999(unidades))
    return " ".join(partes)


def valor_en_letras(valor: Decimal) -> str:
    """Pesos y centavos en letras, como en la factura de referencia.

    >>> valor_en_letras(Decimal("29757.00"))
    'VEINTINUEVE MIL SETECIENTOS CINCUENTA Y SIETE PESOS CERO CENTAVOS'
    >>> valor_en_letras(Decimal("3000000")), valor_en_letras(Decimal("1.50"))
    ('TRES MILLONES DE PESOS CERO CENTAVOS', 'UN PESO CINCUENTA CENTAVOS')
    """
    valor = valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pesos, centavos = divmod(int(valor * 100), 100)
    texto = numero_en_letras(pesos)
    unidad = "PESO" if pesos == 1 else "PESOS"
    # "UN MILLÓN DE PESOS", "TRES MILLONES DE PESOS": la preposición va cuando el número termina en millón(es).
    de = " DE" if pesos and pesos % 10**6 == 0 else ""
    return f"{texto}{de} {unidad} {numero_en_letras(centavos)} CENTAVOS"


def _dec2(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def numero_es_co(x: Decimal, decimales: int) -> str:
    """Formato colombiano: punto de miles y coma decimal. `numero_es_co(Decimal("29757"), 2)` -> "29.757,00"."""
    texto = f"{x.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP):,.{decimales}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def fecha_co(iso: str) -> str:
    a, m, d = iso[:10].split("-")
    return f"{d}/{m}/{a}"


# ------------------------------------------------------------------------------------------ PDF
_ANCHO, _ALTO = letter
_MARGEN = 15 * mm


def generar_pdf(d: DatosRepresentacion, *, validacion: str, comprimir: bool = True) -> bytes:
    """PDF de una página (carta). `validacion`: texto de la validación de la DIAN (fecha, o "SIMULADA…")."""
    salida = io.BytesIO()
    c = Canvas(salida, pagesize=letter, pageCompression=1 if comprimir else 0, invariant=1)
    c.setTitle(f"Factura electrónica de venta {d.numero}")
    c.setAuthor(d.razon_social)
    c.setCreator("estacion (software propio)")

    if d.ambiente != "1":
        _marca_sin_validez(c)
    y = _encabezado(c, d)
    y = _comprador(c, d, y - 4 * mm)
    y = _items(c, d, y - 4 * mm)
    y = _totales(c, d, y - 4 * mm)
    _pie(c, d, y - 4 * mm, validacion)
    c.showPage()
    c.save()
    return salida.getvalue()


def _marca_sin_validez(c: Canvas) -> None:
    c.saveState()
    c.setFillGray(0.85)
    c.setFont("Helvetica-Bold", 38)
    c.translate(_ANCHO / 2, _ALTO / 2)
    c.rotate(35)
    c.drawCentredString(0, 20, "SIN VALIDEZ FISCAL")
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(0, -20, "AMBIENTE DE PRUEBAS")
    c.restoreState()


def _caja(c: Canvas, x: float, y: float, ancho: float, alto: float) -> None:
    c.rect(x, y - alto, ancho, alto)


def _encabezado(c: Canvas, d: DatosRepresentacion) -> float:
    arriba = _ALTO - _MARGEN
    # Recuadro del número, a la izquierda (como la referencia)
    ancho_num = 62 * mm
    _caja(c, _MARGEN, arriba, ancho_num, 30 * mm)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_MARGEN + 2 * mm, arriba - 5 * mm, "FACTURA ELECTRÓNICA DE VENTA N°")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(_MARGEN + 2 * mm, arriba - 11 * mm, d.numero)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_MARGEN + 2 * mm, arriba - 17 * mm, "FECHA Y HORA DE EMISIÓN")
    c.setFont("Helvetica", 9)
    c.drawString(_MARGEN + 2 * mm, arriba - 21 * mm, f"{fecha_co(d.fecha)} {d.hora[:8]}")
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_MARGEN + 2 * mm, arriba - 25.5 * mm, "FECHA DE VENCIMIENTO")
    c.setFont("Helvetica", 9)
    c.drawString(_MARGEN + 38 * mm, arriba - 25.5 * mm, fecha_co(d.vencimiento))

    # Emisor, al centro-derecha
    x = _MARGEN + ancho_num + 6 * mm
    ancho = _ANCHO - _MARGEN - x
    centro = x + ancho / 2
    # La razón social puede ser larga: se parte en renglones dentro de su espacio, nunca encima del número.
    tamano = 12.0
    lineas_rs = simpleSplit(d.razon_social, "Helvetica-Bold", tamano, ancho)
    while len(lineas_rs) > 2 and tamano > 8:
        tamano -= 0.5
        lineas_rs = simpleSplit(d.razon_social, "Helvetica-Bold", tamano, ancho)
    c.setFont("Helvetica-Bold", tamano)
    yy = arriba - 4 * mm
    for r in lineas_rs:
        c.drawCentredString(centro, yy, r)
        yy -= tamano * 0.42 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(centro, yy - 0.5 * mm, f"NIT {d.nit}-{d.dv}")
    yy -= 4.5 * mm
    if d.nombre_comercial != d.razon_social:
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(centro, yy, d.nombre_comercial)
        yy -= 3.8 * mm
    renglones = [
        "RESPONSABLE DE IVA" if d.responsable_iva else "NO RESPONSABLE DE IVA",
        f"AUTORIZACIÓN DE NUMERACIÓN DE FACTURACIÓN DIAN No. {d.resolucion}",
        f"VIGENCIA DEL {fecha_co(d.vigente_desde)} AL {fecha_co(d.vigente_hasta)}",
        f"DESDE EL No. {d.prefijo}-{d.rango_desde} HASTA EL No. {d.prefijo}-{d.rango_hasta}",
        f"{d.direccion} · {d.municipio} - {d.departamento}",
        f"Tel: {d.telefono} · {d.email}",
    ]
    c.setFont("Helvetica", 7.5)
    for r in renglones:
        c.drawCentredString(centro, yy, r)
        yy -= 3.4 * mm
    return min(arriba - 30 * mm, yy)


def _etiqueta_valor(c: Canvas, x: float, y: float, etiqueta: str, valor: str, ancho_etiqueta: float) -> None:
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x, y, etiqueta)
    c.setFont("Helvetica", 8.5)
    c.drawString(x + ancho_etiqueta, y, valor)


def _comprador(c: Canvas, d: DatosRepresentacion, arriba: float) -> float:
    ancho = _ANCHO - 2 * _MARGEN
    alto = 18 * mm
    _caja(c, _MARGEN, arriba, ancho, alto)
    c.setFillGray(0.9)
    c.rect(_MARGEN, arriba - 5 * mm, ancho, 5 * mm, fill=1, stroke=1)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(_MARGEN + 2 * mm, arriba - 3.6 * mm, "VENDIDO A:")
    x = _MARGEN + 2 * mm
    _etiqueta_valor(c, x, arriba - 9.5 * mm, "NOMBRE", d.comprador_nombre, 18 * mm)
    _etiqueta_valor(c, x, arriba - 14.5 * mm, "IDENTIFICACIÓN", f"{d.comprador_tipo} {d.comprador_documento}", 26 * mm)
    x2 = _MARGEN + ancho * 0.62
    _etiqueta_valor(c, x2, arriba - 9.5 * mm, "FORMA DE PAGO", d.forma_pago, 27 * mm)
    _etiqueta_valor(c, x2, arriba - 14.5 * mm, "MEDIO DE PAGO", d.medio_pago, 27 * mm)
    return arriba - alto


# (título, ancho relativo, alineación)
_COLUMNAS = (
    ("ÍTEM", 0.06, "c"),
    ("CÓDIGO", 0.13, "c"),
    ("DESCRIPCIÓN", 0.28, "i"),
    ("UNIDAD", 0.07, "c"),
    ("CANTIDAD", 0.11, "d"),
    ("PRECIO UNITARIO", 0.14, "d"),
    ("IVA", 0.07, "d"),
    ("VALOR TOTAL", 0.14, "d"),
)


def _celda(c: Canvas, x: float, ancho: float, y: float, texto: str, alineacion: str) -> None:
    disponible = ancho - 3 * mm
    while len(texto) > 1 and c.stringWidth(texto) > disponible:
        texto = texto[:-2] + "…"
    if alineacion == "c":
        c.drawCentredString(x + ancho / 2, y, texto)
    elif alineacion == "d":
        c.drawRightString(x + ancho - 1.5 * mm, y, texto)
    else:
        c.drawString(x + 1.5 * mm, y, texto)


def _items(c: Canvas, d: DatosRepresentacion, arriba: float) -> float:
    ancho_total = _ANCHO - 2 * _MARGEN
    anchos = [ancho_total * p for _, p, _ in _COLUMNAS]
    xs = [_MARGEN + sum(anchos[:i]) for i in range(len(anchos))]
    alto_titulo = 6 * mm
    c.setFillGray(0.9)
    c.rect(_MARGEN, arriba - alto_titulo, ancho_total, alto_titulo, fill=1, stroke=1)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 7)
    for (titulo, _, _), x, ancho in zip(_COLUMNAS, xs, anchos, strict=True):
        c.drawCentredString(x + ancho / 2, arriba - 4 * mm, titulo)
    y = arriba - alto_titulo
    c.setFont("Helvetica", 8.5)
    for linea in d.lineas:
        y -= 5 * mm
        valores = (
            linea.item,
            linea.codigo,
            linea.descripcion,
            linea.unidad,
            numero_es_co(linea.cantidad, 3),
            numero_es_co(linea.precio_unitario, 2),
            "$ 0,00",
            numero_es_co(linea.valor_total, 2),
        )
        for (_, _, alineacion), x, ancho, valor in zip(_COLUMNAS, xs, anchos, valores, strict=True):
            _celda(c, x, ancho, y + 1.2 * mm, valor, alineacion)
    alto_cuerpo = max(arriba - alto_titulo - y, 40 * mm)
    fondo = arriba - alto_titulo - alto_cuerpo
    c.rect(_MARGEN, fondo, ancho_total, alto_titulo + alto_cuerpo)
    for x in xs[1:]:
        c.line(x, arriba, x, fondo)
    return fondo


def _totales(c: Canvas, d: DatosRepresentacion, arriba: float) -> float:
    ancho_total = _ANCHO - 2 * _MARGEN
    ancho_tot = ancho_total * 0.38
    x_tot = _ANCHO - _MARGEN - ancho_tot
    filas = (
        ("Total valor bruto", d.valor_bruto),
        ("Total base imponible", d.base_imponible),
        ("Total impuestos", d.impuestos),
        ("Total descuentos", d.descuentos),
        ("TOTAL A PAGAR", d.valor_total),
    )
    alto_fila = 5.5 * mm
    for i, (titulo, valor) in enumerate(filas):
        y = arriba - (i + 1) * alto_fila
        c.rect(x_tot, y, ancho_tot, alto_fila)
        c.setFont("Helvetica-Bold", 9 if i == len(filas) - 1 else 8.5)
        c.drawString(x_tot + 2 * mm, y + 1.7 * mm, titulo)
        c.drawRightString(_ANCHO - _MARGEN - 2 * mm, y + 1.7 * mm, f"$ {numero_es_co(valor, 2)}")
    fondo = arriba - len(filas) * alto_fila
    # Valor en letras, a la izquierda
    ancho_izq = ancho_total - ancho_tot - 3 * mm
    c.rect(_MARGEN, fondo, ancho_izq, len(filas) * alto_fila)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_MARGEN + 2 * mm, arriba - 4.5 * mm, "VALOR FACTURA EN LETRAS:")
    c.setFont("Helvetica", 8.5)
    yy = arriba - 9 * mm
    for r in simpleSplit(valor_en_letras(d.valor_total), "Helvetica", 8.5, ancho_izq - 4 * mm):
        c.drawString(_MARGEN + 2 * mm, yy, r)
        yy -= 4 * mm
    return fondo


def _pie(c: Canvas, d: DatosRepresentacion, arriba: float, validacion: str) -> None:
    lado_qr = 32 * mm
    widget = QrCodeWidget(contenido_qr(d), barLevel="M")
    x0, y0, x1, y1 = widget.getBounds()
    dibujo = Drawing(lado_qr, lado_qr, transform=[lado_qr / (x1 - x0), 0, 0, lado_qr / (y1 - y0), 0, 0])
    dibujo.add(widget)
    renderPDF.draw(dibujo, c, _ANCHO - _MARGEN - lado_qr, arriba - lado_qr)

    ancho_texto = _ANCHO - 2 * _MARGEN - lado_qr - 5 * mm
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_MARGEN, arriba - 4 * mm, "CUFE:")
    c.setFont("Courier", 7.5)
    yy = arriba - 8 * mm
    mitad = len(d.cufe) // 2
    for r in (d.cufe[:mitad], d.cufe[mitad:]):
        c.drawString(_MARGEN, yy, r)
        yy -= 3.5 * mm
    c.setFont("Helvetica-Bold", 8)
    c.drawString(_MARGEN, yy - 2 * mm, "VALIDACIÓN DIAN:")
    c.setFont("Helvetica", 8.5)
    c.drawString(_MARGEN + 30 * mm, yy - 2 * mm, validacion)
    yy -= 8 * mm
    c.setFont("Helvetica", 7)
    for r in simpleSplit(
        "Representación gráfica de la factura electrónica de venta. Consulte su validez en el catálogo de la DIAN "
        "con el CUFE o escaneando el código QR.",
        "Helvetica",
        7,
        ancho_texto,
    ):
        c.drawString(_MARGEN, yy, r)
        yy -= 3.2 * mm
    c.setFont("Helvetica", 7)
    c.drawString(
        _MARGEN,
        _MARGEN - 5 * mm,
        f"Documento electrónico generado con software propio del facturador (NIT {d.nit_proveedor_software}).",
    )
    c.drawRightString(_ANCHO - _MARGEN, _MARGEN - 5 * mm, "Página 1 de 1")


# Datos de ejemplo para los doctests (SINTÉTICOS).
_EJEMPLO = DatosRepresentacion(
    ambiente="2", numero="PRUE1", fecha="2026-10-01", hora="20:13:14-05:00", cufe="a" * 96,
    url_qr="https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey=" + "a" * 96,
    razon_social="EMPRESA FICTICIA DE PRUEBA S A S", nombre_comercial="BANCO DE PRUEBAS", nit="900123456", dv="8",
    responsable_iva=False, direccion="DIRECCION FICTICIA 1-2", municipio="TUNJA", departamento="BOYACÁ",
    telefono="3000000000", email="ficticia@example.com", nit_proveedor_software="900123456", resolucion="0",
    vigente_desde="2026-01-01", vigente_hasta="2026-12-31", prefijo="PRUE", rango_desde="1", rango_hasta="1000",
    comprador_nombre="CLIENTE FICTICIO PRUEBA", comprador_tipo="CC", comprador_documento="1000000017",
    forma_pago="Contado", medio_pago="Efectivo", vencimiento="2026-10-01", valor_bruto_xml="29757.000000",
    valor_total_xml="29757.000000", valor_bruto=Decimal("29757"), base_imponible=Decimal(0), impuestos=Decimal(0),
    descuentos=Decimal(0), valor_total=Decimal("29757"),
    lineas=(LineaRepresentacion("1", "DIESEL", "DIESEL", "GLL", Decimal("1.861"), Decimal("15990"), Decimal("29757")),),
)  # fmt: skip
