"""Representación gráfica (PDF) de la factura, armada desde el XML. Datos sintéticos.

El PDF se genera SIN compresión en las pruebas para poder buscar los textos en el contenido.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from lxml import etree

from estacion.facturacion.representacion import (
    RepresentacionInvalida,
    contenido_qr,
    generar_pdf,
    leer_xml,
    numero_en_letras,
    valor_en_letras,
)
from test_xml_factura import construir, linea, solicitud

CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"


def _pdf(xml: bytes) -> bytes:
    return generar_pdf(leer_xml(xml), validacion="SIMULADA", comprimir=False)


def test_lee_del_xml_lo_que_muestra_el_pdf():
    f = construir()
    d = leer_xml(f.xml)
    assert (d.numero, d.cufe, d.url_qr) == (f.numero, f.cufe, f.qr)
    assert (d.fecha, d.hora) == ("2026-09-29", "10:22:22-05:00")
    assert (d.nit, d.dv, d.razon_social, d.responsable_iva) == ("900123456", "8", "ESTACION DE PRUEBA S A S", True)
    assert (d.prefijo, d.rango_desde, d.rango_hasta, d.resolucion) == ("SETP", "1", "5000000", "18760000001")
    comprador = (d.comprador_tipo, d.comprador_documento, d.comprador_nombre)
    assert comprador == ("CC", "1000000001", "ANA MARÍA PÉREZ GÓMEZ")
    assert (d.forma_pago, d.medio_pago) == ("Contado", "Efectivo")
    assert d.valor_total == Decimal("29757") and d.impuestos == 0
    (ln,) = d.lineas
    assert (ln.codigo, ln.descripcion, ln.unidad, ln.cantidad, ln.precio_unitario, ln.valor_total) == (
        "18096", "GASOLINA CORRIENTE", "GLL", Decimal("1.861"), Decimal("15990"), Decimal("29757"),
    )  # fmt: skip


def test_el_qr_lleva_los_campos_de_la_factura_de_referencia():
    f = construir()
    campos = dict(r.split(": ", 1) for r in contenido_qr(leer_xml(f.xml)).splitlines()[:-1])
    assert list(campos) == [
        "NumFac", "FecFac", "HorFac", "NitFac", "DocAdq", "ValFac", "ValIva", "ValOtroIm", "ValTolFac", "CUFE",
    ]  # fmt: skip
    assert campos["NumFac"] == f.numero and campos["CUFE"] == f.cufe
    assert campos["ValFac"] == campos["ValTolFac"] == "29757.000000"  # como el QR impreso de la referencia
    assert contenido_qr(leer_xml(f.xml)).splitlines()[-1] == f.qr


def test_pdf_muestra_numero_valores_en_formato_colombiano_y_valor_en_letras():
    pdf = _pdf(construir().xml)
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    for texto in (b"SETP215802", b"NIT 900123456-8", b"1,861", b"15.990,00", b"$ 29.757,00", b"GASOLINA CORRIENTE",
                  b"VEINTINUEVE MIL SETECIENTOS CINCUENTA Y SIETE PESOS", b"CENTAVOS", b"SIMULADA",
                  b"DESDE EL No. SETP-1 HASTA EL No. SETP-5000000"):  # fmt: skip
        assert texto in pdf, texto


def test_fuera_de_produccion_siempre_lleva_la_marca_sin_validez():
    f = construir()
    assert b"SIN VALIDEZ FISCAL" in _pdf(f.xml)
    produccion = f.xml.replace(b"<cbc:ProfileExecutionID>2<", b"<cbc:ProfileExecutionID>1<")
    assert b"SIN VALIDEZ FISCAL" not in _pdf(produccion)


def test_pdf_reproducible():
    f = construir()
    assert generar_pdf(leer_xml(f.xml), validacion="X") == generar_pdf(leer_xml(f.xml), validacion="X")


def test_varias_lineas():
    f = construir(solicitud([linea(), linea("2.000", "16025", "32050", "18097", "DIESEL")]))
    d = leer_xml(f.xml)
    assert [ln.item for ln in d.lineas] == ["1", "2"] and d.valor_total == Decimal("61807")
    assert b"$ 61.807,00" in _pdf(f.xml)


def _sin(xml: bytes, xpath: str) -> bytes:
    raiz = etree.fromstring(xml)
    (el,) = raiz.xpath(xpath, namespaces={"cac": CAC, "cbc": CBC})
    el.getparent().remove(el)
    return etree.tostring(raiz)


def _con_texto(xml: bytes, xpath: str, valor: str) -> bytes:
    raiz = etree.fromstring(xml)
    (el,) = raiz.xpath(xpath, namespaces={"cac": CAC, "cbc": CBC})
    el.text = valor
    return etree.tostring(raiz)


@pytest.mark.parametrize(
    ("alterar", "mensaje"),
    [
        (lambda x: _sin(x, "//cbc:UUID"), "cbc:UUID"),
        (lambda x: _sin(x, "//cac:InvoiceLine"), "no tiene líneas"),
        (lambda x: _con_texto(x, "//cac:PaymentMeans/cbc:PaymentMeansCode", "48"), "medio de pago"),
        (lambda x: _con_texto(x, "//cac:PaymentMeans/cbc:ID", "2"), "forma de pago"),
        (lambda x: x[:200], "ilegible"),
    ],
)
def test_xml_incompleto_o_con_codigos_desconocidos_falla_cerrado(alterar, mensaje):
    with pytest.raises(RepresentacionInvalida, match=mensaje):
        leer_xml(alterar(construir().xml))


def test_xml_con_impuestos_no_se_representa_a_medias():
    raiz = etree.fromstring(construir().xml)
    raiz.append(etree.Element(f"{{{CAC}}}TaxTotal"))
    with pytest.raises(RepresentacionInvalida, match="impuestos"):
        leer_xml(etree.tostring(raiz))


def test_no_resuelve_entidades_externas():
    malicioso = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    with pytest.raises(RepresentacionInvalida):
        leer_xml(malicioso)


@pytest.mark.parametrize(
    ("n", "letras"),
    [
        (1, "UN"), (15, "QUINCE"), (16, "DIECISÉIS"), (21, "VEINTIÚN"), (22, "VEINTIDÓS"), (30, "TREINTA"),
        (31, "TREINTA Y UN"), (100, "CIEN"), (101, "CIENTO UN"), (500, "QUINIENTOS"),
        (999, "NOVECIENTOS NOVENTA Y NUEVE"),
        (1000, "MIL"), (1001, "MIL UN"), (21_000, "VEINTIÚN MIL"), (100_000, "CIEN MIL"), (1_000_000, "UN MILLÓN"),
        (1_500_000, "UN MILLÓN QUINIENTOS MIL"), (21_000_000, "VEINTIÚN MILLONES"),
        (1_000_000_000, "MIL MILLONES"),
        (999_999_999_999, "NOVECIENTOS NOVENTA Y NUEVE MIL NOVECIENTOS NOVENTA Y NUEVE MILLONES "
                          "NOVECIENTOS NOVENTA Y NUEVE MIL NOVECIENTOS NOVENTA Y NUEVE"),
    ],
)  # fmt: skip
def test_numero_en_letras(n, letras):
    assert numero_en_letras(n) == letras


def test_valor_en_letras_con_centavos_y_millones_exactos():
    assert valor_en_letras(Decimal("1")) == "UN PESO CERO CENTAVOS"
    assert valor_en_letras(Decimal("2000000.05")) == "DOS MILLONES DE PESOS CINCO CENTAVOS"
    assert valor_en_letras(Decimal("2000001")) == "DOS MILLONES UN PESOS CERO CENTAVOS"
    with pytest.raises(ValueError):
        numero_en_letras(10**12)
    assert not re.search(r"\s{2}", valor_en_letras(Decimal("1100100.10")))


def test_razon_social_larga_se_parte_en_renglones_sin_salirse():
    from dataclasses import replace

    from estacion.facturacion.xml_factura import construir_factura
    from test_xml_factura import EMISOR, NUMERACION, SOFTWARE

    larga = "SOCIEDAD DE PRUEBA CON UNA RAZON SOCIAL MUY LARGA PARA MANTENIMIENTO Y ALISTAMIENTO S.A.S"
    emisor = replace(EMISOR, razon_social=larga, nombre_comercial="EDS DE PRUEBA")
    f = construir_factura(solicitud(), consecutivo=1, emisor=emisor, numeracion=NUMERACION, software=SOFTWARE)
    pdf = _pdf(f.xml)
    # Texto dibujado = "(…) Tj". En un solo renglón se montaría sobre el recuadro del número (en los metadatos sí va).
    assert b"(" + larga.encode() + b") Tj" not in pdf
    assert b"SOCIEDAD DE PRUEBA" in pdf and b"EDS DE PRUEBA" in pdf
