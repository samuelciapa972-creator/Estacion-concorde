"""Paquete de entrega: ZIP con el AttachedDocument y el PDF, nombres de archivo y asunto del correo."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pytest

from estacion.facturacion import paquete
from estacion.facturacion.firma import firmar_xml
from estacion.facturacion.respuesta_simulada import generar_respuesta
from test_xml_factura import construir

AHORA = datetime(2026, 9, 29, 10, 30)


@pytest.fixture
def firmada_y_respuesta(certificado):
    firmada = firmar_xml(construir().xml, certificado)
    return firmada, generar_respuesta(firmada, validada_en=datetime(2026, 9, 29, 10, 25))


def test_nombre_archivo():
    assert paquete.nombre_archivo("z", "900123456", 2026, 10, "zip") == "z0900123456000260000000a.zip"
    assert paquete.nombre_archivo("ad", "900123456", 2026, 0xFFFFFFFF, "xml") == "ad090012345600026ffffffff.xml"
    assert paquete.nombre_archivo("fv", "800197268", 2031, 1, "pdf") == "fv08001972680003100000001.pdf"


@pytest.mark.parametrize("consecutivo", [0, -1, 0x100000000])
def test_nombre_archivo_fuera_de_rango(consecutivo):
    with pytest.raises(ValueError, match="consecutivo"):
        paquete.nombre_archivo("z", "900123456", 2026, consecutivo, "zip")


def test_asunto_desde_el_xml(firmada_y_respuesta):
    firmada, _ = firmada_y_respuesta
    # NIT;Razón social;Número;Tipo;Nombre comercial (anexo v1.8 §9.1; sin nombre comercial va la razón social)
    assert paquete.asunto(firmada) == "900123456;ESTACION DE PRUEBA S A S;SETP215802;01;ESTACION DE PRUEBA S A S"


def test_zip_con_exactamente_dos_archivos(firmada_y_respuesta, certificado):
    firmada, ar = firmada_y_respuesta
    p = paquete.armar(firmada, ar, consecutivo=10, ahora=AHORA, certificado=certificado, validacion="SIMULADA")
    assert p.nombre_zip == "z0900123456000260000000a.zip"
    assert p.asunto == paquete.asunto(firmada)
    with zipfile.ZipFile(io.BytesIO(p.zip)) as z:
        assert sorted(z.namelist()) == ["ad0900123456000260000000a.xml", "fv0900123456000260000000a.pdf"]
        assert z.read("fv0900123456000260000000a.pdf").startswith(b"%PDF")
        assert b"AttachedDocument" in z.read("ad0900123456000260000000a.xml")
        assert z.testzip() is None
    assert len(p.zip) <= paquete.LIMITE_ZIP


def test_zip_demasiado_grande(firmada_y_respuesta, certificado, monkeypatch):
    monkeypatch.setattr(paquete, "LIMITE_ZIP", 100)
    firmada, ar = firmada_y_respuesta
    with pytest.raises(paquete.PaqueteDemasiadoGrande, match="máximo"):
        paquete.armar(firmada, ar, consecutivo=1, ahora=AHORA, certificado=certificado, validacion="x")


def test_asunto_sin_separadores_dentro_de_los_campos(certificado):
    # Un ";" dentro de la razón social correría los campos que el buzón del cliente lee por posición.
    f = construir()
    xml = f.xml.replace(b"ESTACION DE PRUEBA S A S", b"ESTACION; DE PRUEBA\nS A S")
    assert paquete.asunto(xml).split(";")[1] == "ESTACION, DE PRUEBA S A S"
