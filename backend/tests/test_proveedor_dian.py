"""ProveedorDian con un ClienteDian falso: mapeo de respuestas a la interfaz de la cola. Sin red."""

from __future__ import annotations

import base64
import dataclasses
import io
import zipfile
from datetime import datetime
from decimal import Decimal

import pytest

from estacion.facturacion.dian.cliente import (
    ErrorSeguridadDian,
    ErrorTransporteDian,
    RespuestaDian,
    RespuestaEnvioAsincrono,
)
from estacion.facturacion.dian.proveedor import ProveedorDian
from estacion.facturacion.modelos import Cliente, EnvioPendiente, LineaFactura, SolicitudFactura
from estacion.facturacion.proveedor import ProveedorIncierto, ProveedorNoConfigurado, ProveedorRechazo

AHORA = datetime(2026, 10, 13, 15, 0)
VALIDA = RespuestaDian(True, "00", "Procesado Correctamente.", (), b"<ApplicationResponse/>", "cufe-1")
ZIP = "z0901335327000263b023380.zip"
XML = "fv0901335327000263b023380.xml"


class ClienteFalso:
    def __init__(self, **respuestas):
        self.r = respuestas
        self.llamadas: list[tuple] = []

    def _dar(self, nombre, *args):
        self.llamadas.append((nombre, *args))
        r = self.r[nombre]
        if isinstance(r, Exception):
            raise r
        return r

    def enviar_set_pruebas(self, nombre_zip, zip_b64, test_set_id):
        return self._dar("enviar_set_pruebas", nombre_zip, zip_b64, test_set_id)

    def enviar_sincrono(self, nombre_zip, zip_b64):
        return self._dar("enviar_sincrono", nombre_zip, zip_b64)

    def estado_zip(self, track_id):
        return self._dar("estado_zip", track_id)

    def estado(self, cufe):
        return self._dar("estado", cufe)


def solicitud() -> SolicitudFactura:
    linea = LineaFactura(
        "DIESEL", "Diesel", "GLL", Decimal("1.861"), Decimal("15990"), Decimal("29757"), Decimal("0"), "S:1"
    )
    return SolicitudFactura(
        clave="k1", cliente=Cliente("CC", "123", "ANA"), lineas=(linea,), forma_pago="CONTADO", medio_pago="EFECTIVO",
        fecha_emision=AHORA, numero="SETP990000000", cufe="cufe-1", documento=b"<Invoice/>",
    )  # fmt: skip


def proveedor(cliente, test_set_id: str | None = "set-1") -> ProveedorDian:
    return ProveedorDian(cliente, nit_emisor="901335327", test_set_id=test_set_id, reloj=lambda: AHORA)


def consultar(p: ProveedorDian, track_id: str | None = "zip-1"):
    return p.consultar(clave="k1", numero="SETP990000000", cufe="cufe-1", track_id=track_id)


def test_set_de_pruebas_envia_el_zip_y_queda_pendiente():
    c = ClienteFalso(enviar_set_pruebas=RespuestaEnvioAsincrono("zip-1", ()))
    p = proveedor(c)
    assert p.modo == "set_pruebas"
    assert p.emitir(solicitud()) == EnvioPendiente("zip-1")
    _, nombre_zip, zip_b64, test_set = c.llamadas[0]
    assert nombre_zip == ZIP and test_set == "set-1"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(zip_b64))) as z:
        assert z.namelist() == [XML] and z.read(XML) == b"<Invoice/>"


def test_proveedor_recibe_nc_en_el_nombre():
    c = ClienteFalso(enviar_sincrono=VALIDA)
    nota = dataclasses.replace(solicitud(), numero="NCE12", tipo_documento="nc", documento=b"<CreditNote/>")
    proveedor(c, test_set_id=None).emitir(nota)
    _, nombre_zip, zip_b64 = c.llamadas[0]
    assert nombre_zip.startswith("z")  # el ZIP sigue con "z"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(zip_b64))) as z:
        (interno,) = z.namelist()
        assert interno.startswith("nc") and interno.endswith("0000000c.xml")  # consecutivo 12 en hexadecimal


def test_set_sin_zip_key_con_errores_es_rechazo():
    c = ClienteFalso(enviar_set_pruebas=RespuestaEnvioAsincrono("", ("archivo mal formado",)))
    with pytest.raises(ProveedorRechazo, match="archivo mal formado"):
        proveedor(c).emitir(solicitud())


def test_modo_normal_acepta():
    c = ClienteFalso(enviar_sincrono=VALIDA)
    p = proveedor(c, test_set_id=None)
    assert p.modo == "normal"
    r = p.emitir(solicitud())
    assert (r.cufe, r.numero, r.prefijo) == ("cufe-1", "SETP990000000", "SETP")
    assert r.application_response == b"<ApplicationResponse/>" and r.detalle["codigo_dian"] == "00"
    assert c.llamadas[0][1] == ZIP


def test_rechazo_lleva_los_mensajes():
    c = ClienteFalso(enviar_sincrono=RespuestaDian(False, "99", "Errores", ("FAD06 CUFE", "FAJ21 NIT"), None, ""))
    with pytest.raises(ProveedorRechazo, match="FAD06 CUFE; FAJ21 NIT"):
        proveedor(c, test_set_id=None).emitir(solicitud())


def test_documento_procesado_anteriormente_es_incierto():
    # Review Focus 1: un reintento tras una caída reenvía lo mismo; no es un rechazo, se concilia por CUFE.
    errores = ("Regla: 90, Rechazo: Documento procesado anteriormente.",)
    c = ClienteFalso(enviar_sincrono=RespuestaDian(False, "99", "Errores", errores, None, ""))
    with pytest.raises(ProveedorIncierto, match="procesado anteriormente"):
        proveedor(c, test_set_id=None).emitir(solicitud())


@pytest.mark.parametrize(
    "respuesta",
    [
        RespuestaDian(False, "98", "Intente más tarde", (), None, ""),  # visto en habilitación el 2026-11-04
        RespuestaDian(False, "99", "Errores", ("Intente más tarde",), None, ""),
    ],
)
def test_intente_mas_tarde_es_incierto_no_rechazo(respuesta):
    # "98 Intente más tarde" es la DIAN ocupada, no un problema del documento: rechazarlo dejaba la venta pendiente
    # para revisión humana. Incierto = se concilia por CUFE y se reintenta solo (ver servicios/outbox.py).
    c = ClienteFalso(enviar_sincrono=respuesta, estado=respuesta)
    with pytest.raises(ProveedorIncierto, match="más tarde"):
        proveedor(c, test_set_id=None).emitir(solicitud())
    with pytest.raises(ProveedorIncierto, match="más tarde"):
        consultar(proveedor(c), track_id=None)


def test_valida_sin_application_response_no_falla():
    # Review Focus 5: la factura es válida aunque falte el XML de respuesta; la entrega lo manejará.
    c = ClienteFalso(enviar_sincrono=RespuestaDian(True, "00", "OK", (), None, "cufe-1"))
    assert proveedor(c, test_set_id=None).emitir(solicitud()).application_response is None


def test_cufe_vacio_en_la_respuesta_usa_el_de_la_factura():
    c = ClienteFalso(enviar_sincrono=RespuestaDian(True, "00", "OK", (), b"<A/>", ""))
    assert proveedor(c, test_set_id=None).emitir(solicitud()).cufe == "cufe-1"


@pytest.mark.parametrize(
    ("error", "esperado"),
    [(ErrorTransporteDian("timeout"), ProveedorIncierto), (ErrorSeguridadDian("x"), ProveedorNoConfigurado)],
)
def test_errores_del_cliente(error, esperado):
    with pytest.raises(esperado):
        proveedor(ClienteFalso(enviar_set_pruebas=error)).emitir(solicitud())
    with pytest.raises(esperado):
        consultar(proveedor(ClienteFalso(estado_zip=error)))


def test_consultar_con_track_id_usa_get_status_zip():
    en_proceso = RespuestaDian(False, "", "Batch en proceso de validación.", (), None, "")
    c = ClienteFalso(estado_zip=[en_proceso])
    assert consultar(proveedor(c)) == EnvioPendiente("zip-1")
    c.r["estado_zip"] = [VALIDA]
    assert consultar(proveedor(c)).cufe == "cufe-1"
    assert c.llamadas[-1] == ("estado_zip", "zip-1")


def test_consultar_sin_track_id_usa_get_status_por_cufe():
    c = ClienteFalso(estado=RespuestaDian(False, "66", "NSU no encontrado", (), None, ""))
    assert consultar(proveedor(c), track_id=None) is None
    assert c.llamadas == [("estado", "cufe-1")]


def test_consultar_lote_vacio_o_no_encontrado_sigue_pendiente():
    # Revisión final: con ZipKey la DIAN SÍ recibió el lote; "vacío" o "no encontrado" es "todavía no", no "no existe"
    # (devolver None reenviaría y duplicaría el documento).
    assert consultar(proveedor(ClienteFalso(estado_zip=[]))) == EnvioPendiente("zip-1")
    no_hay = RespuestaDian(False, "66", "NSU no encontrado", (), None, "")
    assert consultar(proveedor(ClienteFalso(estado_zip=[no_hay]))) == EnvioPendiente("zip-1")


def test_lote_ya_procesado_se_concilia_por_cufe():
    # Revisión final (crítico): tras una caída se reenvió con otro ZipKey; ese lote dice "procesado anteriormente".
    # Hay que preguntar por el CUFE (la factura original), no volver a consultar el mismo lote para siempre.
    duplicado = RespuestaDian(False, "99", "Errores", ("Regla: 90, Rechazo: Documento procesado anteriormente.",),
                              None, "")  # fmt: skip
    c = ClienteFalso(estado_zip=[duplicado], estado=VALIDA)
    r = consultar(proveedor(c))
    assert r.cufe == "cufe-1" and r.application_response == b"<ApplicationResponse/>"
    assert c.llamadas == [("estado_zip", "zip-1"), ("estado", "cufe-1")]


def test_consultar_rechazo():
    c = ClienteFalso(estado_zip=[RespuestaDian(False, "99", "Errores", ("FAD06",), None, "")])
    with pytest.raises(ProveedorRechazo, match="FAD06"):
        consultar(proveedor(c))


def test_sin_documento_firmado_no_envia():
    sin_doc = dataclasses.replace(solicitud(), documento=None)
    c = ClienteFalso()
    with pytest.raises(ValueError, match="documento firmado"):
        proveedor(c).emitir(sin_doc)
    assert c.llamadas == []
