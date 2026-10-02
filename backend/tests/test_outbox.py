"""Cola de envíos (`servicios/outbox.py`) y worker, con `ProveedorSimulado`: nada sale hacia la DIAN."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from conftest import ENTORNO_DIAN, Escenario
from estacion import worker
from estacion.facturacion.modelos import EstadoFactura, ResultadoEmision, SolicitudFactura, TipoTrabajo
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.emision import Origen, ServicioEmision
from estacion.servicios.outbox import PoliticaReintentos, procesar_pendientes, procesar_siguiente

pytestmark = pytest.mark.bd

AHORA = datetime(2026, 9, 30, 10, 5)


def solicitar(e: Escenario, conn, origen: Origen | None = None, k: str | None = None):
    origen = origen or Origen("despacho", e.datos.despacho())
    f = ServicioEmision(entorno=ENTORNO_DIAN, reloj=lambda: AHORA).solicitar(
        conn, clave=k or uuid.uuid4().hex, origen=origen, cliente_id=e.cliente, usuario_id=e.usuario,
        medio_pago="EFECTIVO",
    )  # fmt: skip
    return f, origen


def factura(e: Escenario, factura_id: int) -> tuple:
    return e.datos.conn.execute(
        "SELECT estado, error, respuesta_dian, numero, clave_emision FROM factura WHERE id = %s", (factura_id,)
    ).fetchone()


def origen_estado(e: Escenario, origen: Origen) -> str:
    return e.datos.uno(f"SELECT estado_facturacion FROM {origen.tipo} WHERE id = %s", (origen.id,))


def cola(e: Escenario, factura_id: int) -> list[tuple]:
    return e.datos.conn.execute(
        "SELECT tipo, estado, intentos FROM outbox WHERE factura_id = %s ORDER BY id", (factura_id,)
    ).fetchall()


def acciones(e: Escenario, factura_id: int) -> list[str]:
    filas = e.datos.conn.execute(
        "SELECT accion FROM auditoria WHERE entidad = 'factura' AND entidad_id = %s ORDER BY id", (str(factura_id),)
    ).fetchall()
    return [a for (a,) in filas]


def test_cola_vacia(conn):
    assert procesar_siguiente(conn, ProveedorSimulado()) is None


def test_envio_aceptado(escenario, conn):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    hechos = procesar_pendientes(conn, proveedor)

    assert [(h.factura_id, h.tipo, h.estado_factura) for h in hechos] == [
        (f.id, TipoTrabajo.ENVIAR_DIAN, EstadoFactura.FACTURADO)
    ]
    estado, error, respuesta, numero, _ = factura(escenario, f.id)
    assert (estado, error, numero) == ("FACTURADO", None, "SETP1")
    assert respuesta["simulado"] is True
    assert origen_estado(escenario, origen) == "FACTURADO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "HECHO", 1)]
    assert proveedor.buscar_por_clave(factura(escenario, f.id)[4]).cufe == f.cufe
    assert acciones(escenario, f.id) == ["FACTURA_SOLICITADA", "FACTURA_EMITIDA"]


def test_el_proveedor_recibe_el_numero_y_el_documento_de_la_factura(escenario, conn):
    recibidas: list[SolicitudFactura] = []

    class Espia(ProveedorSimulado):
        def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision:
            recibidas.append(solicitud)
            return super().emitir(solicitud)

    f, _ = solicitar(escenario, conn)
    procesar_pendientes(conn, Espia())
    (s,) = recibidas
    assert (s.numero, s.cufe) == ("SETP1", f.cufe)
    assert s.documento is not None and b"SETP1" in s.documento


def test_rechazo_libera_la_venta_y_el_reenvio_conserva_el_numero(escenario, conn):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "rechazo"
    (hecho,) = procesar_pendientes(conn, proveedor)

    assert hecho.estado_factura == EstadoFactura.RECHAZADO
    estado, error, *_ = factura(escenario, f.id)
    assert estado == "RECHAZADO" and "rechazado" in error
    assert origen_estado(escenario, origen) == "PENDIENTE"

    # Se corrige y se vuelve a solicitar (nueva clave): misma fila, mismo número, sin gastar otro consecutivo.
    otra, _ = solicitar(escenario, conn, origen)
    assert (otra.id, otra.numero, otra.nueva) == (f.id, "SETP1", True)
    assert factura(escenario, f.id)[0] == "EN_PROCESO" and factura(escenario, f.id)[1] is None
    assert escenario.siguiente() == 2

    procesar_pendientes(conn, proveedor)
    assert factura(escenario, f.id)[0] == "FACTURADO"
    assert origen_estado(escenario, origen) == "FACTURADO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "HECHO", 1), ("ENVIAR_DIAN", "HECHO", 1)]
    assert acciones(escenario, f.id) == [
        "FACTURA_SOLICITADA", "FACTURA_RECHAZADA", "FACTURA_REENVIADA", "FACTURA_EMITIDA",
    ]  # fmt: skip


def test_timeout_sin_emitir_se_concilia_y_se_reenvia(escenario, conn):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_antes"

    assert procesar_siguiente(conn, proveedor).estado_factura == EstadoFactura.INCIERTO
    assert factura(escenario, f.id)[0] == "INCIERTO" and origen_estado(escenario, origen) == "INCIERTO"

    # La consulta dice que NO existe: vuelve a EN_PROCESO y se encola otro envío con la misma clave.
    consulta = procesar_siguiente(conn, proveedor)
    assert (consulta.tipo, consulta.estado_factura) == (TipoTrabajo.CONSULTAR_DIAN, EstadoFactura.EN_PROCESO)
    assert origen_estado(escenario, origen) == "EN_PROCESO"

    assert procesar_siguiente(conn, proveedor).estado_factura == EstadoFactura.FACTURADO
    assert proveedor.llamadas == 2
    assert [t for t, *_ in cola(escenario, f.id)] == ["ENVIAR_DIAN", "CONSULTAR_DIAN", "ENVIAR_DIAN"]
    assert origen_estado(escenario, origen) == "FACTURADO"


def test_timeout_tras_emitir_se_concilia_sin_reenviar(escenario, conn):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_despues"

    hechos = procesar_pendientes(conn, proveedor)
    assert [h.estado_factura for h in hechos] == [EstadoFactura.INCIERTO, EstadoFactura.FACTURADO]
    assert proveedor.llamadas == 1  # la consulta la encontró: no se emitió dos veces
    assert factura(escenario, f.id)[0] == "FACTURADO" and origen_estado(escenario, origen) == "FACTURADO"
    assert acciones(escenario, f.id) == ["FACTURA_SOLICITADA", "FACTURA_INCIERTA", "FACTURA_EMITIDA"]


def test_consulta_sin_respuesta_reintenta_con_espera_y_luego_falla(escenario, conn):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_antes"
    proveedor.falla_consulta = True
    politica = PoliticaReintentos(espera_inicial=timedelta(minutes=5), max_intentos=2)

    procesar_siguiente(conn, proveedor, politica)  # envío -> INCIERTO
    assert procesar_siguiente(conn, proveedor, politica).estado_factura == EstadoFactura.INCIERTO
    espera = escenario.datos.uno(
        "SELECT proximo_intento - now() FROM outbox WHERE factura_id = %s AND tipo = 'CONSULTAR_DIAN'", (f.id,)
    )
    assert timedelta(minutes=4) < espera <= timedelta(minutes=5)
    assert procesar_siguiente(conn, proveedor, politica) is None  # todavía no le toca

    escenario.datos.conn.execute("UPDATE outbox SET proximo_intento = now() WHERE factura_id = %s", (f.id,))
    procesar_siguiente(conn, proveedor, politica)
    assert cola(escenario, f.id)[-1] == ("CONSULTAR_DIAN", "FALLIDO", 2)
    # Sigue INCIERTO: nadie puede volver a facturar esa venta hasta que una persona lo revise.
    assert factura(escenario, f.id)[0] == "INCIERTO" and origen_estado(escenario, origen) == "INCIERTO"
    assert "CONCILIACION_FALLIDA" in acciones(escenario, f.id)


def test_cufe_distinto_del_proveedor_queda_para_revision(escenario, conn):
    class Desconocido(ProveedorSimulado):
        def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision:
            return ResultadoEmision(solicitud.clave, "SETP", "1", "0" * 96, AHORA)

    f, origen = solicitar(escenario, conn)
    procesar_pendientes(conn, Desconocido())
    assert factura(escenario, f.id)[0] == "EN_PROCESO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "FALLIDO", 1)]
    assert "FACTURA_CUFE_DISTINTO" in acciones(escenario, f.id)


def test_un_trabajo_tomado_por_otro_worker_se_salta(escenario, conn):
    f, _ = solicitar(escenario, conn)
    with escenario.conectar() as otro, otro.transaction():
        otro.execute("SELECT id FROM outbox WHERE factura_id = %s FOR UPDATE", (f.id,))
        assert procesar_siguiente(conn, ProveedorSimulado()) is None
    assert procesar_siguiente(conn, ProveedorSimulado()).estado_factura == EstadoFactura.FACTURADO


def test_politica_de_reintentos_crece_al_doble_hasta_el_maximo():
    p = PoliticaReintentos(espera_inicial=timedelta(seconds=30), espera_maxima=timedelta(minutes=3))
    assert [p.espera(n).total_seconds() for n in range(1, 6)] == [30, 60, 120, 180, 180]


def test_worker_una_vez(escenario, conn, monkeypatch):
    f, origen = solicitar(escenario, conn)
    monkeypatch.setenv("DATABASE_URL", escenario.datos.bd.dsn)
    assert worker.main(["--una-vez"]) == 0
    assert factura(escenario, f.id)[0] == "FACTURADO" and origen_estado(escenario, origen) == "FACTURADO"


def test_worker_sin_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert worker.main(["--una-vez"]) == 2
