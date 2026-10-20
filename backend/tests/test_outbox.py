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


def test_cola_vacia(conn, recursos):
    assert procesar_siguiente(conn, ProveedorSimulado(), recursos=recursos) is None


def test_envio_aceptado(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    hechos = procesar_pendientes(conn, proveedor, recursos=recursos)

    assert [(h.factura_id, h.tipo, h.estado_factura) for h in hechos] == [
        (f.id, TipoTrabajo.ENVIAR_DIAN, EstadoFactura.FACTURADO),
        (f.id, TipoTrabajo.ENVIAR_CORREO, EstadoFactura.FACTURADO),  # facturada -> se entrega al cliente
    ]
    estado, error, respuesta, numero, _ = factura(escenario, f.id)
    assert (estado, error, numero) == ("FACTURADO", None, "SETP1")
    assert respuesta["simulado"] is True
    assert origen_estado(escenario, origen) == "FACTURADO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "HECHO", 1), ("ENVIAR_CORREO", "HECHO", 1)]
    assert (
        proveedor.consultar(clave=factura(escenario, f.id)[4], numero="SETP1", cufe=f.cufe, track_id=None).cufe
        == f.cufe
    )
    assert acciones(escenario, f.id) == ["FACTURA_SOLICITADA", "FACTURA_EMITIDA", "FACTURA_ENTREGADA"]


def test_desactivar_al_cliente_no_atasca_una_factura_ya_numerada(escenario, conn, recursos):
    # La factura ya tiene número y XML: desactivar al cliente después (baja lógica) no puede dejarla sin enviar,
    # porque el consecutivo ya se gastó. La baja impide facturas NUEVAS, no las que ya están en la cola.
    f, origen = solicitar(escenario, conn)
    escenario.datos.conn.execute("UPDATE cliente SET activo = FALSE WHERE id = %s", (escenario.cliente,))

    hechos = procesar_pendientes(conn, ProveedorSimulado(), recursos=recursos)

    assert [h.estado_factura for h in hechos] == [EstadoFactura.FACTURADO, EstadoFactura.FACTURADO]  # envío + entrega
    assert factura(escenario, f.id)[0] == "FACTURADO"
    assert origen_estado(escenario, origen) == "FACTURADO"


def test_el_proveedor_recibe_el_numero_y_el_documento_de_la_factura(escenario, conn, recursos):
    recibidas: list[SolicitudFactura] = []

    class Espia(ProveedorSimulado):
        def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision:
            recibidas.append(solicitud)
            return super().emitir(solicitud)

    f, _ = solicitar(escenario, conn)
    procesar_pendientes(conn, Espia(), recursos=recursos)
    (s,) = recibidas
    assert (s.numero, s.cufe) == ("SETP1", f.cufe)
    assert s.documento is not None and b"SETP1" in s.documento


def test_rechazo_libera_la_venta_y_el_reenvio_conserva_el_numero(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "rechazo"
    (hecho,) = procesar_pendientes(conn, proveedor, recursos=recursos)

    assert hecho.estado_factura == EstadoFactura.RECHAZADO
    estado, error, *_ = factura(escenario, f.id)
    assert estado == "RECHAZADO" and "rechazado" in error
    assert origen_estado(escenario, origen) == "PENDIENTE"

    # Se corrige y se vuelve a solicitar (nueva clave): misma fila, mismo número, sin gastar otro consecutivo.
    otra, _ = solicitar(escenario, conn, origen)
    assert (otra.id, otra.numero, otra.nueva) == (f.id, "SETP1", True)
    assert factura(escenario, f.id)[0] == "EN_PROCESO" and factura(escenario, f.id)[1] is None
    assert escenario.siguiente() == 2

    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "FACTURADO"
    assert origen_estado(escenario, origen) == "FACTURADO"
    assert cola(escenario, f.id) == [
        ("ENVIAR_DIAN", "HECHO", 1), ("ENVIAR_DIAN", "HECHO", 1), ("ENVIAR_CORREO", "HECHO", 1),
    ]  # fmt: skip
    assert acciones(escenario, f.id) == [
        "FACTURA_SOLICITADA", "FACTURA_RECHAZADA", "FACTURA_REENVIADA", "FACTURA_EMITIDA", "FACTURA_ENTREGADA",
    ]  # fmt: skip


def test_timeout_sin_emitir_se_concilia_y_se_reenvia(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_antes"

    assert procesar_siguiente(conn, proveedor, recursos=recursos).estado_factura == EstadoFactura.INCIERTO
    assert factura(escenario, f.id)[0] == "INCIERTO" and origen_estado(escenario, origen) == "INCIERTO"

    # La consulta dice que NO existe: vuelve a EN_PROCESO y se encola otro envío con la misma clave.
    consulta = procesar_siguiente(conn, proveedor, recursos=recursos)
    assert (consulta.tipo, consulta.estado_factura) == (TipoTrabajo.CONSULTAR_DIAN, EstadoFactura.EN_PROCESO)
    assert origen_estado(escenario, origen) == "EN_PROCESO"

    assert procesar_siguiente(conn, proveedor, recursos=recursos).estado_factura == EstadoFactura.FACTURADO
    assert proveedor.llamadas == 2
    assert [t for t, *_ in cola(escenario, f.id)] == ["ENVIAR_DIAN", "CONSULTAR_DIAN", "ENVIAR_DIAN", "ENVIAR_CORREO"]
    assert origen_estado(escenario, origen) == "FACTURADO"


def test_timeout_tras_emitir_se_concilia_sin_reenviar(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_despues"

    hechos = procesar_pendientes(conn, proveedor, recursos=recursos)
    assert [(h.tipo, h.estado_factura) for h in hechos] == [
        (TipoTrabajo.ENVIAR_DIAN, EstadoFactura.INCIERTO),
        (TipoTrabajo.CONSULTAR_DIAN, EstadoFactura.FACTURADO),
        (TipoTrabajo.ENVIAR_CORREO, EstadoFactura.FACTURADO),
    ]
    assert proveedor.llamadas == 1  # la consulta la encontró: no se emitió dos veces
    assert factura(escenario, f.id)[0] == "FACTURADO" and origen_estado(escenario, origen) == "FACTURADO"
    assert acciones(escenario, f.id) == [
        "FACTURA_SOLICITADA", "FACTURA_INCIERTA", "FACTURA_EMITIDA", "FACTURA_ENTREGADA",
    ]  # fmt: skip


def test_consulta_sin_respuesta_reintenta_con_espera_y_luego_falla(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_antes"
    proveedor.falla_consulta = True
    politica = PoliticaReintentos(espera_inicial=timedelta(minutes=5), max_intentos=2)

    procesar_siguiente(conn, proveedor, politica, recursos=recursos)  # envío -> INCIERTO
    assert procesar_siguiente(conn, proveedor, politica, recursos=recursos).estado_factura == EstadoFactura.INCIERTO
    espera = escenario.datos.uno(
        "SELECT proximo_intento - now() FROM outbox WHERE factura_id = %s AND tipo = 'CONSULTAR_DIAN'", (f.id,)
    )
    assert timedelta(minutes=4) < espera <= timedelta(minutes=5)
    assert procesar_siguiente(conn, proveedor, politica, recursos=recursos) is None  # todavía no le toca

    escenario.datos.conn.execute("UPDATE outbox SET proximo_intento = now() WHERE factura_id = %s", (f.id,))
    procesar_siguiente(conn, proveedor, politica, recursos=recursos)
    assert cola(escenario, f.id)[-1] == ("CONSULTAR_DIAN", "FALLIDO", 2)
    # Sigue INCIERTO: nadie puede volver a facturar esa venta hasta que una persona lo revise.
    assert factura(escenario, f.id)[0] == "INCIERTO" and origen_estado(escenario, origen) == "INCIERTO"
    assert "CONCILIACION_FALLIDA" in acciones(escenario, f.id)


def test_cufe_distinto_del_proveedor_queda_para_revision(escenario, conn, recursos):
    class Desconocido(ProveedorSimulado):
        def emitir(self, solicitud: SolicitudFactura) -> ResultadoEmision:
            return ResultadoEmision(solicitud.clave, "SETP", "1", "0" * 96, AHORA)

    f, origen = solicitar(escenario, conn)
    procesar_pendientes(conn, Desconocido(), recursos=recursos)
    assert factura(escenario, f.id)[0] == "EN_PROCESO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "FALLIDO", 1)]
    assert "FACTURA_CUFE_DISTINTO" in acciones(escenario, f.id)


def test_un_trabajo_tomado_por_otro_worker_se_salta(escenario, conn, recursos):
    f, _ = solicitar(escenario, conn)
    with escenario.conectar() as otro, otro.transaction():
        otro.execute("SELECT id FROM outbox WHERE factura_id = %s FOR UPDATE", (f.id,))
        assert procesar_siguiente(conn, ProveedorSimulado(), recursos=recursos) is None
    assert procesar_siguiente(conn, ProveedorSimulado(), recursos=recursos).estado_factura == EstadoFactura.FACTURADO


def test_politica_de_reintentos_crece_al_doble_hasta_el_maximo():
    p = PoliticaReintentos(espera_inicial=timedelta(seconds=30), espera_maxima=timedelta(minutes=3))
    assert [p.espera(n).total_seconds() for n in range(1, 6)] == [30, 60, 120, 180, 180]


def test_worker_una_vez(escenario, conn, monkeypatch, tmp_path, certificado):
    from estacion.facturacion.certificado_prueba import guardar_pfx

    f, origen = solicitar(escenario, conn)
    guardar_pfx(certificado, tmp_path / "c.pfx", "clave-de-prueba")
    monkeypatch.setenv("DATABASE_URL", escenario.datos.bd.dsn)
    monkeypatch.setenv("DIAN_CERTIFICADO_RUTA", str(tmp_path / "c.pfx"))
    monkeypatch.setenv("DIAN_CERTIFICADO_CLAVE", "clave-de-prueba")
    monkeypatch.setenv("ESTACION_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("ESTACION_SMTP_PUERTO", "1")  # sin servidor de correo: la entrega queda reintentando
    assert worker.main(["--una-vez"]) == 0
    assert factura(escenario, f.id)[0] == "FACTURADO" and origen_estado(escenario, origen) == "FACTURADO"
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "PENDIENTE", 1)


def test_worker_sin_certificado_no_envia(escenario, conn, monkeypatch):
    f, origen = solicitar(escenario, conn)
    monkeypatch.setenv("DATABASE_URL", escenario.datos.bd.dsn)
    monkeypatch.delenv("DIAN_CERTIFICADO_RUTA", raising=False)
    monkeypatch.delenv("DIAN_CERTIFICADO_CLAVE", raising=False)
    assert worker.main(["--una-vez"]) == 0
    assert factura(escenario, f.id)[0] == "EN_PROCESO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "PENDIENTE", 1)]


def test_worker_sin_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert worker.main(["--una-vez"]) == 2


# ------------------------------------------------------------------------------------------ envío asíncrono (set)
def track(e: Escenario, factura_id: int) -> str | None:
    return e.datos.uno("SELECT dian_track_id FROM factura WHERE id = %s", (factura_id,))


def vencer(e: Escenario) -> None:
    """Pone vencidos los trabajos reprogramados (las esperas de la política no se esperan en las pruebas)."""
    e.datos.conn.execute("UPDATE outbox SET proximo_intento = now() WHERE estado = 'PENDIENTE'")


def test_envio_asincrono_queda_en_proceso_y_la_consulta_lo_factura(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado(asincrono=True)
    proveedor.en_proceso_restantes = 1
    procesar_pendientes(conn, proveedor, recursos=recursos)
    clave = factura(escenario, f.id)[4]
    assert factura(escenario, f.id)[0] == "EN_PROCESO" and track(escenario, f.id) == f"sim-{clave}"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "HECHO", 1), ("CONSULTAR_DIAN", "PENDIENTE", 1)]
    vencer(escenario)
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "FACTURADO" and origen_estado(escenario, origen) == "FACTURADO"
    assert proveedor.llamadas == 1  # un solo envío: la consulta nunca reenvía
    assert escenario.siguiente() == 2  # no se gastó otro número


def test_rechazo_llegado_por_la_consulta(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado(asincrono=True)
    proveedor.rechazo_en_consulta = True
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "RECHAZADO" and origen_estado(escenario, origen) == "PENDIENTE"
    assert "rechazado por el simulador" in factura(escenario, f.id)[1]
    assert "FACTURA_RECHAZADA" in acciones(escenario, f.id)


def test_error_de_seguridad_no_toca_la_factura_y_reintenta(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_seguridad = True
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert factura(escenario, f.id)[0] == "EN_PROCESO" and origen_estado(escenario, origen) == "EN_PROCESO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "PENDIENTE", 1)]
    assert "seguridad" in escenario.datos.uno("SELECT ultimo_error FROM outbox WHERE factura_id = %s", (f.id,))
    assert "DIAN_SEGURIDAD" in acciones(escenario, f.id)


def test_error_de_seguridad_agotado_queda_fallido(escenario, conn, recursos):
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    politica = PoliticaReintentos(max_intentos=2)  # la espera la adelanta vencer()
    for _ in range(2):
        proveedor.falla_seguridad = True
        procesar_pendientes(conn, proveedor, politica, recursos=recursos)
        vencer(escenario)
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "FALLIDO", 2)]


def test_reenvio_de_rechazada_limpia_el_track_id(escenario, conn, recursos):
    f, origen = solicitar(escenario, conn)
    proveedor = ProveedorSimulado(asincrono=True)
    proveedor.rechazo_en_consulta = True
    procesar_pendientes(conn, proveedor, recursos=recursos)
    assert track(escenario, f.id) is not None
    f2, _ = solicitar(escenario, conn, origen)
    assert f2.numero == f.numero and track(escenario, f.id) is None


# ------------------------------------------------------------------------------------------ worker con la DIAN
def test_worker_dian_sin_certificado_no_arranca(escenario, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", escenario.datos.bd.dsn)
    for k, v in ENTORNO_DIAN.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DIAN_CERTIFICADO_RUTA", raising=False)
    monkeypatch.delenv("DIAN_CERTIFICADO_CLAVE", raising=False)
    assert worker.main(["--una-vez", "--proveedor", "dian"]) == 2


def test_construir_proveedor_dian(escenario, conn, certificado):
    from estacion.facturacion.dian.proveedor import ProveedorDian
    from estacion.servicios.configuracion import ConfiguracionIncompleta

    entorno = {**ENTORNO_DIAN, "DIAN_TEST_SET_ID": "set-1"}
    p = worker.construir_proveedor("dian", conn, entorno, certificado)
    assert isinstance(p, ProveedorDian) and p.modo == "set_pruebas"
    sin_set = worker.construir_proveedor("dian", conn, ENTORNO_DIAN, certificado)
    assert isinstance(sin_set, ProveedorDian) and sin_set.modo == "normal"
    with pytest.raises(ConfiguracionIncompleta, match="DIAN_AMBIENTE"):
        worker.construir_proveedor("dian", conn, {**entorno, "DIAN_AMBIENTE": "1"}, certificado)
    with pytest.raises(ConfiguracionIncompleta, match="certificado"):
        worker.construir_proveedor("dian", conn, entorno, None)
    assert isinstance(worker.construir_proveedor("simulado", conn, entorno, None), ProveedorSimulado)


# ------------------------------------------------------------------------------------------ revisión final
def test_reintento_no_vuelve_a_firmar(escenario, conn, recursos):
    # El documento que se reenvía es el MISMO que ya se firmó (si la DIAN lo validó en el primer intento, lo que se
    # guarda y se entrega es lo que vio la DIAN).
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "timeout_antes"
    procesar_siguiente(conn, proveedor, recursos=recursos)  # firma y "envía": incierto
    primero = escenario.datos.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,))
    procesar_pendientes(conn, proveedor, recursos=recursos)  # consulta: no existe -> reenvía -> facturada
    assert factura(escenario, f.id)[0] == "FACTURADO"
    assert escenario.datos.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,)) == primero


def _envio_incierto_y_consulta(e: Escenario, conn, proveedor, politica, recursos) -> None:
    """Un ENVIAR que no responde (y no emitió) seguido de su CONSULTAR, que dice "no existe"."""
    proveedor.falla_siguiente = "timeout_antes"
    procesar_siguiente(conn, proveedor, politica, recursos=recursos)
    vencer(e)
    procesar_siguiente(conn, proveedor, politica, recursos=recursos)


def test_reenvios_por_inciertos_repetidos_esperan_y_se_detienen(escenario, conn, recursos):
    # Revisión final: un envío que falla siempre igual (p. ej. un proxy) y una consulta que dice "no existe" no pueden
    # rebotar ENVIAR <-> CONSULTAR sin espera para siempre.
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    politica = PoliticaReintentos(max_intentos=3)
    for _ in range(3):
        _envio_incierto_y_consulta(escenario, conn, proveedor, politica, recursos)
        vencer(escenario)
    tipos = [t for t, *_ in cola(escenario, f.id)]
    assert tipos.count("ENVIAR_DIAN") == 3  # el original y dos reenvíos
    assert cola(escenario, f.id)[-1][:2] == ("CONSULTAR_DIAN", "FALLIDO")
    assert "REENVIOS_AGOTADOS" in acciones(escenario, f.id)
    assert procesar_siguiente(conn, proveedor, politica, recursos=recursos) is None  # nada más en la cola


def test_el_segundo_reenvio_espera(escenario, conn, recursos):
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    politica = PoliticaReintentos()
    _envio_incierto_y_consulta(escenario, conn, proveedor, politica, recursos)
    pendiente = "SELECT proximo_intento > now() FROM outbox WHERE factura_id = %s AND estado = 'PENDIENTE'"
    assert escenario.datos.uno(pendiente, (f.id,)) is False  # el primer reenvío es inmediato
    vencer(escenario)
    _envio_incierto_y_consulta(escenario, conn, proveedor, politica, recursos)
    assert escenario.datos.uno(pendiente, (f.id,)) is True  # el segundo espera
