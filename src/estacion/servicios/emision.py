"""Servicio de emisión: del despacho (o venta manual) a la factura numerada y encolada para la DIAN.

Máquina de estados del ORIGEN (`despacho.estado_facturacion` / `venta_manual.estado_facturacion`):

    PENDIENTE --solicitar--> EN_PROCESO --worker: aceptada--> FACTURADO
                                 |        --worker: rechazo--> PENDIENTE (la factura queda RECHAZADO)
                                 |        --worker: sin respuesta--> INCIERTO --consulta--> FACTURADO
                                 |                                            \\-> EN_PROCESO (no existía: reenvío)

`solicitar` hace en UNA transacción: bloquear el origen, asignar el consecutivo, generar el XML, guardar la
factura `EN_PROCESO` y encolar el envío (`outbox`). El bombero recibe la respuesta sin esperar a la DIAN; el
envío lo hace el worker (`servicios/outbox.py`).

Garantías (en la base de datos, no solo aquí): una factura por origen (UNIQUE), un número por factura
(UNIQUE(prefijo, consecutivo) + bloqueo de la numeración), una clave de emisión por solicitud (UNIQUE).
Si algo falla antes del commit (dato fiscal faltante, caso no soportado), no se gasta ningún consecutivo.

Una factura RECHAZADA conserva su número: al volver a solicitar el mismo origen se corrige y se reenvía
CON EL MISMO NÚMERO (no deja huecos). SIN VERIFICAR: que la DIAN acepte reenviar un número rechazado; la
habilitación lo confirmará.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ..facturacion.modelos import (
    Cliente,
    EstadoFactura,
    EstadoFacturacion,
    LineaFactura,
    SolicitudFactura,
    TipoTrabajo,
)
from ..facturacion.xml_factura import construir_factura
from ._bd import Conexion, ahora_local, auditar, exigir_autocommit
from .configuracion import ConfiguracionIncompleta, cargar_configuracion
from .numeracion import ConsecutivoAsignado, asignar_consecutivo

TipoOrigen = Literal["despacho", "venta_manual"]
MEDIOS_DE_PAGO = {"EFECTIVO", "TARJETA"}


class OrigenNoDisponible(Exception):
    """El despacho o la venta no existe o no está PENDIENTE (ya facturado, en proceso, incierto...)."""


class ClaveReutilizada(Exception):
    """La clave de emisión ya se usó para OTRA venta: el cliente de la API generó mal la clave."""


class ClienteNoDisponible(Exception):
    """El cliente no existe o está inactivo."""


@dataclass(frozen=True, slots=True)
class Origen:
    tipo: TipoOrigen
    id: int

    def __post_init__(self) -> None:
        if self.tipo not in ("despacho", "venta_manual"):
            raise ValueError(f"tipo de origen desconocido: {self.tipo!r}")


@dataclass(frozen=True, slots=True)
class FacturaRegistrada:
    id: int
    numero: str
    estado: EstadoFactura
    total: Decimal
    cufe: str | None
    nueva: bool  # False = la misma clave ya tenía factura (doble clic): no se hizo nada


# Columnas comunes de los dos orígenes; solo cambian la tabla y el volumen (bruto en el despacho).
_SQL_ORIGEN: dict[TipoOrigen, str] = {
    "despacho": """
        SELECT o.estado_facturacion, o.forma_pago, o.volumen_bruto, o.ppu, o.valor, p.codigo, p.nombre,
               s.marca || '-' || s.codigo_externo || ':' || o.id_externo
        FROM despacho o JOIN producto p ON p.id = o.producto_id JOIN surtidor s ON s.id = o.surtidor_id
        WHERE o.id = %s""",
    "venta_manual": """
        SELECT o.estado_facturacion, o.forma_pago, o.volumen, o.ppu, o.valor, p.codigo, p.nombre,
               'MANUAL:' || o.id
        FROM venta_manual o JOIN producto p ON p.id = o.producto_id
        WHERE o.id = %s""",
}
_COLUMNA_FACTURA: dict[TipoOrigen, str] = {"despacho": "despacho_id", "venta_manual": "venta_manual_id"}


@dataclass(frozen=True, slots=True)
class _DatosOrigen:
    estado: str
    forma_pago: str
    linea: LineaFactura


def leer_origen(conn: Conexion, origen: Origen, *, bloquear: bool) -> _DatosOrigen:
    sql = _SQL_ORIGEN[origen.tipo] + (" FOR UPDATE OF o" if bloquear else "")
    fila = conn.execute(sql, (origen.id,)).fetchone()
    if fila is None:
        raise OrigenNoDisponible(f"no existe {origen.tipo} {origen.id}")
    estado, forma_pago, volumen, ppu, valor, codigo, nombre, referencia = fila
    linea = LineaFactura(
        codigo=codigo,
        descripcion=nombre,
        unidad="GLL",
        cantidad=volumen,
        precio_unitario=ppu,
        valor_total=valor,  # lo que cobró el surtidor (puede diferir unos pesos de cantidad × precio)
        tarifa_iva=Decimal(0),  # SIN DEFINIR: lo decide el contador; el generador rechaza IVA a propósito
        referencia_despacho=referencia,
    )
    return _DatosOrigen(estado=estado, forma_pago=forma_pago, linea=linea)


def leer_cliente(conn: Conexion, cliente_id: int) -> Cliente:
    fila = conn.execute(
        """SELECT tipo_documento, numero_documento, nombres, apellidos, coalesce(email, ''),
                  digito_verificacion, autoriza_tratamiento
           FROM cliente WHERE id = %s AND activo""",
        (cliente_id,),
    ).fetchone()
    if fila is None:
        raise ClienteNoDisponible(f"el cliente {cliente_id} no existe o está inactivo")
    tipo, numero, nombres, apellidos, email, dv, autoriza = fila
    return Cliente(
        tipo_documento=tipo,
        numero_documento=numero,
        nombres=nombres,
        apellidos=apellidos,
        email=email,
        digito_verificacion=dv,
        autoriza_tratamiento_datos=autoriza,
    )


class ServicioEmision:
    def __init__(
        self,
        *,
        entorno: Mapping[str, str] | None = None,
        reloj: Callable[[], datetime] = ahora_local,
    ):
        self._entorno = os.environ if entorno is None else entorno
        self._reloj = reloj

    def solicitar(
        self,
        conn: Conexion,
        *,
        clave: str,
        origen: Origen,
        cliente_id: int,
        usuario_id: int,
        medio_pago: str,
    ) -> FacturaRegistrada:
        """Numera la factura de `origen`, guarda su XML sin firmar y encola el envío. Idempotente por `clave`.

        Lanza (sin gastar consecutivo): OrigenNoDisponible, ClaveReutilizada, ClienteNoDisponible,
        ConfiguracionIncompleta, NumeracionNoDisponible, FacturaNoSoportada, ClienteInvalido, NumeracionInvalida.
        """
        exigir_autocommit(conn)
        if medio_pago not in MEDIOS_DE_PAGO:
            raise ValueError(f"medio de pago desconocido: {medio_pago!r}")
        columna = _COLUMNA_FACTURA[origen.tipo]
        with conn.transaction():
            # 1. El bloqueo del origen serializa a dos cajeros (o dos clics) sobre la misma venta.
            datos = leer_origen(conn, origen, bloquear=True)

            # 2. ¿Esta clave ya tiene factura? (doble clic, reintento del cliente tras un corte)
            previa = conn.execute(
                f"SELECT id, {columna}, numero, estado, total, cufe FROM factura WHERE clave_emision = %s",
                (clave,),
            ).fetchone()
            if previa is not None:
                if previa[1] != origen.id:
                    raise ClaveReutilizada("la clave de emisión ya se usó para otra venta")
                return FacturaRegistrada(previa[0], previa[2], EstadoFactura(previa[3]), previa[4], previa[5], False)

            if datos.estado != EstadoFacturacion.PENDIENTE:
                raise OrigenNoDisponible(f"el {origen.tipo} {origen.id} no está pendiente (estado {datos.estado})")

            # 3. Número: el de una factura rechazada de este mismo origen, o uno nuevo.
            rechazada = conn.execute(
                f"""SELECT id, numeracion_id, prefijo, consecutivo FROM factura
                    WHERE {columna} = %s AND estado = 'RECHAZADO' FOR UPDATE""",
                (origen.id,),
            ).fetchone()
            fecha = self._reloj().replace(microsecond=0)
            if rechazada is not None:
                factura_id: int | None = rechazada[0]
                asignado = ConsecutivoAsignado(rechazada[1], rechazada[2], rechazada[3])
            else:
                factura_id = None
                estacion_id = conn.execute("SELECT id FROM estacion WHERE activa").fetchone()
                if estacion_id is None:
                    raise ConfiguracionIncompleta("no hay una estación (emisor) activa configurada")
                asignado = asignar_consecutivo(conn, estacion_id[0], fecha.date())

            # 4. Configuración fiscal y XML. Cualquier falta aborta la transacción: el consecutivo vuelve.
            config = cargar_configuracion(conn, asignado.numeracion_id, self._entorno)
            solicitud = SolicitudFactura(
                clave=clave,
                cliente=leer_cliente(conn, cliente_id),
                lineas=(datos.linea,),
                forma_pago=datos.forma_pago,
                medio_pago=medio_pago,
                fecha_emision=fecha,
            )
            xml = construir_factura(
                solicitud,
                consecutivo=asignado.consecutivo,
                emisor=config.emisor,
                numeracion=config.numeracion,
                software=config.software,
            )
            xml_texto = xml.xml.decode("utf-8")

            # 5. Guardar la factura, marcar el origen y encolar el envío.
            if factura_id is None:
                factura_id = conn.execute(
                    f"""INSERT INTO factura (clave_emision, {columna}, cliente_id, usuario_id, numeracion_id,
                                             prefijo, consecutivo, forma_pago, medio_pago, total, fecha_emision,
                                             cufe, xml_sin_firmar)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (clave, origen.id, cliente_id, usuario_id, asignado.numeracion_id, asignado.prefijo,
                     asignado.consecutivo, datos.forma_pago, medio_pago, solicitud.total, fecha, xml.cufe,
                     xml_texto),
                ).fetchone()[0]  # type: ignore[index]  # fmt: skip
                accion = "FACTURA_SOLICITADA"
            else:
                conn.execute(
                    """UPDATE factura SET clave_emision = %s, estado = 'EN_PROCESO', cliente_id = %s,
                              usuario_id = %s, forma_pago = %s, medio_pago = %s, total = %s, fecha_emision = %s,
                              cufe = %s, xml_sin_firmar = %s, xml_firmado = NULL, respuesta_dian = NULL,
                              error = NULL, actualizada_en = now()
                       WHERE id = %s""",
                    (clave, cliente_id, usuario_id, datos.forma_pago, medio_pago, solicitud.total, fecha, xml.cufe,
                     xml_texto, factura_id),
                )  # fmt: skip
                accion = "FACTURA_REENVIADA"
            conn.execute(
                f"UPDATE {origen.tipo} SET estado_facturacion = 'EN_PROCESO' WHERE id = %s",
                (origen.id,),
            )
            conn.execute("INSERT INTO outbox (factura_id, tipo) VALUES (%s, %s)", (factura_id, TipoTrabajo.ENVIAR_DIAN))
            auditar(
                conn,
                accion,
                "factura",
                factura_id,
                usuario_id=usuario_id,
                detalle={"numero": asignado.numero, "origen": origen.tipo, "origen_id": origen.id},
            )
            return FacturaRegistrada(
                factura_id, asignado.numero, EstadoFactura.EN_PROCESO, solicitud.total, xml.cufe, True
            )
