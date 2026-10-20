"""Certificado de PRUEBA (autofirmado, sin validez) y firma en cualquier extensión del documento."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest

from estacion import certificado as cli
from estacion.facturacion.certificado_prueba import MARCA, generar_certificado_prueba, guardar_pfx
from estacion.facturacion.firma import FirmaError, cargar_certificado, firmar_xml
from test_xml_factura import construir

UNA_EXTENSION = (
    b'<D xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2">'
    b"<ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent/></ext:UBLExtension></ext:UBLExtensions>"
    b"<x>1</x></D>"
)


def test_genera_guarda_y_carga(tmp_path):
    c = generar_certificado_prueba("EDS PRUEBA SAS")
    assert MARCA in c.cert.subject.rfc4514_string()
    ruta = tmp_path / "prueba.pfx"
    guardar_pfx(c, ruta, "clave-123")
    leido = cargar_certificado(str(ruta), "clave-123")
    assert leido.cert.serial_number == c.cert.serial_number
    with pytest.raises(FirmaError) as e:
        cargar_certificado(str(ruta), "otra")
    assert "clave-123" not in str(e.value) and "otra" not in str(e.value)


def test_certificado_vencido_falla_cerrado():
    viejo = generar_certificado_prueba("X", dias=1, ahora=datetime.now(UTC) - timedelta(days=10))
    with pytest.raises(FirmaError, match="venció"):
        firmar_xml(construir().xml, viejo)


def test_firma_en_la_primera_extension(certificado):
    assert b"SignatureValue" in firmar_xml(UNA_EXTENSION, certificado, extension=1)
    with pytest.raises(FirmaError, match=r"UBLExtension\[2\]"):
        firmar_xml(UNA_EXTENSION, certificado)  # por defecto, la segunda (la de la factura)


def test_cli_genera_el_pfx(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("clave-123\n"))
    ruta = tmp_path / "c.pfx"
    assert cli.main(["--salida", str(ruta), "--nombre", "EDS PRUEBA SAS", "--clave-stdin"]) == 0
    salida = capsys.readouterr().out
    assert "DIAN_CERTIFICADO_RUTA" in salida and "clave-123" not in salida
    assert cargar_certificado(str(ruta), "clave-123").cert


def test_cli_sin_clave(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert cli.main(["--salida", str(tmp_path / "c.pfx"), "--clave-stdin"]) == 2
    assert not (tmp_path / "c.pfx").exists()
