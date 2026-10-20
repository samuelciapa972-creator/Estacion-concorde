"""API de la entrega: estado en la factura, descarga del ZIP y reenvío (admin). Nada sale a un correo real."""

from __future__ import annotations

import pytest

from conftest import clave
from estacion.entrega.correo import EnviadorEnMemoria
from estacion.facturacion.proveedor import ProveedorSimulado
from estacion.servicios.outbox import Recursos, procesar_pendientes

pytestmark = pytest.mark.bd


def emitir(api) -> int:
    d = api.e.datos
    r = api.post("/facturas", {"clave": clave(), "origen_tipo": "despacho", "origen_id": d.despacho(),
                               "cliente_id": api.e.cliente, "medio_pago": "EFECTIVO"}, como=api.bombero)  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()["id"]


def worker(api, certificado, enviador) -> None:
    procesar_pendientes(api.e.datos.conn, ProveedorSimulado(), recursos=Recursos(certificado, enviador))


def test_estado_de_la_entrega_y_descarga(api, certificado):
    fid = emitir(api)
    assert api.get(f"/facturas/{fid}", como=api.bombero).json()["entrega"] is None  # todavía en cola
    worker(api, certificado, EnviadorEnMemoria())

    f = api.get(f"/facturas/{fid}", como=api.bombero).json()
    assert f["entrega"]["estado"] == "ENVIADA" and f["entrega"]["nombre_zip"].startswith("z")
    assert f["entrega"]["enviada_en"] is not None
    z = api.get(f"/facturas/{fid}/zip", como=api.bombero)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert z.content.startswith(b"PK")
    assert f'filename="{f["entrega"]["nombre_zip"]}"' in z.headers["content-disposition"]
    assert api.e.datos.uno("SELECT count(*) FROM auditoria WHERE accion = 'FACTURA_ZIP'") == 1


def test_reenviar_va_al_correo_corregido_con_el_mismo_zip(api, certificado):
    e = EnviadorEnMemoria()
    fid = emitir(api)
    worker(api, certificado, e)
    api.e.datos.conn.execute("UPDATE cliente SET email = 'nuevo@example.com' WHERE id = %s", (api.e.cliente,))

    r = api.post(f"/facturas/{fid}/reenviar", {}, como=api.admin)
    assert r.status_code == 202, r.text
    assert api.get(f"/facturas/{fid}", como=api.admin).json()["entrega"]["estado"] == "PENDIENTE"
    repetido = api.post(f"/facturas/{fid}/reenviar", {}, como=api.admin)
    assert repetido.status_code == 409 and "en curso" in repetido.json()["detail"]

    worker(api, certificado, e)
    assert [m.destinatario for m in e.enviados] == ["ana@example.com", "nuevo@example.com"]
    assert e.enviados[0].adjunto == e.enviados[1].adjunto  # el MISMO ZIP que ya se le entregó
    assert api.get(f"/facturas/{fid}", como=api.admin).json()["entrega"]["estado"] == "ENVIADA"


def test_permisos_y_errores(api, certificado):
    assert api.get("/facturas/999/zip", como=api.bombero).status_code == 404
    assert api.post("/facturas/999/reenviar", {}, como=api.admin).status_code == 404
    fid = emitir(api)
    assert api.get(f"/facturas/{fid}/zip", como=api.bombero).status_code == 404  # sin entrega todavía
    assert api.post(f"/facturas/{fid}/reenviar", {}, como=api.admin).status_code == 409  # no está FACTURADA
    worker(api, certificado, EnviadorEnMemoria())
    assert api.post(f"/facturas/{fid}/reenviar", {}, como=api.bombero).status_code == 403
    assert api.get(f"/facturas/{fid}/zip").status_code == 401


def test_reenviar_recupera_una_entrega_que_no_se_pudo_armar(api, certificado, monkeypatch):
    # I2: si el ZIP no se pudo armar (entrega FALLIDA sin archivo), el admin puede volver a intentarlo.
    from estacion.facturacion import paquete

    original = paquete.armar

    def demasiado_grande(*a, **k):
        raise paquete.PaqueteDemasiadoGrande("el ZIP pesa demasiado")

    monkeypatch.setattr(paquete, "armar", demasiado_grande)
    fid = emitir(api)
    worker(api, certificado, EnviadorEnMemoria())
    assert api.get(f"/facturas/{fid}", como=api.admin).json()["entrega"] is None

    monkeypatch.setattr(paquete, "armar", original)
    assert api.post(f"/facturas/{fid}/reenviar", {}, como=api.admin).status_code == 202
    e = EnviadorEnMemoria()
    worker(api, certificado, e)
    assert api.get(f"/facturas/{fid}", como=api.admin).json()["entrega"]["estado"] == "ENVIADA" and e.enviados
