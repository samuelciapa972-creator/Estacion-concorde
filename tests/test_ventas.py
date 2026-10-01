"""Modo manual, lista de pendientes, enlace venta manual ↔ despacho y el flujo completo con el simulador."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from psycopg import errors

from conftest import ENTORNO_DIAN, Escenario
from estacion.facturacion.modelos import EstadoFactura
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.emision import Origen, OrigenNoDisponible, ServicioEmision
from estacion.servicios.outbox import procesar_pendientes
from estacion.servicios.ventas import (
    EnlaceInvalido,
    VentaInvalida,
    enlazar_venta_manual,
    listar_pendientes,
    registrar_venta_manual,
)
from estacion.surtidores import SimuladorSurtidor, ingerir

pytestmark = pytest.mark.bd

AHORA = datetime(2026, 9, 30, 11, 0)


def surtidor_y_producto(e: Escenario) -> int:
    e.datos.despacho()  # crea el surtidor S1 y el producto DIESEL
    return e.datos.uno("SELECT id FROM surtidor WHERE codigo_externo = 'S1'")


def registrar(e: Escenario, conn, **cambios):
    campos = dict(
        usuario_id=e.usuario, surtidor_id=surtidor_y_producto(e), lado="a", producto="diesel",
        volumen=Decimal("1.861"), valor=Decimal(29757), reloj=lambda: AHORA,
    )  # fmt: skip
    return registrar_venta_manual(conn, **{**campos, **cambios})


def solicitar(e: Escenario, conn, origen: Origen):
    return ServicioEmision(entorno=ENTORNO_DIAN, reloj=lambda: AHORA).solicitar(
        conn, clave=uuid.uuid4().hex, origen=origen, cliente_id=e.cliente, usuario_id=e.usuario, medio_pago="EFECTIVO"
    )


def estado(e: Escenario, origen: Origen) -> str:
    return e.datos.uno(f"SELECT estado_facturacion FROM {origen.tipo} WHERE id = %s", (origen.id,))


# ------------------------------------------------------------------------------------------ modo manual
def test_registrar_venta_manual(escenario, conn):
    venta = registrar(escenario, conn)
    fila = escenario.datos.conn.execute(
        "SELECT lado, volumen, valor, ppu, forma_pago, registrada_en, estado_facturacion FROM venta_manual "
        "WHERE id = %s",
        (venta,),
    ).fetchone()
    assert fila == ("A", Decimal("1.861"), Decimal(29757), Decimal("15989.79"), "CONTADO", AHORA, "PENDIENTE")
    assert escenario.datos.uno("SELECT accion FROM auditoria WHERE entidad = 'venta_manual'") == (
        "VENTA_MANUAL_REGISTRADA"
    )


def test_registrar_con_precio_que_cuadra(escenario, conn):
    venta = registrar(escenario, conn, ppu=Decimal("15990"))
    assert escenario.datos.uno("SELECT ppu FROM venta_manual WHERE id = %s", (venta,)) == Decimal(15990)


@pytest.mark.parametrize(
    ("cambios", "mensaje"),
    [
        ({"volumen": 1.861}, "Decimal"),
        ({"volumen": Decimal("1.8612")}, "3 decimales"),
        ({"volumen": Decimal(0)}, "galones"),
        ({"valor": Decimal("29757.5")}, "entero de pesos"),
        ({"valor": Decimal("NaN")}, "entero de pesos"),
        ({"ppu": Decimal("16500")}, "no coincide"),
        ({"ppu": Decimal("15990.123")}, "2 decimales"),
        ({"lado": "AB"}, "lado"),
        ({"forma_pago": "FIADO"}, "forma de pago"),
        ({"producto": "KEROSENE"}, "producto desconocido"),
        ({"surtidor_id": 999}, "surtidor"),
    ],
)
def test_venta_manual_invalida(escenario, conn, cambios, mensaje):
    with pytest.raises(VentaInvalida, match=mensaje):
        registrar(escenario, conn, **cambios)
    assert escenario.datos.uno("SELECT count(*) FROM venta_manual") == 0


def test_usuario_inactivo_no_registra(escenario, conn):
    inactivo = escenario.datos.usuario(activo=False)
    with pytest.raises(VentaInvalida, match="usuario"):
        registrar(escenario, conn, usuario_id=inactivo)


# ------------------------------------------------------------------------------------------ pendientes
def test_pendientes_incluye_despachos_y_ventas_manuales_y_filtra(escenario, conn):
    despacho = escenario.datos.despacho()  # lado A, fin 10:02
    venta = registrar(escenario, conn, lado="B", volumen=Decimal("3.120"), valor=Decimal(50000))  # 11:00
    credito = escenario.datos.venta_manual(escenario.usuario, forma_pago="CREDITO")

    todos = listar_pendientes(conn)
    assert [p.origen for p in todos][:1] == [Origen("venta_manual", venta)]  # el más reciente primero
    assert Origen("despacho", despacho) in {p.origen for p in todos}
    assert Origen("venta_manual", credito) not in {p.origen for p in todos}  # solo contado por defecto
    assert Origen("venta_manual", credito) in {p.origen for p in listar_pendientes(conn, forma_pago=None)}

    solo_b = listar_pendientes(conn, lado="b")
    assert [p.origen for p in solo_b] == [Origen("venta_manual", venta)]
    assert listar_pendientes(conn, desde=datetime(2026, 9, 30, 10, 30)) == solo_b
    assert len(listar_pendientes(conn, limite=1)) == 1

    solicitar(escenario, conn, Origen("venta_manual", venta))  # ya no está pendiente
    assert Origen("venta_manual", venta) not in {p.origen for p in listar_pendientes(conn)}


# ------------------------------------------------------------------------------------------ enlace
def venta_igual_al_despacho(e: Escenario, despacho: int) -> int:
    return e.datos.venta_manual(e.usuario, lado="A", volumen="1.861", valor=29757, ppu=15990)


def test_enlazar_sin_facturas_manda_el_despacho(escenario, conn):
    despacho = escenario.datos.despacho()
    venta = venta_igual_al_despacho(escenario, despacho)
    queda = enlazar_venta_manual(conn, venta_id=venta, despacho_id=despacho, usuario_id=escenario.usuario)
    assert queda == Origen("despacho", despacho)
    assert estado(escenario, Origen("venta_manual", venta)) == "NO_FACTURABLE"
    with pytest.raises(OrigenNoDisponible):
        solicitar(escenario, conn, Origen("venta_manual", venta))
    assert solicitar(escenario, conn, queda).numero == "SETP1"


def test_enlazar_con_la_venta_ya_facturada_manda_la_venta(escenario, conn):
    despacho = escenario.datos.despacho()
    venta = venta_igual_al_despacho(escenario, despacho)
    solicitar(escenario, conn, Origen("venta_manual", venta))
    queda = enlazar_venta_manual(conn, venta_id=venta, despacho_id=despacho, usuario_id=escenario.usuario)
    assert queda == Origen("venta_manual", venta)
    assert estado(escenario, Origen("despacho", despacho)) == "NO_FACTURABLE"
    with pytest.raises(OrigenNoDisponible):
        solicitar(escenario, conn, Origen("despacho", despacho))


def test_no_se_enlazan_si_las_dos_tienen_factura(escenario, conn):
    despacho = escenario.datos.despacho()
    venta = venta_igual_al_despacho(escenario, despacho)
    solicitar(escenario, conn, Origen("despacho", despacho))
    solicitar(escenario, conn, Origen("venta_manual", venta))
    with pytest.raises(EnlaceInvalido, match="doble facturación"):
        enlazar_venta_manual(conn, venta_id=venta, despacho_id=despacho, usuario_id=escenario.usuario)


@pytest.mark.parametrize(
    ("cambios", "mensaje"),
    [
        ({"lado": "B"}, "lado"),
        ({"volumen": "1.900"}, "galones distintos"),
        ({"valor": 30000}, "valores distintos"),
    ],
)
def test_no_se_enlaza_lo_que_no_coincide(escenario, conn, cambios, mensaje):
    despacho = escenario.datos.despacho()
    campos = {"lado": "A", "volumen": "1.861", "valor": 29757, "ppu": 15990, **cambios}
    venta = escenario.datos.venta_manual(escenario.usuario, **campos)
    with pytest.raises(EnlaceInvalido, match=mensaje):
        enlazar_venta_manual(conn, venta_id=venta, despacho_id=despacho, usuario_id=escenario.usuario)


def test_un_enlace_no_se_repite_ni_se_cambia(escenario, conn):
    despacho, otro = escenario.datos.despacho(), escenario.datos.despacho()
    venta = venta_igual_al_despacho(escenario, despacho)
    enlazar_venta_manual(conn, venta_id=venta, despacho_id=despacho, usuario_id=escenario.usuario)
    with pytest.raises(EnlaceInvalido, match="ya está enlazado"):
        enlazar_venta_manual(conn, venta_id=venta, despacho_id=otro, usuario_id=escenario.usuario)
    with pytest.raises(errors.RaiseException, match="no se cambia"):  # la base también lo impide
        escenario.datos.conn.execute("UPDATE venta_manual SET despacho_id = %s WHERE id = %s", (otro, venta))


# ------------------------------------------------------------------------------------------ garantía en la base
def test_no_se_factura_la_venta_manual_si_su_despacho_ya_tiene_factura(escenario, conn):
    despacho = escenario.datos.despacho()
    # Enlace hecho por fuera del servicio (dejando las dos PENDIENTE): el servicio igual lo detecta.
    venta = escenario.datos.venta_manual(escenario.usuario, despacho_id=despacho)
    solicitar(escenario, conn, Origen("despacho", despacho))
    with pytest.raises(OrigenNoDisponible, match="enlazada"):
        solicitar(escenario, conn, Origen("venta_manual", venta))
    assert escenario.siguiente() == 2  # el intento fallido no gastó número


def test_la_base_impide_la_factura_del_despacho_y_de_su_venta_manual(escenario):
    """Aunque el código no lo revisara, el trigger de la 0003 lo impide (INSERT directo)."""
    d = escenario.datos
    despacho = d.despacho()
    venta = d.venta_manual(escenario.usuario, despacho_id=despacho)
    comunes = dict(
        cliente_id=escenario.cliente, usuario_id=escenario.usuario, numeracion_id=escenario.numeracion,
        prefijo="SETP", forma_pago="CONTADO", medio_pago="EFECTIVO", total=1, fecha_emision=AHORA,
    )  # fmt: skip
    d.insertar("factura", clave_emision=uuid.uuid4().hex, venta_manual_id=venta, consecutivo=1, **comunes)
    with pytest.raises(errors.UniqueViolation, match="ya tiene factura"):
        d.insertar("factura", clave_emision=uuid.uuid4().hex, despacho_id=despacho, consecutivo=2, **comunes)


def test_despacho_y_su_venta_manual_en_paralelo_solo_uno_factura(escenario):
    despacho = escenario.datos.despacho()
    venta = escenario.datos.venta_manual(escenario.usuario, despacho_id=despacho)
    origenes = [Origen("despacho", despacho), Origen("venta_manual", venta)]
    for _ in range(5):  # varias rondas: el orden de llegada cambia (antes terminaba en deadlock)
        barrera = threading.Barrier(2)
        hechas, errores = [], []

        def cajero(origen: Origen, barrera=barrera, hechas=hechas, errores=errores) -> None:
            with escenario.conectar() as c:
                barrera.wait()
                try:
                    hechas.append(solicitar(escenario, c, origen))
                except Exception as err:  # noqa: BLE001 - se revisa en el hilo principal
                    errores.append(err)

        hilos = [threading.Thread(target=cajero, args=(o,)) for o in origenes]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert len(hechas) == 1 and len(errores) == 1 and isinstance(errores[0], OrigenNoDisponible), errores
        assert escenario.datos.uno("SELECT count(*) FROM factura") == 1 and escenario.siguiente() == 2
        escenario.datos.conn.execute("DELETE FROM outbox; DELETE FROM factura")
        escenario.datos.conn.execute("UPDATE despacho SET estado_facturacion = 'PENDIENTE'")
        escenario.datos.conn.execute("UPDATE venta_manual SET estado_facturacion = 'PENDIENTE'")
        escenario.datos.conn.execute("UPDATE numeracion SET siguiente = 1")


# ------------------------------------------------------------------------------------------ aceptación
def test_flujo_completo_desde_el_simulador(escenario, conn):
    """Sin ningún surtidor real: el simulador despacha, el bombero ve el pendiente, factura y el worker envía."""
    sim = SimuladorSurtidor(semilla=3, reloj=lambda: AHORA)
    sim.despachar("A", pistola=1, galones=Decimal("3.120"), forma_pago="CONTADO")
    ingerir(escenario.datos.bd.dsn, sim)

    (pendiente,) = listar_pendientes(conn, lado="A")
    assert (pendiente.producto, pendiente.volumen, pendiente.valor) == ("CORRIENTE", Decimal("3.120"), Decimal(49998))
    factura = solicitar(escenario, conn, pendiente.origen)
    assert listar_pendientes(conn) == []

    (hecho,) = procesar_pendientes(conn, ProveedorSimulado())
    assert hecho.estado_factura == EstadoFactura.FACTURADO
    assert estado(escenario, pendiente.origen) == "FACTURADO"
    assert escenario.datos.uno("SELECT estado FROM factura WHERE id = %s", (factura.id,)) == "FACTURADO"


def test_venta_manual_enlazada_y_rechazada_se_reenvia_con_su_numero(escenario, conn):
    despacho = escenario.datos.despacho()
    venta = venta_igual_al_despacho(escenario, despacho)
    primera = solicitar(escenario, conn, Origen("venta_manual", venta))
    enlazar_venta_manual(conn, venta_id=venta, despacho_id=despacho, usuario_id=escenario.usuario)
    proveedor = ProveedorSimulado()
    proveedor.falla_siguiente = "rechazo"
    procesar_pendientes(conn, proveedor)
    assert estado(escenario, Origen("venta_manual", venta)) == "PENDIENTE"

    otra = solicitar(escenario, conn, Origen("venta_manual", venta))  # su propia factura rechazada no estorba
    assert (otra.id, otra.numero) == (primera.id, "SETP1")
    with pytest.raises(OrigenNoDisponible):
        solicitar(escenario, conn, Origen("despacho", despacho))
