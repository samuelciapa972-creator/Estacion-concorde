"""Utilidad de pruebas: lista de "rutas" de elementos de un XML UBL (sin valores).

Sirve para comparar la ESTRUCTURA de lo que genera el motor contra la factura real que la
DIAN validó (tests/fixtures/rutas_referencia.txt), sin guardar datos personales de nadie.
"""

from __future__ import annotations

from lxml import etree

PREFIJOS = {
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2": "",
    "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2": "cac",
    "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2": "cbc",
    "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2": "ext",
    "dian:gov:co:facturaelectronica:Structures-2-1": "sts",
    "http://www.w3.org/2000/09/xmldsig#": "ds",
    "http://www.w3.org/2001/XMLSchema-instance": "xsi",
}


def _q(tag: str) -> str:
    if tag.startswith("{"):
        uri, local = tag[1:].split("}")
        p = PREFIJOS[uri]
        return f"{p}:{local}" if p else local
    return tag


def rutas(raiz: etree._Element, extensiones_a_omitir: tuple[int, ...] = ()) -> set[str]:
    """Rutas `a/b/c @atributos` de todos los elementos.

    `extensiones_a_omitir`: posiciones de <ext:UBLExtension> propias del proveedor (no de la DIAN).
    El contenido de <ds:Signature> se omite: lo produce el firmador, no el generador.
    """
    salida: set[str] = set()

    def rec(el: etree._Element, ruta: str) -> None:
        r = f"{ruta}/{_q(el.tag)}" if ruta else _q(el.tag)
        attrs = sorted(_q(a) for a in el.attrib)
        salida.add(r + (" @" + ",".join(attrs) if attrs else ""))
        for i, hijo in enumerate(el):
            if _q(el.tag) == "ext:UBLExtensions" and i in extensiones_a_omitir:
                continue
            if _q(hijo.tag) == "ds:Signature":
                continue
            rec(hijo, r)

    rec(raiz, "")
    return salida
