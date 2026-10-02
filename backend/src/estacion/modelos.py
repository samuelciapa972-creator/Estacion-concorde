"""Modelos de dominio normalizados, independientes de la marca del surtidor.

Cada parser (Speed Solutions, Wayne...) traduce su formato a `Despacho`.
Todo lo que viene después (calidad, carga, facturación) trabaja solo con estos tipos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class MarcaSurtidor(StrEnum):
    SPEED_SOLUTIONS = "SPEED_SOLUTIONS"
    WAYNE = "WAYNE"
    SIMULADOR = "SIMULADOR"  # banco de pruebas: nunca es un equipo real


class FormaPago(StrEnum):
    CONTADO = "CONTADO"
    CREDITO = "CREDITO"
    OTRO = "OTRO"


class PlacaTipo(StrEnum):
    PLACA = "PLACA"  # placa colombiana válida (carro, moto, remolque)
    SIN_PLACA = "SIN_PLACA"  # vacío, "0", "1", "5"... (marcador de "sin dato")
    EQUIPO_TEXTO = "EQUIPO_TEXTO"  # "BOBCAT", "MOTONIVELADORA", "CANECAS"...


class Severidad(StrEnum):
    INFO = "INFO"
    AVISO = "AVISO"  # revisar, no bloquea
    ALTA = "ALTA"  # afecta facturación o cuadre de caja


class Rol(StrEnum):
    BOMBERO = "BOMBERO"  # vendedor de pista
    ADMIN = "ADMIN"


class ArchivoInvalido(Exception):
    """El archivo no se puede leer completo (XML truncado o mal formado)."""


@dataclass(frozen=True, slots=True)
class Despacho:
    surtidor_marca: str
    codigo_surtidor: str
    id_externo: int
    id_cierre: int
    lado: str
    pistola: int
    producto: str

    inicio_reloj: datetime  # tal como lo reportó el equipo
    fin_reloj: datetime
    inicio: datetime  # efectivo (igual al reloj salvo corrección)
    fin: datetime

    volumen_bruto: Decimal
    volumen_neto: Decimal
    valor: Decimal
    ppu: Decimal
    forma_pago: FormaPago

    placa_raw: str
    placa_tipo: PlacaTipo
    ref_vehiculo: str | None
    kilometraje_raw: str
    kilometraje: int | None

    totalizador_vol: Decimal | None
    totalizador_valor: Decimal | None

    hash_contenido: str
    crudo: dict[str, str] = field(compare=False)
    fecha_corregida: bool = False


@dataclass(frozen=True, slots=True)
class Anomalia:
    tipo: str
    severidad: Severidad
    id_despacho: int | None = None  # id_externo del despacho afectado
    id_cierre: int | None = None  # o del turno, si aplica
    detalle: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RechazoFila:
    id_externo: int | None
    motivo: str
    crudo: dict[str, str]


@dataclass(frozen=True, slots=True)
class TurnoDerivado:
    id_cierre: int
    inicio: datetime
    fin: datetime
    estado: str  # 'ABIERTO' | 'CERRADO'
    despachos: int
    volumen_bruto: Decimal
    valor: Decimal


@dataclass(frozen=True, slots=True)
class ResultadoLectura:
    marca: str
    archivo_sha256: str
    despachos: list[Despacho]
    rechazos: list[RechazoFila]
