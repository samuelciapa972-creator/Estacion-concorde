"""Cola de envíos a la DIAN (tabla `outbox`), consumida por el worker con `FOR UPDATE SKIP LOCKED`.

Cada trabajo se procesa dentro de UNA transacción que mantiene bloqueada su fila mientras se llama al
proveedor. Si el worker muere a mitad, la transacción se deshace y el trabajo vuelve a quedar PENDIENTE: el
siguiente intento reenvía con la MISMA clave de emisión, y el proveedor es idempotente por clave.
(Mantener la transacción abierta durante la llamada es aceptable con una estación y un worker.)

Resultados de ENVIAR_DIAN:
    aceptada        -> factura FACTURADO, origen FACTURADO
    ProveedorRechazo-> factura RECHAZADO (con el error), origen PENDIENTE (se corrige y se reenvía)
    sin respuesta   -> factura INCIERTO, origen INCIERTO, y se encola CONSULTAR_DIAN
Resultados de CONSULTAR_DIAN (conciliar por la clave):
    existe          -> FACTURADO
    no existe       -> se sabe que NO se emitió: factura y origen EN_PROCESO, y se encola ENVIAR_DIAN
    sin respuesta   -> reintento con espera creciente; al agotar los intentos, FALLIDO (revisión humana)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from psycopg.types.json import Jsonb

from ..facturacion.modelos import EstadoFactura, EstadoFacturacion, ResultadoEmision, SolicitudFactura, TipoTrabajo
from ..facturacion.proveedor import ProveedorFacturacion, ProveedorRechazo
from ._bd import Conexion, auditar, exigir_autocommit
from .emision import Origen, leer_cliente, leer_origen

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
    origen: Origen
    cliente_id: int
    usuario_id: int
    numero: str
    cufe: str
    medio_pago: str
    fecha_emision: Any
    xml: str


def procesar_siguiente(
    conn: Conexion, proveedor: ProveedorFacturacion, politica: PoliticaReintentos | None = None
) -> TrabajoProcesado | None:
    """Toma el siguiente trabajo vencido de la cola y lo procesa. None si no hay nada que hacer."""
    exigir_autocommit(conn)
    politica = politica or PoliticaReintentos()
    with conn.transaction():
        fila = conn.execute(
            """SELECT id, factura_id, tipo, intentos FROM outbox
               WHERE estado = 'PENDIENTE' AND proximo_intento <= now()
               ORDER BY proximo_intento, id
               LIMIT 1 FOR UPDATE SKIP LOCKED"""
        ).fetchone()
        if fila is None:
            return None
        trabajo_id, factura_id, tipo, intentos = fila
        intentos += 1
        conn.execute("UPDATE outbox SET intentos = %s WHERE id = %s", (intentos, trabajo_id))
        factura = _leer_factura(conn, factura_id)

        if tipo == TipoTrabajo.ENVIAR_DIAN:
            estado = _enviar(conn, proveedor, trabajo_id, factura)
        else:
            estado = _consultar(conn, proveedor, politica, trabajo_id, intentos, factura)
        return TrabajoProcesado(trabajo_id, factura_id, TipoTrabajo(tipo), estado)


def procesar_pendientes(
    conn: Conexion, proveedor: ProveedorFacturacion, politica: PoliticaReintentos | None = None, limite: int = 100
) -> list[TrabajoProcesado]:
    """Procesa trabajos vencidos hasta vaciar la cola (o llegar a `limite`)."""
    hechos: list[TrabajoProcesado] = []
    while len(hechos) < limite and (hecho := procesar_siguiente(conn, proveedor, politica)) is not None:
        hechos.append(hecho)
    return hechos


# ------------------------------------------------------------------------------------------ pasos
def _leer_factura(conn: Conexion, factura_id: int) -> _Factura:
    fila = conn.execute(
        """SELECT id, estado, clave_emision, despacho_id, venta_manual_id, cliente_id, usuario_id, numero, cufe,
                  medio_pago, fecha_emision, xml_sin_firmar
           FROM factura WHERE id = %s FOR UPDATE""",
        (factura_id,),
    ).fetchone()
    assert fila is not None, "la FK de outbox garantiza que la factura existe"
    fid, estado, clave, despacho_id, venta_id, cliente_id, usuario_id, numero, cufe, medio, fecha, xml = fila
    origen = Origen("despacho", despacho_id) if despacho_id is not None else Origen("venta_manual", venta_id)
    return _Factura(fid, EstadoFactura(estado), clave, origen, cliente_id, usuario_id, numero, cufe, medio, fecha, xml)


def _solicitud(conn: Conexion, f: _Factura) -> SolicitudFactura:
    datos = leer_origen(conn, f.origen, bloquear=False)
    return SolicitudFactura(
        clave=f.clave,
        cliente=leer_cliente(conn, f.cliente_id),
        lineas=(datos.linea,),
        forma_pago=datos.forma_pago,
        medio_pago=f.medio_pago,
        fecha_emision=f.fecha_emision,
        numero=f.numero,
        cufe=f.cufe,
        # Fase 6: aquí irá el XML FIRMADO. Hoy solo existe ProveedorSimulado, que no lo valida.
        documento=f.xml.encode("utf-8"),
    )


def _enviar(conn: Conexion, proveedor: ProveedorFacturacion, trabajo_id: int, f: _Factura) -> EstadoFactura:
    try:
        solicitud = _solicitud(conn, f)
    except Exception as e:  # error NUESTRO (datos que cambiaron): no se envía nada; lo revisa una persona
        _terminar(conn, trabajo_id, "FALLIDO", error=f"no se pudo armar el envío: {type(e).__name__}: {e}")
        auditar(conn, "ENVIO_NO_ARMADO", "factura", f.id, detalle={"numero": f.numero})
        return f.estado
    try:
        resultado = proveedor.emitir(solicitud)
    except ProveedorRechazo as e:
        _marcar(conn, f, EstadoFactura.RECHAZADO, EstadoFacturacion.PENDIENTE, error=str(e))
        _terminar(conn, trabajo_id, "HECHO", error=f"rechazo: {e}")
        auditar(conn, "FACTURA_RECHAZADA", "factura", f.id, detalle={"numero": f.numero})
        return EstadoFactura.RECHAZADO
    except Exception as e:  # timeout, red, error inesperado: NO se sabe si quedó emitida
        _incierta(conn, trabajo_id, f, e)
        return EstadoFactura.INCIERTO
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
        resultado = proveedor.buscar_por_clave(f.clave)
    except Exception as e:
        error = _recortar(f"consulta sin respuesta: {type(e).__name__}: {e}")
        if intentos >= politica.max_intentos:
            _terminar(conn, trabajo_id, "FALLIDO", error=error)
            auditar(conn, "CONCILIACION_FALLIDA", "factura", f.id, detalle={"numero": f.numero, "intentos": intentos})
        else:
            conn.execute(
                "UPDATE outbox SET proximo_intento = now() + %s, ultimo_error = %s WHERE id = %s",
                (politica.espera(intentos), error, trabajo_id),
            )
        return EstadoFactura.INCIERTO
    if resultado is not None:
        return _aceptada(conn, trabajo_id, f, resultado)
    # Se sabe que NO quedó emitida: se reenvía la misma factura (mismo número, misma clave).
    _marcar(conn, f, EstadoFactura.EN_PROCESO, EstadoFacturacion.EN_PROCESO)
    _terminar(conn, trabajo_id, "HECHO")
    conn.execute("INSERT INTO outbox (factura_id, tipo) VALUES (%s, %s)", (f.id, TipoTrabajo.ENVIAR_DIAN))
    auditar(conn, "FACTURA_NO_EMITIDA_REENVIO", "factura", f.id, detalle={"numero": f.numero})
    return EstadoFactura.EN_PROCESO


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
    _terminar(conn, trabajo_id, "HECHO")
    auditar(conn, "FACTURA_EMITIDA", "factura", f.id, detalle={"numero": f.numero})
    return EstadoFactura.FACTURADO


def _incierta(conn: Conexion, trabajo_id: int, f: _Factura, e: Exception) -> None:
    error = _recortar(f"sin respuesta del proveedor: {type(e).__name__}: {e}")
    _marcar(conn, f, EstadoFactura.INCIERTO, EstadoFacturacion.INCIERTO, error=error)
    _terminar(conn, trabajo_id, "HECHO", error=error)
    conn.execute("INSERT INTO outbox (factura_id, tipo) VALUES (%s, %s)", (f.id, TipoTrabajo.CONSULTAR_DIAN))
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
