"""Datos de la empresa y de su habilitación ante la DIAN que el generador del XML necesita.

Todo esto lo entrega la empresa/contador (RUT, resolución de numeración, portal de habilitación).
NADA de esto va escrito en el código: se carga de la configuración del despliegue.
La clave técnica y el PIN son secretos: variables de entorno, nunca el repositorio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Emisor:
    """La empresa que factura (vendedor). Los datos salen del RUT (tabla `estacion`).

    Sin valores por defecto en los datos fiscales, a propósito: si falta uno, no se puede construir el
    emisor y no se emite (fallar cerrado). Solo son opcionales los que el RUT puede no tener.
    """

    nit: str  # sin dígito de verificación
    razon_social: str
    direccion: str  # la del ESTABLECIMIENTO (hoja de establecimientos del RUT), no la del domicilio principal
    telefono: str
    email: str
    actividades_ciiu: tuple[str, ...]
    # Códigos de responsabilidad fiscal del anexo técnico (p. ej. "O-13;O-15;O-23;R-99-PN").
    # Los define el contador a partir de las responsabilidades del RUT.
    responsabilidades: str
    responsable_iva: bool  # el RUT trae la responsabilidad 48
    codigo_municipio: str  # DANE, 5 dígitos
    municipio: str
    codigo_departamento: str
    departamento: str
    nombre_comercial: str | None = None
    codigo_postal: str | None = None
    matricula_mercantil: str | None = None


@dataclass(frozen=True, slots=True)
class Numeracion:
    """Resolución de numeración autorizada por la DIAN para la estación (con su propio prefijo)."""

    prefijo: str
    desde: int
    hasta: int
    numero_resolucion: str
    vigente_desde: date
    vigente_hasta: date
    clave_tecnica: str  # SECRETO: la entrega la DIAN al asociar el rango


@dataclass(frozen=True, slots=True)
class SoftwareDian:
    """El software propio registrado en el portal de habilitación de la DIAN."""

    software_id: str  # UUID que asigna la DIAN
    pin: str  # SECRETO
    nit_proveedor: str  # con software propio: el NIT de la propia empresa (por confirmar)
    ambiente: str = "2"  # "2" = habilitación (pruebas), "1" = producción
