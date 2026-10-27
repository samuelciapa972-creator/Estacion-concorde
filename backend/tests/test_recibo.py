"""Recibo de la impresora térmica (58 mm, ESC/POS), armado desde el XML. Datos sintéticos."""

from __future__ import annotations

import io
from dataclasses import replace

from PIL import Image

from estacion.facturacion import recibo
from estacion.facturacion.representacion import leer_xml
from test_xml_factura import EMPRESA, construir, linea, solicitud
from test_xml_nota import nota

VALIDACION = "2026-10-28 14:31:50"


def _datos(**cambios):
    return leer_xml(construir(solicitud(**cambios)).xml)


def _texto(d, placa=None):
    return recibo.texto_recibo(recibo.contenido(d, placa=placa, validacion=VALIDACION, logo=None))


def test_a_nombre_de_una_empresa_sale_la_razon_social_y_el_nit_con_dv():
    texto = _texto(_datos(cliente=EMPRESA), placa="GZY858")
    assert "CLIENTE\nTRANSPORTES DE PRUEBA S.A.S.\nNIT 900.123.456-8\nPlaca: GZY858\n" in texto


def test_a_nombre_de_una_persona_sale_su_nombre_y_cedula_sin_dv():
    texto = _texto(_datos())
    assert "CLIENTE\nANA MARÍA PÉREZ GÓMEZ\nCC 1.000.000.001\n" in texto
    assert "Placa" not in texto


def test_lleva_numero_valores_pago_cufe_qr_y_resolucion():
    d = _datos(forma="CREDITO", medio="TARJETA", lineas=[linea("16.475", "11150", "183696", "1", "DIESEL")])
    bloques = recibo.contenido(d, placa=None, validacion=VALIDACION, logo=None)
    texto = recibo.texto_recibo(bloques)
    for esperado in (
        "FACTURA ELECTRONICA DE VENTA",
        "SETP215802",
        "Fecha: 29/09/2026 10:22:22",
        "Vence: 29/10/2026",  # crédito a 30 días
        "DIESEL\n16,475 gal x $11.150\n" + " " * 24 + "$183.696\n",
        "TOTAL   $183.696",
        "Forma de pago: Credito\nMedio de pago: Tarjeta",
        "CUFE:\n" + d.cufe[:32] + "\n" + d.cufe[32:64] + "\n" + d.cufe[64:] + "\n",
        "[QR]",
        "No. 18760000001",
        "Rango: SETP1 a SETP5000000",
    ):
        assert esperado in texto
    (qr,) = [b for b in bloques if isinstance(b, recibo.Imagen)]
    assert qr.imagen.width == recibo.ANCHO_PUNTOS and qr.imagen.mode == "1"


def test_de_contado_no_lleva_vencimiento():
    assert "Vence" not in _texto(_datos())


def test_ningun_renglon_se_sale_del_papel():
    larga = replace(EMPRESA, nombres="Cooperativa Multiactiva de Transportadores Unidos del Valle de Sugamuxi Ltda")
    bloques = recibo.contenido(_datos(cliente=larga), placa="GZY858", validacion=VALIDACION, logo=None)
    for b in bloques:
        if isinstance(b, recibo.Renglon):
            assert len(b.texto) <= (recibo.ANCHO // 2 if b.grande else recibo.ANCHO), b
    assert "COOPERATIVA MULTIACTIVA DE" in recibo.texto_recibo(bloques)


def test_un_total_largo_no_va_en_letra_grande():
    d = _datos(lineas=[linea("1000", "15990", "15990000")])
    renglones = recibo.contenido(d, placa=None, validacion="", logo=None)
    (total,) = [b for b in renglones if isinstance(b, recibo.Renglon) and b.texto.startswith("TOTAL")]
    assert not total.grande and total.texto.endswith("$15.990.000")


def test_fuera_de_produccion_dice_sin_validez_y_en_produccion_no():
    d = _datos()
    assert _texto(d).count("*** SIN VALIDEZ FISCAL ***") == 2
    assert "SIN VALIDEZ" not in _texto(replace(d, ambiente="1"))


def test_nota_credito_dice_que_factura_anula_y_no_lleva_resolucion():
    texto = _texto(leer_xml(nota(construir()).xml))
    assert "NOTA CREDITO ELECTRONICA" in texto and "Anula la factura SETP215802" in texto
    assert "CUDE:" in texto and "Autorizacion de numeracion" not in texto


def test_comandos_escpos():
    d = _datos(cliente=EMPRESA)
    logo = recibo.logo_configurado()
    datos = recibo.escpos(recibo.contenido(d, placa=None, validacion=VALIDACION, logo=logo))
    assert datos.startswith(b"\x1b@\x1c.\x1bt\x00")  # iniciar, salir del modo chino, PC437
    assert b"\x1bE\x01\x1d!\x00TRANSPORTES DE PRUEBA S.A.S.\n" in datos
    assert b"\x1d!\x11SETP215802\n" in datos  # el número en letra grande
    assert datos.endswith(b"\x1bd\x04")
    # El logo: imagen de 48 bytes (384 puntos) por renglón, con su alto exacto.
    cabecera = b"\x1dv0\x00" + (48).to_bytes(2, "little") + logo.height.to_bytes(2, "little")
    assert cabecera in datos


def test_tildes_que_la_impresora_no_tiene_se_quitan():
    assert recibo._a_pc437("PEÑA ÁLVAREZ ÓSCAR") == b"PE\xa5A ALVAREZ OSCAR"


def test_logo_propio_transparente_se_ajusta_al_papel():
    png = io.BytesIO()
    Image.new("RGBA", (1000, 500), (0, 0, 0, 0)).save(png, "PNG")
    logo = recibo.preparar_logo(png.getvalue())
    assert (logo.width, logo.mode) == (recibo.ANCHO_PUNTOS, "1") and logo.height <= 96
    assert logo.getextrema() == (255, 255)  # lo transparente sale como papel, no como un bloque negro


def test_el_logo_incluido_es_biomax():
    logo = recibo.logo_configurado()
    assert logo.width == recibo.ANCHO_PUNTOS and 0 < logo.height <= 96
    assert logo.getextrema() == (0, 255)
