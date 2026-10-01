"""Limpieza de los campos que el operador digita a mano en el surtidor."""

from __future__ import annotations

import re

from .modelos import FormaPago, PlacaTipo

_PLACA_CARRO = re.compile(r"^[A-Z]{3}\d{3}$")  # ABC123
_PLACA_MOTO = re.compile(r"^[A-Z]{3}\d{2}[A-Z]$")  # ABC12D
_PLACA_REMOLQUE = re.compile(r"^R\d{5}$")  # R12345
_SIN_DATO = re.compile(r"^\d{1,2}$")  # "0", "1", "5": relleno del operador

# Un kilometraje real de un vehículo en uso. En los datos reales, 3.884 de 4.095
# registros traen 0 o 1 como relleno, y hay valores absurdos (negativos, 8 mil millones).
KM_MIN, KM_MAX = 100, 5_000_000


def normalizar_placa(raw: str | None) -> tuple[PlacaTipo, str | None]:
    """Devuelve (tipo, referencia normalizada).

    >>> normalizar_placa("gzy-848")
    (<PlacaTipo.PLACA: 'PLACA'>, 'GZY848')
    >>> normalizar_placa("1")
    (<PlacaTipo.SIN_PLACA: 'SIN_PLACA'>, None)
    >>> normalizar_placa("Motoniveladora")
    (<PlacaTipo.EQUIPO_TEXTO: 'EQUIPO_TEXTO'>, 'MOTONIVELADORA')
    """
    limpio = re.sub(r"[\s\-.]", "", (raw or "").upper())
    if not limpio or _SIN_DATO.match(limpio):
        return PlacaTipo.SIN_PLACA, None
    if any(p.match(limpio) for p in (_PLACA_CARRO, _PLACA_MOTO, _PLACA_REMOLQUE)):
        return PlacaTipo.PLACA, limpio
    return PlacaTipo.EQUIPO_TEXTO, limpio


def normalizar_kilometraje(raw: str | None) -> int | None:
    s = (raw or "").strip()
    if s.isdigit() and KM_MIN <= int(s) <= KM_MAX:
        return int(s)
    return None


def normalizar_forma_pago(raw: str | None) -> FormaPago:
    v = (raw or "").strip().upper()
    if v in ("CONTADO", "CREDITO"):
        return FormaPago(v)
    return FormaPago.OTRO
