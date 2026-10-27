"""Entrega por la cola: firmar antes de emitir, guardar la validación y enviar el ZIP por correo.
PostgreSQL real, ProveedorSimulado y EnviadorEnMemoria: nada sale a la DIAN ni a un correo real."""

from __future__ import annotations

import io
import zipfile

import pytest

from estacion.entrega.correo import EnviadorEnMemoria
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.outbox import Recursos, procesar_pendientes, procesar_siguiente
from test_outbox import acciones, cola, factura, solicitar

pytestmark = pytest.mark.bd


def entrega(escenario, factura_id: int) -> tuple | None:
    return escenario.datos.conn.execute(
        "SELECT estado, nombre_zip, zip, intentos, ultimo_error FROM entrega WHERE factura_id = %s", (factura_id,)
    ).fetchone()


def avanzar_reloj(escenario) -> None:
    escenario.datos.conn.execute("UPDATE outbox SET proximo_intento = now() WHERE estado = 'PENDIENTE'")


def test_facturada_se_entrega_por_correo(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    enviador = EnviadorEnMemoria()
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, enviador))

    assert factura(escenario, f.id)[0] == "FACTURADO"
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "HECHO", 1), ("ENVIAR_CORREO", "HECHO", 1)]
    (m,) = enviador.enviados
    assert m.destinatario == "ana@example.com" and m.remitente == "estacion@example.com"
    assert m.asunto == f"900123456;ESTACION DE PRUEBA S A S;{f.numero};01;ESTACION DE PRUEBA S A S"
    estado, nombre_zip, zip_guardado, *_ = entrega(escenario, f.id)
    assert estado == "ENVIADA" and m.adjunto_nombre == nombre_zip and bytes(zip_guardado) == m.adjunto
    with zipfile.ZipFile(io.BytesIO(m.adjunto)) as z:
        assert len(z.namelist()) == 2
    d = escenario.datos
    assert "SignatureValue" in d.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,))
    assert "SIMULADO" in d.uno("SELECT application_response FROM factura WHERE id = %s", (f.id,))
    assert acciones(escenario, f.id)[-1] == "FACTURA_ENTREGADA"


def test_sin_certificado_no_envia_y_se_recupera(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    procesar_pendientes(conn, proveedor, recursos=Recursos(None, EnviadorEnMemoria(), "falta DIAN_CERTIFICADO_RUTA"))
    assert proveedor.llamadas == 0  # NUNCA se envía un documento sin firma
    assert factura(escenario, f.id)[0] == "EN_PROCESO"
    # Configuración que se puede corregir: se reintenta más tarde (no queda atascada), nunca se envía sin firma.
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "PENDIENTE", 1)]
    error = escenario.datos.uno("SELECT ultimo_error FROM outbox WHERE factura_id = %s", (f.id,))
    assert "falta DIAN_CERTIFICADO_RUTA" in error and "python -m estacion.certificado" in error
    assert "FIRMA_NO_DISPONIBLE" in acciones(escenario, f.id)

    # Ya con el certificado configurado, el siguiente intento la firma y la emite.
    avanzar_reloj(escenario)
    procesar_pendientes(conn, proveedor, recursos=Recursos(certificado, EnviadorEnMemoria()))
    assert factura(escenario, f.id)[0] == "FACTURADO"


def test_certificado_vencido_falla_cerrado(escenario, conn):
    from datetime import UTC, datetime, timedelta

    from estacion.facturacion.certificado_prueba import generar_certificado_prueba

    viejo = generar_certificado_prueba("X", dias=1, ahora=datetime.now(UTC) - timedelta(days=10))
    f, _ = solicitar(escenario, conn)
    proveedor = ProveedorSimulado()
    procesar_pendientes(conn, proveedor, recursos=Recursos(viejo, EnviadorEnMemoria()))
    assert proveedor.llamadas == 0
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "PENDIENTE", 1)]
    assert "venció" in escenario.datos.uno("SELECT ultimo_error FROM outbox WHERE factura_id = %s", (f.id,))


def test_correo_temporal_reintenta_y_rechazo_no(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    e = EnviadorEnMemoria(falla_siguiente="temporal")
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "PENDIENTE", 1)
    assert entrega(escenario, f.id)[0] == "PENDIENTE"

    avanzar_reloj(escenario)
    e.falla_siguiente = "rechazo"
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "FALLIDO", 2)
    estado, _, _, intentos, error = entrega(escenario, f.id)
    assert (estado, intentos) == ("FALLIDA", 2) and "rechazó" in error
    assert factura(escenario, f.id)[0] == "FACTURADO"  # ante la DIAN la factura sigue válida


def test_reintentos_agotados(escenario, conn, certificado):
    from estacion.servicios.outbox import PoliticaReintentos

    f, _ = solicitar(escenario, conn)
    politica = PoliticaReintentos(max_intentos=2)
    e = EnviadorEnMemoria(falla_siguiente="temporal")
    procesar_pendientes(conn, ProveedorSimulado(), politica, recursos=Recursos(certificado, e))
    avanzar_reloj(escenario)
    e.falla_siguiente = "temporal"
    procesar_pendientes(conn, ProveedorSimulado(), politica, recursos=Recursos(certificado, e))
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "FALLIDO", 2)
    assert entrega(escenario, f.id)[0] == "FALLIDA"


def test_reintento_reutiliza_el_mismo_zip(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    e = EnviadorEnMemoria(falla_siguiente="temporal")
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    primero = bytes(entrega(escenario, f.id)[2])
    avanzar_reloj(escenario)
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert e.enviados[0].adjunto == primero
    assert escenario.datos.uno("SELECT count(*) FROM entrega_consecutivo") == 1


def test_consecutivo_de_entrega_por_anio_sin_repetir(escenario, conn, certificado):
    e = EnviadorEnMemoria()
    for _ in range(3):
        solicitar(escenario, conn)
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    nombres = sorted(m.adjunto_nombre for m in e.enviados)
    assert [n[-12:-4] for n in nombres] == ["00000001", "00000002", "00000003"]


def test_rechazada_y_reenviada_se_vuelve_a_firmar(escenario, conn, certificado):
    f, origen = solicitar(escenario, conn)
    p = ProveedorSimulado()
    p.falla_siguiente = "rechazo"
    procesar_pendientes(conn, p, recursos=Recursos(certificado, EnviadorEnMemoria()))
    d = escenario.datos
    primera = d.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,))
    assert primera is not None and factura(escenario, f.id)[0] == "RECHAZADO"

    solicitar(escenario, conn, origen)  # se corrige y se reenvía con el mismo número
    assert d.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,)) is None
    procesar_pendientes(conn, p, recursos=Recursos(certificado, EnviadorEnMemoria()))
    assert factura(escenario, f.id)[0] == "FACTURADO"
    assert d.uno("SELECT xml_firmado FROM factura WHERE id = %s", (f.id,)) != primera


def test_dos_workers_un_solo_correo(escenario, conn, certificado):
    f, _ = solicitar(escenario, conn)
    e = EnviadorEnMemoria(falla_siguiente="temporal")
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    avanzar_reloj(escenario)
    otro = escenario.conectar()
    try:
        with otro.transaction():  # otro worker tiene tomado el trabajo de correo
            otro.execute("SELECT id FROM outbox WHERE tipo = 'ENVIAR_CORREO' FOR UPDATE")
            assert procesar_siguiente(conn, ProveedorSimulado(), recursos=Recursos(certificado, e)) is None
    finally:
        otro.close()
    assert e.enviados == []


def test_sin_certificado_agota_los_reintentos(escenario, conn):
    from estacion.servicios.outbox import PoliticaReintentos

    f, _ = solicitar(escenario, conn)
    recursos = Recursos(None, EnviadorEnMemoria(), "falta DIAN_CERTIFICADO_RUTA")
    for _ in range(2):
        procesar_pendientes(conn, ProveedorSimulado(), PoliticaReintentos(max_intentos=2), recursos=recursos)
        avanzar_reloj(escenario)
    assert cola(escenario, f.id) == [("ENVIAR_DIAN", "FALLIDO", 2)]
    assert factura(escenario, f.id)[0] == "EN_PROCESO"


# ------------------------------------------------------------------------------------------ revisión final
def test_error_inesperado_no_bloquea_la_cola(escenario, conn, certificado, monkeypatch):
    # I1: una excepción no prevista (p. ej. del generador de PDF) no puede dejar el trabajo de primero para siempre.
    from estacion.facturacion import paquete

    primera, _ = solicitar(escenario, conn)
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(None, EnviadorEnMemoria(), "x"))  # en espera
    original = paquete.armar

    def roto(*a, **k):
        raise RuntimeError("falla interna con datos de Ana Pérez")

    monkeypatch.setattr(paquete, "armar", roto)
    avanzar_reloj(escenario)
    segunda, _ = solicitar(escenario, conn)
    hechos = procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, EnviadorEnMemoria()))
    assert factura(escenario, primera.id)[0] == "FACTURADO" and factura(escenario, segunda.id)[0] == "FACTURADO"
    assert len(hechos) == 4  # 2 envíos a la DIAN + 2 correos: ninguno se quedó atascado delante de los demás
    assert cola(escenario, primera.id)[-1] == ("ENVIAR_CORREO", "PENDIENTE", 1)  # reprogramado, no en bucle
    error = escenario.datos.uno("SELECT ultimo_error FROM outbox WHERE factura_id = %s AND tipo = 'ENVIAR_CORREO'",
                                (primera.id,))  # fmt: skip
    assert "RuntimeError" in error and "Ana" not in error  # sin datos personales
    assert "TRABAJO_ERROR_INESPERADO" in acciones(escenario, primera.id)

    monkeypatch.setattr(paquete, "armar", original)
    avanzar_reloj(escenario)
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, EnviadorEnMemoria()))
    assert entrega(escenario, primera.id)[0] == "ENVIADA"


def test_sin_certificado_al_entregar_espera_y_se_recupera(escenario, conn, certificado):
    # I2: el contenedor también se firma; sin certificado la entrega espera (no queda FALLIDA para siempre).
    f, _ = solicitar(escenario, conn)
    procesar_siguiente(conn, ProveedorSimulado(), recursos=Recursos(certificado, EnviadorEnMemoria()))  # FACTURADO
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(None, EnviadorEnMemoria(), "falta la clave"))
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "PENDIENTE", 1)
    assert entrega(escenario, f.id) is None
    avanzar_reloj(escenario)
    e = EnviadorEnMemoria()
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))
    assert entrega(escenario, f.id)[0] == "ENVIADA" and len(e.enviados) == 1


def test_el_zip_queda_guardado_antes_de_enviar(escenario, conn, certificado):
    # I3: si el correo sale y luego se pierde la transacción, el reintento manda EL MISMO ZIP (ya confirmado).
    f, _ = solicitar(escenario, conn)

    class SaleYSeCae(EnviadorEnMemoria):
        def enviar(self, m):
            super().enviar(m)
            raise ConnectionError("se cayó el worker después de entregar al servidor de correo")

    e = SaleYSeCae()
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e))  # el envío se deshace
    guardado = entrega(escenario, f.id)
    assert guardado is not None and bytes(guardado[2]) == e.enviados[0].adjunto  # lo enviado ES lo guardado
    assert cola(escenario, f.id)[-1][:2] == ("ENVIAR_CORREO", "PENDIENTE")

    avanzar_reloj(escenario)
    otro = EnviadorEnMemoria()
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, otro))
    assert otro.enviados[0].adjunto == e.enviados[0].adjunto  # el reintento no arma otro ZIP


def _facturada_con_correo_pendiente(escenario, conn, certificado, *, ambiente: str):
    """Una factura FACTURADA con su ENVIAR_CORREO pendiente, marcada del `ambiente` dado."""
    f, _ = solicitar(escenario, conn)
    procesar_siguiente(conn, ProveedorSimulado(), recursos=Recursos(certificado, EnviadorEnMemoria()))  # FACTURADO
    escenario.datos.conn.execute("UPDATE factura SET ambiente = %s WHERE id = %s", (ambiente, f.id))
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "PENDIENTE", 0)
    return f


def test_produccion_con_certificado_de_prueba_no_arma_ni_envia(escenario, conn, certificado):
    # Un documento de producción no sale al cliente con el contenedor firmado por un certificado de prueba.
    f = _facturada_con_correo_pendiente(escenario, conn, certificado, ambiente="1")
    e = EnviadorEnMemoria()
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e, ambiente="1"))

    assert e.enviados == [] and entrega(escenario, f.id) is None
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "FALLIDO", 1)  # sin reintentos en bucle
    error = escenario.datos.uno("SELECT ultimo_error FROM outbox WHERE factura_id = %s AND tipo = 'ENVIAR_CORREO'",
                                (f.id,))  # fmt: skip
    assert "certificado de prueba" in error and "producción" in error
    assert acciones(escenario, f.id)[-1] == "ENTREGA_NO_ARMADA"
    assert escenario.datos.uno("SELECT count(*) FROM entrega_consecutivo") == 0  # no se gastó consecutivo


@pytest.mark.parametrize(("documento", "worker"), [("1", "2"), ("2", "1")])
def test_correo_de_otro_ambiente_no_se_entrega(escenario, conn, certificado, documento, worker):
    # Un worker de habilitación no entrega documentos de producción (firmaría el contenedor con su certificado de
    # prueba) y uno de producción no manda al cliente documentos de habilitación: FALLIDO y auditado.
    f = _facturada_con_correo_pendiente(escenario, conn, certificado, ambiente=documento)
    e = EnviadorEnMemoria()
    procesar_pendientes(conn, ProveedorSimulado(), recursos=Recursos(certificado, e, ambiente=worker))

    assert e.enviados == [] and entrega(escenario, f.id) is None
    assert cola(escenario, f.id)[-1] == ("ENVIAR_CORREO", "FALLIDO", 1)
    error = escenario.datos.uno("SELECT ultimo_error FROM outbox WHERE factura_id = %s AND tipo = 'ENVIAR_CORREO'",
                                (f.id,))  # fmt: skip
    assert f"ambiente {documento}" in error and f"ambiente {worker}" in error
    assert acciones(escenario, f.id)[-1] == "AMBIENTE_NO_COINCIDE"
