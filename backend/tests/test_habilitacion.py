"""Comando de habilitación. Sin red: la DIAN es un cliente falso y el envío lo haría el worker."""

from __future__ import annotations

import pytest

from conftest import ENTORNO_DIAN
from estacion import habilitacion
from estacion.facturacion.dian.cliente import ConsultaRangos, ErrorSeguridadDian, RangoNumeracion

pytestmark = pytest.mark.bd


def entorno(escenario, **extra):
    return {**ENTORNO_DIAN, "DATABASE_URL": escenario.datos.bd.dsn, **extra}


def sin_conflicto(escenario):
    """El escenario trae una SETP 1-5000 con la MISMA resolución del set; en la base real no existe."""
    escenario.datos.conn.execute("UPDATE numeracion SET numero_resolucion = '1' WHERE id = %s", (escenario.numeracion,))


def admin(escenario) -> str:
    escenario.datos.conn.execute("UPDATE usuario SET rol = 'ADMIN' WHERE id = %s", (escenario.usuario,))
    escenario.datos.venta_manual(escenario.usuario)  # asegura un surtidor y un producto
    return escenario.datos.uno("SELECT usuario FROM usuario WHERE id = %s", (escenario.usuario,))


class DianFalsa:
    def __init__(self, resultado):
        self.resultado, self.llamadas = resultado, []

    def rangos_numeracion(self, nit, software_id):
        self.llamadas.append((nit, software_id))
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def test_probar_conexion_ok(escenario, capsys):
    rango = RangoNumeracion("SETP", "18760000001", 990000000, 995000000, "2019-01-19", "2030-01-19")
    dian = DianFalsa(ConsultaRangos("100", "OK", (rango,)))
    assert habilitacion.main(["probar-conexion"], entorno=entorno(escenario), cliente_dian=dian) == 0
    assert dian.llamadas == [("900123456", ENTORNO_DIAN["DIAN_SOFTWARE_ID"])]
    salida = capsys.readouterr().out
    assert "SETP 990000000-995000000" in salida and ENTORNO_DIAN["DIAN_SOFTWARE_PIN"] not in salida


def test_probar_conexion_error_de_seguridad(escenario, capsys):
    dian = DianFalsa(ErrorSeguridadDian("GetNumberingRange: MessageExpired"))
    assert habilitacion.main(["probar-conexion"], entorno=entorno(escenario), cliente_dian=dian) == 1
    assert "reloj" in capsys.readouterr().err  # sugiere revisar la hora del servidor


def test_configurar_numeracion_es_idempotente(escenario):
    # Review Focus 4: dos corridas, una sola numeración activa, sin reiniciar el consecutivo.
    sin_conflicto(escenario)
    e = entorno(escenario)
    assert habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI") == 0
    escenario.datos.conn.execute("UPDATE numeracion SET siguiente = 990000005 WHERE desde = 990000000")
    assert habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI") == 0
    filas = escenario.datos.conn.execute(
        "SELECT prefijo, desde, siguiente, activa FROM numeracion ORDER BY id"
    ).fetchall()
    assert filas == [("SETP", 1, 1, False), ("SETP", 990000000, 990000005, True)]


def test_configurar_numeracion_con_conflicto(escenario):
    # Misma resolución SETP con otro rango: no se adivina, se detiene sin cambiar nada.
    assert habilitacion.main(["configurar-numeracion"], entorno=entorno(escenario), confirmar=lambda _: "SI") == 1
    assert escenario.datos.conn.execute("SELECT desde, activa FROM numeracion").fetchall() == [(1, True)]


def test_configurar_numeracion_sin_confirmar_no_cambia_nada(escenario):
    assert habilitacion.main(["configurar-numeracion"], entorno=entorno(escenario), confirmar=lambda _: "no") == 1
    assert escenario.datos.uno("SELECT count(*) FROM numeracion") == 1


def test_enviar_crea_facturas_setp_consecutivas(escenario):
    sin_conflicto(escenario)
    e = entorno(escenario)
    login = admin(escenario)
    habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI")
    assert habilitacion.main(["enviar", "--cantidad", "2", "--usuario", login], entorno=e,
                             confirmar=lambda _: "ENVIAR") == 0  # fmt: skip
    numeros = [n for (n,) in escenario.datos.conn.execute("SELECT numero FROM factura ORDER BY id").fetchall()]
    assert numeros == ["SETP990000000", "SETP990000001"]
    assert escenario.datos.uno("SELECT count(*) FROM outbox WHERE tipo = 'ENVIAR_DIAN'") == 2
    # el cliente de prueba recibe el correo de la propia estación, no el de un tercero
    assert (
        escenario.datos.uno("SELECT DISTINCT c.email FROM factura f JOIN cliente c ON c.id = f.cliente_id")
        == "estacion@example.com"
    )


def test_enviar_sin_confirmacion_no_crea_nada(escenario):
    login = admin(escenario)
    assert habilitacion.main(["enviar", "--usuario", login], entorno=entorno(escenario), confirmar=lambda _: "si") == 1
    assert escenario.datos.uno("SELECT count(*) FROM factura") == 0


def test_enviar_exige_admin(escenario):
    login = escenario.datos.uno("SELECT usuario FROM usuario WHERE id = %s", (escenario.usuario,))  # BOMBERO
    assert habilitacion.main(["enviar", "--usuario", login], entorno=entorno(escenario),
                             confirmar=lambda _: "ENVIAR") == 2  # fmt: skip


def test_todo_exige_ambiente_de_habilitacion(escenario):
    assert habilitacion.main(["estado"], entorno=entorno(escenario, DIAN_AMBIENTE="1")) == 2


def test_estado_sin_facturas(escenario, capsys):
    assert habilitacion.main(["estado"], entorno=entorno(escenario)) == 0
    assert "sin facturas SETP" in capsys.readouterr().out


def test_estado_lista_las_facturas_del_set(escenario, capsys):
    sin_conflicto(escenario)
    e = entorno(escenario)
    login = admin(escenario)
    habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI")
    habilitacion.main(["enviar", "--usuario", login], entorno=e, confirmar=lambda _: "ENVIAR")
    capsys.readouterr()
    assert habilitacion.main(["estado"], entorno=e) == 0
    assert "SETP990000000  EN_PROCESO" in capsys.readouterr().out


def test_enviar_con_otro_correo_para_el_cliente_de_prueba(escenario):
    sin_conflicto(escenario)
    e = entorno(escenario)
    login = admin(escenario)
    habilitacion.main(["configurar-numeracion"], entorno=e, confirmar=lambda _: "SI")
    argv = ["enviar", "--usuario", login, "--correo", "Pruebas@Example.com"]
    assert habilitacion.main(argv, entorno=e, confirmar=lambda _: "ENVIAR") == 0
    correo = "SELECT c.email FROM factura f JOIN cliente c ON c.id = f.cliente_id ORDER BY f.id DESC LIMIT 1"
    assert escenario.datos.uno(correo) == "pruebas@example.com"
    # una segunda corrida con otro correo actualiza al MISMO cliente de prueba (no crea otro)
    argv[-1] = "otro@example.com"
    assert habilitacion.main(argv, entorno=e, confirmar=lambda _: "ENVIAR") == 0
    assert escenario.datos.uno(correo) == "otro@example.com"
    assert escenario.datos.uno("SELECT count(*) FROM cliente WHERE numero_documento = '1000000001'") == 1


def test_enviar_con_correo_invalido_no_crea_nada(escenario):
    login = admin(escenario)
    argv = ["enviar", "--usuario", login, "--correo", "no-es-correo"]
    assert habilitacion.main(argv, entorno=entorno(escenario), confirmar=lambda _: "ENVIAR") == 2
    assert escenario.datos.uno("SELECT count(*) FROM factura") == 0
