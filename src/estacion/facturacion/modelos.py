"""Tipos del dominio de facturación."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class EstadoFacturacion(StrEnum):
    PENDIENTE = "PENDIENTE"  # listo para facturar
    EN_PROCESO = "EN_PROCESO"  # reservado por una emisión en curso
    FACTURADO = "FACTURADO"
    INCIERTO = "INCIERTO"  # el proveedor no respondió: no se sabe si emitió
    NO_FACTURABLE = "NO_FACTURABLE"


class EstadoFactura(StrEnum):
    """Estado de una factura (tabla `factura`). Un rechazo no gasta el consecutivo: la misma
    factura, con su mismo número, se corrige y se vuelve a enviar."""

    EN_PROCESO = "EN_PROCESO"  # numerada y en cola de envío
    FACTURADO = "FACTURADO"  # aceptada por el proveedor / la DIAN
    RECHAZADO = "RECHAZADO"  # rechazo definitivo: se sabe que NO quedó emitida
    INCIERTO = "INCIERTO"  # sin respuesta: puede haber quedado; se concilia por la clave


class EstadoOutbox(StrEnum):
    """Estado de un trabajo de la cola de envíos (tabla `outbox`)."""

    PENDIENTE = "PENDIENTE"
    EN_CURSO = "EN_CURSO"
    HECHO = "HECHO"
    FALLIDO = "FALLIDO"  # agotó los reintentos: revisión humana


class TipoTrabajo(StrEnum):
    ENVIAR_DIAN = "ENVIAR_DIAN"
    CONSULTAR_DIAN = "CONSULTAR_DIAN"  # conciliar un caso INCIERTO por la clave de emisión


@dataclass(frozen=True, slots=True)
class Cliente:
    tipo_documento: str  # CC, NIT, NIT_EXTERIOR, CE, PA, PPT
    numero_documento: str  # sin dígito de verificación
    nombres: str  # para NIT: razón social
    apellidos: str = ""
    email: str = ""
    digito_verificacion: str | None = None
    autoriza_tratamiento_datos: bool = False


@dataclass(frozen=True, slots=True)
class ProductoFactura:
    codigo: str
    descripcion: str
    unidad: str = "GLL"
    tarifa_iva: Decimal = Decimal("0")  # a confirmar con el contador para cada combustible


@dataclass(frozen=True, slots=True)
class LineaFactura:
    codigo: str
    descripcion: str
    unidad: str
    cantidad: Decimal
    precio_unitario: Decimal
    valor_total: Decimal
    tarifa_iva: Decimal
    referencia_despacho: str  # "<surtidor>:<id>", para poder auditar


@dataclass(frozen=True, slots=True)
class SolicitudFactura:
    clave: str  # identifica ESTE intento de emisión
    cliente: Cliente
    lineas: tuple[LineaFactura, ...]
    forma_pago: str
    medio_pago: str
    fecha_emision: datetime
    # Motor propio: el número, el CUFE y el documento los produce ESTE sistema (no el proveedor).
    # Vacíos cuando la solicitud todavía no está numerada (p. ej. al validar o generar el XML).
    numero: str | None = None
    cufe: str | None = None
    documento: bytes | None = None

    @property
    def total(self) -> Decimal:
        return sum((linea.valor_total for linea in self.lineas), Decimal(0))


@dataclass(frozen=True, slots=True)
class ResultadoEmision:
    clave: str
    prefijo: str
    numero: str
    cufe: str
    emitida_en: datetime
    estado: str = "EMITIDA"
    detalle: dict = field(default_factory=dict, compare=False)
