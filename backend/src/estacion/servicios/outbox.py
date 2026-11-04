"""Cola de envíos a la DIAN (tabla `outbox`), consumida por el worker con `FOR UPDATE SKIP LOCKED`.

Cada trabajo se procesa dentro de UNA transacción que mantiene bloqueada su fila mientras se llama al
proveedor. Si el worker muere a mitad, la transacción se deshace y el trabajo vuelve a quedar PENDIENTE: el
siguiente intento reenvía con la MISMA clave de emisión, y el proveedor es idempotente por clave.
(Mantener la transacción abierta durante la llamada es aceptable con una estación y un worker.)

Resultados de ENVIAR_DIAN:
    aceptada        -> factura FACTURADO, origen FACTURADO
    ProveedorRechazo-> factura RECHAZADO (con el error), origen PENDIENTE (se corrige y se reenvía)
    sin respuesta   -> factura INCIERTO, origen INCIERTO, y se encola CONSULTAR_DIAN
    EnvioPendiente  -> (set de pruebas) se guarda el track id, la factura sigue EN_PROCESO y se encola CONSULTAR_DIAN
    ProveedorNoConfigurado (certificado / firma del mensaje / reloj) -> la factura no cambia; reintento y auditoría
Una NOTA CRÉDITO (`factura.tipo = 'NOTA_CREDITO'`) recorre la misma cola, pero no tiene origen propio: sus líneas,
cliente y forma de pago son los de la factura que anula. Solo cuando la DIAN la ACEPTA, la venta de esa factura pasa a
NO_FACTURABLE y se audita `FACTURA_ANULADA`; rechazo, resultado incierto y consulta solo cambian la fila de la nota.
Bloqueos: el worker bloquea el trabajo y la nota, nunca la factura anulada (`ServicioAnulacion` bloquea factura y
luego nota), así que no hay orden cruzado.

Resultados de CONSULTAR_DIAN (por CUFE, o por el track id de un envío asíncrono):
    existe          -> FACTURADO
    rechazada       -> como un rechazo del envío
    en validación   -> reintento con espera creciente
    no existe       -> se sabe que NO se emitió: factura y origen EN_PROCESO, y se encola ENVIAR_DIAN
    sin respuesta   -> reintento con espera creciente; al agotar los intentos, FALLIDO (revisión humana)

Antes de emitir, el documento se FIRMA (`facturacion/firma.py`) y se guarda en `factura.xml_firmado` en su PROPIA
transacción, confirmada antes de hablar con la DIAN: si el proceso muere a mitad del envío, el reintento manda ese mismo
documento (no uno firmado de nuevo, con otra hora de firma). Sin certificado
(o vencido) NO se envía nada: el trabajo se reprograma con la misma política de reintentos (es configuración que se
puede corregir) y solo al agotarla queda FALLIDO. Al quedar FACTURADA se guarda la respuesta de la DIAN
(`application_response`) y se encola ENVIAR_CORREO, en la misma transacción.

Una factura de OTRO ambiente que el del worker (`factura.ambiente` vs `Recursos.ambiente`; p. ej. una de habilitación
que quedó en la cola al pasar a producción) NO se firma, ni se envía, ni se consulta, ni se ENTREGA por correo: el
trabajo queda FALLIDO con el motivo y se audita (`AMBIENTE_NO_COINCIDE`). No se reintenta: el ambiente de la factura
no cambia solo. La entrega también: un worker de habilitación firmaría el contenedor de un documento de producción con
su certificado de prueba, y uno de producción mandaría al cliente un documento de prueba. Aparte, un documento de
producción nunca se arma con un certificado de prueba (ENTREGA_NO_ARMADA), aunque el worker esté mal configurado.

Resultados de ENVIAR_CORREO (entrega al cliente, ver `facturacion/paquete.py`):
    primera vez     -> arma el ZIP (AttachedDocument firmado + PDF) y lo guarda en `entrega`; los reintentos y los
                       reenvíos mandan ESE MISMO archivo
    enviado         -> entrega ENVIADA
    CorreoTemporal  -> reintento con espera creciente; al agotarla, entrega FALLIDA
    CorreoRechazado -> entrega FALLIDA sin reintentar (se corrige el correo del cliente y se usa `reenviar`)
La factura sigue FACTURADA pase lo que pase con el correo: ante la DIAN ya es válida.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from lxml import etree
from psycopg.types.json import Jsonb

from ..entrega.correo import CorreoRechazado, CorreoTemporal, EnviadorCorreo, mensaje_factura
from ..facturacion import paquete
from ..facturacion.attached import AttachedInvalido
from ..facturacion.firma import Certificado, FirmaError, es_certificado_de_prueba, firmar_xml
from ..facturacion.modelos import (
    EnvioPendiente,
    EstadoEntrega,
    EstadoFactura,
    EstadoFacturacion,
    ResultadoEmision,
    SolicitudFactura,
    TipoTrabajo,
)
from ..facturacion.proveedor import ProveedorFacturacion, ProveedorNoConfigurado, ProveedorRechazo
from ..facturacion.representacion import RepresentacionInvalida, texto_validacion
from . import entrega
from ._bd import Conexion, ahora_local, auditar, encolar, exigir_autocommit
from .emision import Origen, leer_cliente, leer_origen

log = logging.getLogger(__name__)
_LARGO_ERROR = 500  # los errores se guardan recortados; nunca llevan secretos ni datos personales


@dataclass(frozen=True, slots=True)
class PoliticaReintentos:
    espera_inicial: timedelta = timedelta(seconds=30)
    espera_maxima: timedelta = timedelta(hours=1)
    max_intentos: int = 10

    def espera(self, intentos: int) -> timedelta:
        """Espera antes del intento `intentos + 1`: crece al doble hasta el máximo."""
        return min(self.espera_inicial * 2 ** max(intentos - 1, 0), self.espera_maxima)


@dataclass(frozen=True, slots=True)
class Recursos:
    """Lo que la cola necesita además del proveedor: el certificado de firma y cómo mandar correos.
    `certificado=None` (no configurado o inválido): nada se firma ni se envía; `error_certificado` dice por qué."""

    certificado: Certificado | None
    enviador: EnviadorCorreo
    error_certificado: str = ""
    ambiente: str = "2"  # DIAN_AMBIENTE del worker: solo se envían/consultan facturas de ESTE ambiente


@dataclass(frozen=True, slots=True)
class TrabajoProcesado:
    trabajo_id: int
    factura_id: int
    tipo: TipoTrabajo
    estado_factura: EstadoFactura


@dataclass(frozen=True, slots=True)
class _Factura:
    id: int
    estado: EstadoFactura
    clave: str
    origen: Origen | None  # None en una nota crédito: no tiene origen propio, el de la factura que anula se ve aparte
    cliente_id: int
    usuario_id: int
    numero: str
    cufe: str
    medio_pago: str
    fecha_emision: Any
    xml: str
    track_id: str | None  # ZipKey de un envío asíncrono (set de pruebas), o None
    ambiente: str  # "1" producción, "2" habilitación (el de la numeración con que se emitió)
    tipo: str  # 'FACTURA' o 'NOTA_CREDITO'
    referencia_id: int | None  # la factura que anula una nota; None en una factura


def procesar_siguiente(
    conn: Conexion,
    proveedor: ProveedorFacturacion,
    politica: PoliticaReintentos | None = None,
    *,
    recursos: Recursos,
) -> TrabajoProcesado | None:
    """Toma el siguiente trabajo vencido de la cola y lo procesa. None si no hay nada que hacer."""
    exigir_autocommit(conn)
    politica = politica or PoliticaReintentos()
    hecho, preparado = _procesar_uno(conn, proveedor, politica, recursos, None)
    if hecho is not None and preparado:
        # Lo que se va a mandar (el documento firmado, o el ZIP de la entrega) ya quedó guardado y confirmado. Ahora se
        # envía ESO, en otra transacción: si el envío falla o el proceso muere, el reintento manda lo mismo.
        segundo, _ = _procesar_uno(conn, proveedor, politica, recursos, hecho.trabajo_id)
        hecho = segundo or hecho
    return hecho


def _procesar_uno(
    conn: Conexion,
    proveedor: ProveedorFacturacion,
    politica: PoliticaReintentos,
    recursos: Recursos,
    solo_trabajo: int | None,
) -> tuple[TrabajoProcesado | None, bool]:
    """Procesa un trabajo vencido (o ESE trabajo). El bool dice si solo se PREPARÓ el envío (se firmó el documento o se
    armó la entrega) y falta mandarlo, en otra transacción."""
    with conn.transaction():
        fila = conn.execute(
            """SELECT id, factura_id, tipo, intentos FROM outbox
               WHERE estado = 'PENDIENTE' AND proximo_intento <= now() AND (%(id)s::bigint IS NULL OR id = %(id)s)
               ORDER BY proximo_intento, id
               LIMIT 1 FOR UPDATE SKIP LOCKED""",
            {"id": solo_trabajo},
        ).fetchone()
        if fila is None:
            return None, False
        trabajo_id, factura_id, tipo, intentos = fila
        intentos += 1
        conn.execute("UPDATE outbox SET intentos = %s WHERE id = %s", (intentos, trabajo_id))
        factura = _leer_factura(conn, factura_id)

        preparado = False
        try:
            with conn.transaction():  # punto de guardado: un error inesperado deshace SOLO este paso
                if _ambiente_ajeno(conn, trabajo_id, factura, recursos):
                    estado = factura.estado  # ni se firma, ni se envía, ni se consulta, ni se entrega: FALLIDO
                elif tipo == TipoTrabajo.ENVIAR_DIAN:
                    estado, preparado = _enviar(conn, proveedor, politica, trabajo_id, intentos, factura, recursos)
                elif tipo == TipoTrabajo.ENVIAR_CORREO:
                    estado, preparado = _entregar(conn, politica, trabajo_id, intentos, factura, recursos)
                else:
                    estado = _consultar(conn, proveedor, politica, trabajo_id, intentos, factura)
        except Exception as e:  # frontera de la cola: un trabajo roto no puede detener a los demás
            # Solo el tipo del error: su mensaje podría llevar datos personales (Ley 1581).
            log.error("trabajo %s (%s) de la factura %s: error inesperado %s", trabajo_id, tipo, factura_id,
                      type(e).__name__)  # fmt: skip
            _reprogramar(conn, politica, trabajo_id, intentos, f"error inesperado ({type(e).__name__}); se reintenta")
            auditar(conn, "TRABAJO_ERROR_INESPERADO", "factura", factura_id,
                    detalle={"tipo": tipo, "error": type(e).__name__})  # fmt: skip
            estado = factura.estado
        return TrabajoProcesado(trabajo_id, factura_id, TipoTrabajo(tipo), estado), preparado


def procesar_pendientes(
    conn: Conexion,
    proveedor: ProveedorFacturacion,
    politica: PoliticaReintentos | None = None,
    limite: int = 100,
    *,
    recursos: Recursos,
) -> list[TrabajoProcesado]:
    """Procesa trabajos vencidos hasta vaciar la cola (o llegar a `limite`)."""
    hechos: list[TrabajoProcesado] = []
    while (
        len(hechos) < limite and (hecho := procesar_siguiente(conn, proveedor, politica, recursos=recursos)) is not None
    ):
        hechos.append(hecho)
    return hechos


# ------------------------------------------------------------------------------------------ pasos
def _leer_factura(conn: Conexion, factura_id: int) -> _Factura:
    fila = conn.execute(
        """SELECT id, estado, clave_emision, despacho_id, venta_manual_id, cliente_id, usuario_id, numero, cufe,
                  medio_pago, fecha_emision, xml_sin_firmar, dian_track_id, ambiente, tipo, referencia_id
           FROM factura WHERE id = %s FOR UPDATE""",
        (factura_id,),
    ).fetchone()
    assert fila is not None, "la FK de outbox garantiza que la factura existe"
    fid, estado, clave, despacho_id, venta_id, cliente_id, usuario_id, numero, cufe, medio, fecha, xml, track, amb = (
        fila[:14]
    )
    tipo, referencia_id = fila[14:]
    origen = None  # la nota no tiene origen propio
    if tipo != "NOTA_CREDITO":
        origen = Origen("despacho", despacho_id) if despacho_id is not None else Origen("venta_manual", venta_id)
    return _Factura(
        fid, EstadoFactura(estado), clave, origen, cliente_id, usuario_id, numero, cufe, medio, fecha, xml, track, amb,
        tipo, referencia_id,
    )  # fmt: skip


def _origen_de_la_factura(conn: Conexion, factura_id: int) -> tuple[Origen, int]:
    """(origen, cliente) de la factura que una nota anula. Sin bloquearla: `ServicioAnulacion` ya la bloqueó
    (factura y luego nota) y aquí solo se lee; así el worker no puede cruzarse con él en un deadlock."""
    despacho_id, venta_id, cliente_id = conn.execute(
        "SELECT despacho_id, venta_manual_id, cliente_id FROM factura WHERE id = %s", (factura_id,)
    ).fetchone()  # type: ignore[misc]  # la FK de referencia_id garantiza que existe
    origen = Origen("despacho", despacho_id) if despacho_id is not None else Origen("venta_manual", venta_id)
    return origen, cliente_id


def _solicitud(conn: Conexion, f: _Factura, documento: bytes) -> SolicitudFactura:
    if f.origen is not None:
        origen, cliente_id = f.origen, f.cliente_id
    else:  # nota crédito: las líneas, el cliente y la forma de pago son los de la factura que anula
        assert f.referencia_id is not None, "una nota crédito siempre referencia una factura"
        origen, cliente_id = _origen_de_la_factura(conn, f.referencia_id)
    datos = leer_origen(conn, origen, bloquear=False)
    return SolicitudFactura(
        clave=f.clave,
        cliente=leer_cliente(conn, cliente_id, solo_activos=False),  # ya numerada: se envía aunque se diera de baja
        lineas=(datos.linea,),
        forma_pago=datos.forma_pago,
        medio_pago=f.medio_pago,
        fecha_emision=f.fecha_emision,
        numero=f.numero,
        cufe=f.cufe,
        documento=documento,  # el XML FIRMADO
        tipo_documento="nc" if f.tipo == "NOTA_CREDITO" else "fv",
    )


def _ambiente_ajeno(conn: Conexion, trabajo_id: int, f: _Factura, recursos: Recursos) -> bool:
    """True si la factura es de otro ambiente DIAN que el del worker (p. ej. una de habilitación quedó en la cola y se
    pasó a producción): se cierra el trabajo en FALLIDO y no se firma ni se habla con la DIAN. No se reintenta: el
    ambiente de la factura no cambia solo; lo resuelve una persona (con el ambiente correcto o descartándola)."""
    if f.ambiente == recursos.ambiente:
        return False
    _terminar(
        conn, trabajo_id, "FALLIDO",
        error=f"la factura es del ambiente {f.ambiente} y el worker está en ambiente {recursos.ambiente}: no se envió",
    )  # fmt: skip
    detalle = {"numero": f.numero, "ambiente_factura": f.ambiente, "ambiente_worker": recursos.ambiente}
    auditar(conn, "AMBIENTE_NO_COINCIDE", "factura", f.id, detalle=detalle)
    return True


def _firmar(
    conn: Conexion, politica: PoliticaReintentos, trabajo_id: int, intentos: int, f: _Factura, recursos: Recursos
) -> tuple[bytes | None, bool]:
    """(XML firmado, True si se acaba de firmar). None si no se pudo: el trabajo queda reprogramado o FALLIDO.

    Un reintento reenvía el MISMO documento ya firmado: si la DIAN validó el primer envío (y se perdió la respuesta),
    lo que se guarda y se entrega es lo que ella vio. Un reenvío tras un rechazo lo limpia (`emision.solicitar`)."""
    previo = conn.execute("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,)).fetchone()
    if previo is not None and previo[0]:
        return str(previo[0]).encode("utf-8"), False
    error = recursos.error_certificado or "no hay certificado de firma"
    firmado: bytes | None = None
    if recursos.certificado is not None:
        try:
            firmado = firmar_xml(f.xml.encode("utf-8"), recursos.certificado)
        except FirmaError as e:  # vencido, documento dañado: el mensaje nunca lleva la clave
            error = str(e)
    if firmado is None:
        _reprogramar(
            conn, politica, trabajo_id, intentos,
            f"no se puede firmar: {error}. Configure DIAN_CERTIFICADO_RUTA y DIAN_CERTIFICADO_CLAVE, "
            "o genere uno de prueba con `python -m estacion.certificado`",
        )  # fmt: skip
        auditar(conn, "FIRMA_NO_DISPONIBLE", "factura", f.id, detalle={"numero": f.numero})
        return None, False
    conn.execute("UPDATE factura SET xml_firmado = %s WHERE id = %s", (firmado.decode("utf-8"), f.id))
    return firmado, True


def _enviar(
    conn: Conexion,
    proveedor: ProveedorFacturacion,
    politica: PoliticaReintentos,
    trabajo_id: int,
    intentos: int,
    f: _Factura,
    recursos: Recursos,
) -> tuple[EstadoFactura, bool]:
    """(estado de la factura, True si en este paso solo se FIRMÓ: el envío va en otra transacción)."""
    firmado, recien_firmado = _firmar(conn, politica, trabajo_id, intentos, f, recursos)
    if firmado is None:
        return f.estado, False  # NUNCA se envía un documento sin firma
    if recien_firmado:
        # La firma se confirma ANTES de hablar con la DIAN (ver el docstring del módulo). Firmar no cuenta como intento.
        conn.execute("UPDATE outbox SET intentos = intentos - 1 WHERE id = %s", (trabajo_id,))
        return f.estado, True
    return _enviar_firmado(conn, proveedor, trabajo_id, f, firmado, politica, intentos), False


def _enviar_firmado(
    conn: Conexion,
    proveedor: ProveedorFacturacion,
    trabajo_id: int,
    f: _Factura,
    firmado: bytes,
    politica: PoliticaReintentos,
    intentos: int,
) -> EstadoFactura:
    try:
        solicitud = _solicitud(conn, f, firmado)
    except Exception as e:  # error NUESTRO (datos que cambiaron): no se envía nada; lo revisa una persona
        _terminar(conn, trabajo_id, "FALLIDO", error=f"no se pudo armar el envío: {type(e).__name__}: {e}")
        auditar(conn, "ENVIO_NO_ARMADO", "factura", f.id, detalle={"numero": f.numero})
        return f.estado
    try:
        resultado = proveedor.emitir(solicitud)
    except ProveedorRechazo as e:
        return _rechazada(conn, trabajo_id, f, e)
    except ProveedorNoConfigurado as e:
        return _no_configurado(conn, politica, trabajo_id, intentos, f, e)
    except Exception as e:  # timeout, red, error inesperado: NO se sabe si quedó emitida
        _incierta(conn, trabajo_id, f, e)
        return EstadoFactura.INCIERTO
    if isinstance(resultado, EnvioPendiente):
        conn.execute(
            "UPDATE factura SET dian_track_id = %s, actualizada_en = now() WHERE id = %s", (resultado.track_id, f.id)
        )
        _terminar(conn, trabajo_id, "HECHO")
        encolar(conn, f.id, TipoTrabajo.CONSULTAR_DIAN)
        auditar(conn, "FACTURA_ENVIADA_EN_VALIDACION", "factura", f.id, detalle={"numero": f.numero})
        return EstadoFactura.EN_PROCESO
    return _aceptada(conn, trabajo_id, f, resultado)


def _consultar(
    conn: Conexion,
    proveedor: ProveedorFacturacion,
    politica: PoliticaReintentos,
    trabajo_id: int,
    intentos: int,
    f: _Factura,
) -> EstadoFactura:
    try:
        resultado = proveedor.consultar(clave=f.clave, numero=f.numero, cufe=f.cufe, track_id=f.track_id)
    except ProveedorRechazo as e:
        return _rechazada(conn, trabajo_id, f, e)
    except ProveedorNoConfigurado as e:
        return _no_configurado(conn, politica, trabajo_id, intentos, f, e)
    except Exception as e:
        if _reprogramar(conn, politica, trabajo_id, intentos, f"consulta sin respuesta: {type(e).__name__}: {e}"):
            auditar(conn, "CONCILIACION_FALLIDA", "factura", f.id, detalle={"numero": f.numero, "intentos": intentos})
        return f.estado
    if isinstance(resultado, EnvioPendiente):
        if _reprogramar(conn, politica, trabajo_id, intentos, "la DIAN sigue validando el envío"):
            auditar(conn, "VALIDACION_SIN_RESULTADO", "factura", f.id, detalle={"numero": f.numero})
        return f.estado
    if resultado is not None:
        return _aceptada(conn, trabajo_id, f, resultado)
    # Se sabe que NO quedó emitida: se reenvía la misma factura (mismo número, misma clave). Un envío que falla
    # siempre igual no puede rebotar ENVIAR <-> CONSULTAR sin fin: el primer reenvío es inmediato, los siguientes
    # esperan según la política y, al agotarla, queda para revisión humana.
    reenvios = _reenvios(conn, f.id)
    if reenvios + 1 >= politica.max_intentos:
        _terminar(conn, trabajo_id, "FALLIDO", error=f"la DIAN no tiene la factura tras {reenvios + 1} envíos")
        auditar(conn, "REENVIOS_AGOTADOS", "factura", f.id, detalle={"numero": f.numero, "envios": reenvios + 1})
        return f.estado
    _marcar(conn, f, EstadoFactura.EN_PROCESO, EstadoFacturacion.EN_PROCESO)
    _terminar(conn, trabajo_id, "HECHO")
    encolar(conn, f.id, TipoTrabajo.ENVIAR_DIAN, espera=politica.espera(reenvios) if reenvios else None)
    auditar(conn, "FACTURA_NO_EMITIDA_REENVIO", "factura", f.id, detalle={"numero": f.numero})
    return EstadoFactura.EN_PROCESO


def _reenvios(conn: Conexion, factura_id: int) -> int:
    """Reenvíos por "no existe" desde la última solicitud de esta factura (la auditoría es el registro)."""
    fila = conn.execute(
        """SELECT count(*) FROM auditoria a
           WHERE a.entidad = 'factura' AND a.entidad_id = %(id)s AND a.accion = 'FACTURA_NO_EMITIDA_REENVIO'
             AND a.id > coalesce((SELECT max(b.id) FROM auditoria b
                                  WHERE b.entidad = 'factura' AND b.entidad_id = %(id)s
                                    AND b.accion IN ('FACTURA_SOLICITADA', 'FACTURA_REENVIADA')), 0)""",
        {"id": str(factura_id)},
    ).fetchone()
    return int(fila[0]) if fila else 0


def _aceptada(conn: Conexion, trabajo_id: int, f: _Factura, resultado: ResultadoEmision) -> EstadoFactura:
    if resultado.cufe != f.cufe:
        # El proveedor dice haber emitido OTRO documento con esta clave: no se adivina, lo revisa una persona.
        _terminar(conn, trabajo_id, "FALLIDO", error="el proveedor devolvió un CUFE distinto al de la factura")
        auditar(conn, "FACTURA_CUFE_DISTINTO", "factura", f.id, detalle={"numero": f.numero})
        return f.estado
    _marcar(
        conn,
        f,
        EstadoFactura.FACTURADO,
        EstadoFacturacion.FACTURADO,
        respuesta={"estado": resultado.estado, "emitida_en": resultado.emitida_en.isoformat(), **resultado.detalle},
    )
    if resultado.application_response is not None:
        conn.execute(
            "UPDATE factura SET application_response = %s WHERE id = %s",
            (resultado.application_response.decode("utf-8"), f.id),
        )
    _terminar(conn, trabajo_id, "HECHO")
    auditar(conn, "FACTURA_EMITIDA", "factura", f.id, detalle={"numero": f.numero})
    if f.referencia_id is not None:  # nota aceptada: la factura queda anulada y su venta, cerrada
        _anular_venta(conn, f)
    # Ya es válida ante la DIAN: ahora se le entrega al cliente (en la misma transacción: no se pierde).
    encolar(conn, f.id, TipoTrabajo.ENVIAR_CORREO)
    return EstadoFactura.FACTURADO


def _anular_venta(conn: Conexion, f: _Factura) -> None:
    """Una nota crédito aceptada anula la factura que referencia: su venta pasa a NO_FACTURABLE (no se vuelve a
    facturar) y queda auditado en la factura. Solo la nota ACEPTADA lo hace: rechazo, incierto y consulta la dejan."""
    assert f.referencia_id is not None
    origen, _ = _origen_de_la_factura(conn, f.referencia_id)
    conn.execute(
        f"UPDATE {origen.tipo} SET estado_facturacion = %s WHERE id = %s", (EstadoFacturacion.NO_FACTURABLE, origen.id)
    )
    auditar(conn, "FACTURA_ANULADA", "factura", f.referencia_id, detalle={"nota": f.numero})


# ------------------------------------------------------------------------------------------ entrega al cliente
# Errores de los DATOS (no se arreglan solos): la entrega queda FALLIDA para revisión y `reenviar`.
_ERRORES_DE_ARMADO = (paquete.PaqueteDemasiadoGrande, AttachedInvalido, RepresentacionInvalida, ValueError)


@dataclass(frozen=True, slots=True)
class _NoArmada:
    motivo: str
    recuperable: bool  # True: configuración (certificado) que se puede corregir -> se reintenta


_NS_LEER = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}


def _armar_entrega(conn: Conexion, f: _Factura, recursos: Recursos) -> entrega.EntregaGuardada | _NoArmada:
    """Arma el ZIP y lo guarda en `entrega`, o dice por qué no se pudo."""
    fila = conn.execute(
        "SELECT xml_firmado, application_response, respuesta_dian FROM factura WHERE id = %s", (f.id,)
    ).fetchone()
    assert fila is not None
    firmado, respuesta, respuesta_dian = fila
    if not firmado or not respuesta:
        return _NoArmada("falta el XML firmado o la respuesta de validación de la DIAN", recuperable=False)
    if recursos.certificado is None:
        motivo = recursos.error_certificado or "no hay certificado de firma"
        return _NoArmada(f"no se puede firmar el contenedor: {motivo}", recuperable=True)
    if f.ambiente == "1" and es_certificado_de_prueba(recursos.certificado):  # antes de gastar consecutivo
        return _NoArmada(
            "en producción no se firma el contenedor con un certificado de prueba: configure el de la empresa y "
            "use reenviar",
            recuperable=False,
        )
    ahora = ahora_local().replace(microsecond=0)
    consecutivo = entrega.reservar_consecutivo(conn, ahora.year)
    try:
        p = paquete.armar(
            firmado.encode("utf-8"), respuesta.encode("utf-8"), consecutivo=consecutivo, ahora=ahora,
            certificado=recursos.certificado, validacion=texto_validacion(respuesta_dian, respuesta),
        )  # fmt: skip
    except FirmaError as e:  # certificado vencido o inválido: configuración, se reintenta (el mensaje no lleva clave)
        return _NoArmada(f"no se puede firmar el contenedor: {e}", recuperable=True)
    except _ERRORES_DE_ARMADO as e:
        return _NoArmada(f"no se pudo armar el ZIP: {type(e).__name__}: {e}", recuperable=False)
    return entrega.guardar(conn, f.id, ahora.year, consecutivo, p.nombre_zip, p.zip)


def _entregar(
    conn: Conexion, politica: PoliticaReintentos, trabajo_id: int, intentos: int, f: _Factura, recursos: Recursos
) -> tuple[EstadoFactura, bool]:
    """(estado de la factura, True si en este paso solo se ARMÓ la entrega: el envío va en otra transacción)."""
    guardada = entrega.obtener(conn, f.id)
    if guardada is None:
        armada = _armar_entrega(conn, f, recursos)
        if isinstance(armada, _NoArmada):
            if armada.recuperable:
                _reprogramar(conn, politica, trabajo_id, intentos, armada.motivo)
                auditar(conn, "FIRMA_NO_DISPONIBLE", "factura", f.id, detalle={"numero": f.numero})
            else:  # error de los datos, no del correo: lo revisa una persona (y puede usar `reenviar`)
                _terminar(conn, trabajo_id, "FALLIDO", error=_recortar(armada.motivo))
                auditar(conn, "ENTREGA_NO_ARMADA", "factura", f.id, detalle={"numero": f.numero})
            return f.estado, False
        # Armar no cuenta como intento de envío.
        conn.execute("UPDATE outbox SET intentos = intentos - 1 WHERE id = %s", (trabajo_id,))
        return f.estado, True

    firmado = conn.execute("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,)).fetchone()[0]  # type: ignore[index]
    raiz = etree.fromstring(firmado.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
    remitente = raiz.findtext(
        "cac:AccountingSupplierParty/cac:Party/cac:Contact/cbc:ElectronicMail", namespaces=_NS_LEER
    )
    mensaje = mensaje_factura(
        remitente=remitente or "",
        destinatario=leer_cliente(conn, f.cliente_id, solo_activos=False).email,  # el correo ACTUAL del cliente
        asunto=paquete.asunto(firmado.encode("utf-8")),
        nombre_zip=guardada.nombre_zip,
        zip=guardada.zip,
        prueba=raiz.findtext("cbc:ProfileExecutionID", namespaces=_NS_LEER) != "1",
        tipo_documento="nc" if f.tipo == "NOTA_CREDITO" else "fv",
    )
    try:
        recursos.enviador.enviar(mensaje)
    except CorreoTemporal as e:
        agotado = _reprogramar(
            conn, politica, trabajo_id, intentos,
            f"correo sin enviar tras {intentos} intentos: {e}" if intentos >= politica.max_intentos else str(e),
        )  # fmt: skip
        entrega.marcar(
            conn, f.id, EstadoEntrega.FALLIDA if agotado else EstadoEntrega.PENDIENTE, error=_recortar(str(e))
        )
        if agotado:
            auditar(conn, "ENTREGA_FALLIDA", "factura", f.id, detalle={"numero": f.numero, "intentos": intentos})
        return f.estado, False
    except CorreoRechazado as e:
        entrega.marcar(conn, f.id, EstadoEntrega.FALLIDA, error=_recortar(str(e)))
        _terminar(conn, trabajo_id, "FALLIDO", error=_recortar(str(e)))
        auditar(conn, "ENTREGA_RECHAZADA", "factura", f.id, detalle={"numero": f.numero})
        return f.estado, False
    entrega.marcar(conn, f.id, EstadoEntrega.ENVIADA)
    _terminar(conn, trabajo_id, "HECHO")
    auditar(conn, "FACTURA_ENTREGADA", "factura", f.id, detalle={"numero": f.numero, "zip": guardada.nombre_zip})
    return f.estado, False


def _reprogramar(conn: Conexion, politica: PoliticaReintentos, trabajo_id: int, intentos: int, error: str) -> bool:
    """Error pasajero o corregible: reintento con espera creciente; al agotar los intentos, FALLIDO.
    Devuelve True si se agotaron (el llamador audita o marca lo suyo)."""
    if intentos >= politica.max_intentos:
        _terminar(conn, trabajo_id, "FALLIDO", error=_recortar(error))
        return True
    conn.execute(
        "UPDATE outbox SET proximo_intento = now() + %s, ultimo_error = %s WHERE id = %s",
        (politica.espera(intentos), _recortar(error), trabajo_id),
    )
    return False


def _rechazada(conn: Conexion, trabajo_id: int, f: _Factura, e: ProveedorRechazo) -> EstadoFactura:
    _marcar(conn, f, EstadoFactura.RECHAZADO, EstadoFacturacion.PENDIENTE, error=str(e))
    _terminar(conn, trabajo_id, "HECHO", error=f"rechazo: {e}")
    auditar(conn, "FACTURA_RECHAZADA", "factura", f.id, detalle={"numero": f.numero})
    return EstadoFactura.RECHAZADO


def _no_configurado(
    conn: Conexion, politica: PoliticaReintentos, trabajo_id: int, intentos: int, f: _Factura, e: Exception
) -> EstadoFactura:
    """Certificado o firma del mensaje no aceptados: la factura no cambia; se reintenta (es configuración)."""
    _reprogramar(conn, politica, trabajo_id, intentos, f"la DIAN no aceptó la seguridad del mensaje: {e}")
    auditar(conn, "DIAN_SEGURIDAD", "factura", f.id, detalle={"numero": f.numero, "intentos": intentos})
    return f.estado


def _incierta(conn: Conexion, trabajo_id: int, f: _Factura, e: Exception) -> None:
    error = _recortar(f"sin respuesta del proveedor: {type(e).__name__}: {e}")
    _marcar(conn, f, EstadoFactura.INCIERTO, EstadoFacturacion.INCIERTO, error=error)
    _terminar(conn, trabajo_id, "HECHO", error=error)
    encolar(conn, f.id, TipoTrabajo.CONSULTAR_DIAN)
    auditar(conn, "FACTURA_INCIERTA", "factura", f.id, detalle={"numero": f.numero})


def _marcar(
    conn: Conexion,
    f: _Factura,
    estado: EstadoFactura,
    estado_origen: EstadoFacturacion,
    *,
    error: str | None = None,
    respuesta: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """UPDATE factura SET estado = %s, error = %s, respuesta_dian = coalesce(%s, respuesta_dian),
                  actualizada_en = now()
           WHERE id = %s""",
        (estado, None if error is None else _recortar(error), None if respuesta is None else Jsonb(respuesta), f.id),
    )
    if f.origen is not None:  # una nota no tiene origen: el de la factura anulada solo cambia al aceptarse
        conn.execute(
            f"UPDATE {f.origen.tipo} SET estado_facturacion = %s WHERE id = %s",
            (estado_origen, f.origen.id),
        )


def _terminar(conn: Conexion, trabajo_id: int, estado: str, *, error: str | None = None) -> None:
    conn.execute(
        "UPDATE outbox SET estado = %s, terminado_en = now(), ultimo_error = %s WHERE id = %s",
        (estado, None if error is None else _recortar(error), trabajo_id),
    )


def _recortar(texto: str) -> str:
    return texto if len(texto) <= _LARGO_ERROR else texto[: _LARGO_ERROR - 1] + "…"
