"""Numeración atómica y servicio de emisión (`servicios/numeracion.py`, `servicios/emision.py`).

Contra PostgreSQL real: los bloqueos y las restricciones que garantizan "una factura por venta" y "ningún
número repetido ni saltado" solo se pueden probar ahí.
"""

from __future__ import annotations

import threading
import uuid
from datetime import date, datetime
from decimal import Decimal

import psycopg
import pytest

from conftest import ENTORNO_DIAN, Escenario
from estacion.facturacion.modelos import EstadoFactura
from estacion.facturacion.xml_factura import FacturaNoSoportada
from estacion.servicios.configuracion import ConfiguracionIncompleta
from estacion.servicios.emision import (
    ClaveReutilizada,
    ClienteNoDisponible,
    Origen,
    OrigenNoDisponible,
    ServicioEmision,
)
from estacion.servicios.numeracion import NumeracionNoDisponible, asignar_consecutivo

pytestmark = pytest.mark.bd

AHORA = datetime(2026, 9, 30, 10, 5, 30, 123456)


def clave() -> str:
    return uuid.uuid4().hex


def servicio(entorno: dict[str, str] | None = None) -> ServicioEmision:
    return ServicioEmision(entorno=ENTORNO_DIAN if entorno is None else entorno, reloj=lambda: AHORA)


def solicitar(e: Escenario, conn, origen: Origen, *, k: str | None = None, medio: str = "EFECTIVO", **kw):
    return servicio(kw.pop("entorno", None)).solicitar(
        conn, clave=k or clave(), origen=origen, cliente_id=kw.pop("cliente", e.cliente), usuario_id=e.usuario,
        medio_pago=medio,
    )  # fmt: skip


def estado_origen(e: Escenario, origen: Origen) -> str:
    return e.datos.uno(f"SELECT estado_facturacion FROM {origen.tipo} WHERE id = %s", (origen.id,))


def contar(e: Escenario, tabla: str) -> int:
    return e.datos.uno(f"SELECT count(*) FROM {tabla}")


# ------------------------------------------------------------------------------------------ numeración
def test_consecutivo_fuera_de_transaccion_se_rechaza(escenario, conn):
    with pytest.raises(RuntimeError, match="dentro de la transacción"):
        asignar_consecutivo(conn, escenario.estacion, date(2026, 9, 30))


def test_consecutivo_avanza_y_el_rollback_lo_devuelve(escenario, conn):
    with conn.transaction():
        assert asignar_consecutivo(conn, escenario.estacion, date(2026, 9, 30)).numero == "SETP1"
    with pytest.raises(ZeroDivisionError), conn.transaction():
        assert asignar_consecutivo(conn, escenario.estacion, date(2026, 9, 30)).consecutivo == 2
        raise ZeroDivisionError("falla simulada después de reservar el número")
    with conn.transaction():
        assert asignar_consecutivo(conn, escenario.estacion, date(2026, 9, 30)).consecutivo == 2  # sin hueco


@pytest.mark.parametrize(
    ("cambios", "fecha", "mensaje"),
    [
        ({}, date(2028, 1, 1), "no está vigente"),
        ({}, date(2025, 12, 31), "no está vigente"),
        ({"hasta": 3, "siguiente": 4}, date(2026, 9, 30), "agotada"),
        ({"activa": False}, date(2026, 9, 30), "no tiene una resolución"),
    ],
)
def test_numeracion_no_disponible(datos, cambios, fecha, mensaje):
    estacion = datos.estacion()
    datos.numeracion(estacion, **cambios)
    with psycopg.connect(datos.bd.dsn, autocommit=True) as c, c.transaction():
        with pytest.raises(NumeracionNoDisponible, match=mensaje):
            asignar_consecutivo(c, estacion, fecha)


def test_consecutivos_concurrentes_sin_repetidos_ni_huecos(escenario):
    hilos, por_hilo = 8, 5
    barrera = threading.Barrier(hilos)
    obtenidos: list[int] = []
    errores: list[BaseException] = []

    def trabajar() -> None:
        try:
            with escenario.conectar() as c:
                barrera.wait()
                for _ in range(por_hilo):
                    with c.transaction():
                        obtenidos.append(asignar_consecutivo(c, escenario.estacion, date(2026, 9, 30)).consecutivo)
        except BaseException as e:  # noqa: BLE001 - se reporta en el hilo principal
            errores.append(e)

    ts = [threading.Thread(target=trabajar) for _ in range(hilos)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errores
    assert sorted(obtenidos) == list(range(1, hilos * por_hilo + 1))
    assert escenario.siguiente() == hilos * por_hilo + 1


# ------------------------------------------------------------------------------------------ solicitar
def test_solicitar_numera_guarda_el_xml_y_encola(escenario, conn):
    origen = Origen("despacho", escenario.datos.despacho())
    k = clave()
    f = solicitar(escenario, conn, origen, k=k)

    assert f.nueva and f.numero == "SETP1" and f.estado == EstadoFactura.EN_PROCESO
    assert f.total == Decimal(29757)
    assert len(f.cufe or "") == 96
    assert estado_origen(escenario, origen) == "EN_PROCESO"
    assert escenario.siguiente() == 2

    fila = escenario.datos.conn.execute(
        "SELECT clave_emision, estado, numero, cufe, xml_sin_firmar, fecha_emision FROM factura WHERE id = %s",
        (f.id,),
    ).fetchone()
    assert fila[:4] == (k, "EN_PROCESO", "SETP1", f.cufe)
    assert "<cbc:ID>SETP1</cbc:ID>" in fila[4] and f.cufe in fila[4]
    assert fila[5] == AHORA.replace(microsecond=0)
    assert escenario.datos.conn.execute(
        "SELECT tipo, estado FROM outbox WHERE factura_id = %s", (f.id,)
    ).fetchall() == [("ENVIAR_DIAN", "PENDIENTE")]
    assert escenario.datos.uno("SELECT accion FROM auditoria WHERE entidad_id = %s", (str(f.id),)) == (
        "FACTURA_SOLICITADA"
    )


def test_solicitar_desde_venta_manual(escenario, conn):
    origen = Origen("venta_manual", escenario.datos.venta_manual(escenario.usuario))
    f = solicitar(escenario, conn, origen)
    assert f.numero == "SETP1" and f.total == Decimal(50000)
    assert estado_origen(escenario, origen) == "EN_PROCESO"


def test_doble_clic_con_la_misma_clave_no_duplica(escenario, conn):
    origen = Origen("despacho", escenario.datos.despacho())
    k = clave()
    primera = solicitar(escenario, conn, origen, k=k)
    segunda = solicitar(escenario, conn, origen, k=k)
    assert not segunda.nueva
    assert (segunda.id, segunda.numero, segunda.cufe) == (primera.id, primera.numero, primera.cufe)
    assert contar(escenario, "factura") == 1 and contar(escenario, "outbox") == 1
    assert escenario.siguiente() == 2


def test_misma_clave_para_otra_venta_se_rechaza(escenario, conn):
    k = clave()
    solicitar(escenario, conn, Origen("despacho", escenario.datos.despacho()), k=k)
    with pytest.raises(ClaveReutilizada):
        solicitar(escenario, conn, Origen("despacho", escenario.datos.despacho()), k=k)
    with pytest.raises(ClaveReutilizada):
        solicitar(escenario, conn, Origen("venta_manual", escenario.datos.venta_manual(escenario.usuario)), k=k)


def test_venta_ya_en_proceso_con_otra_clave_no_se_factura_de_nuevo(escenario, conn):
    origen = Origen("despacho", escenario.datos.despacho())
    solicitar(escenario, conn, origen)
    with pytest.raises(OrigenNoDisponible, match="no está pendiente"):
        solicitar(escenario, conn, origen)
    assert contar(escenario, "factura") == 1 and escenario.siguiente() == 2


def test_origen_inexistente(escenario, conn):
    with pytest.raises(OrigenNoDisponible, match="no existe"):
        solicitar(escenario, conn, Origen("despacho", 999_999))


def test_tipo_de_origen_desconocido():
    with pytest.raises(ValueError, match="tipo de origen"):
        Origen("factura", 1)  # type: ignore[arg-type]


def test_cliente_inactivo(escenario, conn):
    inactivo = escenario.datos.cliente(activo=False)
    with pytest.raises(ClienteNoDisponible):
        solicitar(escenario, conn, Origen("despacho", escenario.datos.despacho()), cliente=inactivo)


def test_conexion_sin_autocommit_se_rechaza(escenario):
    with psycopg.connect(escenario.datos.bd.dsn) as c, pytest.raises(ValueError, match="autocommit"):
        solicitar(escenario, c, Origen("despacho", escenario.datos.despacho()))


def test_medio_de_pago_desconocido(escenario, conn):
    with pytest.raises(ValueError, match="medio de pago"):
        solicitar(escenario, conn, Origen("despacho", escenario.datos.despacho()), medio="CHEQUE")


# ------------------------------------------------------------------------------------------ fallar cerrado
def _sin(variable: str) -> dict[str, str]:
    return {k: v for k, v in ENTORNO_DIAN.items() if k != variable}


@pytest.mark.parametrize(
    ("medio", "entorno", "error", "mensaje"),
    [
        ("EFECTIVO", _sin("DIAN_CLAVE_TECNICA"), ConfiguracionIncompleta, "DIAN_CLAVE_TECNICA"),
        ("EFECTIVO", _sin("DIAN_SOFTWARE_PIN"), ConfiguracionIncompleta, "DIAN_SOFTWARE_PIN"),
        ("EFECTIVO", {**ENTORNO_DIAN, "DIAN_AMBIENTE": "1"}, ConfiguracionIncompleta, "habilitación"),
        ("EFECTIVO", {}, ConfiguracionIncompleta, "DIAN_CLAVE_TECNICA, DIAN_AMBIENTE, DIAN_SOFTWARE_ID"),
        # Código DIAN de tarjeta SIN VERIFICAR: el generador la rechaza a propósito.
        ("TARJETA", ENTORNO_DIAN, FacturaNoSoportada, "medio de pago"),
    ],
)
def test_si_no_se_puede_emitir_no_se_gasta_el_consecutivo(escenario, conn, medio, entorno, error, mensaje):
    origen = Origen("despacho", escenario.datos.despacho())
    with pytest.raises(error, match=mensaje) as excinfo:
        solicitar(escenario, conn, origen, medio=medio, entorno=entorno)
    assert ENTORNO_DIAN["DIAN_SOFTWARE_PIN"] not in str(excinfo.value)  # el mensaje nunca lleva el secreto
    assert escenario.siguiente() == 1
    assert contar(escenario, "factura") == 0 and contar(escenario, "outbox") == 0
    assert estado_origen(escenario, origen) == "PENDIENTE"


def test_sin_estacion_activa_no_se_emite(datos):
    datos.usuario()
    despacho = datos.despacho()
    with psycopg.connect(datos.bd.dsn, autocommit=True) as c, pytest.raises(ConfiguracionIncompleta):
        servicio().solicitar(
            c, clave=clave(), origen=Origen("despacho", despacho), cliente_id=datos.cliente(),
            usuario_id=datos.usuario(), medio_pago="EFECTIVO",
        )  # fmt: skip


# ------------------------------------------------------------------------------------------ concurrencia
def _en_paralelo(e: Escenario, origenes: list[Origen]) -> tuple[list, list[BaseException]]:
    """Cada origen lo solicita un cajero distinto (su propia conexión), todos a la vez."""
    barrera = threading.Barrier(len(origenes))
    hechas: list = []
    errores: list[BaseException] = []

    def cajero(origen: Origen) -> None:
        with e.conectar() as c:
            barrera.wait()
            try:
                hechas.append(solicitar(e, c, origen))
            except BaseException as err:  # noqa: BLE001 - se revisa en el hilo principal
                errores.append(err)

    ts = [threading.Thread(target=cajero, args=(o,)) for o in origenes]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return hechas, errores


def test_dos_cajeros_sobre_el_mismo_despacho_solo_uno_factura(escenario):
    origen = Origen("despacho", escenario.datos.despacho())
    hechas, errores = _en_paralelo(escenario, [origen] * 4)
    assert len(hechas) == 1
    assert len(errores) == 3 and all(isinstance(err, OrigenNoDisponible) for err in errores)
    assert contar(escenario, "factura") == 1 and escenario.siguiente() == 2


def test_cajeros_sobre_despachos_distintos_numeran_sin_huecos(escenario):
    origenes = [Origen("despacho", escenario.datos.despacho()) for _ in range(6)]
    hechas, errores = _en_paralelo(escenario, origenes)
    assert not errores
    assert sorted(f.numero for f in hechas) == sorted(f"SETP{n}" for n in range(1, 7))
    assert escenario.siguiente() == 7
