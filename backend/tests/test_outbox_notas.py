"""La cola (`servicios/outbox.py`) con notas crédito: firma, envía, concilia y cierra la venta anulada.

Nada sale hacia la DIAN: `ProveedorSimulado`. También se comprueba la entrega
de la nota por correo (ZIP con el AttachedDocument de CreditNote y su PDF).
"""

from __future__ import annotations

import dataclasses
import io
import uuid
import zipfile
from datetime import datetime

import pytest

from conftest import ENTORNO_DIAN, Escenario
from estacion.facturacion.modelos import EstadoFactura, SolicitudFactura, TipoTrabajo
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.anulacion import ServicioAnulacion
from estacion.servicios.emision import Origen, ServicioEmision
from estacion.servicios.outbox import procesar_pendientes

pytestmark = pytest.mark.bd

AHORA = datetime(2026, 9, 30, 10, 5)
AHORA_NOTA = datetime(2026, 10, 1, 8, 30, 15)
MOTIVO = "Error en los datos del cliente"


def facturar(e: Escenario, conn, recursos) -> tuple[int, Origen]:
    """Una venta manual facturada y ACEPTADA (la factura queda FACTURADO)."""
    origen = Origen("venta_manual", e.datos.venta_manual(e.usuario))
    f = ServicioEmision(entorno=ENTORNO_DIAN, reloj=lambda: AHORA).solicitar(
        conn, clave=uuid.uuid4().hex, origen=origen, cliente_id=e.cliente, usuario_id=e.usuario, medio_pago="EFECTIVO"
    )
    procesar_pendientes(conn, ProveedorSimulado(), recursos=recursos)
    return f.id, origen


def anular(e: Escenario, conn, factura_id: int):
    return ServicioAnulacion(entorno=ENTORNO_DIAN, reloj=lambda: AHORA_NOTA).solicitar(
        conn, clave=uuid.uuid4().hex, factura_id=factura_id, usuario_id=e.usuario, motivo=MOTIVO
    )


def estado_factura(e: Escenario, factura_id: int) -> str:
    return e.datos.uno("SELECT estado FROM factura WHERE id = %s", (factura_id,))


def estado_venta(e: Escenario, origen: Origen) -> str:
    return e.datos.uno(f"SELECT estado_facturacion FROM {origen.tipo} WHERE id = %s", (origen.id,))


def acciones(e: Escenario, factura_id: int) -> list[str]:
    filas = e.datos.conn.execute(
        "SELECT accion FROM auditoria WHERE entidad = 'factura' AND entidad_id = %s ORDER BY id", (str(factura_id),)
    ).fetchall()
    return [a for (a,) in filas]


def test_nota_aceptada_anula_y_cierra_la_venta(escenario, conn, recursos):
    factura_id, origen = facturar(escenario, conn, recursos)
    nota = anular(escenario, conn, factura_id)
    recibidas: list[SolicitudFactura] = []

    class Espia(ProveedorSimulado):
        def emitir(self, solicitud):
            recibidas.append(solicitud)
            return super().emitir(solicitud)

    (envio,) = procesar_pendientes(conn, Espia(), limite=1, recursos=recursos)

    assert (envio.factura_id, envio.tipo, envio.estado_factura) == (
        nota.id,
        TipoTrabajo.ENVIAR_DIAN,
        EstadoFactura.FACTURADO,
    )
    assert estado_factura(escenario, nota.id) == "FACTURADO"
    assert estado_venta(escenario, origen) == "NO_FACTURABLE"
    assert estado_factura(escenario, factura_id) == "FACTURADO"  # la factura sigue aceptada ante la DIAN
    assert acciones(escenario, factura_id)[-1] == "FACTURA_ANULADA"
    (detalle,) = escenario.datos.conn.execute(
        "SELECT detalle FROM auditoria WHERE accion = 'FACTURA_ANULADA' AND entidad_id = %s", (str(factura_id),)
    ).fetchone()
    assert detalle == {"nota": "NCP1"}
    assert acciones(escenario, nota.id)[-1] == "FACTURA_EMITIDA"
    assert escenario.datos.conn.execute(
        "SELECT tipo, estado FROM outbox WHERE factura_id = %s ORDER BY id", (nota.id,)
    ).fetchall() == [("ENVIAR_DIAN", "HECHO"), ("ENVIAR_CORREO", "PENDIENTE")]
    # El proveedor recibe la nota firmada, con su número y las líneas de la factura referenciada.
    (s,) = recibidas
    assert (s.numero, s.tipo_documento) == ("NCP1", "nc")
    assert s.cufe == escenario.datos.uno("SELECT cufe FROM factura WHERE id = %s", (nota.id,))
    assert s.documento is not None and b"<CreditNote" in s.documento and b"Signature" in s.documento
    assert len(s.lineas) == 1 and s.forma_pago == "CONTADO"


def test_nota_rechazada_no_libera_la_venta(escenario, conn, recursos):
    factura_id, origen = facturar(escenario, conn, recursos)
    nota = anular(escenario, conn, factura_id)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "rechazo"

    (hecho,) = procesar_pendientes(conn, proveedor, recursos=recursos)

    assert hecho.estado_factura == EstadoFactura.RECHAZADO
    assert estado_factura(escenario, nota.id) == "RECHAZADO"
    assert estado_venta(escenario, origen) == "FACTURADO"  # sigue facturada: nada que reabrir
    assert estado_factura(escenario, factura_id) == "FACTURADO"
    # Se corrige y se reenvía (otra clave): misma fila, mismo número.
    otra = anular(escenario, conn, factura_id)
    assert (otra.id, otra.numero) == (nota.id, "NCP1")
    procesar_pendientes(conn, ProveedorSimulado(), limite=1, recursos=recursos)
    assert estado_factura(escenario, nota.id) == "FACTURADO"
    assert estado_venta(escenario, origen) == "NO_FACTURABLE"


def test_nota_incierta_no_toca_la_venta(escenario, conn, recursos):
    factura_id, origen = facturar(escenario, conn, recursos)
    nota = anular(escenario, conn, factura_id)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_antes"

    (hecho,) = procesar_pendientes(conn, proveedor, limite=1, recursos=recursos)

    assert hecho.estado_factura == EstadoFactura.INCIERTO
    assert estado_factura(escenario, nota.id) == "INCIERTO"
    assert estado_venta(escenario, origen) == "FACTURADO"
    assert estado_factura(escenario, factura_id) == "FACTURADO"
    # La conciliación (la DIAN no tiene la nota) reenvía la nota sin tocar la venta, y luego la acepta.
    # Se detiene antes de la entrega (ENVIAR_CORREO): este test es de la conciliación, no del correo.
    hechos = procesar_pendientes(conn, proveedor, limite=2, recursos=recursos)
    assert [h.tipo for h in hechos] == [TipoTrabajo.CONSULTAR_DIAN, TipoTrabajo.ENVIAR_DIAN]
    assert estado_factura(escenario, nota.id) == "FACTURADO"
    assert estado_venta(escenario, origen) == "NO_FACTURABLE"


def test_correo_de_la_nota_se_entrega(escenario, conn, recursos):
    factura_id, _ = facturar(escenario, conn, recursos)
    nota = anular(escenario, conn, factura_id)
    recursos.enviador.enviados.clear()  # el correo de la factura ya salió en facturar()

    hechos = procesar_pendientes(conn, ProveedorSimulado(), recursos=recursos)

    assert [(h.factura_id, h.tipo) for h in hechos] == [
        (nota.id, TipoTrabajo.ENVIAR_DIAN),
        (nota.id, TipoTrabajo.ENVIAR_CORREO),
    ]
    (mensaje,) = recursos.enviador.enviados
    assert mensaje.asunto.split(";")[2:4] == ["NCP1", "91"]
    assert "nota crédito electrónica NCP1" in mensaje.texto
    with zipfile.ZipFile(io.BytesIO(mensaje.adjunto)) as z:
        pdf = next(n for n in z.namelist() if n.endswith(".pdf"))
        assert pdf.startswith("nc")
        assert b"CreditNote" in z.read(next(n for n in z.namelist() if n.startswith("ad")))
    assert acciones(escenario, nota.id)[-2:] == ["FACTURA_EMITIDA", "FACTURA_ENTREGADA"]
    assert escenario.datos.conn.execute(
        "SELECT tipo, estado FROM outbox WHERE factura_id = %s ORDER BY id", (nota.id,)
    ).fetchall() == [("ENVIAR_DIAN", "HECHO"), ("ENVIAR_CORREO", "HECHO")]


def test_nota_de_otro_ambiente_no_se_envia(escenario, conn, recursos):
    factura_id, origen = facturar(escenario, conn, recursos)
    nota = anular(escenario, conn, factura_id)
    assert escenario.datos.uno("SELECT ambiente FROM factura WHERE id = %s", (nota.id,)) == "2"

    class NoDebeUsarse(ProveedorSimulado):
        def emitir(self, solicitud):
            raise AssertionError("no se debe enviar")

    hechos = procesar_pendientes(conn, NoDebeUsarse(), recursos=dataclasses.replace(recursos, ambiente="1"))

    assert [(h.factura_id, h.tipo) for h in hechos] == [(nota.id, TipoTrabajo.ENVIAR_DIAN)]
    assert estado_factura(escenario, nota.id) == "EN_PROCESO"
    assert escenario.datos.uno("SELECT xml_firmado FROM factura WHERE id = %s", (nota.id,)) is None
    assert estado_venta(escenario, origen) == "FACTURADO"
    assert escenario.datos.uno("SELECT estado FROM outbox WHERE factura_id = %s", (nota.id,)) == "FALLIDO"
    assert acciones(escenario, nota.id)[-1] == "AMBIENTE_NO_COINCIDE"
