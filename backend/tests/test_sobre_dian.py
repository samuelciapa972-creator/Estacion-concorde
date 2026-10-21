"""Mensajes SOAP firmados con WS-Security para el web service de la DIAN. Sin red."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from lxml import etree

from estacion.facturacion.dian.sobre import (
    NS_SOAP,
    NS_WCF,
    NS_WSA,
    NS_WSSE,
    NS_WSU,
    URL_HABILITACION,
    URL_PRODUCCION,
    SobreInvalido,
    accion,
    sobre,
    url_servicio,
    verificar_sobre,
)

AHORA = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)


def _armar(certificado):
    return sobre("GetStatus", [("trackId", "abc123")], destino=URL_HABILITACION, certificado=certificado, ahora=AHORA)


def test_estructura_del_sobre(certificado):
    raiz = etree.fromstring(_armar(certificado))
    assert raiz.tag == f"{{{NS_SOAP}}}Envelope"
    assert raiz.findtext(f".//{{{NS_WSA}}}Action") == accion("GetStatus")
    assert accion("GetStatus") == "http://wcf.dian.colombia/IWcfDianCustomerServices/GetStatus"
    to = raiz.find(f".//{{{NS_WSA}}}To")
    assert to.text == URL_HABILITACION and to.get(f"{{{NS_WSU}}}Id")
    seg = raiz.find(f".//{{{NS_WSSE}}}Security")
    assert seg.get(f"{{{NS_SOAP}}}mustUnderstand") == "true"
    assert seg.findtext(f"{{{NS_WSU}}}Timestamp/{{{NS_WSU}}}Created") == "2026-10-13T15:00:00.000Z"
    assert seg.findtext(f"{{{NS_WSU}}}Timestamp/{{{NS_WSU}}}Expires") == "2026-10-13T15:01:00.000Z"
    assert seg.find(f"{{{NS_WSSE}}}BinarySecurityToken").text
    assert raiz.findtext(f".//{{{NS_WCF}}}GetStatus/{{{NS_WCF}}}trackId") == "abc123"


def test_la_firma_verifica(certificado):
    verificar_sobre(_armar(certificado), ahora=AHORA + timedelta(seconds=30))


def test_cambiar_el_destino_rompe_la_firma(certificado):
    datos = _armar(certificado).replace(URL_HABILITACION.encode(), b"https://otro.example/x")
    with pytest.raises(SobreInvalido, match="digest"):
        verificar_sobre(datos, ahora=AHORA)


def test_cambiar_la_firma_la_invalida(certificado):
    raiz = etree.fromstring(_armar(certificado))
    valor = raiz.find(".//{http://www.w3.org/2000/09/xmldsig#}SignatureValue")
    valor.text = ("B" if valor.text[0] == "A" else "A") + valor.text[1:]
    with pytest.raises(SobreInvalido, match="firma"):
        verificar_sobre(etree.tostring(raiz), ahora=AHORA)


def test_sobre_vencido(certificado):
    with pytest.raises(SobreInvalido, match="venció"):
        verificar_sobre(_armar(certificado), ahora=AHORA + timedelta(seconds=61))


def test_los_parametros_se_escapan(certificado):
    raiz = etree.fromstring(
        sobre("GetStatus", [("trackId", "<x>&")], destino=URL_HABILITACION, certificado=certificado, ahora=AHORA)
    )
    assert raiz.findtext(f".//{{{NS_WCF}}}trackId") == "<x>&"


def test_la_firma_verifica_siempre_y_to_usa_el_prefijo_wsu(certificado):
    # wsa:To está fuera de wsse:Security: si wsu no está declarado en un ancestro común, lxml inventa un prefijo
    # (ns0) y la forma canónica antes y después de serializar puede no coincidir (firma rechazada a veces).
    for _ in range(10):
        datos = _armar(certificado)
        verificar_sobre(datos, ahora=AHORA)
        assert b'<wsa:To wsu:Id="' in datos and b"ns0:" not in datos


def test_el_digest_incluye_los_prefijos_inclusivos(certificado):
    # El receptor canonicaliza wsa:To CON xmlns:soap y xmlns:wcf (InclusiveNamespaces PrefixList="soap wcf"); el
    # digest firmado debe calcularse igual. lxml omite esos prefijos al canonicalizar un nodo en memoria.
    import base64
    import hashlib

    raiz = etree.fromstring(_armar(certificado))
    to = raiz.find(f".//{{{NS_WSA}}}To")
    esperado = base64.b64encode(hashlib.sha256(etree.tostring(
        to, method="c14n", exclusive=True, inclusive_ns_prefixes=["soap", "wcf"])).digest()).decode()  # fmt: skip
    assert b"xmlns:soap=" in etree.tostring(to, method="c14n", exclusive=True, inclusive_ns_prefixes=["soap", "wcf"])
    assert raiz.findtext(".//{http://www.w3.org/2000/09/xmldsig#}DigestValue") == esperado


def test_el_primer_sobre_de_un_proceso_nuevo_verifica(tmp_path, certificado):
    # lxml canonicaliza distinto un árbol recién construido que uno leído de texto, y solo se notaba en la PRIMERA
    # llamada del proceso: se prueba en un intérprete nuevo, como arranca el worker.
    import subprocess
    import sys

    from estacion.facturacion.certificado_prueba import guardar_pfx

    guardar_pfx(certificado, tmp_path / "c.pfx", "clave")
    codigo = (
        "from datetime import datetime, UTC\n"
        "from estacion.facturacion.firma import cargar_certificado\n"
        "from estacion.facturacion.dian.sobre import sobre, verificar_sobre, URL_HABILITACION\n"
        f"c = cargar_certificado({str(tmp_path / 'c.pfx')!r}, 'clave')\n"
        "a = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)\n"
        "verificar_sobre(sobre('GetStatus', [('trackId', 'x')], destino=URL_HABILITACION, certificado=c, ahora=a),"
        " ahora=a)\n"
    )
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]


def test_url_servicio():
    assert url_servicio("1") == URL_PRODUCCION == "https://vpfe.dian.gov.co/WcfDianCustomerServices.svc"
    assert url_servicio("2") == URL_HABILITACION
    with pytest.raises(ValueError):
        url_servicio("3")
