"""Recibo para la impresora térmica de la isla (58 mm, ESC/POS), armado SOLO a partir del XML, como el PDF.

Por qué del XML: el recibo no puede decir algo distinto de la factura electrónica (`representacion.leer_xml`).
Lo único que no está en el XML es la placa, que la pone quien llama.

LA IMPRESORA: térmica genérica "POS-58" (la de la tapa azul en la isla del Wayne): 384 puntos de ancho, 32
caracteres por renglón con la letra normal. Comandos ESC/POS básicos, los que entienden casi todas:
    ESC @ (iniciar) · FS . (salir del modo chino que traen de fábrica) · ESC t 0 (tabla PC437) · ESC a (alinear)
    · ESC E (negrita) · GS ! (tamaño) · GS v 0 (imagen) · ESC d (avanzar papel)
El logo y el QR van como imagen (GS v 0) y no con el comando de QR propio de la impresora: las baratas no siempre
lo traen. SIN VERIFICAR con la impresora real: se prueba en la estación antes de la primera factura.

EL LOGO: `RECIBO_LOGO` (ruta a un PNG) o, sin ella, el logotipo de texto "BIOMAX" de `recursos/logo_recibo.png`.
"""

from __future__ import annotations

import io
import os
import textwrap
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib import resources
from pathlib import Path

from PIL import Image
from reportlab.graphics.barcode import qrencoder

from .representacion import DatosRepresentacion, contenido_qr, fecha_co, numero_es_co

ANCHO_PUNTOS = 384
ANCHO = 32  # caracteres por renglón con la letra normal; con la grande caben la mitad
_ALTO_LOGO = 96  # puntos (12 mm): que el logo no se coma el rollo
_UNIDADES = {"GLL": "gal"}
_ETIQUETA_CODIGO = {"FACTURA": "CUFE", "NOTA_CREDITO": "CUDE"}


@dataclass(frozen=True, slots=True)
class Renglon:
    texto: str
    centro: bool = False
    negrita: bool = False
    grande: bool = False  # doble alto y doble ancho


@dataclass(frozen=True, slots=True)
class Imagen:
    nombre: str  # para la vista en texto: "LOGO", "QR"
    imagen: Image.Image  # modo "1", ANCHO_PUNTOS de ancho, ya centrada


Bloque = Renglon | Imagen
_RAYA = Renglon("-" * ANCHO)


# ------------------------------------------------------------------------------------------ contenido
def _dinero(x: Decimal) -> str:
    """ "$183.696"; con centavos solo si los hay ("$11.150,50")."""
    return "$" + numero_es_co(x, 0 if x == x.to_integral_value() else 2)


def _miles(documento: str) -> str:
    """ "900123456" -> "900.123.456"; un documento con letras queda igual."""
    return numero_es_co(Decimal(documento), 0) if documento.isdigit() else documento


def _partir(texto: str, ancho: int = ANCHO) -> list[str]:
    return textwrap.wrap(texto, ancho, break_long_words=True) or [""]


def _a_los_lados(izquierda: str, derecha: str, ancho: int = ANCHO) -> str:
    return izquierda + " " * max(1, ancho - len(izquierda) - len(derecha)) + derecha


def contenido(d: DatosRepresentacion, *, placa: str | None, validacion: str, logo: Image.Image | None) -> list[Bloque]:
    """Los renglones del recibo, en orden. `validacion`: el texto de `representacion.texto_validacion`."""
    b: list[Bloque] = []

    def centrado(texto: str, *, negrita: bool = False, grande: bool = False) -> None:
        ancho = ANCHO // 2 if grande else ANCHO
        b.extend(Renglon(r, centro=True, negrita=negrita, grande=grande) for r in _partir(texto, ancho))

    def izquierda(texto: str, *, negrita: bool = False) -> None:
        b.extend(Renglon(r, negrita=negrita) for r in _partir(texto))

    sin_validez = d.ambiente != "1"
    nota = d.tipo == "NOTA_CREDITO"
    if logo is not None:
        b.append(Imagen("LOGO", logo))
    if d.nombre_comercial != d.razon_social:
        centrado(d.nombre_comercial, negrita=True, grande=True)
    centrado(d.razon_social, negrita=True)
    centrado(f"NIT {_miles(d.nit)}-{d.dv}")
    centrado("RESPONSABLE DE IVA" if d.responsable_iva else "NO RESPONSABLE DE IVA")
    centrado(d.direccion)
    centrado(f"{d.municipio} - {d.departamento}")
    centrado(f"Tel: {d.telefono}")
    if sin_validez:
        b.append(_RAYA)
        centrado("*** SIN VALIDEZ FISCAL ***", negrita=True)
        centrado("AMBIENTE DE PRUEBAS")

    b.append(_RAYA)
    centrado("NOTA CREDITO ELECTRONICA" if nota else "FACTURA ELECTRONICA DE VENTA", negrita=True)
    centrado(d.numero, negrita=True, grande=True)
    b.append(Renglon(f"Fecha: {fecha_co(d.fecha)} {d.hora[:8]}"))
    if d.vencimiento != d.fecha:
        b.append(Renglon(f"Vence: {fecha_co(d.vencimiento)}"))
    if nota:
        izquierda(f"Anula la factura {d.factura_referencia}", negrita=True)
        izquierda(f"Motivo: {d.motivo}")

    b.append(_RAYA)
    b.append(Renglon("CLIENTE", negrita=True))
    izquierda(d.comprador_nombre, negrita=True)  # la razón social de la empresa, o el nombre de la persona
    dv = f"-{d.comprador_dv}" if d.comprador_dv else ""
    b.append(Renglon(f"{d.comprador_tipo} {_miles(d.comprador_documento)}{dv}"))
    if placa:
        b.append(Renglon(f"Placa: {placa}"))

    b.append(_RAYA)
    for linea in d.lineas:
        izquierda(linea.descripcion, negrita=True)
        unidad = _UNIDADES.get(linea.unidad, linea.unidad.lower())
        izquierda(f"{numero_es_co(linea.cantidad, 3)} {unidad} x {_dinero(linea.precio_unitario)}")
        b.append(Renglon(_a_los_lados("", _dinero(linea.valor_total))))
    b.append(_RAYA)
    if d.descuentos:
        b.append(Renglon(_a_los_lados("Descuentos", "-" + _dinero(d.descuentos))))
    total = _a_los_lados("TOTAL", _dinero(d.valor_total), ANCHO // 2)
    if len(total) <= ANCHO // 2:
        b.append(Renglon(total, negrita=True, grande=True))
    else:  # un total muy largo no cabe con la letra grande
        b.append(Renglon(_a_los_lados("TOTAL", _dinero(d.valor_total)), negrita=True))
    b.append(Renglon(f"Forma de pago: {d.forma_pago}"))
    b.append(Renglon(f"Medio de pago: {d.medio_pago}"))

    b.append(_RAYA)
    b.append(Renglon(f"{_ETIQUETA_CODIGO[d.tipo]}:", negrita=True))
    b.extend(Renglon(d.cufe[i : i + ANCHO]) for i in range(0, len(d.cufe), ANCHO))
    b.append(Imagen("QR", _qr(contenido_qr(d))))
    izquierda(f"Validacion DIAN: {validacion}")
    if not nota:
        b.append(_RAYA)
        izquierda(f"Autorizacion de numeracion DIAN No. {d.resolucion}")
        izquierda(f"Vigencia: {fecha_co(d.vigente_desde)} a {fecha_co(d.vigente_hasta)}")
        izquierda(f"Rango: {d.prefijo}{d.rango_desde} a {d.prefijo}{d.rango_hasta}")
    b.append(_RAYA)
    documento = "nota credito electronica" if nota else "factura electronica de venta"
    izquierda(f"Representacion grafica de la {documento}. Consultela en el catalogo de la DIAN con el QR.")
    izquierda(f"Software propio del facturador (NIT {d.nit_proveedor_software}).")
    if sin_validez:
        centrado("*** SIN VALIDEZ FISCAL ***", negrita=True)
    b.append(Renglon(""))
    centrado("Gracias por su compra")
    return b


def texto_recibo(bloques: list[Bloque]) -> str:
    """Vista en texto del recibo (pruebas y revisión): las imágenes salen como "[LOGO]" o "[QR]"."""
    salida = []
    for bloque in bloques:
        if isinstance(bloque, Imagen):
            salida.append(f"[{bloque.nombre}]".center(ANCHO).rstrip())
            continue
        ancho = ANCHO // 2 if bloque.grande else ANCHO
        salida.append((bloque.texto.center(ancho) if bloque.centro else bloque.texto).rstrip())
    return "\n".join(salida) + "\n"


# ------------------------------------------------------------------------------------------ imágenes
def _centrar(imagen: Image.Image) -> Image.Image:
    lienzo = Image.new("1", (ANCHO_PUNTOS, imagen.height), 255)
    lienzo.paste(imagen, ((ANCHO_PUNTOS - imagen.width) // 2, 0))
    return lienzo


def _qr(texto: str) -> Image.Image:
    codigo = qrencoder.QRCode(None, qrencoder.QRErrorCorrectLevel.M)  # el mismo nivel que el QR del PDF
    codigo.addData(texto)
    codigo.make()
    modulos = codigo.modules
    n = len(modulos) + 8  # 4 módulos de margen a cada lado
    escala = min(5, ANCHO_PUNTOS // n)
    if escala < 2:
        raise ValueError(f"el QR tiene {len(modulos)} módulos: no cabe legible en {ANCHO_PUNTOS} puntos")
    imagen = Image.new("1", (n, n), 255)
    for fila, valores in enumerate(modulos):
        for columna, oscuro in enumerate(valores):
            if oscuro:
                imagen.putpixel((columna + 4, fila + 4), 0)
    return _centrar(imagen.resize((n * escala, n * escala), Image.Resampling.NEAREST))


def preparar_logo(datos: bytes) -> Image.Image:
    """Cualquier imagen -> blanco y negro, a lo sumo `ANCHO_PUNTOS` x `_ALTO_LOGO`, centrada."""
    original = Image.open(io.BytesIO(datos))
    if original.mode in ("RGBA", "LA", "P"):  # lo transparente se imprime como papel
        fondo = Image.new("RGBA", original.size, "white")
        gris = Image.alpha_composite(fondo, original.convert("RGBA")).convert("L")
    else:
        gris = original.convert("L")
    gris.thumbnail((ANCHO_PUNTOS, _ALTO_LOGO), Image.Resampling.LANCZOS)
    return _centrar(gris.point(lambda v: 0 if v < 128 else 255).convert("1"))


@cache
def logo_configurado() -> Image.Image:
    """El logo de `RECIBO_LOGO`, o el logotipo de texto incluido. Se lee una vez por proceso."""
    ruta = os.environ.get("RECIBO_LOGO", "").strip()
    if ruta:
        return preparar_logo(Path(ruta).read_bytes())
    return preparar_logo(resources.files(__package__).joinpath("recursos/logo_recibo.png").read_bytes())


# ------------------------------------------------------------------------------------------ ESC/POS
_INICIO = b"\x1b@" + b"\x1c." + b"\x1bt\x00"  # iniciar · salir del modo chino · tabla PC437
_FILAS_POR_TROZO = 128  # las impresoras baratas tienen poca memoria: la imagen va por trozos


def _a_pc437(texto: str) -> bytes:
    """PC437 tiene ñ, Ñ, á, é, í, ó, ú, ü y É; lo demás pierde la tilde (Á -> A) o sale como "?".

    >>> _a_pc437("MUÑOZ ÁLVAREZ €")
    b'MU\\xa5OZ ALVAREZ ?'
    """
    salida = bytearray()
    for letra in texto:
        try:
            salida += letra.encode("cp437")
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", letra).encode("ascii", "ignore")
            salida += base[:1] or b"?"
    return bytes(salida)


def _raster(imagen: Image.Image) -> bytes:
    ancho_bytes = imagen.width // 8
    salida = bytearray()
    for arriba in range(0, imagen.height, _FILAS_POR_TROZO):
        trozo = imagen.crop((0, arriba, imagen.width, min(imagen.height, arriba + _FILAS_POR_TROZO)))
        # En modo "1" de PIL el blanco es 1; en ESC/POS un bit en 1 es un punto impreso (negro).
        datos = bytes(b ^ 0xFF for b in trozo.tobytes())
        salida += b"\x1dv0\x00" + ancho_bytes.to_bytes(2, "little") + trozo.height.to_bytes(2, "little") + datos
    return bytes(salida)


def escpos(bloques: list[Bloque], *, avance: int = 4) -> bytes:
    """Los comandos para la impresora. `avance`: renglones en blanco al final, para cortar el papel a mano."""
    salida = bytearray(_INICIO)
    for bloque in bloques:
        if isinstance(bloque, Imagen):
            salida += b"\x1ba\x01" + _raster(bloque.imagen) + b"\n"
            continue
        salida += b"\x1ba" + (b"\x01" if bloque.centro else b"\x00")
        salida += b"\x1bE" + (b"\x01" if bloque.negrita else b"\x00")
        salida += b"\x1d!" + (b"\x11" if bloque.grande else b"\x00")
        salida += _a_pc437(bloque.texto) + b"\n"
    salida += b"\x1bE\x00\x1d!\x00\x1ba\x00" + b"\x1bd" + bytes([avance])
    return bytes(salida)


def generar_recibo(d: DatosRepresentacion, *, placa: str | None, validacion: str) -> bytes:
    return escpos(contenido(d, placa=placa, validacion=validacion, logo=logo_configurado()))
