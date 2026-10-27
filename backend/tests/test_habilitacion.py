"""Comando de habilitación. Sin red: la DIAN es un cliente falso y el envío lo haría el worker."""

from __future__ import annotations

import uuid

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


# ------------------------------------------------------------------------------------------ producción
CLAVE_PFX = "clave-pfx-de-prueba"
CLAVE_TECNICA = ENTORNO_DIAN["DIAN_CLAVE_TECNICA"]
RANGO_EDSC = dict(prefijo="EDSC", resolucion="18764000000001", desde=1, hasta=1000000,
                  vigente_desde="2026-10-01", vigente_hasta="2036-10-01", clave_tecnica=CLAVE_TECNICA)  # fmt: skip


def dian_produccion(**cambios):
    return DianFalsa(ConsultaRangos("100", "OK", (RangoNumeracion(**{**RANGO_EDSC, **cambios}),)))


def certificado_pfx(tmp_path, *, de_prueba: bool) -> str:
    """Un .pfx autofirmado: con la marca de prueba, o con el nombre de una empresa."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    from estacion.facturacion.certificado_prueba import MARCA, generar_certificado_prueba, guardar_pfx
    from estacion.facturacion.firma import Certificado

    c = generar_certificado_prueba("EDS")
    if not de_prueba:
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ESTACION REAL SAS")])
        cert = (
            x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(c.cert.public_key())
            .serial_number(1).not_valid_before(c.cert.not_valid_before_utc)
            .not_valid_after(c.cert.not_valid_after_utc).sign(c.clave, c.cert.signature_hash_algorithm)
        )  # fmt: skip
        c = Certificado(c.clave, cert)
        assert MARCA not in str(cert.subject)
    ruta = tmp_path / ("prueba.pfx" if de_prueba else "empresa.pfx")
    guardar_pfx(c, ruta, CLAVE_PFX)
    return str(ruta)


def entorno_produccion(escenario, tmp_path, **extra):
    propios = dict(
        DIAN_AMBIENTE="1", DIAN_CERTIFICADO_RUTA=certificado_pfx(tmp_path, de_prueba=False),
        DIAN_CERTIFICADO_CLAVE=CLAVE_PFX, ESTACION_SMTP_USUARIO="alista@example.com",
    )  # fmt: skip
    return entorno(escenario, **{**propios, **extra})


def activa_edsc(escenario):
    """La numeración EDSC de producción activa (lo que deja configurar-produccion)."""
    c = escenario.datos.conn
    c.execute("UPDATE numeracion SET activa = FALSE")
    c.execute(
        """INSERT INTO numeracion (estacion_id, prefijo, numero_resolucion, desde, hasta, siguiente, vigente_desde,
                                   vigente_hasta, activa, ambiente)
           VALUES (%s, 'EDSC', '18764000000001', 1, 1000000, 1, '2026-10-01', '2036-10-01', TRUE, '1')""",
        (escenario.estacion,),
    )


def pendiente_de_habilitacion(escenario) -> None:
    """Una factura de habilitación (ambiente 2) con su envío esperando en la cola."""
    from estacion.servicios.emision import Origen, ServicioEmision

    ServicioEmision(entorno=ENTORNO_DIAN).solicitar(
        escenario.datos.conn, clave=uuid.uuid4().hex, origen=Origen("despacho", escenario.datos.despacho()),
        cliente_id=escenario.cliente, usuario_id=escenario.usuario, medio_pago="EFECTIVO",
    )  # fmt: skip


def numeraciones(escenario):
    return escenario.datos.conn.execute(
        "SELECT prefijo, ambiente, siguiente, activa FROM numeracion ORDER BY id"
    ).fetchall()


def test_configurar_produccion_activa_edsc(escenario, tmp_path, capsys):
    dian = dian_produccion()
    e = entorno_produccion(escenario, tmp_path)
    assert habilitacion.main(["configurar-produccion", "--prefijo", "EDSC"], entorno=e,
                             confirmar=lambda _: "EDSC", cliente_dian=dian) == 0  # fmt: skip
    assert numeraciones(escenario) == [("SETP", "2", 1, False), ("EDSC", "1", 1, True)]
    assert dian.llamadas == [("900123456", ENTORNO_DIAN["DIAN_SOFTWARE_ID"])]
    salida = capsys.readouterr().out
    assert "18764000000001" in salida and "1-1000000" in salida and CLAVE_TECNICA not in salida
    assert escenario.datos.uno("SELECT count(*) FROM auditoria WHERE accion = 'NUMERACION_PRODUCCION_ACTIVADA'") == 1


def test_configurar_produccion_sin_confirmar_no_cambia_nada(escenario, tmp_path):
    e = entorno_produccion(escenario, tmp_path)
    assert habilitacion.main(["configurar-produccion", "--prefijo", "EDSC"], entorno=e,
                             confirmar=lambda _: "no", cliente_dian=dian_produccion()) == 1  # fmt: skip
    assert numeraciones(escenario) == [("SETP", "2", 1, True)]


def test_configurar_produccion_clave_tecnica_distinta(escenario, tmp_path, capsys):
    e = entorno_produccion(escenario, tmp_path)
    dian = dian_produccion(clave_tecnica="otra-clave-secreta")
    assert habilitacion.main(["configurar-produccion", "--prefijo", "EDSC"], entorno=e,
                             confirmar=lambda _: "EDSC", cliente_dian=dian) == 1  # fmt: skip
    captura = capsys.readouterr()
    assert "no coincide" in captura.err
    assert CLAVE_TECNICA not in captura.out + captura.err and "otra-clave-secreta" not in captura.out + captura.err
    assert numeraciones(escenario) == [("SETP", "2", 1, True)]


def test_configurar_produccion_prefijo_que_la_dian_no_tiene(escenario, tmp_path):
    e = entorno_produccion(escenario, tmp_path)
    assert habilitacion.main(["configurar-produccion", "--prefijo", "XXXX"], entorno=e,
                             confirmar=lambda _: "XXXX", cliente_dian=dian_produccion()) == 1  # fmt: skip
    assert numeraciones(escenario) == [("SETP", "2", 1, True)]


def test_configurar_produccion_exige_ambiente_1(escenario):
    assert habilitacion.main(["configurar-produccion", "--prefijo", "EDSC"], entorno=entorno(escenario),
                             confirmar=lambda _: "EDSC", cliente_dian=dian_produccion()) == 2  # fmt: skip


def test_configurar_produccion_se_niega_con_pendientes_de_habilitacion(escenario, tmp_path, capsys):
    pendiente_de_habilitacion(escenario)
    dian = dian_produccion()
    e = entorno_produccion(escenario, tmp_path)
    assert habilitacion.main(["configurar-produccion", "--prefijo", "EDSC"], entorno=e,
                             confirmar=lambda _: "EDSC", cliente_dian=dian) == 1  # fmt: skip
    assert "1 documento(s) de habilitación" in capsys.readouterr().err
    assert numeraciones(escenario) == [("SETP", "2", 2, True)]  # la SETP sigue activa
    assert dian.llamadas == []  # ni siquiera consulta a la DIAN


def verificar(escenario, tmp_path, capsys, *, dian=None, **extra):
    e = entorno_produccion(escenario, tmp_path, **extra)
    codigo = habilitacion.main(["verificar-produccion"], entorno=e, cliente_dian=dian or dian_produccion())
    return codigo, capsys.readouterr().out


def test_verificar_produccion_todo_bien(escenario, tmp_path, capsys):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys)
    assert codigo == 0 and "✘" not in salida
    assert "certificado vigente" in salida and "GetNumberingRange responde" in salida
    assert CLAVE_TECNICA not in salida and CLAVE_PFX not in salida


def test_verificar_produccion_con_test_set_id(escenario, tmp_path, capsys):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys, DIAN_TEST_SET_ID="algo")
    assert codigo != 0 and "✘ DIAN_TEST_SET_ID" in salida


def test_verificar_produccion_certificado_de_prueba(escenario, tmp_path, capsys):
    activa_edsc(escenario)
    ruta = certificado_pfx(tmp_path, de_prueba=True)
    codigo, salida = verificar(escenario, tmp_path, capsys, DIAN_CERTIFICADO_RUTA=ruta)
    assert codigo != 0 and "✘ el certificado es de PRUEBA" in salida


def test_verificar_produccion_sin_numeracion_de_produccion(escenario, tmp_path, capsys):
    codigo, salida = verificar(escenario, tmp_path, capsys)  # solo la SETP (ambiente 2)
    assert codigo != 0 and "✘ no hay una numeración activa de producción" in salida


def test_verificar_produccion_sin_smtp(escenario, tmp_path, capsys):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys, ESTACION_SMTP_USUARIO="")
    assert codigo != 0 and "✘ falta ESTACION_SMTP_USUARIO" in salida


def test_verificar_produccion_prefijo_que_la_dian_no_devuelve(escenario, tmp_path, capsys):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys, dian=dian_produccion(prefijo="OTRO"))
    assert codigo != 0 and "✘ la DIAN no devolvió el prefijo EDSC" in salida


def test_verificar_produccion_con_pendientes_de_habilitacion(escenario, tmp_path, capsys):
    pendiente_de_habilitacion(escenario)  # primero: la factura se emite con la SETP (ambiente 2) activa
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys)
    assert codigo != 0 and "✘ 1 documento(s) de habilitación (ambiente 2) pendientes" in salida


def test_verificar_produccion_exige_ambiente_1(escenario):
    assert habilitacion.main(["verificar-produccion"], entorno=entorno(escenario), cliente_dian=dian_produccion()) == 2


def test_correo_emisor_muestra_el_actual(escenario, capsys):
    assert habilitacion.main(["correo-emisor"], entorno=entorno(escenario)) == 0
    assert "estacion@example.com" in capsys.readouterr().out


def test_correo_emisor_cambia_y_audita(escenario):
    nuevo = "alistaconcordefactutacion@gmail.com"
    assert habilitacion.main(["correo-emisor", "--nuevo", nuevo], entorno=entorno(escenario),
                             confirmar=lambda _: "SI") == 0  # fmt: skip
    assert escenario.datos.uno("SELECT email FROM estacion WHERE id = %s", (escenario.estacion,)) == nuevo
    filas = escenario.datos.conn.execute(
        "SELECT detalle::text FROM auditoria WHERE accion = 'EMISOR_CORREO_CAMBIADO'"
    ).fetchall()
    assert len(filas) == 1 and nuevo not in filas[0][0]  # la auditoría no guarda el correo


def test_correo_emisor_invalido_o_sin_confirmar_no_cambia_nada(escenario):
    e = entorno(escenario)
    assert habilitacion.main(["correo-emisor", "--nuevo", "no-es-correo"], entorno=e, confirmar=lambda _: "SI") != 0
    assert habilitacion.main(["correo-emisor", "--nuevo", "otro@example.com"], entorno=e, confirmar=lambda _: "no") != 0
    assert (
        escenario.datos.uno("SELECT email FROM estacion WHERE id = %s", (escenario.estacion,)) == "estacion@example.com"
    )
    assert escenario.datos.uno("SELECT count(*) FROM auditoria WHERE accion = 'EMISOR_CORREO_CAMBIADO'") == 0


def test_guardar_clave_tecnica_escribe_el_archivo_y_no_la_imprime(escenario, tmp_path, capsys):
    e = entorno_produccion(escenario, tmp_path, DIAN_CLAVE_TECNICA="")  # no hace falta tenerla en el .env
    ruta = tmp_path / "clave.env"
    dian = dian_produccion()
    assert habilitacion.main(["guardar-clave-tecnica", "--prefijo", "EDSC", "--archivo", str(ruta)], entorno=e,
                             cliente_dian=dian) == 0  # fmt: skip
    assert ruta.read_text() == f"DIAN_CLAVE_TECNICA={CLAVE_TECNICA}\n"
    assert (ruta.stat().st_mode & 0o777) == 0o600
    captura = capsys.readouterr()
    assert CLAVE_TECNICA not in captura.out + captura.err and str(ruta) in captura.out
    assert dian.llamadas == [("900123456", ENTORNO_DIAN["DIAN_SOFTWARE_ID"])]


def test_guardar_clave_tecnica_se_niega_si_el_archivo_existe(escenario, tmp_path, capsys):
    ruta = tmp_path / "clave.env"
    ruta.write_text("lo que había")
    e = entorno_produccion(escenario, tmp_path)
    assert habilitacion.main(["guardar-clave-tecnica", "--prefijo", "EDSC", "--archivo", str(ruta)], entorno=e,
                             cliente_dian=dian_produccion()) == 1  # fmt: skip
    assert ruta.read_text() == "lo que había"
    captura = capsys.readouterr()
    assert CLAVE_TECNICA not in captura.out + captura.err


def test_guardar_clave_tecnica_sin_clave_o_sin_prefijo_no_escribe(escenario, tmp_path):
    e = entorno_produccion(escenario, tmp_path)
    vacia = tmp_path / "a.env"
    assert habilitacion.main(["guardar-clave-tecnica", "--prefijo", "EDSC", "--archivo", str(vacia)], entorno=e,
                             cliente_dian=dian_produccion(clave_tecnica="")) == 1  # fmt: skip
    otro = tmp_path / "b.env"
    assert habilitacion.main(["guardar-clave-tecnica", "--prefijo", "XXXX", "--archivo", str(otro)], entorno=e,
                             cliente_dian=dian_produccion()) == 1  # fmt: skip
    assert not vacia.exists() and not otro.exists()


def test_guardar_clave_tecnica_exige_ambiente_1(escenario, tmp_path):
    ruta = tmp_path / "clave.env"
    assert habilitacion.main(["guardar-clave-tecnica", "--prefijo", "EDSC", "--archivo", str(ruta)],
                             entorno=entorno(escenario), cliente_dian=dian_produccion()) == 2  # fmt: skip
    assert not ruta.exists()


def test_configurar_produccion_clave_no_ascii_no_revienta(escenario, tmp_path, capsys):
    # hmac.compare_digest sobre str con caracteres no ASCII lanzaba TypeError: se comparan bytes.
    e = entorno_produccion(escenario, tmp_path, DIAN_CLAVE_TECNICA="clavé-técnica")
    assert habilitacion.main(["configurar-produccion", "--prefijo", "EDSC"], entorno=e,
                             confirmar=lambda _: "EDSC", cliente_dian=dian_produccion()) == 1  # fmt: skip
    captura = capsys.readouterr()
    assert "no coincide" in captura.err and "clavé-técnica" not in captura.out + captura.err
    assert numeraciones(escenario) == [("SETP", "2", 1, True)]


def test_verificar_produccion_todo_bien_compara_clave_resolucion_y_rango(escenario, tmp_path, capsys):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys)
    assert codigo == 0
    assert "✔ DIAN_CLAVE_TECNICA coincide con la del rango EDSC en la DIAN" in salida
    assert "✔ numeración EDSC igual a la de la DIAN: resolución 18764000000001, 1-1000000" in salida


@pytest.mark.parametrize("clave", ["otra-clave-secreta", "clavé-técnica-ñ"])
def test_verificar_produccion_clave_tecnica_distinta(escenario, tmp_path, capsys, clave):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys, DIAN_CLAVE_TECNICA=clave)
    assert codigo != 0 and "✘ DIAN_CLAVE_TECNICA no coincide con la clave técnica del rango EDSC" in salida
    assert clave not in salida and CLAVE_TECNICA not in salida


def test_verificar_produccion_dian_sin_clave_tecnica(escenario, tmp_path, capsys):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys, dian=dian_produccion(clave_tecnica=""))
    assert codigo != 0 and "✘ DIAN_CLAVE_TECNICA no coincide" in salida


@pytest.mark.parametrize(
    "cambio",
    [dict(resolucion="18764000000999"), dict(desde=5), dict(hasta=500000)],
    ids=["resolucion", "desde", "hasta"],
)
def test_verificar_produccion_numeracion_distinta_de_la_dian(escenario, tmp_path, capsys, cambio):
    activa_edsc(escenario)
    codigo, salida = verificar(escenario, tmp_path, capsys, dian=dian_produccion(**cambio))
    assert codigo != 0
    assert "✘ la numeración EDSC activa (resolución 18764000000001, 1-1000000) no es la de la DIAN" in salida


def _guardar(escenario, tmp_path, ruta, dian=None):
    e = entorno_produccion(escenario, tmp_path, DIAN_CLAVE_TECNICA="")
    return habilitacion.main(["guardar-clave-tecnica", "--prefijo", "EDSC", "--archivo", str(ruta)], entorno=e,
                             cliente_dian=dian or dian_produccion())  # fmt: skip


def test_guardar_clave_tecnica_se_niega_dentro_del_repositorio(escenario, tmp_path, capsys):
    raiz = habilitacion._raiz_del_repositorio()
    assert raiz is not None and (raiz / "backend").is_dir()
    ruta = raiz / "backend" / f"clave-{uuid.uuid4().hex[:8]}.env"
    dian = dian_produccion()
    assert _guardar(escenario, tmp_path, ruta, dian) == 1
    assert not ruta.exists() and dian.llamadas == []  # ni consulta a la DIAN
    captura = capsys.readouterr()
    assert "dentro del repositorio" in captura.err and CLAVE_TECNICA not in captura.out + captura.err


def test_guardar_clave_tecnica_se_niega_si_un_enlace_lleva_al_repositorio(escenario, tmp_path, capsys):
    raiz = habilitacion._raiz_del_repositorio()
    enlace = tmp_path / "enlace"
    enlace.symlink_to(raiz / "backend", target_is_directory=True)
    nombre = f"clave-{uuid.uuid4().hex[:8]}.env"
    assert _guardar(escenario, tmp_path, enlace / nombre) == 1
    assert not (raiz / "backend" / nombre).exists()
    assert "dentro del repositorio" in capsys.readouterr().err
    # Un enlace roto en la ruta pedida tampoco se sigue: ya "existe".
    colgante = tmp_path / "colgante.env"
    colgante.symlink_to(raiz / "backend" / nombre)
    assert _guardar(escenario, tmp_path, colgante) == 1
    assert not (raiz / "backend" / nombre).exists()


def test_guardar_clave_tecnica_fallo_al_escribir_borra_el_archivo(escenario, tmp_path, capsys, monkeypatch):
    import errno

    real = habilitacion.os.fdopen

    class Llena:  # el disco se llena a mitad de la línea
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.f.close()

        def write(self, texto):
            self.f.write(texto[:22])
            self.f.flush()
            raise OSError(errno.ENOSPC, "No queda espacio en el dispositivo")

    monkeypatch.setattr(habilitacion.os, "fdopen", lambda *a, **k: Llena(real(*a, **k)))
    ruta = tmp_path / "clave.env"
    assert _guardar(escenario, tmp_path, ruta) == 1
    assert not ruta.exists()  # no queda media clave en el disco
    captura = capsys.readouterr()
    assert "no se pudo escribir" in captura.err and CLAVE_TECNICA not in captura.out + captura.err
