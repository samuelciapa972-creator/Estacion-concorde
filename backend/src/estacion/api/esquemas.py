"""Esquemas de entrada y salida de la API (Pydantic v2).

Dinero y volúmenes son `Decimal` y en JSON salen como TEXTO ("29757", "1.861"): el frontend nunca los
convierte a número de punto flotante (ver PROMPT_CLAUDE_CODE.md). Se aceptan como texto o número en la entrada.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..facturacion.documentos import TIPOS_DOCUMENTO

TipoDocumento = Literal["CC", "NIT", "NIT_EXTERIOR", "CE", "PA", "PPT"]
assert set(TipoDocumento.__args__) == TIPOS_DOCUMENTO  # type: ignore[attr-defined]  # una sola fuente de verdad

# Valores cerrados: los mismos CHECK de la base. En el OpenAPI salen como enum (el frontend genera sus tipos de ahí).
Rol = Literal["BOMBERO", "ADMIN"]
FormaPago = Literal["CONTADO", "CREDITO", "OTRO"]
MedioPago = Literal["EFECTIVO", "TARJETA"]
OrigenTipo = Literal["despacho", "venta_manual"]
EstadoFactura = Literal["EN_PROCESO", "FACTURADO", "RECHAZADO", "INCIERTO"]
Severidad = Literal["ALTA", "AVISO", "INFO"]
EstadoTurno = Literal["ABIERTO", "CERRADO"]

Galones = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3, examples=["1.861"])]
Pesos = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=0, examples=["29757"])]
Precio = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2, examples=["15990"])]


class Esquema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Error(BaseModel):
    detail: Any


# ------------------------------------------------------------------------------------------ sesión
class LoginEntrada(Esquema):
    usuario: str = Field(min_length=3, max_length=40, examples=["bombero1"])
    pin: str = Field(min_length=4, max_length=8, examples=["1234"])


class UsuarioSalida(BaseModel):
    id: int
    usuario: str
    nombre: str
    rol: Rol


class TokenSalida(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expira_en: int = Field(description="segundos de validez del token")
    usuario: UsuarioSalida


# ------------------------------------------------------------------------------------------ catálogos y ventas
class SurtidorSalida(BaseModel):
    id: int
    marca: str
    codigo: str
    descripcion: str | None


class ProductoSalida(BaseModel):
    id: int
    codigo: str
    nombre: str


class CatalogosSalida(BaseModel):
    surtidores: list[SurtidorSalida]
    productos: list[ProductoSalida]
    medios_pago: list[str]
    tipos_documento: list[str]


class PendienteSalida(BaseModel):
    origen_tipo: OrigenTipo
    origen_id: int
    surtidor_id: int
    lado: str
    producto: str
    volumen: Decimal
    valor: Decimal
    ppu: Decimal
    forma_pago: FormaPago
    fecha: datetime
    placa: str | None


class VentaManualEntrada(Esquema):
    surtidor_id: int
    lado: str = Field(min_length=1, max_length=1, examples=["A"])
    producto: str = Field(min_length=1, max_length=40, examples=["CORRIENTE"])
    volumen: Galones
    valor: Pesos
    ppu: Precio | None = None
    forma_pago: Literal["CONTADO", "CREDITO"] = "CONTADO"


class Creado(BaseModel):
    id: int


# ------------------------------------------------------------------------------------------ clientes
class ClienteEntrada(Esquema):
    tipo_documento: TipoDocumento
    numero_documento: str = Field(min_length=3, max_length=20)
    digito_verificacion: str | None = Field(
        default=None, pattern=r"^[0-9]$", description="solo NIT; si falta se calcula"
    )
    nombres: str = Field(min_length=1, max_length=200, description="para NIT: razón social")
    apellidos: str = Field(default="", max_length=200)
    email: str = Field(min_length=5, max_length=200)
    autoriza_tratamiento: bool = Field(description="autorización de tratamiento de datos (Ley 1581 de 2012)")


class RegistroPublicoEntrada(ClienteEntrada):
    captcha: str | None = Field(
        default=None, max_length=4096, description="token del widget; obligatorio si la API tiene captcha configurado"
    )


class ClienteSalida(BaseModel):
    id: int
    tipo_documento: TipoDocumento
    numero_documento: str
    digito_verificacion: str | None
    nombre_mostrar: str = Field(description="razón social si es NIT; nombres y apellidos si es persona")
    nombres: str
    apellidos: str
    email_enmascarado: str
    activo: bool


class RegistroPublicoSalida(BaseModel):
    mensaje: str


class CaptchaSalida(BaseModel):
    proveedor: Literal["turnstile", "hcaptcha"]
    clave_sitio: str = Field(description="clave PÚBLICA del widget")


class EstacionPublicaSalida(BaseModel):
    nombre: str = Field(description="nombre comercial de la estación (o la razón social si no tiene)")


class ConfiguracionPublicaSalida(BaseModel):
    captcha: CaptchaSalida | None = Field(description="null: el registro no pide captcha (banco de pruebas)")
    estacion: EstacionPublicaSalida | None = Field(description="null: no hay una estación activa configurada")


# ------------------------------------------------------------------------------------------ facturas
class FacturaEntrada(Esquema):
    clave: str = Field(
        pattern=r"^[A-Za-z0-9]{16,64}$",
        description="generada UNA vez por intento de emisión (p. ej. uuid sin guiones); reintentar con la misma "
        "clave no duplica la factura",
    )
    origen_tipo: OrigenTipo
    origen_id: int
    cliente_id: int
    medio_pago: MedioPago


class ClienteFactura(BaseModel):
    id: int
    tipo_documento: TipoDocumento
    nombre_mostrar: str


class FacturaSalida(BaseModel):
    id: int
    numero: str
    estado: EstadoFactura
    total: Decimal
    cufe: str | None
    fecha_emision: datetime
    medio_pago: MedioPago
    origen_tipo: OrigenTipo
    origen_id: int
    cliente: ClienteFactura
    error: str | None
    nueva: bool | None = Field(default=None, description="solo al emitir: False si la clave ya tenía factura")


# ------------------------------------------------------------------------------------------ reportes
class FilaVentasSalida(BaseModel):
    surtidor_id: int
    periodo: date
    producto: str
    forma_pago: FormaPago
    origen: Literal["SURTIDOR", "MANUAL"] = Field(description="MANUAL: digitada en la pista, sin despacho enlazado")
    despachos: int
    galones: Decimal
    valor: Decimal


class PosibleDuplicadoSalida(BaseModel):
    venta_manual_id: int
    despacho_id: int
    surtidor_id: int
    lado: str
    producto: str
    galones: Decimal
    valor: Decimal
    registrada_en: datetime
    inicio_despacho: datetime


class FilaTurnoSalida(BaseModel):
    turno_id: int
    surtidor_id: int
    id_cierre: int
    estado: EstadoTurno
    inicio: datetime
    fin: datetime
    lado: str
    despachos: int
    galones: Decimal
    valor: Decimal
    lectura_final_vol: Decimal | None
    lectura_final_valor: Decimal | None


class AnomaliaSalida(BaseModel):
    id: int
    tipo: str
    severidad: Severidad
    surtidor_id: int | None
    despacho_id_externo: int | None
    detalle: dict[str, Any]
    creada_en: datetime
    resuelta: bool


class SaludSalida(BaseModel):
    estado: Literal["ok"]
    base_de_datos: Literal["ok"]
    migracion: str | None


# ------------------------------------------------------------------------------------------ administración
EstadoFacturacion = Literal["PENDIENTE", "EN_PROCESO", "FACTURADO", "INCIERTO", "NO_FACTURABLE"]


class VentaSalida(BaseModel):
    origen_tipo: OrigenTipo
    origen_id: int
    id_externo: int | None = Field(description="número del despacho en el surtidor (null en venta manual)")
    surtidor_id: int
    surtidor: str
    lado: str
    producto: str
    volumen: Decimal
    valor: Decimal
    forma_pago: FormaPago
    fecha: datetime
    placa: str | None
    estado_facturacion: EstadoFacturacion
    factura_numero: str | None
    factura_estado: EstadoFactura | None
    cliente_tipo: TipoDocumento | None
    cliente_documento: str | None
    cliente_nombre: str | None
    vendedor_id: int | None
    vendedor: str | None


class TotalesSalida(BaseModel):
    ventas: int
    galones: Decimal
    valor: Decimal


class TotalMangueraSalida(TotalesSalida):
    surtidor_id: int
    lado: str
    producto: str


class BusquedaVentasSalida(BaseModel):
    filas: list[VentaSalida]
    total: TotalesSalida = Field(description="del filtro completo, no solo de esta página")
    por_manguera: list[TotalMangueraSalida]


class UsuarioListadoSalida(BaseModel):
    id: int
    usuario: str
    nombre: str
    rol: Rol
    activo: bool


class ClientesPaginaSalida(BaseModel):
    filas: list[ClienteSalida]
    total: int


class VehiculoEntrada(Esquema):
    placa: str = Field(min_length=1, max_length=60, description="placa o nombre del equipo; se normaliza")
    cliente_id: int
    descripcion: str | None = Field(default=None, max_length=200)


class VehiculoSalida(BaseModel):
    id: int
    identificador: str
    tipo: Literal["PLACA", "EQUIPO_TEXTO"]
    cliente_id: int
    cliente_nombre: str
    descripcion: str | None
    activo: bool


class VehiculosPaginaSalida(BaseModel):
    filas: list[VehiculoSalida]
    total: int


class FilaRechazadaSalida(BaseModel):
    fila: int = Field(description="línea del archivo (el encabezado es la 1)")
    problemas: list[str]


class ImportacionSalida(BaseModel):
    en_seco: bool = Field(description="true: solo se validó; no se guardó nada")
    filas: int
    nuevas: int
    existentes: int = Field(description="ya estaban registrados: no se modificaron")
    rechazadas: list[FilaRechazadaSalida]
