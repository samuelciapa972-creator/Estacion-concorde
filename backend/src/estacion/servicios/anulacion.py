"""Servicio de anulación: de una factura aceptada a su nota crédito numerada y encolada para la DIAN.

`solicitar` hace en UNA transacción: bloquear la factura, numerar la nota (serie propia por ambiente: NCE en
producción, NCP en habilitación), generar el XML, guardar la nota `EN_PROCESO` y encolar el envío (`outbox`). La
nota es una fila de `factura` (`tipo = 'NOTA_CREDITO'`) que apunta a la factura anulada con `referencia_id`; el
worker la envía igual que a una factura. La venta sigue FACTURADO: lo que la saca de los reportes es que la nota
quede aceptada (`factura_anulada`).

Si algo falla antes del commit (factura no anulable, configuración incoherente), no se gasta ningún consecutivo.
Una nota RECHAZADA conserva su número: al volver a solicitar se corrige y se reenvía CON EL MISMO NÚMERO.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from psycopg import pq

from ..facturacion.modelos import EstadoFactura, SolicitudFactura, TipoTrabajo
from ..facturacion.xml_nota import ReferenciaFactura, construir_nota_credito
from ._bd import Conexion, ahora_local, auditar, encolar, exigir_autocommit
from .configuracion import ConfiguracionIncompleta, cargar_configuracion
from .emision import ClaveReutilizada, Origen, leer_cliente, leer_origen

PREFIJO_NOTA = {"1": "NCE", "2": "NCP"}
LARGO_MOTIVO = 500
_ESTADOS_VIVOS = ("EN_PROCESO", "INCIERTO", "FACTURADO")


def es_anulable(
    *, tipo: str, estado: str, venta_manual_id: int | None, ambiente: str, ambiente_actual: str,
    estado_nota: str | None,
) -> bool:  # fmt: skip
    """Las reglas de estado, tipo, origen, ambiente y nota viva de `solicitar`, sin bloquear nada. Sirve de pista
    para la pantalla (no mira el total de la venta): quien decide es `solicitar`."""
    return (
        tipo == "FACTURA"
        and estado == EstadoFactura.FACTURADO
        and venta_manual_id is not None
        and ambiente == ambiente_actual
        and estado_nota not in _ESTADOS_VIVOS
    )


class FacturaNoAnulable(Exception):
    """La factura no existe o no se puede anular ahora (el mensaje no lleva datos personales)."""


class FacturaNoEncontrada(FacturaNoAnulable):
    """No hay ninguna factura con ese id."""


@dataclass(frozen=True, slots=True)
class NotaRegistrada:
    id: int
    numero: str
    estado: EstadoFactura
    nueva: bool  # False = la misma clave ya tenía nota (doble clic): no se hizo nada


def asignar_consecutivo_nota(conn: Conexion, estacion_id: int, ambiente: str) -> tuple[str, int]:
    """Reserva el siguiente número de la serie de notas de la estación en `ambiente`: `(prefijo, consecutivo)`.

    Crea la fila de la serie si falta. Debe llamarse DENTRO de la transacción que guarda la nota. Falla cerrado si
    el prefijo guardado no es el del ambiente (la BD no lo impide): no gasta ningún número.
    """
    if conn.info.transaction_status != pq.TransactionStatus.INTRANS:
        raise RuntimeError("asignar_consecutivo_nota debe llamarse dentro de la transacción que guarda la nota")
    if ambiente not in PREFIJO_NOTA:
        raise ConfiguracionIncompleta(f"ambiente desconocido para numerar notas: {ambiente!r}")
    conn.execute(
        "INSERT INTO numeracion_nota (estacion_id, ambiente, prefijo) VALUES (%s, %s, %s) "
        "ON CONFLICT (estacion_id, ambiente) DO NOTHING",
        (estacion_id, ambiente, PREFIJO_NOTA[ambiente]),
    )
    id_, prefijo, siguiente = conn.execute(
        "SELECT id, prefijo, siguiente FROM numeracion_nota WHERE estacion_id = %s AND ambiente = %s FOR UPDATE",
        (estacion_id, ambiente),
    ).fetchone()  # type: ignore[misc]  # la fila existe: se acaba de insertar o ya estaba
    if prefijo != PREFIJO_NOTA[ambiente]:
        raise ConfiguracionIncompleta(
            f"la serie de notas del ambiente {ambiente} tiene el prefijo {prefijo}, y debe ser {PREFIJO_NOTA[ambiente]}"
        )
    conn.execute("UPDATE numeracion_nota SET siguiente = siguiente + 1 WHERE id = %s", (id_,))
    return prefijo, siguiente


class ServicioAnulacion:
    def __init__(
        self,
        *,
        entorno: Mapping[str, str] | None = None,
        reloj: Callable[[], datetime] = ahora_local,
    ):
        self._entorno = os.environ if entorno is None else entorno
        self._reloj = reloj

    @property
    def ambiente_actual(self) -> str:
        """El ambiente de la DIAN con el que está configurado el sistema ('1' producción, '2' habilitación)."""
        return self._entorno.get("DIAN_AMBIENTE", "").strip()

    def solicitar(self, conn: Conexion, *, clave: str, factura_id: int, usuario_id: int, motivo: str) -> NotaRegistrada:
        """Numera la nota crédito que anula `factura_id`, guarda su XML sin firmar y encola el envío.
        Idempotente por `clave`.

        Lanza (sin gastar consecutivo): ValueError (motivo vacío), FacturaNoAnulable, ClaveReutilizada,
        ClienteNoDisponible, ConfiguracionIncompleta, FacturaNoSoportada, ClienteInvalido.
        """
        exigir_autocommit(conn)
        motivo = motivo.strip()[:LARGO_MOTIVO].strip()
        if not motivo:
            raise ValueError("el motivo de la anulación es obligatorio")
        with conn.transaction():
            # 1. El bloqueo de la factura serializa a dos usuarios (o dos clics) sobre la misma anulación.
            f = conn.execute(
                """SELECT tipo, estado, venta_manual_id, cliente_id, forma_pago, medio_pago, ambiente, numero, cufe,
                          fecha_emision, total
                   FROM factura WHERE id = %s FOR UPDATE""",
                (factura_id,),
            ).fetchone()
            if f is None:
                raise FacturaNoEncontrada("la factura no existe")
            (
                tipo,
                estado,
                venta_manual_id,
                cliente_id,
                forma_pago,
                medio_pago,
                ambiente,
                numero,
                cufe,
                fecha_f,
                total_f,
            ) = f

            # 2. ¿Esta clave ya tiene nota? (doble clic, reintento del cliente tras un corte)
            previa = conn.execute(
                "SELECT id, referencia_id, numero, estado FROM factura WHERE clave_emision = %s", (clave,)
            ).fetchone()
            if previa is not None:
                if previa[1] != factura_id:
                    raise ClaveReutilizada("la clave de emisión ya se usó para otra operación")
                return NotaRegistrada(previa[0], previa[2], EstadoFactura(previa[3]), False)

            # 3. ¿Se puede anular? (solo facturas aceptadas, de venta manual, del ambiente actual)
            if tipo != "FACTURA":
                raise FacturaNoAnulable("solo se anulan facturas, no notas")
            if estado != EstadoFactura.FACTURADO:
                raise FacturaNoAnulable(f"la factura {numero} no está aceptada por la DIAN (estado {estado})")
            if venta_manual_id is None:
                raise FacturaNoAnulable(
                    f"la factura {numero} es de un despacho de surtidor: solo se anulan ventas manuales"
                )
            if ambiente != self.ambiente_actual:
                raise FacturaNoAnulable(f"la factura {numero} se emitió en otro ambiente de la DIAN")
            nota = conn.execute(
                "SELECT id, estado, prefijo, consecutivo FROM factura WHERE referencia_id = %s FOR UPDATE",
                (factura_id,),
            ).fetchone()
            if nota is not None and nota[1] in _ESTADOS_VIVOS:
                raise FacturaNoAnulable(f"la factura {numero} ya tiene una nota crédito ({nota[1]})")

            # 4. Configuración fiscal: la numeración activa de facturas fija emisor y software (y su ambiente).
            fecha = self._reloj().replace(microsecond=0)
            estacion = conn.execute("SELECT id FROM estacion WHERE activa").fetchone()
            if estacion is None:
                raise ConfiguracionIncompleta("no hay una estación (emisor) activa configurada")
            activa = conn.execute(
                "SELECT id FROM numeracion WHERE estacion_id = %s AND activa", (estacion[0],)
            ).fetchone()
            if activa is None:
                raise ConfiguracionIncompleta("la estación no tiene una resolución de numeración activa")
            config = cargar_configuracion(conn, activa[0], self._entorno)

            # 5. Número: el de una nota rechazada de esta factura, o uno nuevo de la serie del ambiente.
            if nota is not None:
                nota_id: int | None = nota[0]
                prefijo, consecutivo = nota[2], nota[3]
                if prefijo != PREFIJO_NOTA.get(config.software.ambiente):  # misma vigilancia que al numerar
                    raise ConfiguracionIncompleta(
                        f"la nota a reenviar tiene el prefijo {prefijo}, y en el ambiente "
                        f"{config.software.ambiente} debe ser {PREFIJO_NOTA.get(config.software.ambiente)}"
                    )
            else:
                nota_id = None
                prefijo, consecutivo = asignar_consecutivo_nota(conn, estacion[0], config.software.ambiente)

            # 6. XML: las líneas y el cliente de la venta anulada; la fecha y hora son las de la NOTA.
            datos = leer_origen(conn, Origen("venta_manual", venta_manual_id), bloquear=False)
            solicitud = SolicitudFactura(
                clave=clave,
                cliente=leer_cliente(conn, cliente_id, solo_activos=False),  # ya facturada: la baja no la frena
                lineas=(datos.linea,),
                forma_pago=forma_pago,
                medio_pago=medio_pago,
                fecha_emision=fecha,
            )
            if solicitud.total != total_f:  # anulación TOTAL: la nota vale lo mismo que la factura
                raise FacturaNoAnulable(f"el valor de la venta cambió desde que se facturó {numero}")
            xml = construir_nota_credito(
                solicitud,
                prefijo=prefijo,
                consecutivo=consecutivo,
                referencia=ReferenciaFactura(numero=numero, cufe=cufe, fecha_emision=fecha_f),
                motivo=motivo,
                emisor=config.emisor,
                software=config.software,
            )
            xml_texto = xml.xml.decode("utf-8")

            # 7. Guardar la nota y encolar el envío.
            if nota_id is None:
                nota_id = conn.execute(
                    """INSERT INTO factura (clave_emision, tipo, referencia_id, motivo, cliente_id, usuario_id,
                                            prefijo, consecutivo, forma_pago, medio_pago, total, fecha_emision,
                                            cufe, xml_sin_firmar, ambiente)
                       VALUES (%s, 'NOTA_CREDITO', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (clave, factura_id, motivo, cliente_id, usuario_id, prefijo, consecutivo, forma_pago,
                     medio_pago, total_f, fecha, xml.cufe, xml_texto, config.software.ambiente),
                ).fetchone()[0]  # type: ignore[index]  # fmt: skip
                accion = "ANULACION_SOLICITADA"
            else:
                conn.execute(
                    """UPDATE factura SET clave_emision = %s, estado = 'EN_PROCESO', motivo = %s, cliente_id = %s,
                              usuario_id = %s, forma_pago = %s, medio_pago = %s, total = %s, fecha_emision = %s,
                              cufe = %s, xml_sin_firmar = %s, ambiente = %s, xml_firmado = NULL,
                              respuesta_dian = NULL, application_response = NULL, dian_track_id = NULL,
                              error = NULL, actualizada_en = now()
                       WHERE id = %s""",
                    (clave, motivo, cliente_id, usuario_id, forma_pago, medio_pago, total_f, fecha,
                     xml.cufe, xml_texto, config.software.ambiente, nota_id),
                )  # fmt: skip
                accion = "NOTA_REENVIADA"
            numero_nota = f"{prefijo}{consecutivo}"
            encolar(conn, nota_id, TipoTrabajo.ENVIAR_DIAN)
            # El motivo puede llevar datos personales: a la auditoría solo va su largo.
            auditar(
                conn,
                accion,
                "factura",
                nota_id,
                usuario_id=usuario_id,
                detalle={"factura": numero, "nota": numero_nota, "motivo_largo": len(motivo)},
            )
            return NotaRegistrada(nota_id, numero_nota, EstadoFactura.EN_PROCESO, True)
