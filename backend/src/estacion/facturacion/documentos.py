"""Validación de documentos de identidad de clientes (sin conexión a ningún servicio)."""

from __future__ import annotations

import re

from .modelos import Cliente

# Pesos oficiales de la DIAN para el dígito de verificación, de derecha a izquierda.
_PESOS_DV = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# Los tipos de documento que ofrece el formulario de registro que se vio en Terpel.
TIPOS_DOCUMENTO = {"CC", "NIT", "NIT_EXTERIOR", "CE", "PA", "PPT"}


class ClienteInvalido(ValueError):
    def __init__(self, problemas: list[str]):
        super().__init__("; ".join(problemas))
        self.problemas = problemas


def calcular_dv(nit: str) -> int:
    """Dígito de verificación de un NIT colombiano (sin el DV).

    >>> calcular_dv("900123456")
    8
    >>> calcular_dv("800197268")
    4
    """
    if not nit.isdigit() or not 1 <= len(nit) <= len(_PESOS_DV):
        raise ValueError(f"NIT inválido: {nit!r}")
    total = sum(int(d) * p for d, p in zip(reversed(nit), _PESOS_DV, strict=False))  # el NIT es más corto que los pesos
    r = total % 11
    return r if r < 2 else 11 - r


def validar_cliente(c: Cliente) -> None:
    """Lanza ClienteInvalido con TODOS los problemas encontrados (no solo el primero).

    Los largos son aproximados; hay que ajustarlos a las tablas del anexo técnico de la DIAN.
    """
    p: list[str] = []
    doc = c.numero_documento.strip()

    if c.tipo_documento not in TIPOS_DOCUMENTO:
        p.append(f"tipo de documento desconocido: {c.tipo_documento!r}")
    elif c.tipo_documento == "CC":
        if not (doc.isdigit() and 5 <= len(doc) <= 10):
            p.append("la cédula debe tener entre 5 y 10 dígitos")
    elif c.tipo_documento == "NIT":
        if not (doc.isdigit() and 8 <= len(doc) <= 10):
            p.append("el NIT debe tener entre 8 y 10 dígitos, sin dígito de verificación")
        elif c.digito_verificacion is not None and str(calcular_dv(doc)) != str(c.digito_verificacion):
            p.append(f"dígito de verificación incorrecto (debería ser {calcular_dv(doc)})")
        elif c.digito_verificacion is None:
            p.append("falta el dígito de verificación del NIT")
    elif not (doc.isalnum() and 3 <= len(doc) <= 20):
        p.append("el número de documento debe ser alfanumérico, de 3 a 20 caracteres")

    if not c.nombres.strip():
        p.append("falta el nombre")
    if c.tipo_documento != "NIT" and not c.apellidos.strip():
        p.append("faltan los apellidos")
    if not _EMAIL.match(c.email.strip()):
        p.append("el correo no es válido")
    if not c.autoriza_tratamiento_datos:
        p.append("falta la autorización de tratamiento de datos (Ley 1581 de 2012)")

    if p:
        raise ClienteInvalido(p)
